import asyncio
import copy
import logging
import time
import unittest
from io import StringIO

import httpx

from app import create_app
from config import Settings
from sessions import Page


HEADERS = {'X-Fanpage-Client': '1', 'Origin': 'http://127.0.0.1:8000'}
TOKEN = 'dummy-page-secret'


def item():
    return {'key': 'published_posts:11_123', 'page_id': '11', 'object_id': '11_123',
            'sources': [{'source': 'published_posts', 'object_id': '11_123'}],
            'kind': 'Postingan', 'post_status_type':'mobile_status_update', 'text': 'Post yang diperiksa', 'published_wib': '10/10/2026 17:00:00 WIB',
            'published_at': '2026-10-10T10:00:00+00:00', 'permalink': 'https://www.facebook.com/11/posts/123',
            'selectable': True}


class SingleDelete(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.allowed_targets = {'11_123'}
        self.response = httpx.Response(200, json={'success': True})
        async def handler(request):
            self.calls.append(request)
            self.assertEqual(request.method, 'DELETE')
            self.assertEqual(request.url.host, 'graph.facebook.com')
            self.assertIn(request.url.path, {'/v26.0/'+node for node in self.allowed_targets})
            self.assertEqual(request.url.query, b'')
            self.assertEqual(request.headers['authorization'], 'Bearer '+TOKEN)
            if isinstance(self.response, Exception):
                raise self.response
            if callable(self.response):
                return await self.response(request)
            return self.response
        self.app = create_app(Settings(graph_request_interval=.001), httpx.MockTransport(handler))
        self.lifecycle = self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.client = self.browser()
        self.store = self.app.state.store
        self.bid, _, _ = self.store.bootstrap(None)
        self.sid, self.session = self.store.replace(self.bid, 'dummy-user-secret', [Page('11', 'Fanpage', TOKEN), Page('22', 'Lain', '')])
        self.client.cookies.set('fanpage_browser', self.bid)
        self.client.cookies.set('fanpage_session', self.sid)
        self.audit = {'page_id': '11', 'displayed': 1, 'found': 1, 'items': [item()], 'safe_for_delete': False}
        self.seed()

    def browser(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 1)), base_url=HEADERS['Origin'])

    def seed(self):
        self.store.audits.reserve(self.sid, 1, '11')
        self.store.audits.put(self.sid, copy.deepcopy(self.audit), self.session.expires_at)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)

    async def prepare(self, payload=None, client=None):
        return await (client or self.client).post('/api/posts/delete/prepare', headers=HEADERS,
                    json=payload or {'page_id': '11', 'keys': [item()['key']]})

    async def execute(self, confirmation, client=None, **extra):
        return await (client or self.client).post('/api/posts/delete/execute', headers=HEADERS,
                    json={'confirmation_id': confirmation, 'manual_target_confirmed': True, **extra})

    async def test_bound_snapshot_single_use_and_success_invalidates_old_cache(self):
        prepared = await self.prepare()
        self.assertEqual(prepared.status_code, 200, prepared.text)
        data = prepared.json()
        self.assertEqual(data['target']['target_id'], '11_123')
        self.assertEqual(data['target']['page_name'], 'Fanpage')
        self.assertFalse(data['story_automatically_verified'])
        self.assertEqual(self.calls, [])
        self.assertEqual((await self.execute(data['confirmation_id'], target_id='11_999')).status_code, 400)
        result = await self.execute(data['confirmation_id'])
        self.assertEqual(result.json()['outcome'], 'succeeded', result.text)
        self.assertTrue(result.json()['manual_target_confirmed'])
        self.assertFalse(result.json()['story_automatically_verified'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((await self.execute(data['confirmation_id'])).status_code, 409)
        self.assertEqual((await self.client.get('/api/posts?page_id=11', headers=HEADERS)).json()['items'], [])
        self.seed()  # A stale load snapshot must not resurrect a deleted target.
        self.assertEqual(self.store.audits.get(self.sid, '11')['items'], [])

    async def test_invalid_selection_story_reel_video_ambiguous_and_missing_link(self):
        for payload in [{'page_id':'11', 'keys':[item()['key']]*2}, {'page_id':'11', 'keys':['11_123']},
                        {'page_id':'11','keys':[item()['key']],'token':TOKEN}, {'page_id':'22','keys':[item()['key']]},
                        {'page_id':'99','keys':[item()['key']]}]:
            self.assertGreaterEqual((await self.prepare(payload)).status_code, 400)
        for changes in [{'kind':'Story'}, {'kind':'Reel'}, {'kind':'Video'},
                        {'sources':[{'source':'published_posts','object_id':'11_999'}]},
                        {'sources':[{'source':'published_posts','object_id':'11_123'},{'source':'published_posts','object_id':'11_999'}]},
                        {'sources':[{'source':'published_posts','object_id':'11_123'},{'source':'video_reels','object_id':'123','post_id':'11_123'}]},
                        {'identified_story':True}, {'permalink':'javascript:alert(1)'}]:
            self.audit['items'] = [{**item(), **changes}]
            self.seed()
            self.assertEqual((await self.prepare()).status_code, 409, changes)
        self.assertEqual(self.calls, [])

    async def test_foreign_browser_page_origin_and_expiry(self):
        cid = (await self.prepare()).json()['confirmation_id']
        other = self.browser()
        bid, _, _ = self.store.bootstrap(None)
        sid, _ = self.store.replace(bid, 'other-user', [Page('11','Same Page',TOKEN)])
        other.cookies.set('fanpage_browser',bid)
        other.cookies.set('fanpage_session',sid)
        self.assertEqual((await self.execute(cid,other)).status_code,404)
        self.assertEqual((await self.prepare(client=other)).status_code,409)
        self.assertEqual((await self.client.post('/api/posts/delete/execute', headers={**HEADERS,'Origin':'https://evil.example'},
                              json={'confirmation_id':cid,'manual_target_confirmed':True})).status_code,403)
        self.store.audits.entries[self.sid].expires_at = time.monotonic()-1
        self.assertEqual((await self.execute(cid)).status_code,409)
        self.assertEqual(self.calls, [])
        await other.aclose()

    async def test_confirmation_ttl_and_manual_confirmation_required(self):
        cid = (await self.prepare()).json()['confirmation_id']
        self.assertEqual((await self.client.post('/api/posts/delete/execute',headers=HEADERS,
            json={'confirmation_id':cid,'manual_target_confirmed':False})).status_code,400)
        self.app.state.single_delete.confirmations[cid].expires_at = time.monotonic()-1
        self.app.state.single_delete.cleanup()
        self.assertNotIn(cid,self.app.state.single_delete.confirmations)
        self.assertEqual((await self.execute(cid)).status_code,404)
        self.assertEqual(self.calls, [])

    async def test_bad_success_shapes_are_uncertain_no_retry_and_retain_audit(self):
        for response in [httpx.Response(200,json={'success':False}), httpx.Response(200,json={'success':'true'}),
                         httpx.Response(200,json={'success':1}), httpx.Response(200,json={}),
                         httpx.Response(204), httpx.Response(200,content=b'broken'),
                         httpx.Response(200,json={'success':True,'error':None}),
                         httpx.Response(500,json={'success':True}),
                         httpx.ReadTimeout(TOKEN),httpx.ConnectError(TOKEN)]:
            self.response = response
            before = len(self.calls)
            cid = (await self.prepare()).json()['confirmation_id']
            result = await self.execute(cid)
            self.assertEqual(result.json()['outcome'], 'uncertain', result.text)
            self.assertEqual(len(self.calls),before+1)
            self.assertEqual(len(self.store.audits.get(self.sid,'11')['items']),1)
            self.assertNotIn(TOKEN,result.text)

    async def test_token_permission_rate_limit_and_shared_cooldown(self):
        for code, expected in [(190,'token_invalid'),(10,'permission_denied'),(4,'rate_limited')]:
            self.response = httpx.Response(400,json={'error':{'message':TOKEN,'code':code,'error_subcode':77}},headers={'Retry-After':'120'} if code==4 else {})
            cid = (await self.prepare()).json()['confirmation_id']
            result = (await self.execute(cid)).json()
            self.assertEqual(result['outcome'],'failed')
            self.assertEqual(result['reason'],expected)
            self.assertEqual(result['diagnostic']['error_subcode'],77)
            self.assertNotIn(TOKEN,str(result))
        before = len(self.calls)
        cid = (await self.prepare()).json()['confirmation_id']
        result = (await self.execute(cid)).json()
        self.assertEqual(result['reason'],'cooldown')
        self.assertGreaterEqual(result['retry_after'],119)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(len(self.store.audits.get(self.sid,'11')['items']),1)

    async def test_double_execute_and_shared_canonical_target_lock(self):
        cid = (await self.prepare()).json()['confirmation_id']
        cid2 = (await self.prepare()).json()['confirmation_id']
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            started.set()
            await release.wait()
            return httpx.Response(200,json={'success':True})
        self.response = delayed
        first = asyncio.create_task(self.execute(cid))
        await started.wait()
        self.assertIn('post:11_123',self.app.state.bulk.locks)
        self.assertEqual((await self.execute(cid)).status_code,409)
        self.assertEqual((await self.execute(cid2)).status_code,409)
        self.assertEqual(len(self.calls),1)
        release.set()
        await first
        self.assertNotIn('post:11_123',self.app.state.bulk.locks)

    async def test_dry_run_lock_blocks_delete_and_disconnect_during_governor_wait(self):
        cid = (await self.prepare()).json()['confirmation_id']
        self.app.state.bulk.locks.add('post:11_123')
        self.assertEqual((await self.execute(cid)).status_code,409)
        self.app.state.bulk.locks.clear()
        governor = self.store.governor
        await governor.slots.acquire()
        await governor.slots.acquire()
        task = asyncio.create_task(self.execute(cid))
        await asyncio.sleep(.02)
        await self.client.post('/api/disconnect',headers=HEADERS)
        governor.slots.release()
        governor.slots.release()
        result = await task
        self.assertEqual(result.json().get('reason') or result.json().get('error',{}).get('code'),'session_expired')
        self.assertEqual(self.calls, [])

    async def test_missing_page_token_expired_session_and_safe_logs(self):
        self.session.pages[0].access_token = ''
        self.assertEqual((await self.prepare()).json()['error']['code'],'page_token_missing')
        self.session.pages[0].access_token = TOKEN
        cid = (await self.prepare()).json()['confirmation_id']
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger('httpx')
        prior_level = logger.level
        logger.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        try:
            self.response = httpx.Response(200,json={'success':False,'detail':TOKEN})
            result = await self.execute(cid)
        finally:
            logging.getLogger().removeHandler(handler)
            logger.setLevel(prior_level)
        self.assertEqual(result.json()['outcome'],'uncertain')
        self.assertNotIn(TOKEN, stream.getvalue())
        self.assertIn('DELETE', stream.getvalue())
        self.assertNotIn(TOKEN, repr(self.app.state.single_delete.diagnostics))
        self.session.expires_at = time.monotonic()-1
        self.assertEqual((await self.prepare()).status_code,401)

    async def test_snapshot_change_rejected_without_meta_and_confirmation_capacity(self):
        cid = (await self.prepare()).json()['confirmation_id']
        self.store.audits.entries[self.sid].data['items'][0]['text'] = 'changed after preview'
        self.assertEqual((await self.execute(cid)).status_code,409)
        self.seed()
        for _ in range(9):
            self.assertEqual((await self.prepare()).status_code,200)
        self.assertEqual((await self.prepare()).json()['error']['code'],'delete_capacity')
        self.assertEqual(self.calls, [])

    async def test_loader_unknown_type_is_auditable_but_not_single_delete_candidate(self):
        from test_content import post
        for status in [None, 'unrecognized_type', 'added_photos', 'added_video', 'shared_story']:
            raw = post(123, status_type=status)
            normalized = self.app.state.jobs.loader._normalize(raw, 'published_posts', '11', [])
            normalized.pop('_date')
            self.audit['items'] = [normalized]
            self.seed()
            self.assertIsNone(normalized['post_status_type'])
            self.assertEqual((await self.prepare()).json()['error']['code'],'post_delete_unsupported')
        self.assertEqual(self.calls, [])

    async def test_session_expiry_after_prepare_and_known_story_ids_rejected(self):
        cid = (await self.prepare()).json()['confirmation_id']
        from content_jobs import ContentJob
        job = ContentJob(self.bid,self.sid,'11',100,self.session.expires_at,self.store.settings)
        job.story_ids.add('11_123')
        self.app.state.jobs.entries[job.id] = job
        self.assertEqual((await self.execute(cid)).json()['error']['code'],'post_delete_unsupported')
        job.story_ids.clear()
        self.session.expires_at = time.monotonic()-1
        self.assertEqual((await self.execute(cid)).json()['error']['code'],'session_expired')
        self.assertEqual(self.calls, [])

    async def test_success_removes_terminal_job_snapshot_and_linked_reel(self):
        from content_jobs import ContentJob
        from test_content import post, reel
        job = ContentJob(self.bid,self.sid,'11',100,self.session.expires_at,self.store.settings)
        job.terminal = True
        self.app.state.jobs.entries[job.id] = job
        normalized = self.app.state.jobs.loader._normalize(post(123), 'published_posts', '11', [])
        job.sources[0].candidates['11_123'] = normalized
        # Linked metadata in a different session cache must also be invalidated.
        other_item = {**item(), 'kind':'Reel', 'key':'video_reels:456', 'object_id':'456',
                      'sources':[{'source':'video_reels','object_id':'456','post_id':'11_123'}]}
        other_id, _, _ = self.store.bootstrap(None)
        other_sid, other_session = self.store.replace(other_id,'other-user',[Page('11','Same Page',TOKEN)])
        self.store.audits.reserve(other_sid,1,'11')
        self.store.audits.put(other_sid,{**self.audit,'items':[other_item]},other_session.expires_at)
        cid = (await self.prepare()).json()['confirmation_id']
        self.assertEqual((await self.execute(cid)).json()['outcome'],'succeeded')
        self.assertEqual(self.app.state.jobs.snapshot(job)['items'], [])
        self.assertEqual(self.store.audits.get(other_sid,'11')['items'], [])
        # Another old page entering a job cannot restore the tombstoned candidate.
        job.sources[1].candidates['456'] = self.app.state.jobs.loader._normalize(reel(456,'11_123'),'video_reels','11',[])
        self.assertEqual(self.app.state.jobs.snapshot(job)['items'], [])

    async def test_response_size_bound_and_actual_request_timeout_no_retry(self):
        self.app.state.graph.settings = Settings(graph_request_interval=.001, max_response_bytes=16, request_timeout=.02)
        cid = (await self.prepare()).json()['confirmation_id']
        self.response = httpx.Response(200,content=b'x'*17)
        self.assertEqual((await self.execute(cid)).json()['outcome'],'uncertain')
        async def slow(request):
            await asyncio.sleep(1)
            return httpx.Response(200,json={'success':True})
        self.response = slow
        cid = (await self.prepare()).json()['confirmation_id']
        result = (await self.execute(cid)).json()
        self.assertEqual(result['reason'],'timeout_after_send')
        self.assertEqual(len(self.calls),2)
        self.assertEqual(len(self.store.audits.get(self.sid,'11')['items']),1)

    async def test_governor_pacing_concurrency_and_cross_browser_target_lock(self):
        other = self.browser()
        bid, _, _ = self.store.bootstrap(None)
        sid, session = self.store.replace(bid,'other-user',[Page('11','Same Page',TOKEN)])
        other.cookies.set('fanpage_browser',bid)
        other.cookies.set('fanpage_session',sid)
        self.store.audits.reserve(sid,1,'11')
        self.store.audits.put(sid,copy.deepcopy(self.audit),session.expires_at)
        cid = (await self.prepare()).json()['confirmation_id']
        other_cid = (await self.prepare(client=other)).json()['confirmation_id']
        self.allowed_targets.update({'11_124','11_125'})
        second = {**item(), 'key':'published_posts:11_124','object_id':'11_124',
                  'sources':[{'source':'published_posts','object_id':'11_124'}]}
        self.store.audits.entries[self.sid].data['items'].append(second)
        cid2 = (await self.prepare({'page_id':'11','keys':[second['key']]})).json()['confirmation_id']
        third = {**item(), 'key':'published_posts:11_125','object_id':'11_125',
                 'sources':[{'source':'published_posts','object_id':'11_125'}]}
        self.store.audits.entries[self.sid].data['items'].append(third)
        cid3 = (await self.prepare({'page_id':'11','keys':[third['key']]})).json()['confirmation_id']
        governor = self.store.governor
        governor.interval = .035
        active, peak, times = 0, 0, []
        release, started = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            times.append(time.perf_counter())
            started.set()
            await release.wait()
            active -= 1
            return httpx.Response(200,json={'success':True})
        self.response = delayed
        first = asyncio.create_task(self.execute(cid))
        await started.wait()
        self.assertEqual((await self.execute(other_cid,other)).json()['error']['code'],'target_busy')
        second_task = asyncio.create_task(self.execute(cid2))
        for _ in range(100):
            if len(times)==2: break
            await asyncio.sleep(.002)
        self.assertEqual(peak,2)
        self.assertGreaterEqual(times[1]-times[0],.030)
        third_task = asyncio.create_task(self.execute(cid3))
        await asyncio.sleep(.03)
        self.assertEqual(len(self.calls),2)  # Third target cannot exceed the two shared permits.
        release.set()
        await asyncio.gather(first,second_task,third_task)
        self.assertEqual(peak,2)
        self.assertEqual(len(self.calls),3)
        await other.aclose()

