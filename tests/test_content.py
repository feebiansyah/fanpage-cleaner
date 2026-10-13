import asyncio
import logging
import time
import unittest
from dataclasses import replace
from io import StringIO

import httpx

from app import create_app
from config import Settings


USER_TOKEN = "dummy-user-secret"
PAGE_TOKEN = "dummy-page-secret"
HEADERS = {"X-Fanpage-Client": "1", "Origin": "http://127.0.0.1:8000"}


def post(n, date="2026-10-10T10:00:00+0000", **extra):
    return {"id": f"11_{n}", "from": {"id": "11"}, "is_published": True,
            "created_time": date, "message": "Isi postingan", "status_type": "mobile_status_update",
            "permalink_url": f"https://www.facebook.com/11/posts/{n}", **extra}


def reel(n, post_id=None, **extra):
    return {"id": str(n), "from": {"id": "11"}, "published": True,
            "post_id": post_id, "created_time": "2026-10-13T10:00:00+0000",
            "description": "Reel", "permalink_url": f"https://www.facebook.com/reel/{n}", **extra}


def response(data, edge=None, cursor=None):
    payload = {"data": data}
    if cursor is not None:
        payload["paging"] = {"cursors": {"after": cursor}, "next": f"https://graph.facebook.com/v26.0/11/{edge}?after={cursor}&access_token={PAGE_TOKEN}"}
    return httpx.Response(200, json=payload)


class LoadFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.queues = {"stories": [response([])], "published_posts": [response([post(1)])], "video_reels": [response([])]}
        self.settings = Settings(graph_request_interval=.001, retry_base=0.001, retry_jitter=0, local_attempts=20, global_attempts=50)
        async def handler(request):
            self.calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.host, "graph.facebook.com")
            edge = request.url.path.rsplit("/", 1)[-1]
            self.assertNotIn(PAGE_TOKEN, str(request.url))
            if edge == "accounts":
                self.assertEqual(request.headers["authorization"], "Bearer " + USER_TOKEN)
                return response([{"id": "11", "name": "Fanpage", "access_token": PAGE_TOKEN}, {"id": "22", "name": "Tanpa token"}])
            self.assertEqual(request.headers["authorization"], "Bearer " + PAGE_TOKEN)
            item = self.queues[edge].pop(0)
            if isinstance(item, Exception):
                raise item
            if callable(item):
                return await item(request)
            return item
        self.application = create_app(self.settings, httpx.MockTransport(handler))
        self.assertIn("/api/posts/load", [route.path for route in self.application.routes], "Endpoint Load Posts belum dibuat")
        self.lifecycle = self.application.router.lifespan_context(self.application)
        await self.lifecycle.__aenter__()
        self.client = self.browser()
        await self.client.get("/api/session", headers=HEADERS)
        await self.client.post("/api/connect", json={"token": USER_TOKEN}, headers=HEADERS)
        self.calls.clear()

    def browser(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application, client=("127.0.0.1", 1)), base_url=HEADERS["Origin"])

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()
            await self.lifecycle.__aexit__(None, None, None)

    async def load(self, limit=100, page_id="11", client=None, headers=None):
        return await (client or self.client).post("/api/posts/load", json={"page_id": page_id, "limit": limit}, headers=headers or HEADERS)

    def error(self, result, code):
        self.assertEqual(result.json()["error"]["code"], code)
        self.assertNotIn(PAGE_TOKEN, result.text)
        self.assertNotIn(USER_TOKEN, result.text)


