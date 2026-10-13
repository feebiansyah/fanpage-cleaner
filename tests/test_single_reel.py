"""Experimental original Reel deletion: dummy transport only, no live Meta calls."""
import asyncio
import copy
import tempfile
import time
import unittest
from pathlib import Path

import httpx
from app import create_app
from config import Settings
from sessions import Page

HEADERS = {'X-Fanpage-Client': '1', 'Origin': 'http://127.0.0.1:8000'}
TOKEN = 'dummy-reel-page-secret'


def reel():
    return {'key': 'video_reels:987', 'page_id': '11', 'object_id': '987',
            'sources': [{'source': 'video_reels', 'object_id': '987'}],
            'kind': 'Reel', 'text': 'Audited original Reel',
            'permalink': 'https://www.facebook.com/reel/987',
            'thumbnail': 'https://scontent.xx.fbcdn.net/reel.jpg', 'selectable': True}


class ReelFixture:
    async def asyncSetUp(self):
        self.calls = []
        self.allowed_targets = {'987', '11_123'}
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
        self.temp = tempfile.TemporaryDirectory(prefix='reel-test-')
        self.app = create_app(Settings(graph_request_interval=.001), httpx.MockTransport(handler),
                              checkpoint_root=Path(self.temp.name))
        self.lifecycle = self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.client = self.browser()
        self.store = self.app.state.store
        self.bid, _, _ = self.store.bootstrap(None)
        self.sid, self.session = self.store.replace(self.bid, 'dummy-user-secret',
            [Page('11', 'Fanpage', TOKEN), Page('22', 'No token', '')])
        self.client.cookies.set('fanpage_browser', self.bid)
        self.client.cookies.set('fanpage_session', self.sid)
        self.audit = {'page_id': '11', 'displayed': 1, 'found': 1, 'items': [reel()], 'safe_for_delete': False}
        self.seed()

    def browser(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 1)), base_url=HEADERS['Origin'])

    def seed(self):
        self.store.audits.reserve(self.sid, 1, '11')
        self.store.audits.put(self.sid, copy.deepcopy(self.audit), self.session.expires_at)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)
        self.temp.cleanup()

    async def prepare(self, payload=None, client=None):
        return await (client or self.client).post('/api/reels/delete/prepare', headers=HEADERS,
                    json=payload or {'page_id': '11', 'keys': [reel()['key']]})

    async def execute(self, confirmation, client=None, **extra):
        return await (client or self.client).post('/api/reels/delete/execute', headers=HEADERS,
                    json={'confirmation_id': confirmation, 'manual_target_confirmed': True, **extra})


