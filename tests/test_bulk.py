import asyncio
import json
import tempfile
import time
import unittest
import httpx
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from test_content import LoadFixture, HEADERS, USER_TOKEN, PAGE_TOKEN


class BulkAPI(LoadFixture):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        await super().asyncSetUp()
        # Runtime checkpoint is redirected only by test injection, never HTTP.
        from bulk_checkpoint import Checkpoints
        self.application.state.bulk.checkpoints=Checkpoints(Path(self.temp.name),self.settings)

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.temp.cleanup()

    async def test_dry_run_only_keys_owned_audit_and_no_meta_delete(self):
        audit=(await self.load(5)).json()
        keys=[item['key'] for item in audit['items']]
        before=len(self.calls)
        result=await self.client.post('/api/dry-run/jobs',headers=HEADERS,json={'selections':[{'page_id':'11','keys':keys}]})
        self.assertEqual(result.status_code,200,result.text)
        job=result.json()
        for _ in range(100):
            job=(await self.client.get('/api/dry-run/jobs/'+job['job_id'],headers=HEADERS)).json()
            if job['terminal']: break
            await asyncio.sleep(.01)
        self.assertTrue(job['terminal'])
        self.assertEqual(job['checked'],len(keys))
        self.assertEqual(job['valid'],0)
        self.assertEqual(job['ambiguous'],len(keys))
        self.assertEqual(job['items'][0]['status'],'dry_run_checked')
        self.assertIn('story_exclusion_unverified',job['items'][0]['reasons'])
        self.assertFalse(job['safe_for_delete'])
        self.assertEqual(len(self.calls),before)
        files=list(Path(self.temp.name).glob('*.json'))
        manifests=[p for p in files if p.name.count('.')==1]
        self.assertEqual(len(manifests),1)
        saved=''.join(p.read_text() for p in files)
        for secret in [USER_TOKEN,PAGE_TOKEN,self.client.cookies.get('fanpage_session'),self.client.cookies.get('fanpage_browser')]:
            self.assertNotIn(secret,saved)
        self.assertNotIn('session_id',saved)
        for payload in [{'selections':[{'page_id':'11','keys':['11_999']}]},
                        {'selections':[{'page_id':'11','keys':keys,'token':'secret'}]},
                        {'selections':[{'page_id':'11','keys':keys}], 'real_delete':True}]:
            self.assertEqual((await self.client.post('/api/dry-run/jobs',headers=HEADERS,json=payload)).status_code,400)
        self.assertTrue(all(r.method=='GET' for r in self.calls))

    async def test_foreign_browser_stale_result_and_origin_denied(self):
        audit=(await self.load(5)).json()
        payload={'selections':[{'page_id':'11','keys':[audit['items'][0]['key']]}]}
        other=self.browser()
        await other.get('/api/session',headers=HEADERS)
        self.assertIn((await other.post('/api/dry-run/jobs',headers=HEADERS,json=payload)).status_code,[401,403])
        result=await self.client.post('/api/dry-run/jobs',headers=HEADERS,json=payload)
        job_id=result.json()['job_id']
        self.assertEqual((await other.get('/api/dry-run/jobs/'+job_id,headers=HEADERS)).status_code,404)
        self.assertEqual((await self.client.post('/api/dry-run/jobs/'+job_id+'/stop',headers={**HEADERS,'Origin':'https://evil.example'})).status_code,403)
        sid=self.client.cookies.get('fanpage_session')
        self.application.state.store.audits.entries[sid].expires_at=time.monotonic()-1
        self.assertEqual((await self.client.post('/api/dry-run/jobs',headers=HEADERS,json=payload)).status_code,409)
        await other.aclose()

    async def test_multi_page_selection_retained_cache_dedup_and_no_free_target(self):
        audit=(await self.load(5)).json()
        sid=self.client.cookies.get('fanpage_session')
        self.application.state.store.sessions[sid].pages[1].access_token=PAGE_TOKEN
        second=json.loads(json.dumps(audit))
        second['page_id']='22'
        item=second['items'][0]
        item.update(page_id='22',object_id='22_1',key='published_posts:22_1',sources=[{'source':'published_posts','object_id':'22_1'}])
        cache=self.application.state.store.audits
        cache.reserve(sid,1,'22')
        cache.put(sid,second,self.application.state.store.sessions[sid].expires_at)
        payload={'selections':[{'page_id':'11','keys':[audit['items'][0]['key']]*2},{'page_id':'22','keys':[item['key']]}]}
        result=await self.client.post('/api/dry-run/jobs',headers=HEADERS,json=payload)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['unique'],2)
        self.assertEqual(result.json()['duplicates'],1)
        self.assertEqual(len(result.json()['pages']),2)
        self.assertEqual((await self.client.delete('/api/dry-run/jobs/'+result.json()['job_id'],headers=HEADERS)).status_code,405)
        self.assertEqual((await self.client.post('/api/dry-run/jobs',headers=HEADERS,json={'selections':[{'page_id':'99','keys':[item['key']]}]})).status_code,403)

    async def test_reload_same_page_does_not_double_count_archive_at_capacity(self):
        audit=(await self.load(1)).json()
        sid=self.client.cookies.get('fanpage_session')
        cache=self.application.state.store.audits
        cache.settings=replace(self.settings,max_audit_items=1)
        cache.reserve(sid,1,'11')
        cache.put(sid,audit,self.application.state.store.sessions[sid].expires_at)
        self.assertEqual(cache.get(sid,'11')['displayed'],1)


