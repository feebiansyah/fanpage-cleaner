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
    "stories": "id,post_id,media_id,status",
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
        self.exhausted = False
        self.stopped = False
        self.candidates = {}


class ContentLoader:
    def __init__(self, graph, store, settings):
        self.graph, self.store, self.settings = graph, store, settings

    async def load(self, browser_id, session_id, page, limit, deadline):
        token_key = self.store.token_key(page.access_token)
        session, _ = self.store.require_page(browser_id, session_id, page.id)
        secrets = [session.user_token] + [p.access_token for p in session.pages if p.access_token]
        story_ids, story_complete, errors = set(), False, []
        excluded, scanned, skipped = 0, 0, 0
        sources = [Source("published_posts"), Source("video_reels")]
        stories = Source("stories")

        async def fetch(source, size):
            self.store.require_page(browser_id, session_id, page.id)
            if source.page_count >= self.settings.posts_max_pages:
                raise AppError("pagination_incomplete", 502)
            payload = await self.graph.content_page(page.id, source.edge, FIELDS[source.edge], page.access_token,
                token_key, size, source.cursor, deadline,
                {"status": '["PUBLISHED","ARCHIVED"]'} if source.edge == "stories" else None)
            self.store.require_page(browser_id, session_id, page.id)
            data = payload.get("data")
            if not isinstance(data, list) or len(data) > self.settings.page_size or any(not isinstance(i, dict) for i in data):
                raise AppError("upstream_error", 502)
            signature = hashlib.sha256(json.dumps(sorted(str(item.get("id", "")) for item in data)).encode()).digest()
            if data and signature in source.seen_pages:
                raise AppError("pagination_incomplete", 502)
            if data:
                source.seen_pages.add(signature)
            source.page_count += 1
            paging = payload.get("paging", {})
            if not isinstance(paging, dict):
                raise AppError("pagination_incomplete", 502)
            # Do not lose useful records if a malicious/repeated next is discovered.
            next_error = None
            if "next" not in paging:
                source.exhausted = True
            else:
                try:
                    cursor = self.graph._next_cursor(paging, page.id, source.edge)
                    if cursor in source.seen_cursors or any(secret and secret in cursor for secret in secrets):
                        raise AppError("pagination_incomplete", 502)
                    source.seen_cursors.add(cursor)
                    source.cursor = cursor
                except AppError as error:
                    next_error = error
            return data, next_error

        def record_error(error):
            if error.code == "session_expired":
                raise error
            errors.append(error)

        try:
            story_count = 0
            while not stories.exhausted:
                data, next_error = await fetch(stories, min(self.settings.page_size, self.settings.story_max_items - story_count))
                for item in data:
                    for key in ("id", "post_id", "media_id"):
                        value = identifier(item.get(key))
                        if value:
                            story_ids.add(value)
                    # Missing cross-reference means exclusion cannot be claimed verified.
                    if not identifier(item.get("post_id")) or not identifier(item.get("media_id")):
                        errors.append(AppError("pagination_incomplete", 502))
                    story_count += 1
                if next_error:
                    raise next_error
                if story_count >= self.settings.story_max_items and not stories.exhausted:
                    raise AppError("pagination_incomplete", 502)
            story_complete = not errors
        except AppError as error:
            record_error(error)

        # Round-robin bounded acquisition. No assumption of API chronological order.
        while any(not s.exhausted and not s.stopped and len(s.candidates) < limit for s in sources):
            for source in sources:
                if source.exhausted or source.stopped or len(source.candidates) >= limit:
                    continue
                try:
                    data, next_error = await fetch(source, min(self.settings.page_size, max(1, limit - len(source.candidates))))
                    scanned += len(data)
                    for item in data:
                        candidate = self._normalize(item, source.edge, page.id, secrets)
                        if candidate is None:
                            skipped += 1
                            continue
                        if any(value in story_ids for value in candidate.pop("_story_ids")):
                            excluded += 1
                            continue
                        source.candidates.setdefault(candidate["object_id"], candidate)
                    if next_error:
                        raise next_error
                except AppError as error:
                    record_error(error)
                    source.stopped = True
                    if error.code in {"token_invalid", "rate_limited", "cooldown", "timeout"}:
                        for remaining in sources:
                            remaining.stopped = True
                        break

        self.store.require_page(browser_id, session_id, page.id)
        items, unlinked = self._merge(sources)
        items.sort(key=lambda item: (item["_date"] is None,
                    -item["_date"].timestamp() if item["_date"] else 0, item["key"]))
        found = len(items)
        selected = items[:limit]
        for item in selected:
            date = item.pop("_date")
            item.pop("_linked_post", None)
            item.pop("_video_id", None)
            item["published_at"] = date.isoformat() if date else None
            item["published_wib"] = date.astimezone(WIB).strftime("%d/%m/%Y %H:%M:%S WIB") if date else "Tanggal tidak tersedia"
            item["selectable"] = story_complete
        limitations = list(LIMITATIONS)
        if unlinked:
            limitations.append("cross_source_identity_unverified")
        if skipped:
            limitations.append("unverified_or_unpublished_candidates_skipped")
        if not story_complete:
            limitations.append("story_scan_incomplete")
        exhausted = all(source.exhausted for source in sources)
        state = "partial" if errors else "limit_reached" if found >= limit else "empty" if not selected else "source_exhausted"
        result = {"page_id": page.id, "limit": limit, "state": state, "items": selected,
                  "found": found, "displayed": len(selected), "scanned": scanned, "excluded_stories": excluded,
                  "sources_exhausted": exhausted, "limit_reached": found >= limit,
                  "retrieval_complete": not errors, "coverage_complete": False, "latest_guaranteed": False,
                  "story_exclusion": "matched_ids_only" if story_complete else "unverified",
                  "bulk_selectable": not errors and story_complete, "safe_for_delete": False,
                  "limitations": limitations, "warning": AUDIT_WARNING,
                  "sources": [{"source": s.edge, "pages": s.page_count, "exhausted": s.exhausted} for s in sources]}
        if errors:
            result["error"] = errors[0].payload()["error"]
            if errors[0].code == "pagination_incomplete":
                result["error"]["message"] = "Pagination atau pencocokan Story tidak selesai. Hasil audit parsial; pilihan massal dinonaktifkan."
            elif errors[0].code == "timeout":
                result["error"]["message"] = "Waktu Load Posts habis. Hasil yang ditampilkan belum lengkap."
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
        raw_type = item.get("type")
        status_type = item.get("status_type")
        kind = "Reel" if is_reel else "Foto" if raw_type == "photo" or status_type == "added_photos" else "Video" if raw_type == "video" or status_type == "added_video" else "Postingan"
        linked_post = identifier(item.get("post_id")) if is_reel else None
        video_id = identifier(item.get("object_id")) if not is_reel and kind == "Video" else None
        date = None if is_reel else publication_date(item.get("created_time"))
        return {"key": edge + ":" + raw_id, "object_id": raw_id, "page_id": page_id, "kind": kind,
                "sources": [{"source": edge, "object_id": raw_id}],
                "publication_basis": "post_created_time" if date else "unknown",
                "text": redact(item.get("description") if is_reel else item.get("message"), secrets),
                "thumbnail": safe_url(item.get("picture") if is_reel else item.get("full_picture"), True, secrets),
                "permalink": safe_url(item.get("permalink_url"), False, secrets),
                "_date": date, "_linked_post": linked_post, "_video_id": video_id,
                "_story_ids": [value for value in (raw_id, linked_post, video_id) if value]}

    def _merge(self, sources):
        posts, reels = sources[0].candidates, sources[1].candidates
        items = list(posts.values())
        videos = {p["_video_id"]: p for p in items if p["_video_id"]}
        unlinked = False
        for reel in reels.values():
            target = posts.get(reel["_linked_post"]) if reel["_linked_post"] else None
            target = target or videos.get(reel["object_id"])
            if target is not None:
                target["kind"] = "Reel"
                target["sources"].extend(reel["sources"])
                target["permalink"] = reel["permalink"] or target["permalink"]
                target["thumbnail"] = target["thumbnail"] or reel["thumbnail"]
            else:
                items.append(reel)
                unlinked = True
        return items, unlinked