class SingleReel(ReelFixture, unittest.IsolatedAsyncioTestCase):
    async def test_prepare_without_upstream_permalink_uses_original_reel_id(self):
        for permalink in ('', None):
            with self.subTest(permalink=permalink):
                item = {**reel(), 'object_id': '00987', 'permalink': permalink,
                        'sources': [{'source': 'video_reels', 'object_id': '00987'}]}
                self.audit['items'] = [item]
                self.seed()
                response = await self.prepare()
                self.assertEqual(response.status_code, 200, response.text)
                target = response.json()['target']
                self.assertEqual(target['target_id'], '00987')
                self.assertEqual(target['permalink'], 'https://www.facebook.com/reel/00987')
        self.assertEqual(self.calls, [])

    async def test_preview_original_reel_without_published_post(self):
        response = await self.prepare()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data['experimental'])
        self.assertFalse(data['contract_verified'])
        self.assertEqual(data['target']['target_id'], '987')
        self.assertEqual(data['target']['operation_scope'], 'reel_video')
        self.assertEqual(data['target']['page_name'], 'Fanpage')
        self.assertEqual(data['target']['thumbnail'], reel()['thumbnail'])
        self.assertLessEqual(data['expires_in_seconds'], 60)
        self.assertEqual(self.calls, [])

    async def test_strict_payload_no_free_id_token_or_multiple_selection(self):
        for payload in [{'page_id':'11','keys':[reel()['key'],reel()['key']]},
                        {'page_id':'11','keys':[reel()['key']],'target_id':'987'},
                        {'page_id':'11','keys':[reel()['key']],'access_token':TOKEN},
                        {'page_id':'11','keys':['video_reels:999']}]:
            response = await self.prepare(payload)
            self.assertGreaterEqual(response.status_code, 400, response.text)
        cid = (await self.prepare()).json()['confirmation_id']
        for extra in [{'manual_target_confirmed':False}, {'manual_target_confirmed':'true'},
                      {'target_id':'987'}, {'access_token':TOKEN}]:
            self.assertEqual((await self.execute(cid, **extra)).status_code, 400)
        self.assertEqual(self.calls, [])

    async def test_reject_story_bad_provenance_and_ambiguous_original_ids(self):
        cases = [ {'kind':'Story'}, {'identified_story':True}, {'page_id':'22'},
            {'sources':[{'source':'stories','object_id':'987'}]},
            {'sources':[{'source':'published_posts','object_id':'11_123'}]},
            {'sources':[{'source':'video_reels','object_id':'11_987'}]},
            {'sources':[{'source':'video_reels','object_id':'987'},{'source':'video_reels','object_id':'988'}]},
            {'sources':[{'source':'video_reels','object_id':'987','post_id':'22_123'}]},
            {'object_id':'999'},
            {'sources':[{'source':'video_reels','object_id':'987','post_id':'11_123'},
                        {'source':'published_posts','object_id':'11_124'}]} ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.audit['items'] = [{**reel(), **changes}]
                self.seed()
                response = await self.prepare()
                self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertEqual(self.calls, [])

    async def test_audit_ttl_snapshot_change_confirmation_expiry(self):
        cid = (await self.prepare()).json()['confirmation_id']
        self.store.audits.entries[self.sid].data['items'][0]['text'] = 'Changed'
        self.assertEqual((await self.execute(cid)).status_code,409)
        self.seed()
        cid = (await self.prepare()).json()['confirmation_id']
        self.app.state.single_delete.confirmations[cid].expires_at = time.monotonic()-1
        expired = await self.execute(cid)
        self.assertIn(expired.status_code,(409,404))
        self.assertIn(expired.json()['error']['code'],('confirmation_expired','confirmation_not_found'))
        self.store.audits.entries[self.sid].expires_at = time.monotonic()-1
        self.assertEqual((await self.prepare()).status_code, 409)
        self.assertEqual(self.calls, [])

    async def test_foreign_browser_page_and_origin_cannot_dispatch(self):
        cid = (await self.prepare()).json()['confirmation_id']
        other = self.browser()
        try:
            self.assertGreaterEqual((await self.execute(cid, other)).status_code, 400)
            self.assertGreaterEqual((await self.prepare({'page_id':'33','keys':[reel()['key']]})).status_code, 400)
            response = await self.client.post('/api/reels/delete/execute',
                headers={**HEADERS,'Origin':'https://foreign.example'},
                json={'confirmation_id':cid,'manual_target_confirmed':True})
            self.assertEqual(response.status_code, 403)
        finally:
            await other.aclose()
        self.assertEqual(self.calls, [])

    async def test_wrong_post_endpoint_does_not_consume_reel_confirmation(self):
        cid = (await self.prepare()).json()['confirmation_id']
        response = await self.client.post('/api/posts/delete/execute',headers=HEADERS,
            json={'confirmation_id':cid,'manual_target_confirmed':True})
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(self.app.state.single_delete.confirmations[cid].used)
        result = (await self.execute(cid)).json()
        self.assertEqual(result['outcome'], 'succeeded')
        self.assertEqual(result['target']['operation_scope'], 'reel_video')
        self.assertEqual(len(self.calls), 1)
        self.assertGreaterEqual((await self.execute(cid)).status_code, 400)
        last = (await self.client.get('/api/posts/delete/last',headers=HEADERS)).json()
        self.assertEqual(last['result'], result)
        self.assertEqual(len(self.calls), 1)

    async def test_bad_response_shapes_uncertain_single_send_retain_cache(self):
        responses = [httpx.Response(200,json={'success':False}), httpx.Response(200,json={'success':'true'}),
            httpx.Response(200,json={'success':1}), httpx.Response(204), httpx.Response(200,json={}),
            httpx.Response(200,content=b'broken'), httpx.Response(200,json={'success':True,'error':None}),
            httpx.Response(500,json={'success':True}), httpx.ReadTimeout(TOKEN)]
        for response in responses:
            self.response = response
            before = len(self.calls)
            cid = (await self.prepare()).json()['confirmation_id']
            result = await self.execute(cid)
            self.assertEqual(result.json()['outcome'], 'uncertain', result.text)
            self.assertNotIn(TOKEN, result.text)
            self.assertEqual(len(self.calls), before+1)
            self.assertEqual(len(self.store.audits.get(self.sid,'11')['items']), 1)

    async def test_coded_errors_sanitized_and_shared_cooldown(self):
        for code, reason in [(190,'token_invalid'),(10,'permission_denied'),(4,'rate_limited')]:
            self.app.state.bulk.blocked_scopes.clear()
            self.app.state.bulk.blocked_pages.clear()
            self.response = httpx.Response(400,json={'error':{'message':TOKEN,'code':code}},
                                          headers={'Retry-After':'120'} if code==4 else {})
            cid = (await self.prepare()).json()['confirmation_id']
            result = (await self.execute(cid)).json()
            self.assertEqual(result['outcome'], 'failed')
            self.assertEqual(result['reason'], reason)
            self.assertNotIn(TOKEN, str(result))
        before = len(self.calls)
        cid = (await self.prepare()).json()['confirmation_id']
        self.assertEqual((await self.execute(cid)).json()['reason'], 'cooldown')
        self.assertEqual(len(self.calls), before)

    async def test_double_click_and_linked_post_alias_lock(self):
        self.audit['items'][0]['sources'][0]['post_id'] = '11_123'
        self.seed()
        cid = (await self.prepare()).json()['confirmation_id']
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            started.set()
            await release.wait()
            return httpx.Response(200,json={'success':True})
        self.response = delayed
        task = asyncio.create_task(self.execute(cid))
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertIn('post:11_123', self.app.state.bulk.locks)
            self.assertEqual((await self.execute(cid)).status_code, 409)
            cid2 = (await self.prepare()).json()['confirmation_id']
            self.assertEqual((await self.execute(cid2)).json()['error']['code'], 'target_busy')
            self.assertEqual(len(self.calls), 1)
        finally:
            release.set()
            await task

    async def test_standalone_reel_prepares_in_bulk_without_dispatch(self):
        response = await self.client.post('/api/bulk-delete/prepare',headers=HEADERS,
            json={'selections':[{'page_id':'11','keys':[reel()['key']]}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['supported'], 1)
        self.assertEqual(response.json()['unsupported'], 0)
        self.assertEqual(response.json()['items'][0]['target_id'],'987')
        self.assertEqual(response.json()['items'][0]['operation_scope'],'reel_video')
        self.assertEqual(self.calls, [])

    async def test_known_story_and_expired_session_deny_dispatch(self):
        from content_jobs import ContentJob
        job = ContentJob(self.bid,self.sid,'11',100,self.session.expires_at,self.store.settings)
        job.story_ids.add('987')
        self.app.state.jobs.entries[job.id] = job
        self.assertEqual((await self.prepare()).json()['error']['code'], 'identified_story')
        job.story_ids.clear()
        cid = (await self.prepare()).json()['confirmation_id']
        job.story_ids.add('987')
        self.assertEqual((await self.execute(cid)).json()['error']['code'], 'identified_story')
        job.story_ids.clear()
        self.session.expires_at = time.monotonic()-1
        self.assertEqual((await self.execute(cid)).json()['error']['code'], 'session_expired')
        self.assertEqual(self.calls, [])

    async def test_success_tombstones_video_linked_post_and_old_job_aliases(self):
        from content_jobs import ContentJob
        from test_content import post, reel as raw_reel
        self.audit['items'][0]['sources'][0]['post_id'] = '11_123'
        self.seed()
        stale = copy.deepcopy(self.audit)
        job = ContentJob(self.bid,self.sid,'11',100,self.session.expires_at,self.store.settings)
        job.terminal = True
        self.app.state.jobs.entries[job.id] = job
        loader = self.app.state.jobs.loader
        job.sources[0].candidates['11_123'] = loader._normalize(post(123),'published_posts','11',[])
        job.sources[1].candidates['987'] = loader._normalize(raw_reel(987,'11_123'),'video_reels','11',[])
        bid, _, _ = self.store.bootstrap(None)
        sid, session = self.store.replace(bid,'other-user',[Page('11','Same Page',TOKEN)])
        self.store.audits.reserve(sid,1,'11')
        self.store.audits.put(sid,copy.deepcopy(stale),session.expires_at)
        cid = (await self.prepare()).json()['confirmation_id']
        result = (await self.execute(cid)).json()
        self.assertEqual(result['outcome'],'succeeded')
        self.assertEqual(self.calls[0].url.path,'/v26.0/987')
        self.assertEqual(self.store.audits.get(self.sid,'11')['items'],[])
        self.assertEqual(self.store.audits.get(sid,'11')['items'],[])
        self.assertEqual(self.app.state.jobs.snapshot(job)['items'],[])
        self.store.audits.reserve(self.sid,1,'11')
        self.store.audits.put(self.sid,stale,self.session.expires_at)
        self.assertEqual(self.store.audits.get(self.sid,'11')['items'],[])

    async def test_unlinked_reel_locks_original_video_identity(self):
        cid = (await self.prepare()).json()['confirmation_id']
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            started.set()
            await release.wait()
            return httpx.Response(200,json={'success':True})
        self.response = delayed
        task = asyncio.create_task(self.execute(cid))
        try:
            await asyncio.wait_for(started.wait(),2)
            self.assertIn('video:987',self.app.state.bulk.locks)
            self.assertNotIn('post:987',self.app.state.bulk.locks)
        finally:
            release.set()
            await task

    async def test_original_numeric_id_kept_verbatim(self):
        original = '000987'
        self.allowed_targets.add(original)
        entry = {**reel(),'key':'video_reels:'+original,'object_id':original,
                 'sources':[{'source':'video_reels','object_id':original}]}
        self.audit['items'] = [entry]
        self.seed()
        response = await self.prepare({'page_id':'11','keys':[entry['key']]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['target']['target_id'],original)
        result = await self.execute(response.json()['confirmation_id'])
        self.assertEqual(result.json()['outcome'],'succeeded')
        self.assertEqual(self.calls[0].url.path,'/v26.0/'+original)

    async def test_reel_endpoint_rejects_post_confirmation_without_consuming(self):
        from test_single_delete import item
        self.audit['items'] = [item()]
        self.seed()
        prepared = await self.client.post('/api/posts/delete/prepare',headers=HEADERS,
            json={'page_id':'11','keys':[item()['key']]})
        cid = prepared.json()['confirmation_id']
        self.assertGreaterEqual((await self.execute(cid)).status_code,400)
        self.assertFalse(self.app.state.single_delete.confirmations[cid].used)
        response = await self.client.post('/api/posts/delete/execute',headers=HEADERS,
            json={'confirmation_id':cid,'manual_target_confirmed':True})
        self.assertEqual(response.json()['outcome'],'succeeded')
        self.assertEqual(len(self.calls),1)

    async def test_unsafe_thumbnail_is_omitted(self):
        self.audit['items'][0]['thumbnail'] = 'https://foreign.example/image.jpg'
        self.seed()
        response = await self.prepare()
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['target']['thumbnail'])

    async def test_linked_reel_needs_two_tombstone_capacity_slots(self):
        from dataclasses import replace
        self.store.settings = replace(self.store.settings,max_audit_items=1)
        self.audit['items'][0]['sources'][0]['post_id'] = '11_123'
        self.seed()
        cid = (await self.prepare()).json()['confirmation_id']
        response = await self.execute(cid)
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(response.json()['error']['code'],'delete_capacity')
        self.assertEqual(self.calls,[])

    async def test_unlinked_reel_blocks_related_single_and_queued_bulk_post(self):
        from test_single_delete import item
        linked = {**item(),'kind':'Reel','sources':[
            {'source':'published_posts','object_id':'11_123'},
            {'source':'video_reels','object_id':'987','post_id':'11_123'}]}
        self.audit.update(items=[reel(),linked],displayed=2,found=2)
        self.store.audits.reserve(self.sid,2,'11')
        self.store.audits.put(self.sid,copy.deepcopy(self.audit),self.session.expires_at)
        post_prepared = await self.client.post('/api/posts/delete/prepare',headers=HEADERS,
            json={'page_id':'11','keys':[linked['key']]})
        bulk_prepared = await self.client.post('/api/bulk-delete/prepare',headers=HEADERS,
            json={'selections':[{'page_id':'11','keys':[linked['key']]}]})
        self.assertEqual(post_prepared.status_code,200,post_prepared.text)
        self.assertEqual(bulk_prepared.status_code,200,bulk_prepared.text)
        cid = (await self.prepare()).json()['confirmation_id']
        started, release = asyncio.Event(),asyncio.Event()
        async def delayed(request):
            started.set()
            await release.wait()
            return httpx.Response(200,json={'success':True})
        self.response = delayed
        task = asyncio.create_task(self.execute(cid))
        try:
            await asyncio.wait_for(started.wait(),2)
            response = await self.client.post('/api/posts/delete/execute',headers=HEADERS,
                json={'confirmation_id':post_prepared.json()['confirmation_id'],'manual_target_confirmed':True})
            self.assertEqual(response.json()['error']['code'],'target_busy')
            queued = await self.client.post('/api/bulk-delete/confirm',headers=HEADERS,
                json={'confirmation_id':bulk_prepared.json()['confirmation_id'],'manual_targets_confirmed':True})
            self.assertEqual(queued.status_code,200,queued.text)
            jid = queued.json()['job_id']
            await asyncio.sleep(.03)
            self.assertEqual(len(self.calls),1)
        finally:
            release.set()
            await task
        for _ in range(300):
            snapshot = (await self.client.get('/api/bulk-delete/jobs/'+jid,headers=HEADERS)).json()
            if snapshot['terminal']:
                break
            await asyncio.sleep(.01)
        self.assertTrue(snapshot['terminal'])
        self.assertEqual(snapshot['counts']['blocked'],1,snapshot)
        self.assertEqual(len(self.calls),1,'Tombstoned related Reel must stop queued Page Post before transport')

    async def test_confirmed_post_suppresses_exact_known_reel_alias(self):
        from test_single_delete import item
        linked = {**item(),'kind':'Reel','sources':[
            {'source':'published_posts','object_id':'11_123'},
            {'source':'video_reels','object_id':'987','post_id':'11_123'}]}
        unrelated = {**reel(),'key':'video_reels:988','object_id':'988',
                     'sources':[{'source':'video_reels','object_id':'988'}],
                     'permalink':'https://www.facebook.com/reel/988'}
        self.audit.update(items=[linked,reel(),unrelated],displayed=3,found=3)
        self.store.audits.reserve(self.sid,3,'11')
        self.store.audits.put(self.sid,copy.deepcopy(self.audit),self.session.expires_at)
        response = await self.client.post('/api/posts/delete/prepare',headers=HEADERS,
            json={'page_id':'11','keys':[linked['key']]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['target']['related_video_ids'],['987'])
        response = await self.client.post('/api/posts/delete/execute',headers=HEADERS,
            json={'confirmation_id':response.json()['confirmation_id'],'manual_target_confirmed':True})
        self.assertEqual(response.json()['outcome'],'succeeded')
        self.assertEqual(self.calls[0].url.path,'/v26.0/11_123')
        remaining = self.store.audits.get(self.sid,'11')['items']
        self.assertEqual([entry['key'] for entry in remaining],[unrelated['key']])
        self.assertGreaterEqual((await self.prepare()).status_code,400)
        untouched = await self.prepare({'page_id':'11','keys':[unrelated['key']]})
        self.assertEqual(untouched.status_code,200,untouched.text)
        self.assertEqual(len(self.calls),1)
