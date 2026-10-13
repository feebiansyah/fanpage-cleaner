"""Read-only bounded audit candidates; never a deletion target or global latest guarantee."""

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, unquote, urlsplit

from errors import AppError


FIELDS = {
    "published_posts": "id,from,is_published,created_time,scheduled_publish_time,message,full_picture,permalink_url,status_type",
    "video_reels": "id,from,published,post_id,scheduled_publish_time,description,picture,permalink_url",
    "stories": "id,post_id,media_id",
}
WIB = timezone(timedelta(hours=7), "Asia/Jakarta")
LIMITATIONS = ["source_order_unverified", "story_history_coverage_unverified", "api_coverage_unverified", "post_publication_semantics_not_independently_verified"]
AUDIT_WARNING = ("Audit kandidat API, bukan jaminan N terbaru seluruh Fanpage atau hasil aman dihapus. "
                 "Urutan pagination, cakupan semua Reels, dan histori Story lengkap belum terverifikasi. "
                 "Tanggal Post dari API masih perlu dicocokkan dengan publikasi Facebook; tanggal Reel tanpa relasi Post tidak diketahui. "
                 "Bandingkan dengan tab Postingan Facebook; pin atau susunan khusus dapat berbeda.")


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,40}(?:_[0-9]{1,40})?", value) else None


def publication_date(value):
    try:
        if type(value) in {int, float}:
            return datetime.fromtimestamp(value, timezone.utc)
        if not isinstance(value, str) or len(value) > 64:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def redact(text, secrets):
    if not isinstance(text, str):
        return ""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[disamarkan]").replace(quote(secret, safe=""), "[disamarkan]")
    return "".join(c for c in text if c.isprintable() or c == "\n")[:600]


def safe_url(value, thumbnail=False, secrets=()):
    if not isinstance(value, str) or not value or len(value) > 2048 or not value.isascii() or re.search(r"[\s\\]", value):
        return None
    decoded = unquote(value)
    if any(secret and secret in decoded for secret in secrets):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
            return None
        host = parsed.hostname or ""
        domains = ("fbcdn.net", "fbsbx.com") if thumbnail else ("facebook.com", "fb.watch")
        if not any(host == domain or host.endswith("." + domain) for domain in domains):
            return None
        query = parse_qs(parsed.query, max_num_fields=40)
        if any(any(word in key.lower() for word in ("token", "authorization", "secret")) for key in query):
            return None
        return value
    except (ValueError, TypeError):
        return None


class Source:
    def __init__(self, edge):
        self.edge = edge
        self.cursor = None
        self.seen_cursors = set()
        self.seen_pages = set()
        self.page_count = 0
        self.requests = 0
        self.received = 0
        self.skipped = 0
        self.excluded = 0
        self.exhausted = False
        self.visited = False
        self.status = None
        self.stop_reason = None
        self.candidates = {}

    def attempted(self):
        self.requests += 1

    def summary(self):
        return {"source": self.edge, "requests": self.requests, "pages": self.page_count,
                "received": self.received, "eligible": len(self.candidates),
                "skipped": self.skipped, "excluded_stories": self.excluded,
                "exhausted": self.exhausted, "status": self.status,
                "stop_reason": self.stop_reason}


def stop_error(reason, code="pagination_incomplete"):
    error = AppError(code, 502)
    error.stop_reason = reason
    return error


