"""Real-delete contracts, exercised exclusively against dummy MockTransport."""
import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import create_app
from config import Settings
from sessions import Page


HEADERS = {'X-Fanpage-Client': '1', 'Origin': 'http://127.0.0.1:8000'}
TOKEN = 'dummy-bulk-page-secret'
OTHER_TOKEN = 'dummy-other-page-secret'


def post(n, page='11', **changes):
    node = f'{page}_{n}'
    return {**{'key': 'published_posts:'+node, 'page_id': page, 'object_id': node,
               'sources': [{'source': 'published_posts', 'object_id': node}],
               'kind': 'Postingan', 'text': f'Audited post {n}',
               'permalink': f'https://www.facebook.com/{page}/posts/{n}',
               'selectable': True}, **changes}


class BulkDelete(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_standalone_and_merged_reel_retains_proven_post_lock(self):
        standalone=post(2,key='video_reels:987',object_id='987',sources=[{'source':'video_reels','object_id':'987'}],permalink=None)
        merged=post(1,sources=[{'source':'published_posts','object_id':'11_1'},
                             {'source':'video_reels','object_id':'987','post_id':'11_1'}])
        self.seed([standalone,merged])
        data=(await self.prepare(selections=[{'page_id':'11','keys':[standalone['key'],merged['key']]}])).json()
        self.assertEqual(data['supported'],1)
        self.assertEqual(data['duplicates'],1)
        self.assertEqual(data['items'][0]['linked_post_id'],'11_1')
        self.assertEqual(data['items'][0]['operation_scope'],'reel_video')
        self.assertEqual(self.calls,[])

    async def test_merged_candidate_uses_video_once_and_rejects_story_ambiguity(self):
        merged=post(1, kind='Reel', sources=[{'source':'published_posts','object_id':'11_1'},
            {'source':'video_reels','object_id':'987','post_id':'11_1'}])
        story=post(2,kind='Story')
        ambiguous=post(3,sources=[{'source':'video_reels','object_id':'988'},
                                  {'source':'video_reels','object_id':'989'}])
        self.allowed.add('987')
        self.seed([merged,story,ambiguous])
        preview=(await self.prepare(3)).json()
        self.assertEqual(preview['supported'],1)
        self.assertEqual(preview['unsupported'],2)
        self.assertEqual(preview['items'][0]['target_id'],'987')
        result=await self.finished((await self.confirm(preview['confirmation_id'])).json()['job_id'])
        self.assertEqual(result['counts']['succeeded'],1)
        self.assertEqual([r.url.path for r,_ in self.calls],['/v26.0/987'])
        ledger=''.join(path.read_text() for path in Path(self.temp.name).glob('*.json'))
        self.assertIn('video_node',ledger)
        self.assertNotIn(TOKEN,ledger)

    async def test_mixed_reel_failure_uncertain_and_success_without_retry(self):
        items=[post(1)]
        for n in ('987','988','989'):
            self.allowed.add(n)
            items.append(post(2,key='video_reels:'+n,object_id=n,kind='Reel',
                sources=[{'source':'video_reels','object_id':n}],permalink=None))
        self.seed(items)
        self.responses['988']=httpx.Response(403,json={'error':{'code':200,'message':'Object denied'}})
        self.responses['989']=httpx.ReadTimeout('dummy')
        selections=[{'page_id':'11','keys':[item['key'] for item in items]}]
        result=await self.finished(await self.start(selections=selections))
        self.assertEqual(result['counts']['succeeded'],2)
        self.assertEqual(result['counts']['failed'],1)
        self.assertEqual(result['counts']['uncertain'],1)
        self.assertEqual(result['counts']['blocked'],0)
        self.assertEqual(len(self.calls),4)
        self.assertEqual({item['key'] for item in self.store.audits.get(self.sid,'11')['items']},
                         {'video_reels:988','video_reels:989'})

    async def test_mixed_queue_original_ids_and_reel_transport(self):
        reel = post(2, key='video_reels:987', object_id='987', kind='Reel',
                    sources=[{'source':'video_reels','object_id':'987'}], permalink='')
        self.allowed.add('987')
        self.seed([post(1), reel])
        prepared = await self.prepare(selections=[{'page_id':'11','keys':[post(1)['key'],reel['key']]}])
        self.assertEqual(prepared.json()['supported'],2)
        self.assertEqual(self.calls,[])
        with patch.object(self.app.state.graph, 'delete_reel_once', wraps=self.app.state.graph.delete_reel_once) as send:
            result = await self.finished((await self.confirm(prepared.json()['confirmation_id'])).json()['job_id'])
            self.assertEqual(send.call_count,1)
        self.assertEqual(result['counts']['succeeded'],2)
        self.assertEqual({r.url.path for r,_ in self.calls},{'/v26.0/11_1','/v26.0/987'})

    async def test_linked_post_and_reel_choose_one_media_operation(self):
        reel = post(2, key='video_reels:987', object_id='987', kind='Reel',
                    sources=[{'source':'video_reels','object_id':'987','post_id':'11_1'}], permalink=None)
        self.allowed.add('987')
        self.seed([post(1), reel])
        prepared = await self.prepare(selections=[{'page_id':'11','keys':[post(1)['key'],reel['key']]}])
        data=prepared.json()
        self.assertEqual(data['supported'],1)
        self.assertEqual(data['duplicates'],1)
        self.assertEqual(data['items'][0]['operation_scope'],'reel_video')
        self.assertEqual(data['items'][0]['permalink'],'https://www.facebook.com/reel/987')
        result=await self.finished((await self.confirm(data['confirmation_id'])).json()['job_id'])
        self.assertEqual(result['counts']['succeeded'],1)
        self.assertEqual([r.url.path for r,_ in self.calls],['/v26.0/987'])

    async def test_message_sanitization_bounds_controls_and_unknown_bearer_header(self):
        from graph_api import sanitized_meta_message
        value=sanitized_meta_message('Authorization: Bearer UNTRACKED_EXAMPLE\n\x00'+TOKEN,{TOKEN})
        self.assertNotIn('UNTRACKED_EXAMPLE',value)
        self.assertNotIn(TOKEN,value)
        self.assertNotIn('\x00',value)
        self.assertIsNone(sanitized_meta_message({'message':TOKEN},{TOKEN}))
        long_token='x'*700
        value=sanitized_meta_message('a'*400+' '+long_token,{long_token})
        self.assertLessEqual(len(value),500)
        self.assertNotIn('x',value)
        self.assertNotIn(TOKEN,sanitized_meta_message('a'*8192+TOKEN,{TOKEN}))

    async def test_permission_window_is_rolling_and_success_resets_it(self):
        from types import SimpleNamespace
        queue=self.app.state.bulk
        target=SimpleNamespace(page_id='11',scope='dummy-scope')
        base=time.monotonic()
        for elapsed in (0,59,118):
            with patch('bulk_queue.time.monotonic',return_value=base+elapsed):
                queue.record_delete_outcome(target,'permission_denied')
        self.assertEqual(queue.permission_circuits,{})
        with patch('bulk_queue.time.monotonic',return_value=base+119):
            queue.record_delete_outcome(target,'success_boolean_true')
            queue.record_delete_outcome(target,'permission_denied')
        self.assertEqual(queue.permission_circuits,{})
        for elapsed in (120,121):
            with patch('bulk_queue.time.monotonic',return_value=base+elapsed):
                queue.record_delete_outcome(target,'permission_denied')
        self.assertIn(('11','dummy-scope'),queue.permission_circuits)

    async def test_permission_breaker_memory_capacity_fails_closed(self):
        from dataclasses import replace
        from types import SimpleNamespace
        queue=self.app.state.bulk
        queue.settings=replace(queue.settings,max_token_entries=1)
        one=SimpleNamespace(page_id='11',scope='one')
        two=SimpleNamespace(page_id='22',scope='two')
        queue.record_delete_outcome(one,'permission_denied')
        queue.record_delete_outcome(two,'permission_denied')
        self.assertLessEqual(len(queue.permission_denials),1)
        self.assertEqual(queue.block_reason(two),'permission_circuit_capacity')
        self.assertEqual(self.calls,[])

    async def test_job_preserves_unsupported_count_from_confirmed_preview(self):
        self.seed([post(1),post(2,kind='Story')])
        result=await self.finished(await self.start(2))
        self.assertEqual(result['unsupported'],1)
        self.assertEqual(result['selected'],2)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['counts']['succeeded'],1)
    async def test_generic_permission_is_per_target_and_later_post_continues(self):
        self.store.governor.interval=.03
        self.responses['11_3']=httpx.Response(403,json={'error':{'code':200,'error_subcode':77,
            'message':'One object cannot be deleted','error_user_msg':'Periksa target ini'}})
        result=await self.finished(await self.start(4))
        self.assertEqual(result['counts']['succeeded'],3)
        self.assertEqual(result['counts']['failed'],1)
        self.assertEqual(result['counts']['blocked'],0)
        diagnostic=next(row['diagnostic'] for row in result['items'] if row['status']=='failed')
        self.assertEqual(diagnostic['error_code'],200)
        self.assertEqual(diagnostic['error_subcode'],77)
        self.assertEqual(diagnostic['error_message'],'One object cannot be deleted')

    async def test_repeated_permission_opens_circuit_not_page_permission_claim(self):
        self.store.governor.interval=.03
        self.response=httpx.Response(403,json={'error':{'code':200,'message':'Object denied'}})
        result=await self.finished(await self.start(20))
        self.assertEqual(len(self.calls),3)
        self.assertEqual(result['counts']['failed'],3)
        self.assertEqual(result['counts']['blocked'],17)
        self.assertTrue(all('permission_circuit_open' in row['reasons'] for row in result['items'] if row['status']=='blocked'))
        queue=self.app.state.bulk
        queue.permission_circuits={key:time.monotonic()-1 for key in queue.permission_circuits}
        queue.sync_sessions()
        self.response=httpx.Response(200,json={'success':True})
        new=await self.finished(await self.start(selections=[{'page_id':'11','keys':[post(4)['key']]}]))
        self.assertEqual(new['counts']['succeeded'],1)
        self.assertEqual((await self.snapshot(result['job_id']))['counts']['blocked'],17)

    async def test_meta_messages_redact_all_sessions_tokens_and_urls_before_truncating(self):
        bid,_,_=self.store.bootstrap(None)
        foreign='dummy-foreign-secret'
        self.store.replace(bid,foreign,[Page('33','Foreign',foreign)])
        message=f'{TOKEN} {OTHER_TOKEN} dummy-bulk-user-secret {foreign} https://graph.facebook.com/x?access_token={TOKEN}\n<script>visible as text</script>'
        self.response=httpx.Response(403,json={'error':{'code':200,'message':message,'error_user_msg':message}})
        result=await self.finished(await self.start(1))
        diag=result['items'][0]['diagnostic']
        for key in ('error_message','error_user_msg'):
            self.assertIsInstance(diag[key],str)
            self.assertLessEqual(len(diag[key]),500)
            self.assertNotIn('https://',diag[key])
        for secret in (TOKEN,OTHER_TOKEN,'dummy-bulk-user-secret',foreign):
            self.assertNotIn(secret,json.dumps(result))

    async def test_slow_checkpoint_cannot_dispatch_after_deadline(self):
        from errors import AppError
        def slow_preflight():
            time.sleep(.05)
            return TOKEN
        with self.assertRaises(AppError) as raised:
            await self.store.governor.pace_dispatch(self.store.token_key(TOKEN),
                    time.monotonic()+.02, slow_preflight)
        self.assertEqual(raised.exception.code, 'timeout')
        self.assertEqual(self.store.governor.last_dispatch_at, 0)
        self.assertEqual(self.calls, [])
    async def test_accepted_confirmation_replay_survives_preview_ttl(self):
        cid = (await self.prepare(5)).json()['confirmation_id']
        first = await self.confirm(cid)
        jid = first.json()['job_id']
        await self.finished(jid)
        self.app.state.bulk_delete.confirmations[cid].expires_at = time.monotonic()-1
        repeated = await self.confirm(cid)
        self.assertEqual(repeated.json()['job_id'], jid)
        self.assertEqual(len(self.calls), 5)

    async def test_single_generic_denial_does_not_block_other_bulk_targets(self):
        self.response = httpx.Response(403, json={'error': {'code': 10}})
        preview = await self.client.post('/api/posts/delete/prepare', headers=HEADERS,
                    json={'page_id':'11','keys':[post(1)['key']]})
        result = await self.client.post('/api/posts/delete/execute', headers=HEADERS,
                    json={'confirmation_id':preview.json()['confirmation_id'], 'manual_target_confirmed':True})
        self.assertEqual(result.json()['reason'], 'permission_denied')
        self.response = httpx.Response(200, json={'success':True})
        job = await self.finished(await self.start(selections=[
            {'page_id':'11','keys':[post(2)['key']]},
            {'page_id':'22','keys':[post(1,'22')['key']]}]))
        self.assertEqual(job['counts']['blocked'], 0)
        self.assertEqual(job['counts']['succeeded'], 2)
        self.assertEqual(len(self.calls), 3)

    async def test_checkpoint_failure_does_not_finish_before_sent_response(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'success':True})
        self.response = delayed
        jid = await self.start(5)
        await asyncio.wait_for(entered.wait(), 2)
        job = self.app.state.bulk.jobs[jid]
        with patch.object(self.app.state.bulk.checkpoints, 'write', side_effect=OSError('dummy')):
            await self.client.post('/api/bulk-delete/jobs/'+jid+'/stop', headers=HEADERS)
            self.assertFalse(job.terminal)
            sent = len(self.calls)
            release.set()
            for _ in range(200):
                if job.terminal: break
                await asyncio.sleep(.01)
            self.assertTrue(job.terminal)
            self.assertEqual(len(self.calls), sent)
            self.assertEqual(sum(t.status=='succeeded' for t in job.targets), sent)

    async def test_second_job_on_same_page_uses_new_targets_after_empty_bucket(self):
        first=await self.finished(await self.start(2))
        self.assertEqual(first['counts']['succeeded'],2)
        await asyncio.sleep(.15)
        jid=await self.start(selections=[{'page_id':'11','keys':[post(n)['key'] for n in (3,4,5)]}])
        second=await self.finished(jid)
        self.assertEqual(second['counts']['succeeded'],3)
        self.assertEqual(len(self.calls),5)
        self.assertEqual(self.app.state.bulk.success_reservations,set())

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.responses = {}
        self.response = httpx.Response(200, json={'success': True})
        self.allowed = {f'{page}_{n}' for page in ('11', '22') for n in range(1, 31)}
        async def handler(request):
            # Match the governor's high-resolution clock; Windows monotonic can round to 15 ms.
            self.calls.append((request, time.perf_counter()))
            self.assertEqual(request.url.host, 'graph.facebook.com')
            if request.method == 'GET':
                return httpx.Response(200, json={'data': []})
            self.assertEqual(request.method, 'DELETE')
            self.assertTrue(request.url.path.startswith('/v26.0/'))
            self.assertIn(request.url.path.removeprefix('/v26.0/'), self.allowed)
            self.assertEqual(request.url.query, b'')
            self.assertIn(request.headers['authorization'], ['Bearer '+TOKEN, 'Bearer '+OTHER_TOKEN])
            response = self.responses.get(request.url.path.rsplit('/', 1)[-1], self.response)
            if isinstance(response, Exception):
                raise response
            return await response(request) if callable(response) else response
        self.app = create_app(Settings(graph_request_interval=.005,cooldown_seconds=.1,
                              bulk_delete_max_targets=getattr(self, 'max_targets', 20)), httpx.MockTransport(handler),
                              checkpoint_root=Path(self.temp.name))
        self.lifecycle = self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.store = self.app.state.store
        self.bid, _, _ = self.store.bootstrap(None)
        self.sid, self.session = self.store.replace(self.bid, 'dummy-bulk-user-secret',
                    [Page('11', 'First Page', TOKEN), Page('22', 'Second Page', OTHER_TOKEN)])
        self.client = self.browser(self.bid, self.sid)
        self.seed([post(n) for n in range(1, 26)])
        self.seed([post(n, '22') for n in range(1, 26)], '22')

    def browser(self, bid, sid):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=HEADERS['Origin'])
        client.cookies.set('fanpage_browser', bid)
        client.cookies.set('fanpage_session', sid)
        return client

    def seed(self, items, page='11'):
        self.store.audits.reserve(self.sid, len(items), page)
        self.store.audits.put(self.sid, {'page_id': page, 'items': copy.deepcopy(items),
                 'displayed': len(items), 'found': len(items), 'safe_for_delete': False}, self.session.expires_at)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)
        self.temp.cleanup()

    async def prepare(self, n=5, selections=None):
        return await self.client.post('/api/bulk-delete/prepare', headers=HEADERS,
                 json={'selections': selections or [{'page_id': '11', 'keys': [post(i)['key'] for i in range(1, n+1)]}]})

    async def confirm(self, cid, client=None, **extra):
        return await (client or self.client).post('/api/bulk-delete/confirm', headers=HEADERS,
                 json={'confirmation_id': cid, 'manual_targets_confirmed': True, **extra})

    async def start(self, n=5, selections=None):
        response = await self.prepare(n, selections)
        self.assertEqual(response.status_code, 200, response.text)
        confirmed = await self.confirm(response.json()['confirmation_id'])
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        return confirmed.json()['job_id']

    async def snapshot(self, jid):
        response = await self.client.get('/api/bulk-delete/jobs/'+jid, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        return response.json()

    async def finished(self, jid):
        for _ in range(300):
            result = await self.snapshot(jid)
            if result['terminal']:
                return result
            await asyncio.sleep(.01)
        self.fail('Mock-only bulk delete did not reach a terminal state')

    async def test_five_and_twenty_exact_original_targets_and_local_polling(self):
        for count in (5, 20):
            page_id='11' if count==5 else '22'
            self.seed([post(n,page_id) for n in range(1, 26)],page_id)
            before = len(self.calls)
            jid = await self.start(selections=[{'page_id':page_id,'keys':[post(n,page_id)['key'] for n in range(1,count+1)]}])
            result = await self.finished(jid)
            self.assertEqual(result['mode'], 'delete_page_posts')
            self.assertEqual(result['total'], count)
            self.assertEqual(result['completed'], count)
            self.assertEqual(result['counts']['succeeded'], count)
            self.assertEqual(sum(result['counts'].values()), count)
            self.assertEqual(len(self.calls)-before, count)
            self.assertTrue(all(item['attempts'] == 1 for item in result['items']))
            for _ in range(3):
                self.assertEqual((await self.snapshot(jid))['items'], result['items'])
            active = (await self.client.get('/api/bulk-delete/jobs/active', headers=HEADERS)).json()
            self.assertEqual(active['jobs'], [])
            self.assertEqual(active['last_job']['job_id'], jid)
            self.assertEqual(len(self.calls)-before, count)

    async def test_limit_dedup_and_no_client_supplied_delete_target(self):
        self.assertEqual((await self.prepare(21)).status_code, 400)
        keys = [post(1)['key'], post(1)['key'], post(2)['key']]
        prepared = await self.prepare(selections=[{'page_id': '11', 'keys': keys}])
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(prepared.json()['duplicates'], 1)
        self.assertEqual(prepared.json()['supported'], 2)
        self.assertEqual((await self.confirm(prepared.json()['confirmation_id'], target_id='11_30')).status_code, 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(Settings().bulk_delete_max_targets, 5000)
        with self.assertRaises(ValueError):
            Settings(bulk_delete_max_targets=5001)

    async def test_owned_preview_explicit_unsupported_and_immutable_snapshot(self):
        items = [post(1), post(2, kind='Story'),
                 post(3, sources=[{'source': 'video_reels', 'object_id': '3'}]),
                 post(4, sources=[{'source': 'published_posts', 'object_id': '11_4'},
                                  {'source': 'published_posts', 'object_id': '11_5'}])]
        self.seed(items)
        prepared = await self.prepare(4)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        data = prepared.json()
        self.assertEqual(data['supported'], 1)
        self.assertEqual(data['unsupported'], 3)
        supported=next(item for item in data['items'] if item['supported'])
        self.assertEqual(supported['target_id'], '11_1')
        self.assertEqual(supported['page_name'], 'First Page')
        self.assertTrue(all(item['reason'] and item['message'] for item in data['items'] if not item['supported']))
        self.assertEqual(self.calls, [])
        self.store.audits.entries[self.sid].expires_at = time.monotonic()-1
        confirmed = await self.confirm(data['confirmation_id'])
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        done = await self.finished(confirmed.json()['job_id'])
        self.assertEqual(done['counts']['succeeded'], 1)
        self.assertEqual([r.url.path for r, _ in self.calls], ['/v26.0/11_1'])

    async def test_invalid_owned_key_foreign_page_manual_confirmation_origin_and_owner(self):
        for selections in ([{'page_id': '99', 'keys': [post(1)['key']]}],
                           [{'page_id': '11', 'keys': ['untrusted:11_1']}]):
            self.assertGreaterEqual((await self.prepare(selections=selections)).status_code, 400)
        data = (await self.prepare(1)).json()
        cid = data['confirmation_id']
        self.assertEqual((await self.confirm(cid, manual_targets_confirmed=False)).status_code, 400)
        bid, _, _ = self.store.bootstrap(None)
        sid, _ = self.store.replace(bid, 'other-user', [Page('11', 'Other', TOKEN)])
        other = self.browser(bid, sid)
        try:
            self.assertEqual((await self.confirm(cid, client=other)).status_code, 404)
            evil = await self.client.post('/api/bulk-delete/confirm', headers={**HEADERS, 'Origin': 'https://evil.example'},
                      json={'confirmation_id': cid, 'manual_targets_confirmed': True})
            self.assertEqual(evil.status_code, 403)
            jid = (await self.confirm(cid)).json()['job_id']
            self.assertEqual((await other.get('/api/bulk-delete/jobs/'+jid, headers=HEADERS)).status_code, 404)
            self.assertEqual((await other.get('/api/bulk-delete/jobs/active', headers=HEADERS)).json()['jobs'], [])
            await self.finished(jid)
        finally:
            await other.aclose()

    async def test_same_confirmation_concurrent_and_later_confirm_is_idempotent(self):
        cid = (await self.prepare(5)).json()['confirmation_id']
        first, second = await asyncio.gather(self.confirm(cid), self.confirm(cid))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        jid = first.json()['job_id']
        self.assertEqual(second.json()['job_id'], jid)
        await self.finished(jid)
        self.assertEqual((await self.confirm(cid)).json()['job_id'], jid)
        self.assertEqual(len(self.calls), 5)

    async def test_confirmation_ttl_expires_before_any_dispatch(self):
        prepared = await self.prepare(1)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        data = prepared.json()
        self.assertGreater(data['expires_in_seconds'], 0)
        self.app.state.bulk_delete.confirmations[data['confirmation_id']].expires_at = time.monotonic()-1
        response = await self.confirm(data['confirmation_id'])
        self.assertIn(response.status_code, (404, 409))
        self.assertEqual(self.calls, [])

    async def test_bulk_and_single_delete_share_canonical_target_lock(self):
        cid = (await self.client.post('/api/posts/delete/prepare', headers=HEADERS,
                 json={'page_id': '11', 'keys': [post(1)['key']]})).json()['confirmation_id']
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'success': True})
        self.response = delayed
        jid = await self.start(1)
        await asyncio.wait_for(entered.wait(), 2)
        self.assertIn('post:11_1', self.app.state.bulk.locks)
        response = await self.client.post('/api/posts/delete/execute', headers=HEADERS,
                 json={'confirmation_id': cid, 'manual_target_confirmed': True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.calls), 1)
        release.set()
        await self.finished(jid)
        self.assertNotIn('post:11_1', self.app.state.bulk.locks)

    async def test_mixed_results_no_retry_secrets_and_cleanup_warning(self):
        self.responses['11_2'] = httpx.Response(400, json={'error': {'code': 100, 'message': TOKEN}})
        self.responses['11_3'] = httpx.ReadTimeout(TOKEN)
        self.responses['11_4'] = httpx.Response(200, json={'success': 'true'})
        with patch.object(self.store.audits, 'invalidate_post', side_effect=KeyError(TOKEN)):
            result = await self.finished(await self.start(5))
        self.assertEqual(result['counts']['succeeded'], 2)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['counts']['uncertain'], 2)
        self.assertEqual(len(self.calls), 5)
        self.assertNotIn(TOKEN, json.dumps(result))
        for item in result['items']:
            self.assertEqual(item['attempts'], 1)
            for field in ('reasons', 'details', 'diagnostic', 'cleanup_warning'):
                self.assertIn(field, item)
            if item['status'] == 'succeeded':
                self.assertEqual(item['cleanup_warning'], 'cache_cleanup_failed')

    async def test_stop_waits_for_inflight_prevents_remaining_dispatch(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'success': True})
        self.response = delayed
        jid = await self.start(20)
        await asyncio.wait_for(entered.wait(), 2)
        stopped = await self.client.post('/api/bulk-delete/jobs/'+jid+'/stop', headers=HEADERS)
        self.assertEqual(stopped.status_code, 200, stopped.text)
        pending = await self.snapshot(jid)
        self.assertFalse(pending['terminal'])
        sent = len(self.calls)
        await asyncio.sleep(.03)
        self.assertEqual(len(self.calls), sent)
        release.set()
        result = await self.finished(jid)
        self.assertEqual(result['counts']['succeeded'], sent)
        self.assertEqual(result['counts']['cancelled'], 20-sent)
        self.assertEqual(len(self.calls), sent)

    async def test_two_workers_and_global_get_delete_pacing(self):
        active, maximum = 0, 0
        two_started = asyncio.Event()
        self.store.governor.interval = .025
        async def delayed(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                two_started.set()
            await asyncio.wait_for(two_started.wait(), 5)
            await asyncio.sleep(.01)
            active -= 1
            return httpx.Response(200, json={'success': True})
        self.response = delayed
        jid = await self.start(5)
        await self.app.state.graph.accounts('dummy-bulk-user-secret', self.store.token_key('dummy-bulk-user-secret'))
        await self.finished(jid)
        self.assertEqual(maximum, 2)
        self.assertEqual(len(self.calls), 6)
        times = sorted(moment for _, moment in self.calls)
        self.assertTrue(all(b-a >= .020 for a, b in zip(times, times[1:])), times)

    async def test_generic_permission_failure_allows_other_targets_and_page(self):
        self.store.governor.interval = .03
        self.responses['11_1'] = httpx.Response(403, json={'error': {'code': 10, 'message': TOKEN}})
        groups = [{'page_id': page, 'keys': [post(n, page)['key'] for n in range(1, 4)]}
                  for page in ('11', '22')]
        result = await self.finished(await self.start(selections=groups))
        first = [row for row in result['items'] if row['page_id'] == '11']
        second = [row for row in result['items'] if row['page_id'] == '22']
        self.assertEqual(sum(row['status'] == 'failed' for row in first), 1)
        self.assertEqual(sum(row['status'] == 'blocked' for row in first), 0)
        self.assertEqual(sum(row['status'] == 'succeeded' for row in first), 2)
        self.assertTrue(all(row['status'] == 'succeeded' for row in second))
        self.assertEqual(sum(request.url.path == '/v26.0/11_1' for request, _ in self.calls), 1)
        self.assertNotIn(TOKEN, json.dumps(result))

    async def test_token_invalid_blocks_same_scope_but_other_token_continues(self):
        self.store.governor.interval = .03
        self.responses['11_1'] = httpx.Response(400, json={'error': {'code': 190, 'message': TOKEN}})
        groups = [{'page_id': page, 'keys': [post(n, page)['key'] for n in range(1, 4)]}
                  for page in ('11', '22')]
        result = await self.finished(await self.start(selections=groups))
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['counts']['blocked'], 2)
        self.assertEqual(result['counts']['succeeded'], 3)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(OTHER_TOKEN, json.dumps(result))

    async def test_rate_limit_installs_shared_cooldown_without_retry(self):
        self.store.governor.interval = .03
        self.responses['11_1'] = httpx.Response(429, json={'error': {'code': 4, 'message': TOKEN}},
                                               headers={'Retry-After': '1'})
        jid = await self.start(3)
        for _ in range(100):
            if self.calls:
                break
            await asyncio.sleep(.01)
        await asyncio.sleep(.08)
        self.assertEqual(len(self.calls), 1)
        result = await self.finished(jid)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['counts']['succeeded'], 2)
        self.assertEqual(len(self.calls), 3)
        self.assertGreaterEqual(self.calls[1][1]-self.calls[0][1], .9)

    async def test_disconnect_and_expiry_keep_inflight_until_response(self):
        for expire in (False, True):
            page_id='22' if expire else '11'
            if expire:
                self.bid, _, _ = self.store.bootstrap(None)
                self.sid, self.session = self.store.replace(self.bid, 'dummy-bulk-user-secret',
                                   [Page('22', 'Second Page', OTHER_TOKEN)])
                self.client.cookies.set('fanpage_browser', self.bid)
                self.client.cookies.set('fanpage_session', self.sid)
                self.seed([post(n,'22') for n in range(1, 6)],'22')
            entered, release = asyncio.Event(), asyncio.Event()
            async def delayed(request):
                entered.set()
                await release.wait()
                return httpx.Response(200, json={'success': True})
            self.response = delayed
            jid = await self.start(selections=[{'page_id':page_id,'keys':[post(n,page_id)['key'] for n in range(1,6)]}])
            await asyncio.wait_for(entered.wait(), 2)
            job = self.app.state.bulk.jobs[jid]
            if expire:
                self.session.expires_at = time.monotonic()-1
                self.store.cleanup()
            else:
                response = await self.client.post('/api/disconnect', headers=HEADERS)
                self.assertEqual(response.status_code, 200)
            sent = len(self.calls)
            await asyncio.sleep(.03)
            self.assertFalse(job.terminal)
            self.assertEqual(len(self.calls), sent)
            release.set()
            for _ in range(200):
                if job.terminal:
                    break
                await asyncio.sleep(.01)
            self.assertTrue(job.terminal)
            self.assertTrue(all(target.status in {'succeeded', 'blocked', 'cancelled'} for target in job.targets))
            self.assertEqual(len(self.calls), sent)
            self.assertFalse(job.active)
            saved = '\n'.join(path.read_text() for path in Path(self.temp.name).glob('*.json'))
            for secret in (TOKEN, self.sid, self.bid, 'dummy-bulk-user-secret'):
                self.assertNotIn(secret, saved)


