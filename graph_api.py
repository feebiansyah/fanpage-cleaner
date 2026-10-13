"""Shared bounded GET transport. Never follow upstream URLs or expose Meta messages."""

import asyncio
import hashlib
import json
import math
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

import httpx

from config import Settings
from errors import AppError
from sessions import MemoryStore, Page


def retry_after(value: str | None):
    if not value or len(value) > 128:
        return None
    try:
        if value.isascii() and value.isdigit():
            seconds = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = math.ceil((date - datetime.now(timezone.utc)).total_seconds())
        # Retain long valid instructions as cooldowns; never shorten them to retry sooner.
        return float(seconds) if seconds >= 0 and math.isfinite(float(seconds)) else None
    except (ValueError, OverflowError, TypeError):
        return None


class GraphClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings, store: MemoryStore):
        self.http = http
        self.settings = settings
        self.store = store

    async def accounts(self, token: str, token_key: str):
        pages = {}
        seen_cursors = set()
        seen_pages = set()
        cursor = None
        completed = 0
        deadline = time.monotonic() + self.settings.operation_timeout
        try:
            for number in range(self.settings.max_pages):
                self.store.check_cooldown(token_key)
                payload = await self._request(token, token_key, cursor, deadline)
                data = payload.get("data")
                if not isinstance(data, list) or len(data) > self.settings.page_size:
                    raise AppError("upstream_error", 502)
                parsed_pages = [self._page(item) for item in data]
                # An empty page with next is legal; repeated nonempty page indicates no progress.
                signature = hashlib.sha256(json.dumps(sorted(page.id for page in parsed_pages)).encode()).digest()
                if parsed_pages and signature in seen_pages:
                    raise AppError("pagination_incomplete", 502)
                if parsed_pages:
                    seen_pages.add(signature)
                for page in parsed_pages:
                    if page.id not in pages:
                        pages[page.id] = page
                completed += 1
                paging = payload.get("paging", {})
                if not isinstance(paging, dict):
                    raise AppError("pagination_incomplete", 502)
                if "next" not in paging:
                    return list(pages.values())
                cursor = self._next_cursor(paging)
                if token in cursor or any(page.access_token and page.access_token in cursor for page in pages.values()):
                    raise AppError("pagination_incomplete", 502)
                if cursor in seen_cursors:
                    raise AppError("pagination_incomplete", 502)
                seen_cursors.add(cursor)
            raise AppError("pagination_incomplete", 502)
        except asyncio.CancelledError:
            if completed:
                failure = AppError("pagination_incomplete", 504)
                failure.cause_code = "timeout"
                raise failure from None
            raise
        except AppError as error:
            if completed and error.code != "pagination_incomplete":
                # Preserve cause code and cooldown, but never return a partial list.
                failure = AppError("pagination_incomplete", error.status, error.retry_after)
                failure.cause_code = error.code
                raise failure from None
            raise

    def _page(self, item):
        if not isinstance(item, dict):
            raise AppError("upstream_error", 502)
        page_id, name, token = item.get("id"), item.get("name"), item.get("access_token", "")
        if (not isinstance(page_id, str) or not re.fullmatch(r"[0-9]{1,40}", page_id)
                or not isinstance(name, str) or not name or len(name) > 512
                or not isinstance(token, str) or len(token) > self.settings.max_token_length
                or (token and not re.fullmatch(r"[!-~]+", token))):
            raise AppError("upstream_error", 502)
        name = "".join(character for character in name if character.isprintable())
        return Page(page_id, name, token)

    def _next_cursor(self, paging, page_id=None, edge=None):
        link = paging["next"]
        if not isinstance(link, str) or len(link) > 32768:
            raise AppError("pagination_incomplete", 502)
        try:
            parsed = urlsplit(link)
            path_pattern = ("/" + re.escape(self.settings.graph_version) + r"/(me|[0-9]{1,40})/accounts/?" if page_id is None else
                            "/" + re.escape(self.settings.graph_version) + "/" + re.escape(page_id) + "/" + re.escape(edge) + "/?")
            if (parsed.scheme != "https" or parsed.hostname != "graph.facebook.com"
                    or parsed.port not in {None, 443} or parsed.username or parsed.password or parsed.fragment
                    or not re.fullmatch(path_pattern, parsed.path)):
                raise ValueError()
            query = parse_qs(parsed.query, max_num_fields=20)
            cursors = paging.get("cursors", {})
            after = cursors.get("after") if isinstance(cursors, dict) else None
            url_cursors = query.get("after", [])
            if len(url_cursors) != 1:
                raise ValueError()
            if after is None:
                after = url_cursors[0]
            if after != url_cursors[0] or not isinstance(after, str) or not re.fullmatch(r"[A-Za-z0-9_+=/\-]{1,4096}", after):
                raise ValueError()
            return after
        except (ValueError, TypeError):
            raise AppError("pagination_incomplete", 502) from None

    def _usage(self, headers, token_key):
        # Only numeric allowlisted values are inspected; raw headers are never retained/logged.
        for header, global_scope in [("x-app-usage", True), ("x-business-use-case-usage", True)]:
            raw = headers.get(header)
            if not raw or len(raw) > 16384:
                continue
            try:
                usage = json.loads(raw)
            except (ValueError, TypeError, RecursionError):
                continue
            if not isinstance(usage, dict):
                continue
            if header == "x-app-usage":
                items = [usage]
            else:
                items = [entry for entries in usage.values() if isinstance(entries, list) for entry in entries]
            for item in items:
                if not isinstance(item, dict):
                    continue
                values = [item.get(key) for key in ("call_count", "total_time", "total_cputime")]
                values = [v for v in values if type(v) in {int, float} and 0 <= v <= 100 and math.isfinite(v)]
                if values and max(values) >= self.settings.usage_threshold:
                    # Local safety policy, not a claim about official quota/reset timing.
                    self.store.cooldown(token_key, self.settings.cooldown_seconds, global_scope)

    async def _request(self, token, token_key, cursor, deadline):
        params = {"fields": "id,name,access_token", "limit": str(self.settings.page_size)}
        if cursor is not None:
            params["after"] = cursor
        endpoint = "https://graph.facebook.com/" + self.settings.graph_version + "/me/accounts"
        return await self._get(token, token_key, endpoint, params, deadline)

    async def content_page(self, page_id, edge, fields, token, token_key, limit, cursor, deadline, extra=None, on_request=None):
        if edge not in {"published_posts", "video_reels", "stories"} or not re.fullmatch(r"[0-9]{1,40}", page_id):
            raise AppError("invalid_page")
        endpoint = "https://graph.facebook.com/" + self.settings.graph_version + "/" + page_id + "/" + edge
        params = {"fields": fields, "limit": str(limit), **(extra or {})}
        if cursor is not None:
            params["after"] = cursor
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppError("timeout", 504)
        try:
            return await asyncio.wait_for(self._get(token, token_key, endpoint, params, deadline, on_request), remaining)
        except asyncio.TimeoutError:
            raise AppError("timeout", 504) from None

    async def _get(self, token, token_key, endpoint, params, deadline, on_request=None):
        for attempt in range(3):  # One initial attempt, at most two retries.
            self.store.check_cooldown(token_key)
            if time.monotonic() >= deadline:
                raise AppError("timeout", 504)
            transient, instruction = False, None
            try:
                if on_request is not None:
                    on_request()
                async with self.http.stream("GET", endpoint, params=params, headers={"Authorization": "Bearer " + token}) as response:
                    status, headers = response.status_code, response.headers
                    if 300 <= status < 400:
                        raise AppError("upstream_error", 502)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self.settings.max_response_bytes:
                            raise AppError("upstream_error", 502)
                        body.extend(chunk)
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError, RecursionError):
                        payload = None
                self._usage(headers, token_key)
                instruction = retry_after(headers.get("retry-after"))
                error = payload.get("error") if isinstance(payload, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                code = code if type(code) is int else None
                if code in {102, 190} or status == 401:
                    raise AppError("token_invalid", 401)
                if code in {10, 200}:
                    raise AppError("permission_denied", 403)
                if status == 429 or code in {4, 17, 32, 613, 80001, 80002, 80003, 80004, 80005, 80006}:
                    seconds = max(instruction or 0, self.settings.cooldown_seconds)
                    # App/unknown/page scope conservatively stops all local Graph work.
                    self.store.cooldown(token_key, seconds, global_scope=code != 17)
                    raise AppError("rate_limited", 429, math.ceil(seconds))
                if status == 403:
                    raise AppError("permission_denied", 403)
                if 200 <= status < 300 and error is None:
                    if not isinstance(payload, dict):
                        raise AppError("upstream_error", 502)
                    return payload
                transient = (status in {500, 502, 503, 504} or
                             (isinstance(error, dict) and error.get("is_transient") is True))
                failure = AppError("upstream_error", 502)
            except httpx.TimeoutException:
                transient, failure = True, AppError("timeout", 504)
            except (httpx.NetworkError, httpx.RemoteProtocolError):
                transient, failure = True, AppError("network_error", 502)
            except httpx.HTTPError:
                raise AppError("network_error", 502) from None
            if not transient:
                raise failure from None
            wait = self.settings.retry_base * (2 ** attempt) + random.uniform(0, self.settings.retry_jitter)
            if instruction is not None:
                wait = max(wait, instruction)
                self.store.cooldown(token_key, instruction)
                if instruction > self.settings.max_retry_wait or time.monotonic() + wait >= deadline:
                    raise AppError("cooldown", 429, max(1, math.ceil(instruction)))
            if attempt == 2:
                raise failure from None
            if wait > self.settings.max_retry_wait or time.monotonic() + wait >= deadline:
                raise AppError("timeout", 504)
            await asyncio.sleep(wait)
        raise AppError("upstream_error", 502)