class ContentLoader:
    def __init__(self, graph, store, settings):
        self.graph, self.store, self.settings = graph, store, settings

    async def _fetch(self, source, page, token_key, secrets, size, deadline, guard):
        guard()
        if source.page_count >= self.settings.posts_max_pages:
            raise stop_error("page_limit")
        source.visited = True
        payload = await self.graph.content_page(page.id, source.edge, FIELDS[source.edge],
            page.access_token, token_key, size, source.cursor, deadline,
            {"status": '["PUBLISHED","ARCHIVED"]'} if source.edge == "stories" else None,
            on_request=source.attempted)
        guard()
        data = payload.get("data")
        if not isinstance(data, list) or len(data) > self.settings.page_size or any(not isinstance(i, dict) for i in data):
            raise stop_error("invalid_response", "upstream_error")
        source.page_count += 1
        source.received += len(data)
        signature = hashlib.sha256(json.dumps(sorted(str(i.get("id", "")) for i in data)).encode()).digest()
        if data and signature in source.seen_pages:
            raise stop_error("pagination_repeated")
        if data:
            source.seen_pages.add(signature)
        paging = payload.get("paging", {})
        if not isinstance(paging, dict):
            return data, stop_error("invalid_pagination")
        if "next" not in paging:
            source.exhausted = True
            return data, None
        try:
            cursor = self.graph._next_cursor(paging, page.id, source.edge)
        except AppError:
            return data, stop_error("invalid_pagination")
        if cursor in source.seen_cursors:
            return data, stop_error("pagination_repeated")
        if any(secret and secret in cursor for secret in secrets):
            return data, stop_error("invalid_pagination")
        source.seen_cursors.add(cursor)
        source.cursor = cursor
        return data, None

    async def load(self, browser_id, session_id, page, limit, deadline):
        sources = [Source("published_posts"), Source("video_reels")]
        stories = Source("stories")
        all_sources = sources + [stories]
        session, _ = self.store.require_page(browser_id, session_id, page.id)
        secrets = [session.user_token] + [p.access_token for p in session.pages if p.access_token]
        token_key = self.store.token_key(page.access_token)
        errors, uncertainties, story_ids = [], [], set()
        story_checked = False
        story_complete = False
        fatal = None

        def guard():
            self.store.require_page(browser_id, session_id, page.id)

        def failure(source, error):
            nonlocal fatal
            if error.code == "session_expired":
                raise error
            source.status = "failed"
            source.stop_reason = getattr(error, "stop_reason", error.code)
            diagnostic = {**error.payload()["error"], "source": source.edge, "stop_reason": source.stop_reason}
            if error.code == "pagination_incomplete":
                diagnostic["message"] = "Pagination sumber konten tidak selesai; hasil parsial."
            if error.code == "timeout":
                diagnostic["message"] = "Waktu pengambilan sumber konten habis; hasil parsial."
            errors.append(diagnostic)
            if error.code in {"token_invalid", "rate_limited", "cooldown", "timeout"}:
                fatal = error.code

        async def acquire(source, size):
            try:
                data, next_error = await self._fetch(source, page, token_key, secrets, size, deadline, guard)
                for item in data:
                    candidate = self._normalize(item, source.edge, page.id, secrets)
                    if candidate is None:
                        source.skipped += 1
                    elif any(value in story_ids for value in candidate["_story_ids"]):
                        source.excluded += 1
                    else:
                        source.candidates.setdefault(candidate["object_id"], candidate)
                if next_error:
                    raise next_error
                if source.exhausted:
                    source.status, source.stop_reason = "source_exhausted", "source_exhausted"
            except AppError as error:
                failure(source, error)

        async def check_stories():
            nonlocal story_checked, story_complete
            story_checked = True
            try:
                # No source has verified Story exclusion. One bounded probe preserves
                # known-ID exclusion; an incomplete probe never enables selection.
                size = min(limit, self.settings.page_size, self.settings.story_max_items)
                data, next_error = await self._fetch(stories, page, token_key, secrets, size, deadline, guard)
                references_complete = True
                for item in data:
                    for key in ("id", "post_id", "media_id"):
                        value = identifier(item.get(key))
                        if value:
                            story_ids.add(value)
                    if not identifier(item.get("post_id")) or not identifier(item.get("media_id")):
                        references_complete = False
                for source in sources:
                    for key, candidate in list(source.candidates.items()):
                        if any(value in story_ids for value in candidate["_story_ids"]):
                            del source.candidates[key]
                            source.excluded += 1
                if next_error:
                    raise next_error
                if not references_complete:
                    uncertainties.append("story_reference_unverified")
                story_complete = stories.exhausted and references_complete
                stories.status = "source_exhausted" if stories.exhausted else "limit_reached"
                stories.stop_reason = "source_exhausted" if stories.exhausted else "story_verification_budget"
                if not stories.exhausted:
                    uncertainties.append("story_probe_incomplete")
            except AppError as error:
                failure(stories, error)
                uncertainties.append("story_probe_failed")

        try:
            # Neither content source is proven to subsume the other, so visit both
            # once before deciding on a global unique-candidate stop.
            for source in sources:
                if fatal:
                    break
                await acquire(source, min(self.settings.page_size, limit))
            items, unlinked = self._merge(sources)
            if items and not fatal:
                await check_stories()
            while not fatal:
                items, unlinked = self._merge(sources)
                if len(items) >= limit:
                    break
                active = [s for s in sources if s.status is None and not s.exhausted]
                if not active:
                    break
                for source in active:
                    items, unlinked = self._merge(sources)
                    if len(items) >= limit or fatal:
                        break
                    await acquire(source, min(self.settings.page_size, max(1, limit - len(items))))
                    items, unlinked = self._merge(sources)
                    if items and not story_checked and not fatal:
                        await check_stories()
            guard()
        except asyncio.CancelledError:
            error = AppError("session_expired", 401) if session.expires_at <= time.monotonic() else AppError("load_cancelled", 409)
            for source in all_sources:
                if source.status is None:
                    source.status, source.stop_reason = "cancelled", error.code
            error.sources = [s.summary() for s in all_sources]
            raise error from None
        except AppError as error:
            for source in all_sources:
                if source.status is None:
                    source.status, source.stop_reason = "cancelled", error.code
            error.sources = [s.summary() for s in all_sources]
            raise

        items, unlinked = self._merge(sources)
        items.sort(key=lambda i: (i["_date"] is None, -i["_date"].timestamp() if i["_date"] else 0, i["key"]))
        found = len(items)
        for source in sources:
            if source.status is None:
                source.status = "cancelled" if fatal else "limit_reached"
                source.stop_reason = "stopped_after_" + fatal if fatal else "unique_limit"
        if stories.status is None:
            stories.status = "cancelled" if fatal else "not_needed"
            stories.stop_reason = "stopped_after_" + fatal if fatal else "no_eligible_candidates"
        selected = items[:limit]
        for item in selected:
            date = item.pop("_date")
            item.pop("_linked_post", None)
            item.pop("_story_ids", None)
            item["published_at"] = date.isoformat() if date else None
            item["published_wib"] = date.astimezone(WIB).strftime("%d/%m/%Y %H:%M:%S WIB") if date else "Tanggal tidak tersedia"
            item["selectable"] = story_complete
        limitations = list(LIMITATIONS) + uncertainties
        if unlinked:
            limitations.append("cross_source_identity_unverified")
        if any(s.skipped for s in sources):
            limitations.append("unverified_or_unpublished_candidates_skipped")
        exhausted = all(s.exhausted for s in sources)
        state = "partial" if errors else "limit_reached" if found >= limit else "empty" if not selected else "source_exhausted"
        result = {"page_id": page.id, "limit": limit, "state": state, "items": selected,
                  "found": found, "displayed": len(selected), "scanned": sum(s.received for s in sources),
                  "excluded_stories": sum(s.excluded for s in sources), "sources_exhausted": exhausted,
                  "limit_reached": found >= limit, "retrieval_complete": not errors, "coverage_complete": False,
                  "latest_guaranteed": False, "story_exclusion": "matched_ids_only" if story_complete else "unverified",
                  "bulk_selectable": not errors and story_complete, "safe_for_delete": False,
                  "limitations": limitations, "warning": AUDIT_WARNING,
                  "sources": [s.summary() for s in all_sources], "errors": errors}
        if not story_complete and selected:
            result["warning"] += " Pemeriksaan Story belum memadai; seluruh checkbox dinonaktifkan."
        if errors:
            result["error"] = errors[0]
        return result

    def _normalize(self, item, edge, page_id, secrets):
        raw_id = identifier(item.get("id"))
        owner = item.get("from")
        if not raw_id or not isinstance(owner, dict) or owner.get("id") != page_id:
            return None
        published = item.get("is_published") if edge == "published_posts" else item.get("published")
        if published is not True:
            return None
        scheduled = item.get("scheduled_publish_time")
        if type(scheduled) in {int, float} and scheduled > time.time():
            return None
        is_reel = edge == "video_reels"
        status_type = item.get("status_type")
        kind = "Reel" if is_reel else "Foto" if status_type == "added_photos" else "Video" if status_type == "added_video" else "Postingan"
        linked_post = identifier(item.get("post_id")) if is_reel else None
        date = None if is_reel else publication_date(item.get("created_time"))
        return {"key": edge + ":" + raw_id, "object_id": raw_id, "page_id": page_id, "kind": kind,
                "sources": [{"source": edge, "object_id": raw_id}],
                "publication_basis": "post_created_time" if date else "unknown",
                "text": redact(item.get("description") if is_reel else item.get("message"), secrets),
                "thumbnail": safe_url(item.get("picture") if is_reel else item.get("full_picture"), True, secrets),
                "permalink": safe_url(item.get("permalink_url"), False, secrets),
                "_date": date, "_linked_post": linked_post,
                "_story_ids": [value for value in (raw_id, linked_post) if value]}

    def _merge(self, sources):
        # Pure: repeated counting must not append the same provenance twice.
        posts = {key: {**value, "sources": list(value["sources"])} for key, value in sources[0].candidates.items()}
        items = list(posts.values())
        related = dict(posts)
        unlinked = False
        for original in sources[1].candidates.values():
            reel = {**original, "sources": list(original["sources"])}
            target = related.get(reel["_linked_post"]) if reel["_linked_post"] else None
            if target is not None:
                target["kind"] = "Reel"
                target["sources"].extend(reel["sources"])
                target["permalink"] = reel["permalink"] or target["permalink"]
                target["thumbnail"] = target["thumbnail"] or reel["thumbnail"]
            else:
                items.append(reel)
                if reel["_linked_post"]:
                    related[reel["_linked_post"]] = reel
                unlinked = True
        return items, unlinked
