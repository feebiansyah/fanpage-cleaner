import asyncio
import logging
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import StringIO
from unittest.mock import patch
from html.parser import HTMLParser

import httpx

import app as app_module


TOKEN = "dummy-user-secret"
PAGE_TOKEN = "dummy-page-secret"
HEADERS = {"X-Fanpage-Client": "1", "Origin": "http://127.0.0.1:8000"}


def page(page_id="11", name="Halaman satu"):
    return {"id": page_id, "name": name, "access_token": PAGE_TOKEN}


def next_page(cursor="CURSOR_2"):
    return {"cursors": {"after": cursor}, "next":
            "https://graph.facebook.com/v26.0/me/accounts?after=" + cursor + "&access_token=" + TOKEN}


class FoundationContract(unittest.IsolatedAsyncioTestCase):
    async def test_session_route_is_local_and_cache_disabled(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1:8000",
        ) as client:
            response = await client.get("/api/session", headers={"X-Fanpage-Client": "1"})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["connected"])
            self.assertEqual(response.headers["cache-control"], "no-store")


class ConnectionContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue(callable(getattr(app_module, "create_app", None)), "App factory dan lifecycle koneksi belum dibuat")
        from config import Settings
        self.settings = Settings(graph_request_interval=.001, retry_base=0.001, retry_jitter=0, local_attempts=20)
        self.calls = []
        self.responses = [httpx.Response(200, json={"data": [page()]})]

        async def handler(request):
            self.calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.host, "graph.facebook.com")
            self.assertEqual(request.url.path, "/v26.0/me/accounts")
            self.assertNotIn(TOKEN, str(request.url))
            self.assertEqual(request.headers["authorization"], "Bearer " + TOKEN)
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            if callable(result):
                return await result(request)
            return result

        self.application = app_module.create_app(self.settings, httpx.MockTransport(handler))
        self.lifecycle = self.application.router.lifespan_context(self.application)
        await self.lifecycle.__aenter__()
        self.client = self.browser()
        await self.client.get("/api/session", headers=HEADERS)

    def browser(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application, client=("127.0.0.1", 1234)),
                                 base_url="http://127.0.0.1:8000")

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()
            await self.lifecycle.__aexit__(None, None, None)

    async def connect(self, client=None, token=TOKEN, headers=None):
        return await (client or self.client).post("/api/connect", json={"token": token}, headers=headers or HEADERS)

    def error(self, response, code):
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn(PAGE_TOKEN, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_foundation_and_client_lifecycle(self):
        for url, content_type in [("/", "text/html"), ("/health", "application/json"),
                                  ("/static/style.css", "text/css"), ("/static/app.js", "javascript")]:
            response = await self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn(content_type, response.headers["content-type"])
            self.assertNotIn(TOKEN, response.text)
        self.assertEqual((await self.client.get("/health")).json(), {"status": "ok"})
        class Operations(HTMLParser):
            def __init__(self):
                super().__init__()
                self.buttons, self.disabled = [], {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "button":
                    self.buttons.append(attrs.get("id"))
                    if attrs.get("id") not in {"connect", "disconnect"}:
                        self.disabled[attrs.get('id') or 'bulk-delete'] = "disabled" in attrs
                if tag == "input" and attrs.get("type") == "checkbox":
                    self.disabled[attrs.get('id')] = "disabled" in attrs
        operations = Operations()
        operations.feed((await self.client.get("/")).text)
        self.assertEqual(len(operations.buttons), 10)
        for control in ['load-posts','select-all','bulk-delete','dry-run','stop-load','delete-one','delete-confirm','delete-read-last']:
            self.assertTrue(operations.disabled[control], control)
        self.assertFalse(operations.disabled['delete-cancel'])
        graph_http = self.application.state.http
        self.assertFalse(graph_http.follow_redirects)
        self.assertFalse(graph_http.is_closed)
        await self.lifecycle.__aexit__(None, None, None)
        self.assertTrue(graph_http.is_closed)
        # Restore lifespan for teardown, with no external requests.
        self.lifecycle = self.application.router.lifespan_context(self.application)
        await self.lifecycle.__aenter__()

    async def test_complete_pagination_dedup_and_refresh_no_meta(self):
        self.responses = [httpx.Response(200, json={"data": [page()], "paging": next_page()}),
                          httpx.Response(200, json={"data": [page(), page("22", "Halaman dua")],
                                                   "paging": {"cursors": {"after": "LAST"}}})]
        response = await self.connect()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pages"], [{"id": "11", "name": "Halaman satu"}, {"id": "22", "name": "Halaman dua"}])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1].url.params["after"], "CURSOR_2")
        self.assertEqual(self.calls[0].url.params["fields"], "id,name,access_token")
        status = await self.client.get("/api/session", headers=HEADERS)
        self.assertEqual(status.json()["pages"], response.json()["pages"])
        self.assertEqual(len(self.calls), 2)
        for secret in [TOKEN, PAGE_TOKEN]:
            self.assertNotIn(secret, response.text + status.text + response.headers.get("set-cookie", ""))
        cookie = response.headers["set-cookie"]
        for flag in ["HttpOnly", "SameSite=strict", "Path=/"]:
            self.assertIn(flag, cookie)
        self.assertNotIn("Domain=", cookie)

    async def test_after_alone_stops_and_empty_page_with_next_continues(self):
        self.responses = [httpx.Response(200, json={"data": [], "paging": next_page()}),
                          httpx.Response(200, json={"data": [page()], "paging": {"cursors": {"after": "LAST"}}})]
        self.assertEqual((await self.connect()).status_code, 200)
        self.assertEqual(len(self.calls), 2)

    async def test_repeated_cursor_and_page_are_incomplete(self):
        for second in [next_page(), next_page("DIFFERENT")]:
            with self.subTest(second=second):
                self.responses = [httpx.Response(200, json={"data": [page()], "paging": next_page()}),
                                  httpx.Response(200, json={"data": [page()], "paging": second})]
                self.error(await self.connect(), "pagination_incomplete")
                self.assertFalse((await self.client.get("/api/session", headers=HEADERS)).json()["connected"])

    async def test_page_limit_and_partial_failure_preserve_old_session(self):
        await self.connect()
        old_cookie = self.client.cookies.get("fanpage_session")
        self.responses = [httpx.Response(200, json={"data": [page("22")], "paging": next_page()}),
                          httpx.Response(403, json={"error": {"code": 200, "message": TOKEN}})]
        self.error(await self.connect(), "pagination_incomplete")
        self.assertEqual(self.client.cookies.get("fanpage_session"), old_cookie)
        self.assertEqual((await self.client.get("/api/session", headers=HEADERS)).json()["pages"][0]["id"], "11")
        self.application.state.graph.settings = replace(self.settings, max_pages=1)
        self.responses = [httpx.Response(200, json={"data": [page()], "paging": next_page()})]
        self.error(await self.connect(), "pagination_incomplete")

    async def test_invalid_permission_and_expired_never_retry(self):
        for meta_code, expected in [(190, "token_invalid"), (102, "token_invalid"), (10, "permission_denied"), (200, "permission_denied")]:
            with self.subTest(meta_code=meta_code):
                before = len(self.calls)
                self.responses = [httpx.Response(400, json={"error": {"code": meta_code, "is_transient": True, "message": TOKEN}})]
                self.error(await self.connect(), expected)
                self.assertEqual(len(self.calls), before + 1)

    async def test_timeout_network_and_transient_retries_bounded(self):
        for issue, expected in [(httpx.ReadTimeout(TOKEN), "timeout"), (httpx.ConnectError(TOKEN), "network_error"),
                                (httpx.Response(503, text=TOKEN), "upstream_error")]:
            with self.subTest(expected=expected):
                self.responses = [issue, issue, issue]
                before = len(self.calls)
                self.error(await self.connect(), expected)
                self.assertEqual(len(self.calls), before + 3)
        self.responses = [httpx.Response(400, json={"error": {"code": 2, "is_transient": True}}),
                          httpx.Response(200, json={"data": [page()]})]
        self.assertEqual((await self.connect()).status_code, 200)

    async def test_unclassified_error_malformed_payload_and_redirect_no_retry(self):
        for issue in [httpx.Response(400, json={"error": {"code": 999, "message": TOKEN}}),
                      httpx.Response(200, text=TOKEN), httpx.Response(200, json={"data": "wrong"}),
                      httpx.Response(302, headers={"Location": "https://evil.example/?access_token=" + TOKEN})]:
            with self.subTest(status=issue.status_code):
                before = len(self.calls)
                self.responses = [issue]
                self.error(await self.connect(), "upstream_error")
                self.assertEqual(len(self.calls), before + 1)

    async def test_rate_limit_cooldown_blocks_meta_even_from_another_browser(self):
        self.responses = [httpx.Response(429, headers={"Retry-After": "30"}, json={"error": {"code": 17, "message": TOKEN}})]
        self.error(await self.connect(), "rate_limited")
        self.error(await self.connect(), "cooldown")
        async with self.browser() as other:
            await other.get("/api/session", headers=HEADERS)
            self.error(await self.connect(other), "cooldown")
        self.assertEqual(len(self.calls), 1)

    async def test_app_usage_high_sets_global_cooldown_and_missing_header_unknown(self):
        self.responses = [httpx.Response(200, headers={"X-App-Usage": '{"call_count":95,"total_time":2,"total_cputime":1}'}, json={"data": [page()]})]
        self.assertEqual((await self.connect()).status_code, 200)
        self.error(await self.connect(), "cooldown")
        self.assertEqual(len(self.calls), 1)

    async def test_retry_after_long_and_http_date_no_early_retry(self):
        for header in ["30", format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)]:
            self.application.state.store.global_cooldown_until = 0
            self.application.state.store.token_cooldowns.clear()
            self.responses = [httpx.Response(503, headers={"Retry-After": header}, text=TOKEN)]
            before = len(self.calls)
            self.error(await self.connect(), "cooldown")
            self.assertEqual(len(self.calls), before + 1)
            self.error(await self.connect(), "cooldown")

    async def test_empty_result_has_specific_message(self):
        self.responses = [httpx.Response(200, json={"data": []})]
        response = await self.connect()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pages"], [])
        self.assertEqual(response.json()["state"], "empty")
        self.assertIn("Tidak ada Fanpage", response.json()["message"])

    async def test_untrusted_next_never_receives_token(self):
        for link in ["https://evil.example/?after=CURSOR_2", "http://graph.facebook.com/v26.0/me/accounts?after=CURSOR_2",
                     "https://graph.facebook.com.evil.example/v26.0/me/accounts?after=CURSOR_2",
                     "https://graph.facebook.com/v26.0/other?after=CURSOR_2"]:
            before = len(self.calls)
            paging = next_page()
            paging["next"] = link
            self.responses = [httpx.Response(200, json={"data": [page()], "paging": paging})]
            self.error(await self.connect(), "pagination_incomplete")
            self.assertEqual(len(self.calls), before + 1)

    async def test_token_validation_never_reflects_body(self):
        for token, expected in [("", "token_empty"), ("   ", "token_empty"), ("x" * 8193, "token_invalid"),
                                (TOKEN + "\n", "token_invalid"), (123, "token_invalid")]:
            self.error(await self.connect(token=token), expected)
        response = await self.client.post("/api/connect", content='{"token":"' + TOKEN,
                                          headers={**HEADERS, "Content-Type": "application/json"})
        self.error(response, "bad_request")
        response = await self.client.post("/api/connect", content="x" * 17000, headers=HEADERS)
        self.error(response, "body_too_large")
        self.assertEqual(len(self.calls), 0)

    async def test_origin_and_bootstrap_required_and_host_restricted(self):
        for headers in [{"X-Fanpage-Client": "1"}, {**HEADERS, "Origin": "https://evil.example"},
                        {**HEADERS, "Sec-Fetch-Site": "cross-site"}, {"Origin": HEADERS["Origin"]}]:
            self.assertEqual((await self.connect(headers=headers)).status_code, 403)
            self.assertEqual((await self.client.post("/api/disconnect", headers=headers)).status_code, 403)
        async with self.browser() as fresh:
            self.error(await self.connect(fresh), "browser_required")
        response = await self.client.get("/api/session", headers={**HEADERS, "Host": "evil.example"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.calls), 0)

    async def test_browser_isolation_and_disconnect(self):
        await self.connect()
        async with self.browser() as other:
            self.assertFalse((await other.get("/api/session", headers=HEADERS)).json()["connected"])
            # Session ID alone from another browser is insufficient.
            other.cookies.set("fanpage_session", self.client.cookies.get("fanpage_session"))
            self.assertFalse((await other.get("/api/session?session=" + self.client.cookies.get("fanpage_session"), headers=HEADERS)).json()["connected"])
            await other.post("/api/disconnect", headers=HEADERS)
            self.assertTrue((await self.client.get("/api/session", headers=HEADERS)).json()["connected"])
        self.assertEqual((await self.client.post("/api/disconnect", headers=HEADERS)).status_code, 200)
        self.assertFalse((await self.client.get("/api/session", headers=HEADERS)).json()["connected"])
        self.assertNotIn("fanpage_session", self.client.cookies)
        self.assertEqual(len(self.application.state.store.sessions), 0)
        self.assertEqual(len(self.calls), 1)

    async def test_double_connect_and_disconnect_during_connect(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"data": [page()]})
        self.responses = [delayed]
        first = asyncio.create_task(self.connect())
        await entered.wait()
        try:
            self.error(await self.connect(), "connection_busy")
            self.error(await self.client.post("/api/disconnect", headers=HEADERS), "connection_busy")
            self.assertEqual(len(self.calls), 1)
        finally:
            release.set()
        self.assertEqual((await first).status_code, 200)

    async def test_session_expiry_and_capacity_no_meta(self):
        self.application.state.store.settings = replace(self.settings, max_sessions=1)
        await self.connect()
        async with self.browser() as other:
            await other.get("/api/session", headers=HEADERS)
            self.error(await self.connect(other), "session_capacity")
        self.assertEqual(len(self.calls), 1)
        session = next(iter(self.application.state.store.sessions.values()))
        session.expires_at = 0
        response = await self.client.get("/api/session", headers=HEADERS)
        self.assertEqual(response.json()["state"], "expired")
        self.assertFalse(response.json()["connected"])
        self.assertEqual(len(self.application.state.store.sessions), 0)

    async def test_local_limiter_and_bounded_entries(self):
        self.application.state.store.settings = replace(self.settings, local_attempts=1, max_browser_entries=2)
        await self.connect()
        self.error(await self.connect(), "local_rate_limit")
        async with self.browser() as other, self.browser() as third:
            self.assertEqual((await other.get("/api/session", headers=HEADERS)).status_code, 200)
            self.error(await third.get("/api/session", headers=HEADERS), "limiter_capacity")
        self.assertEqual(len(self.calls), 1)

    async def test_global_jobs_and_session_reservations(self):
        self.application.state.store.settings = replace(self.settings, max_jobs=1)
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"data": [page()]})
        self.responses = [delayed]
        first = asyncio.create_task(self.connect())
        await entered.wait()
        try:
            async with self.browser() as other:
                await other.get("/api/session", headers=HEADERS)
                self.error(await self.connect(other), "job_capacity")
        finally:
            release.set()
        await first
        self.assertEqual(len(self.calls), 1)

    async def test_overall_deadline_cancels_and_releases_work(self):
        self.application.state.settings = replace(self.settings, operation_timeout=0.02)
        async def slow(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json={"data": [page()]})
        self.responses = [slow]
        self.error(await self.connect(), "timeout")
        self.responses = [httpx.Response(200, json={"data": [page()]})]
        self.assertEqual((await self.connect()).status_code, 200)

    async def test_upstream_names_and_exceptions_do_not_leak_tokens_or_logs(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        original_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger().addHandler(handler)
        try:
            self.responses = [httpx.Response(200, json={"data": [page(name=TOKEN + PAGE_TOKEN)]})]
            response = await self.connect()
            self.assertEqual(response.status_code, 200)
            self.responses = [httpx.ConnectError(TOKEN)] * 3
            error_response = await self.connect()
            combined = response.text + error_response.text + stream.getvalue() + str(self.client.cookies)
            self.assertNotIn(TOKEN, combined)
            self.assertNotIn(PAGE_TOKEN, combined)
        finally:
            logging.getLogger().removeHandler(handler)
            logging.getLogger().setLevel(original_level)

    async def test_timeout_after_first_page_is_incomplete(self):
        self.application.state.settings = replace(self.settings, operation_timeout=0.03)
        async def slow(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json={"data": [page("22")]})
        self.responses = [httpx.Response(200, json={"data": [page()], "paging": next_page()}), slow]
        response = await self.connect()
        self.error(response, "pagination_incomplete")
        self.assertEqual(response.json()["error"]["cause_code"], "timeout")
        self.assertFalse((await self.client.get("/api/session", headers=HEADERS)).json()["connected"])

    async def test_cursor_cannot_reflect_token_into_url(self):
        for secret in [TOKEN, PAGE_TOKEN]:
            self.responses = [httpx.Response(200, json={"data": [page()], "paging": next_page(secret)})]
            before = len(self.calls)
            self.error(await self.connect(), "pagination_incomplete")
            self.assertEqual(len(self.calls), before + 1)

    async def test_resolved_user_path_still_requests_fixed_endpoint(self):
        paging = next_page()
        paging["next"] = paging["next"].replace("/me/", "/123456/")
        self.responses = [httpx.Response(200, json={"data": [page()], "paging": paging}),
                          httpx.Response(200, json={"data": [page("22")]})]
        response = await self.connect()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1].url.path, "/v26.0/me/accounts")

    async def test_replacing_live_session_does_not_double_count_capacity(self):
        self.application.state.store.settings = replace(self.settings, max_sessions=2)
        await self.connect()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"data": [page()]})
        self.responses = [delayed, httpx.Response(200, json={"data": [page("22")]})]
        first = asyncio.create_task(self.connect())
        await entered.wait()
        try:
            async with self.browser() as other:
                await other.get("/api/session", headers=HEADERS)
                self.assertEqual((await self.connect(other)).status_code, 200)
        finally:
            release.set()
            await first
        self.assertEqual(len(self.application.state.store.sessions), 2)

    async def test_long_valid_retry_after_is_not_shortened(self):
        self.responses = [httpx.Response(503, headers={"Retry-After": "700000"})]
        response = await self.connect()
        self.error(response, "cooldown")
        self.assertGreaterEqual(response.json()["error"]["retry_after"], 700000)
        self.assertEqual(len(self.calls), 1)

    async def test_expired_replacement_retains_slot_reservation(self):
        self.application.state.store.settings = replace(self.settings, max_sessions=1)
        await self.connect()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"data": [page()]})
        self.responses = [delayed]
        first = asyncio.create_task(self.connect())
        await entered.wait()
        next(iter(self.application.state.store.sessions.values())).expires_at = 0
        try:
            async with self.browser() as other:
                await other.get("/api/session", headers=HEADERS)
                self.error(await self.connect(other), "session_capacity")
        finally:
            release.set()
            await first
        self.assertEqual(len(self.application.state.store.sessions), 1)

    async def test_session_slot_reserved_before_first_connection_finishes(self):
        self.application.state.store.settings = replace(self.settings, max_sessions=1)
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"data": [page()]})
        self.responses = [delayed]
        first = asyncio.create_task(self.connect())
        await entered.wait()
        try:
            async with self.browser() as other:
                await other.get("/api/session", headers=HEADERS)
                self.error(await self.connect(other), "session_capacity")
        finally:
            release.set()
            await first
        self.assertEqual(len(self.calls), 1)

    async def test_expired_limiter_entries_and_attempts_are_reclaimed(self):
        await self.connect()
        store = self.application.state.store
        future = __import__('time').monotonic() + 2000
        with patch("sessions.time.monotonic", return_value=future):
            store.cleanup()
            self.assertEqual(len(store.browsers), 0)
            self.assertEqual(len(store.sessions), 0)
            self.assertEqual(len(store.global_attempt_times), 0)

    async def test_token_cooldown_table_bounded_without_eviction(self):
        store = self.application.state.store
        store.settings = replace(self.settings, max_token_entries=1)
        first = store.token_key(TOKEN)
        second = store.token_key("another-dummy-token")
        store.cooldown(first, 30)
        store.cooldown(second, 30)
        self.assertEqual(len(store.token_cooldowns), 1)
        self.assertNotIn(TOKEN, str(store.token_cooldowns))
        self.error(await self.connect(), "cooldown")
        self.assertEqual(len(self.calls), 0)

    async def test_usage_business_header_and_malformed_headers(self):
        for raw in ["broken", "[]", '{"call_count":"95"}', '{"call_count":null}']:
            self.responses = [httpx.Response(200, headers={"X-App-Usage": raw}, json={"data": [page()]})]
            self.assertEqual((await self.connect()).status_code, 200)
        self.responses = [httpx.Response(200, headers={"X-Business-Use-Case-Usage":
            '{"123":[{"type":"pages","call_count":95,"total_time":1,"total_cputime":1,"estimated_time_to_regain_access":0}]}'}, json={"data": [page()]})]
        self.assertEqual((await self.connect()).status_code, 200)
        before = len(self.calls)
        self.error(await self.connect(), "cooldown")
        self.assertEqual(len(self.calls), before)

    async def test_cursor_mismatch_duplicate_and_invalid_cursor(self):
        for link in ["https://graph.facebook.com/v26.0/me/accounts?after=OTHER",
                     "https://graph.facebook.com/v26.0/me/accounts?after=CURSOR_2&after=CURSOR_2",
                     "https://graph.facebook.com/v26.0/me/accounts?after=CURSOR_2%0a",
                     "https://user@graph.facebook.com/v26.0/me/accounts?after=CURSOR_2"]:
            paging = next_page()
            paging["next"] = link
            self.responses = [httpx.Response(200, json={"data": [page()], "paging": paging})]
            self.error(await self.connect(), "pagination_incomplete")

    async def test_retry_after_short_is_waited_and_invalid_header_not_retried_forever(self):
        self.responses = [httpx.Response(503, headers={"Retry-After": "1"}), httpx.Response(200, json={"data": [page()]})]
        start = __import__('time').monotonic()
        self.assertEqual((await self.connect()).status_code, 200)
        self.assertGreaterEqual(__import__('time').monotonic() - start, 1)
        for value in ["-1", "NaN", "not-a-date"]:
            self.responses = [httpx.Response(503, headers={"Retry-After": value})] * 3
            before = len(self.calls)
            self.error(await self.connect(), "upstream_error")
            self.assertEqual(len(self.calls), before + 3)

    async def test_retry_after_early_timer_does_not_turn_get_retry_into_cooldown_error(self):
        # Windows timers can wake before the monotonic cooldown deadline.
        original_sleep = asyncio.sleep
        woke_early = False
        async def early_sleep(seconds):
            nonlocal woke_early
            if seconds >= .5 and not woke_early:
                woke_early = True
                return await original_sleep(seconds-.03)
            return await original_sleep(seconds)
        self.responses = [httpx.Response(503, headers={'Retry-After':'1'}),
                          httpx.Response(200,json={'data':[page()]})]
        with patch('graph_api.asyncio.sleep', early_sleep):
            result = await self.connect()
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(self.calls),2)

    async def test_chunked_body_size_and_deep_invalid_json_are_safe(self):
        async def chunks():
            yield b'{"token":"'
            yield b'x' * 17000
        response = await self.client.post("/api/connect", content=chunks(), headers={**HEADERS, "Content-Type": "application/json"})
        self.error(response, "body_too_large")
        response = await self.client.post("/api/connect", content="[" * 1200 + "]" * 1200,
                                          headers={**HEADERS, "Content-Type": "application/json"})
        self.error(response, "bad_request")
        self.assertEqual(len(self.calls), 0)

    async def test_missing_page_token_is_metadata_only_and_oversized_response_rejected(self):
        self.responses = [httpx.Response(200, json={"data": [{"id": "11", "name": "Tanpa page token"}]})]
        self.assertEqual((await self.connect()).json()["pages"], [{"id": "11", "name": "Tanpa page token"}])
        self.responses = [httpx.Response(200, content=b'x' * 1048577)]
        self.error(await self.connect(), "upstream_error")

    async def test_forwarded_headers_cannot_override_origin(self):
        self.responses = [httpx.Response(200, json={"data": [page()]})]
        response = await self.connect(headers={**HEADERS, "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
        self.assertEqual(response.status_code, 200)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application, client=("192.0.2.1", 1234)),
                                     base_url="http://127.0.0.1:8000") as remote:
            self.assertEqual((await remote.get("/", headers={"X-Forwarded-For": "127.0.0.1"})).status_code, 403)


if __name__ == "__main__":
    unittest.main()