class LoadContract(LoadFixture):
    async def test_limit_one_and_cache_local(self):
        result = await self.load(1)
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(data["displayed"], 1)
        self.assertEqual(data["state"], "limit_reached")
        self.assertFalse(data["latest_guaranteed"])
        self.assertFalse(data["safe_for_delete"])
        self.assertEqual(data["items"][0]["object_id"], "11_1")
        before = len(self.calls)
        cached = await self.client.get("/api/posts", params={"page_id": "11"}, headers=HEADERS)
        self.assertEqual(cached.json()["items"], data["items"])
        self.assertEqual(len(self.calls), before)
        self.assertEqual(result.headers["cache-control"], "no-store")

    async def test_limit_100_and_thirty_item_pages_with_next(self):
        self.queues["published_posts"] = [response([post(n) for n in range(i, i + 30)], "published_posts", f"C{i}") for i in (1, 31, 61)]
        self.queues["published_posts"].append(response([post(n) for n in range(91, 101)]))
        result = await self.load(100)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["displayed"], 100)
        self.assertEqual(len([r for r in self.calls if r.url.path.endswith("/published_posts")]), 4)

    async def test_invalid_inputs_never_call_meta(self):
        for limit in [0, 5001, -1, 1.5, True, "100", None]:
            self.error(await self.load(limit), "invalid_limit")
        for value in ["", "11_1", 11, "javascript:1"]:
            self.error(await self.load(1, value), "invalid_page")
        self.assertEqual(len(self.calls), 0)

    async def test_story_and_duplicate_removed_before_limit(self):
        self.queues["stories"] = [response([{"id": "S1", "post_id": "11_1", "media_id": "900", "status": "PUBLISHED"}])]
        self.queues["published_posts"] = [response([post(1), post(2), post(2)], "published_posts", "C2"), response([post(3)])]
        result = await self.load(2)
        self.assertEqual(result.status_code, 200)
        self.assertEqual([item["object_id"] for item in result.json()["items"]], ["11_2", "11_3"])
        self.assertGreaterEqual(result.json()["excluded_stories"], 1)

    async def test_reels_merge_date_from_post_relation_and_global_sort(self):
        self.queues["published_posts"] = [response([post(1, "2026-10-01T00:00:00Z"), post(2, "2026-10-12T00:00:00Z", status_type="added_video"), post(3, None)])]
        self.queues["video_reels"] = [response([reel(900, "11_2"), reel(901)])]
        result = await self.load(3)
        data = result.json()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(data["items"][0]["kind"], "Reel")
        self.assertEqual(data["items"][0]["published_at"], "2026-10-12T00:00:00+00:00")
        self.assertEqual(data["items"][0]["published_wib"], "12/10/2026 07:00:00 WIB")
        self.assertEqual(len([i for i in data["items"] if any(s["object_id"] == "900" for s in i["sources"])]), 1)
        self.assertEqual(data["found"], 4)

    async def test_cross_source_without_proven_relation_not_silently_dropped(self):
        self.queues["video_reels"] = [response([reel(900)])]
        result = await self.load(100)
        self.assertEqual(result.json()["displayed"], 2)
        self.assertIn("cross_source_identity_unverified", result.json()["limitations"])

    async def test_same_date_stable_missing_date_not_updated_time(self):
        self.queues["published_posts"] = [response([post(2), post(1), post(3, None, updated_time="2099-01-01T00:00:00Z")])]
        data = (await self.load()).json()
        self.assertEqual([i["object_id"] for i in data["items"]], ["11_1", "11_2", "11_3"])
        self.assertIsNone(data["items"][-1]["published_at"])
        self.assertEqual(data["items"][-1]["published_wib"], "Tanggal tidak tersedia")

    async def test_exhausted_empty_and_no_after_loop(self):
        payload = {"data": [], "paging": {"cursors": {"after": "LAST"}}}
        self.queues["published_posts"] = [httpx.Response(200, json=payload)]
        result = await self.load()
        self.assertEqual(result.json()["state"], "empty")
        self.assertTrue(result.json()["sources_exhausted"])
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(any(request.url.path.endswith("/stories") for request in self.calls))

    async def test_next_page_errors_are_partial_not_success(self):
        for fault, cause in [(httpx.Response(403), "permission_denied"), (httpx.Response(429), "rate_limited"),
                             (httpx.ReadTimeout(PAGE_TOKEN), "timeout")]:
            with self.subTest(cause=cause):
                self.application.state.store.global_cooldown_until = 0
                self.application.state.store.token_cooldowns.clear()
                self.queues["stories"] = [response([])]
                self.queues["published_posts"] = [response([post(1)], "published_posts", "C1")] + [fault] * 3
                self.queues["video_reels"] = [response([])]
                data = (await self.load(100)).json()
                self.assertEqual(data["state"], "partial")
                self.assertFalse(data["bulk_selectable"])
                self.assertEqual(data["error"]["code"], cause)

    async def test_story_or_reel_failure_cannot_claim_coverage(self):
        for edge in ["stories", "video_reels"]:
            with self.subTest(edge=edge):
                self.queues = {"stories": [response([])], "published_posts": [response([post(1)])], "video_reels": [response([])]}
                self.queues[edge] = [httpx.Response(403)]
                data = (await self.load()).json()
                self.assertEqual(data["state"], "partial")
                self.assertFalse(data["coverage_complete"])
                self.assertFalse(data["bulk_selectable"])
                if edge == "stories":
                    self.assertTrue(all(not i["selectable"] for i in data["items"]))

    async def test_owner_and_page_token_validation(self):
        self.error(await self.load(1, "999"), "page_not_owned")
        self.error(await self.load(1, "22"), "page_token_missing")
        async with self.browser() as other:
            await other.get("/api/session", headers=HEADERS)
            self.error(await self.load(1, client=other), "session_expired")
        self.assertEqual(len(self.calls), 0)

    async def test_duplicate_load_and_disconnect_cancels_without_resurrection(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(request):
            entered.set()
            await release.wait()
            return response([post(1)])
        self.queues["published_posts"] = [slow]
        first = asyncio.create_task(self.load())
        await asyncio.wait_for(entered.wait(), 3)
        self.error(await self.load(), "load_busy")
        self.assertEqual((await self.client.post("/api/disconnect", headers=HEADERS)).status_code, 200)
        release.set()
        result = await first
        self.error(result, "load_cancelled")
        self.assertFalse((await self.client.get("/api/session", headers=HEADERS)).json()["connected"])
        self.assertEqual(len(self.application.state.store.sessions), 0)

    async def test_session_expiry_never_extends_ttl_or_saves_results(self):
        session = next(iter(self.application.state.store.sessions.values()))
        session.expires_at = time.monotonic() + .03
        async def slow(request):
            await asyncio.sleep(.1)
            return response([post(1)])
        self.queues["published_posts"] = [slow]
        self.error(await self.load(), "session_expired")
        self.assertLessEqual(session.expires_at, time.monotonic())

    async def test_secret_text_urls_and_logs_are_sanitized(self):
        stream, handler = StringIO(), None
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        try:
            self.queues["published_posts"] = [response([post(1, message=PAGE_TOKEN + USER_TOKEN,
                full_picture="javascript:alert(1)", permalink_url="https://user:pass@www.facebook.com/11")])]
            result = await self.load()
            self.assertNotIn(PAGE_TOKEN, result.text + stream.getvalue())
            self.assertNotIn(USER_TOKEN, result.text + stream.getvalue())
            self.assertIsNone(result.json()["items"][0]["thumbnail"])
            self.assertIsNone(result.json()["items"][0]["permalink"])
        finally:
            logging.getLogger().removeHandler(handler)

    async def test_untrusted_pagination_and_large_response(self):
        self.queues["published_posts"] = [httpx.Response(200, json={"data": [post(1)], "paging": {"next": "https://evil.example/x?after=C1", "cursors": {"after": "C1"}}})]
        result = await self.load()
        self.assertEqual(result.json()["state"], "partial")
        self.assertFalse(result.json()["bulk_selectable"])
        self.queues["stories"] = [response([])]
        self.queues["published_posts"] = [httpx.Response(200, content=b"x" * 1048577)]
        self.queues["video_reels"] = [response([])]
        self.assertEqual((await self.load()).json()["state"], "partial")

    async def test_repeated_page_and_page_limit_partial(self):
        self.queues["published_posts"] = [response([post(1)], "published_posts", "C1"), response([post(1)], "published_posts", "C2")]
        data = (await self.load()).json()
        self.assertEqual(data["state"], "partial")
        self.assertEqual(data["error"]["code"], "pagination_incomplete")

    async def test_origin_rejected_and_cache_other_page_not_returned(self):
        self.assertEqual((await self.load(headers={**HEADERS, "Origin": "https://evil.example"})).status_code, 403)
        await self.load()
        self.error(await self.client.get("/api/posts", params={"page_id": "999"}, headers=HEADERS), "page_not_owned")
        self.assertEqual((await self.client.get("/api/posts", params={"page_id": "22"}, headers=HEADERS)).json()["items"], [])

    async def test_drafts_visitor_and_future_schedule_excluded(self):
        self.queues["published_posts"] = [response([post(1, is_published=False), post(2, **{"from": {"id": "99"}}),
            post(3, scheduled_publish_time=4102444800), post(4, story="Graph story text is not Facebook Story")])]
        data = (await self.load()).json()
        self.assertEqual([i["object_id"] for i in data["items"]], ["11_4"])

    async def test_story_missing_cross_reference_disables_individual_selection(self):
        self.queues["stories"] = [response([{"id": "123", "status": "PUBLISHED"}])]
        data = (await self.load()).json()
        self.assertEqual(data["state"], "source_exhausted")
        self.assertIn("story_reference_unverified", data["limitations"])
        self.assertTrue(data["retrieval_complete"])
        self.assertFalse(data["bulk_selectable"])
        self.assertTrue(all(not item["selectable"] for item in data["items"]))

    async def test_memory_capacity_before_meta_and_other_browser_cache_isolated(self):
        self.application.state.store.audits.settings = replace(self.settings, max_audit_entries=1)
        await self.load(1)
        async with self.browser() as other:
            await other.get("/api/session", headers=HEADERS)
            await other.post("/api/connect", json={"token": USER_TOKEN}, headers=HEADERS)
            cached = await other.get("/api/posts", params={"page_id": "11"}, headers=HEADERS)
            self.assertEqual(cached.json()["items"], [])
            before = len(self.calls)
            self.error(await self.load(1, client=other), "audit_capacity")
            self.assertEqual(len(self.calls), before)
        self.assertEqual((await self.client.get("/api/posts", params={"page_id": "11"}, headers=HEADERS)).json()["displayed"], 1)

    async def test_byte_and_row_reservation_capacity_and_expiry(self):
        cache = self.application.state.store.audits
        cache.settings = replace(self.settings, max_audit_items=10)
        self.error(await self.load(100), "audit_capacity")
        self.assertEqual(len(self.calls), 0)
        cache.settings = replace(self.settings, max_audit_bytes=8000)
        self.error(await self.load(1), "audit_capacity")
        cache.settings = self.settings
        session = next(iter(self.application.state.store.sessions.values()))
        expiry = session.expires_at
        await self.load(1)
        self.assertEqual(session.expires_at, expiry)
        self.assertLessEqual(next(iter(cache.entries.values())).expires_at, expiry)
        next(iter(cache.entries.values())).expires_at = 0
        cached = await self.client.get("/api/posts", params={"page_id": "11"}, headers=HEADERS)
        self.assertEqual(cached.json()["items"], [])
        self.assertEqual(len(cache.entries), 0)

    async def test_limit5000_accepted_bounded_acquisition_without_fetch_all(self):
        data = (await self.load(5000)).json()
        self.assertEqual(data["limit"], 5000)
        self.assertEqual(data["displayed"], 1)
        self.assertEqual(data["state"], "source_exhausted")
        self.assertEqual(len(self.calls), 3)

    async def test_page_bound_and_content_cooldown_no_more_meta(self):
        self.application.state.settings = replace(self.settings, posts_max_pages=1)
        self.queues["published_posts"] = [response([post(1)], "published_posts", "C1")]
        data = (await self.load(100)).json()
        self.assertEqual(data["state"], "partial")
        self.assertEqual(data["error"]["code"], "pagination_incomplete")
        self.queues = {"stories": [response([])], "published_posts": [httpx.Response(429, headers={"Retry-After": "30"})], "video_reels": [response([])]}
        data = (await self.load()).json()
        self.assertEqual(data["error"]["code"], "rate_limited")
        before = len(self.calls)
        self.error(await self.load(), "cooldown")
        self.assertEqual(len(self.calls), before)

    async def test_global_work_shared_with_connections_and_local_rate(self):
        self.application.state.store.settings = replace(self.settings, max_jobs=1)
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(request):
            entered.set()
            await release.wait()
            return response([post(1)])
        self.queues["published_posts"] = [slow]
        first = asyncio.create_task(self.load())
        await asyncio.wait_for(entered.wait(), 3)
        try:
            async with self.browser() as other:
                await other.get("/api/session", headers=HEADERS)
                result = await other.post("/api/connect", json={"token": USER_TOKEN}, headers=HEADERS)
                self.error(result, "job_capacity")
        finally:
            release.set()
            await first
        self.application.state.store.settings = replace(self.settings, local_attempts=1)
        before = len(self.calls)
        self.error(await self.load(), "local_rate_limit")
        self.assertEqual(len(self.calls), before)

    async def test_partial_pagination_message_does_not_claim_results_unsaved(self):
        self.queues["published_posts"] = [response([post(1)], "published_posts", "C1"), response([post(1)], "published_posts", "C2")]
        data = (await self.load()).json()
        self.assertEqual(data["state"], "partial")
        self.assertNotIn("tidak disimpan", data["error"]["message"])
        self.assertIn("parsial", data["error"]["message"])

    async def test_story_only_media_reference_is_not_sufficient_for_posts(self):
        self.queues["stories"] = [response([{"id": "123", "media_id": "900", "status": "PUBLISHED"}])]
        data = (await self.load()).json()
        self.assertEqual(data["state"], "source_exhausted")
        self.assertIn("story_reference_unverified", data["limitations"])
        self.assertTrue(all(not item["selectable"] for item in data["items"]))


class EfficiencyContract(LoadFixture):
    async def test_story_history_not_scanned_for_small_load(self):
        self.queues["published_posts"] = [response([post(n) for n in range(1, 21)])]
        self.queues["stories"] = [response([{"id": "900", "post_id": "11_900", "media_id": "901"}], "stories", "MORE")]
        result = await self.load(20)
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(data["state"], "limit_reached")
        self.assertTrue(data["retrieval_complete"])
        self.assertFalse(data["bulk_selectable"])
        self.assertTrue(all(not item["selectable"] for item in data["items"]))
        self.assertEqual([r.url.path.rsplit('/', 1)[-1] for r in self.calls], ["published_posts", "video_reels", "stories"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[-1].url.params["limit"], "20")
        diag = next(s for s in data["sources"] if s["source"] == "stories")
        self.assertEqual(diag["stop_reason"], "story_verification_budget")

    async def test_empty_candidates_need_no_story_request(self):
        self.queues["published_posts"] = [response([])]
        data = (await self.load(20)).json()
        self.assertEqual(data["state"], "empty")
        self.assertFalse(any(r.url.path.endswith('/stories') for r in self.calls))
        self.assertEqual(len(self.calls), 2)

    async def test_missing_story_reference_is_uncertainty_not_pagination_failure(self):
        self.queues["stories"] = [response([{"id": "900", "post_id": "11_900"}])]
        data = (await self.load(20)).json()
        self.assertEqual(data["state"], "source_exhausted")
        self.assertTrue(data["retrieval_complete"])
        self.assertNotIn("error", data)
        self.assertFalse(data["bulk_selectable"])
        self.assertIn("story_reference_unverified", data["limitations"])

    async def test_cross_dedup_before_stop_and_only_requested_relations(self):
        self.queues["published_posts"] = [response([post(1), post(2)], "published_posts", "P2"), response([post(3)])]
        self.queues["video_reels"] = [response([reel(900, "11_1"), reel(901, "11_2")], "video_reels", "R2"), response([reel(902, "11_3")])]
        data = (await self.load(3)).json()
        self.assertEqual(data["displayed"], 3)
        self.assertEqual(data["found"], 3)
        for request in self.calls:
            if request.url.path.endswith('/video_reels'):
                self.assertIn("post_id", request.url.params["fields"].split(','))
            if request.url.path.endswith('/published_posts'):
                self.assertNotIn("object_id", request.url.params["fields"].split(','))
        self.assertGreater(len([r for r in self.calls if r.url.path.endswith('/published_posts')]), 1)

    async def test_cross_alias_duplicates_cannot_pretend_source_exhausted(self):
        self.queues["published_posts"] = [response([post(1)])]
        self.queues["video_reels"] = [response([reel(900, "11_1"), reel(901, "11_1"), reel(902, "11_1")], "video_reels", "R2"),
            response([reel(903, "11_2"), reel(904, "11_3")])]
        data = (await self.load(3)).json()
        self.assertEqual(data["displayed"], 3)
        self.assertEqual(len([r for r in self.calls if r.url.path.endswith('/video_reels')]), 2)

    async def test_request_counts_retry_and_safe_source_errors(self):
        self.queues["published_posts"] = [httpx.Response(503), response([post(1)], "published_posts", "P2"), httpx.Response(403)]
        data = (await self.load(20)).json()
        diag = next(s for s in data["sources"] if s["source"] == "published_posts")
        self.assertEqual(diag["requests"], 3)
        self.assertEqual(diag["pages"], 1)
        self.assertEqual(diag["received"], 1)
        self.assertEqual(diag["status"], "failed")
        self.assertEqual(diag["stop_reason"], "permission_denied")
        self.assertEqual(data["errors"][0]["source"], "published_posts")
        self.assertNotIn(PAGE_TOKEN, str(data["sources"]) + str(data["errors"]))

    async def test_page_limit_and_repeat_specific_reasons(self):
        self.application.state.settings = replace(self.settings, posts_max_pages=1)
        self.queues["published_posts"] = [response([post(1)], "published_posts", "P2")]
        data = (await self.load(20)).json()
        diag = next(s for s in data["sources"] if s["source"] == "published_posts")
        self.assertEqual(diag["stop_reason"], "page_limit")
        self.assertEqual(diag["requests"], 1)

    async def test_exhausted_1743_is_maximum_not_promised_count(self):
        self.queues["published_posts"] = [response([post(n) for n in range(start, min(start + 100, 1744))], "published_posts", f"P{start}")
            for start in range(1, 1702, 100)]
        self.queues["published_posts"][-1] = response([post(n) for n in range(1701, 1744)])
        data = (await self.load(3000)).json()
        self.assertEqual(data["displayed"], 1743)
        self.assertEqual(data["state"], "source_exhausted")
        diag = next(s for s in data["sources"] if s["source"] == "published_posts")
        self.assertEqual(diag["received"], 1743)
        self.assertEqual(diag["stop_reason"], "source_exhausted")
        self.assertEqual(diag["requests"], 18)

    async def test_multi_page_20_actual_request_total(self):
        self.queues["published_posts"] = [response([post(n) for n in range(1, 11)], "published_posts", "P2"), response([post(n) for n in range(11, 21)])]
        data = (await self.load(20)).json()
        self.assertEqual(data["displayed"], 20)
        self.assertEqual(sum(s["requests"] for s in data["sources"]), 4)
        self.assertEqual(len(self.calls), 4)

    async def test_retry_after_and_fatal_error_stop_correct_sources(self):
        for issue, reason, attempts in [(httpx.Response(503, headers={"Retry-After": "30"}), "cooldown", 1),
                (httpx.ReadTimeout(PAGE_TOKEN), "timeout", 3),
                (httpx.Response(400, json={"error": {"code": 190, "message": PAGE_TOKEN}}), "token_invalid", 1)]:
            with self.subTest(reason=reason):
                self.application.state.store.token_cooldowns.clear()
                self.application.state.store.global_cooldown_until = 0
                self.queues["published_posts"] = [issue] * 3
                before = len(self.calls)
                data = (await self.load(20)).json()
                first, second, story = data["sources"]
                self.assertEqual(first["stop_reason"], reason)
                self.assertEqual(first["requests"], attempts)
                self.assertEqual(second["status"], "cancelled")
                self.assertEqual(second["requests"], 0)
                self.assertEqual(story["requests"], 0)
                self.assertEqual(len(self.calls) - before, attempts)
                self.assertNotIn(PAGE_TOKEN, str(data["sources"]) + str(data["errors"]))

    async def test_repeat_invalid_response_and_foreign_pagination_source_reasons(self):
        for second, expected in [(response([post(1)], "published_posts", "OTHER"), "pagination_repeated"),
                (httpx.Response(200, json={"data": "wrong"}), "invalid_response"),
                (httpx.Response(200, json={"data": [post(2)], "paging": {"next": "https://evil.example/?after=X", "cursors": {"after": "X"}}}), "invalid_pagination")]:
            self.queues = {"published_posts": [response([post(1)], "published_posts", "P2"), second],
                           "video_reels": [response([])], "stories": [response([])]}
            data = (await self.load(20)).json()
            diag = data["sources"][0]
            self.assertEqual(diag["stop_reason"], expected)
            self.assertEqual(diag["requests"], 2)
            self.assertEqual(data["state"], "partial")

    async def test_disconnect_includes_cancelled_source_diagnosis(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def pending(request):
            entered.set()
            await release.wait()
            return response([post(1)])
        self.queues["published_posts"] = [pending]
        first = asyncio.create_task(self.load(20))
        await asyncio.wait_for(entered.wait(), 3)
        await self.client.post("/api/disconnect", headers=HEADERS)
        result = await first
        self.error(result, "load_cancelled")
        self.assertEqual(result.json()["sources"][0]["status"], "cancelled")
        self.assertEqual(result.json()["sources"][0]["requests"], 1)
        self.assertTrue(all(source["stop_reason"] == "load_cancelled" for source in result.json()["sources"]))

    async def test_story_ids_still_excluded_from_available_references(self):
        self.queues["stories"] = [response([{"id": "900", "post_id": "11_1"}])]
        self.queues["published_posts"] = [response([post(1), post(2)])]
        data = (await self.load(20)).json()
        self.assertEqual([item["object_id"] for item in data["items"]], ["11_2"])
        self.assertFalse(data["items"][0]["selectable"])
        self.assertNotIn("error", data)

    async def test_exhausted_server_retries_are_not_invalid_response(self):
        self.queues["published_posts"] = [httpx.Response(503)] * 3
        data = (await self.load(20)).json()
        diagnostic = data["sources"][0]
        self.assertEqual(diagnostic["requests"], 3)
        self.assertEqual(diagnostic["stop_reason"], "upstream_error")
        self.assertEqual(diagnostic["status"], "failed")
        self.assertEqual(data["state"], "partial")