class BulkDeleteCheckpoint(unittest.TestCase):
    def test_real_mode_blocked_and_cooldown_checkpoint_is_secret_free_quarantined(self):
        from bulk_checkpoint import Checkpoints
        with tempfile.TemporaryDirectory() as directory:
            cp = Checkpoints(Path(directory), Settings())
            target = {'page_id': '11', 'key': 'published_posts:11_1', 'object_id': '11_1',
                      'canonical': 'post:11_1', 'delete_target': '11_1', 'operation': 'post_node',
                      'eligible': True, 'reasons': [], 'status': 'processing', 'attempts': 1}
            record = {'version': 1, 'job_id': 'a'*32, 'owner_id': 'b'*32,
                      'created_at': time.time(), 'state': 'stopping', 'mode': 'delete_page_posts', 'targets': [target]}
            cp.write(record)
            restored = cp.restore()[0]
            self.assertEqual(restored['state'], 'paused_needs_reconnect')
            self.assertEqual(restored['targets'][0]['status'], 'uncertain')
            cp.write({**record, 'state': 'cooldown', 'targets': [{**target, 'status': 'blocked'}]})
            self.assertEqual(cp.restore()[0]['targets'][0]['status'], 'blocked')
            for field in ('access_token', 'session_id', 'browser_id'):
                with self.assertRaises(ValueError):
                    cp.write({**record, field: TOKEN})
            self.assertFalse(list(Path(directory).glob('*.tmp')))