class CheckpointContract(unittest.TestCase):
    def test_atomic_versioned_no_secret_restart_processing_uncertain(self):
        from bulk_checkpoint import Checkpoints
        from config import Settings
        with tempfile.TemporaryDirectory() as folder:
            cp=Checkpoints(Path(folder),Settings())
            record={'version':1,'job_id':'a'*32,'owner_id':'b'*32,'created_at':time.time(),'state':'running',
                    'mode':'dry_run','targets':[{'page_id':'11','key':'published_posts:11_1','object_id':'11_1',
                    'canonical':'post:11_1','delete_target':'11_1','operation':'post_node','eligible':False,
                    'reasons':['story_exclusion_unverified'],'status':'processing','attempts':1}]}
            cp.write(record)
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])
            restored=cp.restore()[0]
            self.assertEqual(restored['state'],'paused_needs_reconnect')
            self.assertEqual(restored['targets'][0]['status'],'uncertain')
            with self.assertRaises(ValueError): cp.write({**record,'version':True})
            record['session_id']='secret'
            with self.assertRaises(ValueError): cp.write(record)
            with self.assertRaises(ValueError): cp.write({**record,'job_id':'../outside'})

    def test_progress_atomic_crash_generation_and_capacity(self):
        from bulk_checkpoint import Checkpoints
        from config import Settings
        with tempfile.TemporaryDirectory() as folder:
            cp=Checkpoints(Path(folder),Settings())
            target={'page_id':'11','key':'published_posts:11_1','object_id':'11_1','canonical':'post:11_1',
                    'delete_target':'11_1','operation':'post_node','eligible':False,'reasons':['story_exclusion_unverified'],'status':'queued','attempts':0}
            record={'version':1,'job_id':'a'*32,'owner_id':'b'*32,'created_at':time.time(),'state':'queued','mode':'dry_run','targets':[target]}
            cp.write(record)
            original=(Path(folder)/('a'*32+'.json')).read_bytes()
            with patch('bulk_checkpoint.os.replace',side_effect=OSError('disk error')):
                with self.assertRaises(OSError): cp.write({**record,'state':'cancelled'})
            self.assertEqual((Path(folder)/('a'*32+'.json')).read_bytes(),original)
            self.assertFalse(list(Path(folder).glob('*.tmp')))
            cp.write_progress({**record,'state':'running','targets':[{**target,'status':'processing','attempts':1}]},0)
            self.assertEqual(cp.restore()[0]['targets'][0]['status'],'uncertain')
            cp.write(record)  # New manifest generation ignores obsolete progress.
            self.assertEqual(cp.restore()[0]['targets'][0]['status'],'queued')
            cp.settings=replace(cp.settings,checkpoint_max_file_bytes=10)
            with self.assertRaises(ValueError): cp.write(record)

    def test_restore_includes_progress_bytes_in_global_budget(self):
        from bulk_checkpoint import Checkpoints
        from config import Settings
        with tempfile.TemporaryDirectory() as folder:
            cp=Checkpoints(Path(folder),Settings())
            target={'page_id':'11','key':'published_posts:11_1','object_id':'11_1','canonical':'post:11_1',
                    'delete_target':'11_1','operation':'post_node','eligible':False,'reasons':['story_exclusion_unverified'],'status':'queued','attempts':0}
            record={'version':1,'job_id':'a'*32,'owner_id':'b'*32,'created_at':time.time(),'state':'queued','mode':'dry_run','targets':[target]}
            cp.write(record)
            cp.write_progress(record,0)
            size=sum(p.stat().st_size for p in Path(folder).glob('*.json'))
            cp.settings=replace(cp.settings,checkpoint_max_bytes=size-1)
            reads=[]
            original=Path.read_bytes
            def tracked(path):
                reads.append(path.name)
                return original(path)
            with patch.object(Path,'read_bytes',tracked):
                self.assertEqual(cp.restore(),[])
            self.assertEqual(len(reads),1)

    def test_checkpoint_budget_includes_orphan_staging_files(self):
        from bulk_checkpoint import Checkpoints
        from config import Settings
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/('c'*32+'.tmp')).write_bytes(b'x'*2048)
            cp=Checkpoints(root,Settings(checkpoint_max_bytes=1024))
            record={'version':1,'job_id':'a'*32,'owner_id':'b'*32,'created_at':time.time(),'state':'queued','mode':'dry_run','targets':[]}
            with self.assertRaises(ValueError): cp.write(record)
            self.assertFalse(list(root.glob('*.json')))


class SchedulerContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from config import Settings
        from sessions import MemoryStore,Page
        from bulk_checkpoint import Checkpoints
        from bulk_queue import BulkQueue
        self.temp=tempfile.TemporaryDirectory()
        self.settings=Settings(graph_request_interval=.005,dry_job_ttl=10)
        self.store=MemoryStore(self.settings)
        self.bid,_,_=self.store.bootstrap(None)
        self.sid,_=self.store.replace(self.bid,USER_TOKEN,[Page('11','A',PAGE_TOKEN),Page('22','B',PAGE_TOKEN)])
        self.queue=BulkQueue(self.store,self.settings,Checkpoints(Path(self.temp.name),self.settings))
        self.store.bulk_queue=self.queue
        self.scheduler=None

    async def asyncTearDown(self):
        if self.scheduler: await self.scheduler.shutdown()
        self.temp.cleanup()

    def target(self,n,page='11'):
        from bulk_targets import Target
        return Target(page,'published_posts:'+page+'_'+str(n),page+'_'+str(n),'post:'+page+'_'+str(n),page+'_'+str(n),'post_node',False,['story_exclusion_unverified'])

    def add(self,targets):
        return self.queue.add(self.bid,self.sid,targets,0,{'11':'A','22':'B'})

    async def run_scheduler(self,executor):
        from bulk_scheduler import BulkScheduler
        self.scheduler=BulkScheduler(self.queue,executor)
        await self.scheduler.start()

    async def wait_complete(self,*jobs):
        for _ in range(200):
            if all(j.terminal for j in jobs): return
            await asyncio.sleep(.01)
        self.fail('jobs did not finish')

    async def test_global_concurrency_fairness_target_locks_and_same_page_parallel(self):
        from bulk_executor import Outcome
        active=set();maximum=0;starts=[];samepage=False
        async def check(target):
            nonlocal maximum,samepage
            self.assertNotIn(target.canonical,active)
            active.add(target.canonical);maximum=max(maximum,len(active))
            if len(active)==2 and all(key.startswith('post:11_') for key in active): samepage=True
            starts.append(target.page_id)
            await asyncio.sleep(.03)
            active.remove(target.canonical)
            return Outcome('dry_run_checked','local_identity_checked')
        class Fake:
            async def check(self,target): return await check(target)
        first=self.add([self.target(n) for n in range(8)])
        second=self.add([self.target(1),self.target(90,'22')])
        await self.run_scheduler(Fake())
        await self.wait_complete(first,second)
        self.assertEqual(maximum,2)
        self.assertTrue(samepage)
        self.assertIn('22',starts[:4])
        self.assertFalse(self.queue.locks)

    async def test_rate_and_shared_cooldown_usage_scope(self):
        from bulk_executor import Outcome
        started=[]
        self.store.governor.interval=.04
        class Fake:
            async def check(inner,target):
                started.append(time.monotonic())
                return Outcome('dry_run_checked','local_identity_checked')
        job=self.add([self.target(n) for n in range(3)])
        self.store.cooldown(self.store.token_key(PAGE_TOKEN),.08)
        await self.run_scheduler(Fake())
        await asyncio.sleep(.03)
        self.assertEqual(started,[])
        await self.wait_complete(job)
        self.assertTrue(all(b-a>=.025 for a,b in zip(started,started[1:])))
        self.store.governor.observe_usage(85)
        self.assertGreaterEqual(self.store.governor.interval,.04)
        self.assertTrue(self.store.governor.usage_known)

    async def test_uncertain_no_retry_temporary_bounded_and_cancel(self):
        from bulk_executor import Outcome
        calls={}
        class Fake:
            async def check(inner,target):
                calls[target.object_id]=calls.get(target.object_id,0)+1
                return Outcome('uncertain','timeout_after_send') if target.object_id.endswith('_1') else Outcome('failed','temporary_before_send',True)
        job=self.add([self.target(1),self.target(2)])
        await self.run_scheduler(Fake())
        await self.wait_complete(job)
        self.assertEqual(calls,{'11_1':1,'11_2':3})
        self.assertEqual(job.targets[0].status,'uncertain')
        other=self.add([self.target(9)])
        self.queue.cancel(other)
        await asyncio.sleep(.03)
        self.assertNotIn('11_9',calls)

    async def test_disconnect_expiry_shutdown_checkpoint_recovery_quarantined(self):
        from bulk_executor import Outcome
        entered=asyncio.Event()
        class Fake:
            async def check(inner,target):
                entered.set();await asyncio.sleep(5)
                return Outcome('dry_run_checked','local_identity_checked')
        job=self.add([self.target(n) for n in range(4)])
        await self.run_scheduler(Fake())
        await entered.wait()
        self.store.disconnect(self.bid,self.sid)
        await asyncio.sleep(.03)
        self.assertEqual(job.state,'paused_needs_reconnect')
        self.assertIsNone(job.session_id)
        self.assertTrue(any(t.status=='queued' for t in job.targets))
        self.assertFalse(self.queue.locks)
        restored=self.queue.checkpoints.restore()
        self.assertEqual(restored[0]['state'],'paused_needs_reconnect')
        newbid,_,_=self.store.bootstrap(None)
        new_sid,_=self.store.replace(newbid,USER_TOKEN,[])
        from errors import AppError
        with self.assertRaises(AppError): self.queue.owned(newbid,new_sid,job.id)
        saved=json.dumps(restored)
        for secret in [USER_TOKEN,PAGE_TOKEN,self.sid,self.bid]: self.assertNotIn(secret,saved)

    async def test_capacity_and_future_response_contract(self):
        from errors import AppError
        from bulk_executor import classify_future_response
        self.queue.settings=replace(self.settings,dry_max_targets=1)
        self.add([self.target(1)])
        with self.assertRaises(AppError): self.add([self.target(2)])
        self.assertEqual(classify_future_response(200).status,'uncertain')
        self.assertFalse(classify_future_response(error_code=190).temporary)
        self.assertEqual(classify_future_response(timeout=True,sent=True).status,'uncertain')
        self.assertFalse(classify_future_response(timeout=True,sent=True).temporary)

    async def test_expiry_and_shutdown_preserve_unprocessed_without_credentials(self):
        from bulk_executor import Outcome
        entered=asyncio.Event()
        class Fake:
            async def check(inner,target):
                entered.set();await asyncio.sleep(5)
                return Outcome('dry_run_checked','local_identity_checked')
        job=self.add([self.target(n) for n in range(3)])
        await self.run_scheduler(Fake())
        await entered.wait()
        self.store.sessions[self.sid].expires_at=time.monotonic()-1
        self.store.cleanup()
        await asyncio.sleep(.03)
        self.assertEqual(job.state,'paused_needs_reconnect')
        self.assertTrue(any(t.status=='queued' for t in job.targets))
        self.assertFalse(self.queue.locks)
        await self.scheduler.shutdown()
        self.assertTrue(all(t.done() for t in self.scheduler.workers))
        self.assertEqual(self.queue.checkpoints.restore()[0]['state'],'paused_needs_reconnect')

    async def test_rate_limit_outcome_pauses_other_jobs_and_uncertain_never_retried(self):
        from bulk_executor import Outcome
        self.queue.settings=replace(self.settings,cooldown_seconds=.08)
        starts=[]
        class Fake:
            async def check(inner,target):
                starts.append((target.object_id,time.monotonic()))
                if len(starts)==1: return Outcome('failed','rate_limited',True,.08)
                return Outcome('uncertain','timeout_after_send',True)
        first=self.add([self.target(1)])
        second=self.add([self.target(2)])
        await self.run_scheduler(Fake())
        await self.wait_complete(first,second)
        self.assertGreaterEqual(starts[1][1]-starts[0][1],.065)
        self.assertEqual(sum(identity=='11_2' for identity,_ in starts),1)
        self.assertEqual(sum(identity=='11_1' for identity,_ in starts),2)

    async def test_admit_50000_targets_200_pages_without_dispatch_or_delete(self):
        from sessions import Page
        self.store.sessions[self.sid].pages=[Page(str(n),'Page',PAGE_TOKEN) for n in range(1,201)]
        targets=[self.target(i,str(page)) for page in range(1,201) for i in range(250)]
        job=self.queue.add(self.bid,self.sid,targets,0,{str(n):'Page' for n in range(1,201)})
        self.assertEqual(len(job.targets),50000)
        self.assertEqual(len(self.queue.rotation),200)
        self.assertTrue(all(t.status=='queued' for t in job.targets))
        from errors import AppError
        with self.assertRaises(AppError): self.add([self.target(999)])
        self.queue.cancel(job)
        self.assertEqual(self.queue.checkpoints.restore()[0]['state'],'paused_needs_reconnect')

    async def test_canonical_lock_cross_browser_not_page_lock(self):
        from sessions import Page
        other_bid,_,_=self.store.bootstrap(None)
        other_sid,_=self.store.replace(other_bid,USER_TOKEN,[Page('11','A',PAGE_TOKEN)])
        first=self.add([self.target(1),self.target(2)])
        second=self.queue.add(other_bid,other_sid,[self.target(1),self.target(3)],0,{'11':'A'})
        a=self.queue.claim()
        b=self.queue.claim()
        c=self.queue.claim()
        self.assertEqual(len({a[1].canonical,b[1].canonical,c[1].canonical}),3)
        self.assertTrue(all(t.page_id=='11' for _,t,_ in [a,b,c]))
        self.assertIsNone(self.queue.claim())
        self.queue.cancel(first);self.queue.cancel(second)

    async def test_graph_get_and_fake_workers_share_one_global_rate_gate(self):
        from bulk_executor import Outcome
        from graph_api import GraphClient
        dispatches=[]
        self.store.governor.interval=.04
        class Fake:
            async def check(inner,target):
                dispatches.append(time.perf_counter())
                return Outcome('dry_run_checked','local_identity_checked')
        async def http_handler(request):
            self.assertEqual(request.method,'GET')
            dispatches.append(time.perf_counter())
            return httpx.Response(200,json={'data':[]})
        job=self.add([self.target(1),self.target(2)])
        async with httpx.AsyncClient(transport=httpx.MockTransport(http_handler)) as client:
            graph=GraphClient(client,self.settings,self.store)
            await self.run_scheduler(Fake())
            await graph.accounts(USER_TOKEN,self.store.token_key(USER_TOKEN))
            await self.wait_complete(job)
        self.assertEqual(len(dispatches),3)
        dispatches.sort()
        self.assertTrue(all(b-a>=.039 for a,b in zip(dispatches,dispatches[1:])))
        self.assertFalse(self.store.governor.usage_known)
