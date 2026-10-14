import asyncio
import time
from dataclasses import replace
from unittest.mock import patch

import httpx
from errors import AppError

from test_content import LoadFixture, HEADERS, PAGE_TOKEN, USER_TOKEN, post, reel, response


class JobContract(LoadFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        asyncio.get_running_loop().slow_callback_duration = 1
    async def start(self, limit=100):
        result = await self.client.post('/api/posts/jobs', headers=HEADERS, json={'page_id': '11', 'limit': limit})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    async def step(self, state):
        result = await self.client.post('/api/posts/jobs/' + state['job_id'] + '/step', headers=HEADERS,
                                      json={'revision': state['revision']})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    async def finish(self, state):
        for _ in range(200):
            if state['terminal']:
                return state
            state = await self.step(state)
        self.fail('job did not terminate')

    async def test_thousands_resume_with_exact_cursor_and_no_exhausted_refetch(self):
        self.queues['published_posts'] = [response([post(i) for i in range(n, n+100)], 'published_posts', str(n+100) if n < 2900 else None) for n in range(0, 3000, 100)]
        self.queues['video_reels'] = [response([])]
        state = await self.start(3000)
        self.assertEqual(len(self.calls), 0)  # start is local
        steps = 0
        while not state['terminal']:
            before = len(self.calls)
            state = await self.step(state)
            self.assertLessEqual(len(self.calls)-before, 6)
            steps += 1
        self.assertGreater(steps, 1)
        self.assertEqual(state['displayed'], 3000)
        self.assertEqual(state['request_count'], 30)
        requests = [r for r in self.calls if r.url.path.endswith('/published_posts')]
        self.assertNotIn('after', requests[0].url.params)
        self.assertEqual([r.url.params['after'] for r in requests[1:]], [str(n) for n in range(100,3000,100)])
        self.assertEqual(len([r for r in self.calls if r.url.path.endswith('/video_reels')]), 0)

    async def test_replayed_and_concurrent_step_does_not_acquire_twice(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def pending(request):
            entered.set()
            await release.wait()
            return response([post(1)])
        self.queues['published_posts'] = [pending]
        state = await self.start(20)
        first = asyncio.create_task(self.step(state))
        await entered.wait()
        second = asyncio.create_task(self.step(state))
        await asyncio.sleep(.01)
        release.set()
        a, b = await asyncio.gather(first, second)
        for key in ('cache_ttl_seconds','job_remaining_seconds'):
            a.pop(key,None)
            b.pop(key,None)
        self.assertEqual(a, b)
        before = len(self.calls)
        replay = await self.step(state)
        # Remaining TTL is real time; revision, data and acquisition stay identical.
        for key in ('cache_ttl_seconds','job_remaining_seconds'):
            replay.pop(key,None)
            a.pop(key,None)
        self.assertEqual(replay, a)
        self.assertEqual(len(self.calls), before)

    async def test_step_timeout_keeps_committed_page_cursor(self):
        self.application.state.jobs.settings = replace(self.settings, posts_step_timeout=.02)
        async def slow(request):
            await asyncio.sleep(.1)
            return response([])
        self.queues['published_posts'] = [response([post(1)], 'published_posts', 'P2'), response([post(2)])]
        self.queues['published_posts'] = [response([post(1)], 'published_posts', 'P2'), slow, response([post(2)])]
        state = await self.step(await self.start(20))
        self.assertFalse(state['terminal'])
        self.assertEqual(state['displayed'], 1)
        self.assertEqual(state['step_stop_reason'], 'step_timeout')
        state = await self.finish(state)
        self.assertEqual(state['displayed'], 2)
        requests = [r for r in self.calls if r.url.path.endswith('/published_posts')]
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[1].url.params['after'], 'P2')

    async def test_meta_timeout_preserves_pages_and_has_distinct_reason(self):
        self.queues['published_posts'] = [response([post(1)], 'published_posts', 'P2')] + [httpx.ReadTimeout(PAGE_TOKEN)]*3
        state = await self.finish(await self.start(20))
        self.assertEqual(state['displayed'], 1)
        self.assertEqual(state['state'], 'partial')
        self.assertEqual(state['sources'][0]['stop_reason'], 'meta_request_timeout')
        self.assertEqual(state['sources'][0]['requests'], 4)

    async def test_cooldown_pauses_without_meta_polling(self):
        self.queues['published_posts'] = [httpx.Response(429, headers={'Retry-After':'30'}), response([post(1)])]
        state = await self.step(await self.start(20))
        self.assertEqual(state['state'], 'cooldown')
        before = len(self.calls)
        state = await self.step(state)
        self.assertEqual(len(self.calls), before)
        self.assertGreater(state['resume_after_seconds'], 0)
        self.application.state.store.global_cooldown_until = 0
        self.application.state.store.token_cooldowns.clear()
        state = await self.finish(state)
        self.assertEqual(state['displayed'], 1)

    async def test_stop_is_idempotent_preserves_audit_prevents_next_requests(self):
        self.application.state.jobs.settings = replace(self.settings, posts_step_requests=1)
        self.queues['published_posts'] = [response([post(1)], 'published_posts', 'P2')]
        state = await self.step(await self.start(20))
        path = '/api/posts/jobs/' + state['job_id'] + '/stop'
        stopped = (await self.client.post(path, headers=HEADERS)).json()
        self.assertTrue(stopped['terminal'])
        self.assertEqual(stopped['displayed'], 1)
        before = len(self.calls)
        self.assertEqual((await self.client.post(path, headers=HEADERS)).json()['state'], 'cancelled')
        self.assertEqual((await self.step(stopped))['state'], 'cancelled')
        self.assertEqual(len(self.calls), before)

    async def test_job_owner_status_and_origin_without_requests(self):
        state = await self.start()
        path = '/api/posts/jobs/' + state['job_id']
        other = self.browser()
        await other.get('/api/session', headers=HEADERS)
        before = len(self.calls)
        for method, suffix, data in [('GET','',None), ('POST','/step',{'revision':0}),('POST','/stop',None)]:
            result = await other.request(method, path+suffix, headers=HEADERS, json=data)
            self.assertIn(result.status_code, [401,403,404])
        self.assertEqual((await self.client.get(path, headers=HEADERS)).status_code, 200)
        self.assertEqual((await self.client.post(path+'/step', headers={**HEADERS,'Origin':'https://evil.example'},json={'revision':0})).status_code,403)
        self.assertEqual(len(self.calls),before)
        await other.aclose()

    async def test_disconnect_and_expiry_discard_jobs(self):
        for expiry in [False, True]:
            if expiry:
                await self.client.post('/api/connect',headers=HEADERS,json={'token':USER_TOKEN})
            state = await self.start()
            if expiry:
                sid=self.client.cookies.get('fanpage_session')
                self.application.state.store.sessions[sid].expires_at=time.monotonic()-1
                self.application.state.store.cleanup()
            else:
                await self.client.post('/api/disconnect',headers=HEADERS)
            self.assertEqual(len(self.application.state.jobs.entries),0)
            self.assertEqual(self.application.state.store.active_jobs,0)
            result=await self.client.post('/api/posts/jobs/'+state['job_id']+'/step',headers=HEADERS,json={'revision':0})
            self.assertIn(result.status_code,[401,404])

    async def test_job_capacity_and_idle_ttl_release_reservations(self):
        self.application.state.jobs.settings=replace(self.settings,max_content_job_bytes=100)
        result=await self.client.post('/api/posts/jobs',headers=HEADERS,json={'page_id':'11','limit':100})
        self.assertEqual(result.status_code,503)
        self.application.state.jobs.settings=self.settings
        state=await self.start()
        job=self.application.state.jobs.entries[state['job_id']]
        job.idle_expires_at=time.monotonic()-1
        self.application.state.store.cleanup()
        self.assertEqual(len(self.application.state.jobs.entries),0)
        self.assertEqual(self.application.state.store.active_jobs,0)
        self.assertFalse(self.application.state.store.audits.reservations)

    async def test_total_limits_stop_with_received_results(self):
        self.application.state.jobs.settings=replace(self.settings, posts_job_requests=1)
        self.queues['published_posts']=[response([post(1)],'published_posts','P2')]
        state=await self.finish(await self.start(20))
        self.assertEqual(state['step_stop_reason'],'job_request_limit')
        self.assertEqual(state['displayed'],1)
        self.assertEqual(state['state'],'partial')

    async def test_sources_incremental_limit_dedup_and_date_counts(self):
        self.queues['published_posts']=[response([post(1),post(2,date=None)],'published_posts','P2'),response([post(3)])]
        self.queues['video_reels']=[response([reel(901,'11_1'),reel(902)])]
        state=await self.finish(await self.start(3))
        self.assertEqual(state['displayed'],3)
        self.assertEqual(state['dated_count'],2)
        self.assertEqual(state['undated_count'],1)
        self.assertEqual(state['items'][0]['object_id'],'11_1')
        self.assertEqual(state['items'][0]['kind'],'Postingan')
        self.assertNotIn(PAGE_TOKEN,str(state))
        self.assertNotIn('cursor',str(state))
        self.assertFalse(state['safe_for_delete'])

    async def test_total_time_stop_retains_received_and_releases_slot(self):
        self.application.state.jobs.settings=replace(self.settings,posts_step_requests=1)
        self.queues['published_posts']=[response([post(1)],'published_posts','P2')]
        state=await self.step(await self.start(20))
        job=self.application.state.jobs.entries[state['job_id']]
        job.deadline=time.monotonic()-1
        before=len(self.calls)
        status=(await self.client.get('/api/posts/jobs/'+job.id,headers=HEADERS)).json()
        self.assertEqual(status['state'],'partial')
        self.assertEqual(status['step_stop_reason'],'job_timeout')
        self.assertEqual(status['displayed'],1)
        self.assertFalse(status['retrieval_complete'])
        self.assertEqual(self.application.state.store.active_jobs,0)
        self.assertEqual(len(self.calls),before)

    async def test_retry_budget_persists_across_step_boundaries(self):
        self.application.state.jobs.settings=replace(self.settings,posts_step_requests=1)
        self.queues['published_posts']=[httpx.Response(503)]*3
        state=await self.finish(await self.start(20))
        source=state['sources'][0]
        self.assertEqual(source['requests'],3)
        self.assertEqual(source['stop_reason'],'request_retry_limit')
        self.assertEqual(source['status'],'failed')

    async def test_long_cooldown_survives_idle_ttl_but_not_session_expiry(self):
        self.queues['published_posts']=[httpx.Response(429,headers={'Retry-After':'300'})]
        state=await self.step(await self.start(20))
        job=self.application.state.jobs.entries[state['job_id']]
        self.assertGreater(job.idle_expires_at,time.monotonic()+300)
        self.assertLessEqual(job.idle_expires_at,self.application.state.store.sessions[job.session_id].expires_at)

    async def test_stop_inflight_releases_slot_and_no_following_requests(self):
        entered=asyncio.Event()
        async def pending(request):
            entered.set()
            await asyncio.sleep(5)
            return response([post(1)])
        self.queues['published_posts']=[pending]
        state=await self.start(20)
        first=asyncio.create_task(self.step(state))
        await entered.wait()
        result=await self.client.post('/api/posts/jobs/'+state['job_id']+'/stop',headers=HEADERS)
        self.assertEqual(result.json()['state'],'cancelled')
        await first
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.application.state.store.active_jobs,0)

    async def test_pending_job_disconnect_cancels_request_and_clears_memory(self):
        entered=asyncio.Event()
        async def pending(request):
            entered.set()
            await asyncio.sleep(5)
            return response([post(1)])
        self.queues['published_posts']=[pending]
        state=await self.start(20)
        request=asyncio.create_task(self.client.post('/api/posts/jobs/'+state['job_id']+'/step',headers=HEADERS,json={'revision':0}))
        await entered.wait()
        await self.client.post('/api/disconnect',headers=HEADERS)
        result=await request
        self.assertEqual(result.json()['error']['code'],'load_cancelled')
        self.assertFalse(self.application.state.jobs.entries)
        self.assertEqual(len(self.calls),1)

    async def test_start_is_idempotent_and_invalid_inputs_never_meta(self):
        for value in [0,5001,True,'100',1.5]:
            result=await self.client.post('/api/posts/jobs',headers=HEADERS,json={'page_id':'11','limit':value})
            self.assertEqual(result.status_code,400)
        a=await self.start(1)
        b=await self.start(1)
        self.assertEqual(a['job_id'],b['job_id'])
        self.assertEqual(self.application.state.store.active_jobs,1)
        self.assertEqual(len(self.calls),0)

    async def test_stop_and_disconnect_translate_all_concurrent_step_callers(self):
        for disconnect in (False,True):
            entered=asyncio.Event()
            async def pending(request):
                entered.set()
                await asyncio.sleep(5)
                return response([post(1)])
            self.queues['published_posts']=[pending]
            state=await self.start(20)
            path='/api/posts/jobs/'+state['job_id']
            first=asyncio.create_task(self.client.post(path+'/step',headers=HEADERS,json={'revision':0}))
            await entered.wait()
            second=asyncio.create_task(self.client.post(path+'/step',headers=HEADERS,json={'revision':0}))
            await asyncio.sleep(.02)
            await self.client.post('/api/disconnect' if disconnect else path+'/stop',headers=HEADERS)
            a,b=await asyncio.gather(first,second)
            if disconnect:
                self.assertEqual(a.json()['error']['code'],'load_cancelled')
                self.assertEqual(b.json()['error']['code'],'load_cancelled')
            else:
                self.assertEqual(a.json()['state'],'cancelled')
                self.assertEqual(b.json()['state'],'cancelled')

    async def test_unexpected_secret_exception_becomes_safe_partial_and_releases_slot(self):
        self.application.state.jobs.settings=replace(self.settings,posts_step_requests=1)
        self.queues['published_posts']=[response([post(1)],'published_posts','P2')]
        state=await self.step(await self.start(20))
        async def broken(request):
            raise RuntimeError(PAGE_TOKEN)
        self.queues['published_posts']=[broken]
        state=await self.finish(state)
        self.assertEqual(state['state'],'partial')
        self.assertEqual(state['displayed'],1)
        self.assertEqual(self.application.state.store.active_jobs,0)
        self.assertNotIn(PAGE_TOKEN,str(state))
        self.assertEqual(state['errors'][0]['source'],'local_job')

    async def test_early_windows_deadline_timer_does_not_report_expiry_early(self):
        deadline=time.monotonic()+.03
        original_wait_for=asyncio.wait_for
        async def early_timer(coro, timeout):
            # asyncio is shared with idle queue workers: only simulate the Graph timer.
            if getattr(getattr(coro,'cr_code',None),'co_name',None)!='_get':
                return await original_wait_for(coro,timeout)
            coro.close()
            raise asyncio.TimeoutError()
        graph=self.application.state.graph
        with patch('graph_api.asyncio.wait_for',early_timer):
            with self.assertRaises(AppError) as raised:
                await graph.content_page('11','published_posts','id',PAGE_TOKEN,
                    self.application.state.store.token_key(PAGE_TOKEN),1,None,deadline)
        self.assertEqual(raised.exception.code,'timeout')
        self.assertGreaterEqual(time.monotonic(),deadline)
        self.assertEqual(len(self.calls),0)

    async def test_session_expiry_cancels_active_step_and_destroys_job(self):
        entered=asyncio.Event()
        async def pending(request):
            entered.set()
            await asyncio.sleep(5)
            return response([post(1)])
        self.queues['published_posts']=[pending]
        state=await self.start(20)
        path='/api/posts/jobs/'+state['job_id']
        step=asyncio.create_task(self.client.post(path+'/step',headers=HEADERS,json={'revision':0}))
        await entered.wait()
        sid=self.client.cookies.get('fanpage_session')
        self.application.state.store.sessions[sid].expires_at=time.monotonic()-1
        self.application.state.store.cleanup()
        result=await step
        self.assertEqual(result.json()['error']['code'],'session_expired')
        self.assertFalse(self.application.state.jobs.entries)
        self.assertFalse(self.application.state.store.sessions)
        self.assertFalse(self.application.state.store.audits.reservations)
        self.assertEqual(self.application.state.store.active_jobs,0)
        self.assertEqual(len(self.calls),1)


    async def test_filtered_album_and_unknown_pages_resume_to_unique_limit(self):
        self.application.state.jobs.settings = replace(self.settings, posts_step_requests=1)
        album = {'data':[{'type':'album','subattachments':{'data':[{'type':'photo'}]}}]}
        self.queues['published_posts'] = [response([post(1, status_type='added_video'),post(2),post(2)], 'published_posts','P2'), response([post(3, attachments={'data':[{'type':'reel'}]}),post(4,status_type='added_photos',attachments=album)])]
        state = await self.finish(await self.start(2))
        self.assertEqual([i['object_id'] for i in state['items']], ['11_2','11_4'])
        self.assertEqual(state['request_count'],2)
        self.assertEqual(state['sources'][0]['skipped'],2)
        self.assertEqual(state['sources'][0]['skip_reasons'], {'unsupported_status_type':1,'unsupported_attachment_type':1})
        self.assertEqual([r.url.path.rsplit('/',1)[-1] for r in self.calls],['published_posts']*2)

    async def test_malformed_scope_metadata_does_not_fail_job(self):
        self.queues['published_posts'] = [response([post(1,status_type={}),post(2,attachments={'data':[{'type':[]}]}),post(3)])]
        state = await self.finish(await self.start(20))
        self.assertEqual(state['state'],'source_exhausted')
        self.assertEqual([i['object_id'] for i in state['items']],['11_3'])
        self.assertEqual(state['sources'][0]['skip_reasons'],{'unsupported_status_type':1,'unknown_attachment_type':1})
        self.assertEqual(state['errors'],[])

    async def test_photo_link_supported_and_photo_video_mix_rejected(self):
        self.queues['published_posts'] = [response([
            post(1,attachments={'data':[{'type':'photo'},{'type':'share'}]}),
            post(2,attachments={'data':[{'type':'photo'},{'type':'video'}]})])]
        state = await self.finish(await self.start(20))
        self.assertEqual([i['object_id'] for i in state['items']],['11_1'])
        self.assertEqual(state['sources'][0]['skip_reasons'],{'unsupported_attachment_type':1})
