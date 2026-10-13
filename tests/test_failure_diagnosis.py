"""Read-only failure probes with dummy credentials; never call live Facebook."""
import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
import httpx
from app import create_app
from config import Settings
from sessions import Page

HEADERS={'X-Fanpage-Client':'1','Origin':'http://127.0.0.1:8000'}
USER='dummy-diagnosis-user-token'
PAGE='dummy-diagnosis-page-token'

class FailureDiagnosis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[]
        self.responses={}
        async def handle(request):
            self.calls.append(request)
            self.assertEqual(request.method,'GET')
            self.assertEqual(request.url.host,'graph.facebook.com')
            self.assertNotIn('access_token',request.url.params)
            key=request.url.path if request.url.path.endswith(('permissions','accounts','attachments')) else request.url.params.get('fields')
            response=self.responses.get(key)
            if isinstance(response,Exception): raise response
            if response is not None: return response
            payload={
                '/v26.0/me/permissions':{'data':[{'permission':'pages_manage_posts','status':'granted'}]},
                '/v26.0/me/accounts':{'data':[{'id':'11','tasks':['CREATE_CONTENT','MANAGE']}]},
                'id,from,status_type':{'id':'11_123','from':{'id':'11','name':'Page'},'status_type':'added_photos'},
                'id,application,object_id':{'id':'11_123','application':{'id':'42','name':'App'},'object_id':'555'},
                '/v26.0/11_123/attachments':{'data':[{'type':'photo','target':{'id':'555'}}]},
            }[key]
            return httpx.Response(200,json=payload)
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(Settings(graph_request_interval=.001,retry_base=.001,retry_jitter=0),httpx.MockTransport(handle),checkpoint_root=Path(self.temp.name))
        self.life=self.app.router.lifespan_context(self.app)
        await self.life.__aenter__()
        self.store=self.app.state.store
        self.bid,_,_=self.store.bootstrap(None)
        self.sid,self.session=self.store.replace(self.bid,USER,[Page('11','Page',PAGE)])
        item={'key':'published_posts:11_123','page_id':'11','object_id':'11_123','kind':'Foto','text':'Photo',
              'sources':[{'source':'published_posts','object_id':'11_123'}],'permalink':'https://www.facebook.com/photo.php?fbid=555'}
        self.store.audits.reserve(self.sid,1,'11')
        self.store.audits.put(self.sid,{'page_id':'11','items':[item],'displayed':1},self.session.expires_at)
        preview=self.app.state.bulk_delete.prepare(self.bid,self.sid,{'selections':[{'page_id':'11','keys':[item['key']]}]})
        job=self.app.state.bulk_delete.confirm(self.bid,self.sid,{'confirmation_id':preview['confirmation_id'],'manual_targets_confirmed':True})
        self.job=self.app.state.bulk.jobs[job['job_id']]
        self.job.targets[0].status='failed'
        self.job.targets[0].diagnostic={'http_status':403,'error_code':200,'reason':'permission_denied'}
        self.job.state='completed'
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=HEADERS['Origin'])
        self.client.cookies.set('fanpage_browser',self.bid)
        self.client.cookies.set('fanpage_session',self.sid)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.life.__aexit__(None,None,None)
        self.temp.cleanup()

    async def diagnose(self,**changes):
        return await self.client.post('/api/delete-diagnosis',headers=HEADERS,
            json={'job_id':self.job.id,'key':'published_posts:11_123',**changes})

    async def test_permissions_tasks_owner_and_proven_photo_relation_get_only(self):
        response=await self.diagnose()
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['request_count'],5)
        self.assertEqual(data['permissions']['pages_manage_posts'],'granted')
        self.assertEqual(data['tasks'],['CREATE_CONTENT','MANAGE'])
        self.assertEqual(data['owner']['id'],'11')
        self.assertEqual(data['photo_ids'],['555'])
        self.assertFalse(data['delete_permission_proven'])
        self.assertEqual(self.calls[0].headers['authorization'],'Bearer '+USER)
        self.assertEqual(self.calls[-1].headers['authorization'],'Bearer '+PAGE)
        self.assertNotIn(PAGE,json.dumps(data))
        self.assertNotIn(USER,json.dumps(data))

    async def test_budget_includes_retries_and_never_follows_pagination(self):
        self.responses['/v26.0/me/permissions']=httpx.Response(503,json={})
        self.responses['/v26.0/me/accounts']=httpx.Response(200,json={'data':[],'paging':{'next':'https://evil.example/?access_token='+PAGE}})
        data=(await self.diagnose()).json()
        self.assertEqual(data['request_count'],5)
        self.assertEqual(len(self.calls),5)
        self.assertTrue(any(s['reason']=='diagnosis_budget' for s in data['checks']))
        self.assertEqual(data['tasks'],None)

    async def test_invalid_field_and_messages_are_sanitized_without_fallback(self):
        self.responses['id,application,object_id']=httpx.Response(400,json={'error':{'code':100,'message':'Unknown field application '+PAGE+' '+USER,'error_user_msg':'https://example.com/?access_token='+PAGE}})
        data=(await self.diagnose()).json()
        failed=next(s for s in data['checks'] if s['source']=='post_media')
        self.assertEqual(failed['failed_fields'],['application'])
        self.assertEqual(len(self.calls),5)
        for token in (PAGE,USER): self.assertNotIn(token,json.dumps(data))

    async def test_missing_data_does_not_claim_permission_or_deleted_object(self):
        self.responses={path:httpx.Response(200,json={}) for path in
            ('/v26.0/me/permissions','/v26.0/me/accounts','id,from,status_type','id,application,object_id','/v26.0/11_123/attachments')}
        data=(await self.diagnose()).json()
        self.assertEqual(data['permissions']['pages_manage_posts'],'unknown')
        self.assertIsNone(data['tasks'])
        self.assertIsNone(data['owner'])
        self.assertEqual(data['photo_ids'],[])
        self.assertFalse(data['delete_permission_proven'])

    async def test_free_id_wrong_key_success_and_foreign_browser_denied_without_graph(self):
        self.assertEqual((await self.diagnose(target_id='11_999')).status_code,400)
        self.assertGreaterEqual((await self.diagnose(key='published_posts:11_999')).status_code,400)
        self.job.targets[0].status='succeeded'
        self.assertEqual((await self.diagnose()).status_code,409)
        self.job.targets[0].status='failed'
        bid,_,_=self.store.bootstrap(None)
        sid,_=self.store.replace(bid,'dummy-other',[Page('11','Page','dummy-other-page')])
        self.client.cookies.set('fanpage_browser',bid)
        self.client.cookies.set('fanpage_session',sid)
        self.assertEqual((await self.diagnose()).status_code,404)
        self.assertEqual(self.calls,[])

    async def test_cooldown_and_expired_session_send_no_graph(self):
        self.store.cooldown(self.store.token_key(PAGE),30)
        data=(await self.diagnose()).json()
        self.assertEqual(data['request_count'],0)
        self.assertTrue(all(s['reason']=='cooldown' for s in data['checks']))
        self.session.expires_at=time.monotonic()-1
        self.assertEqual((await self.diagnose()).status_code,401)
        self.assertEqual(self.calls,[])

    async def test_last_failure_local_metadata_and_restart_unavailable(self):
        target=copy.deepcopy(self.job.targets[0].details)
        self.app.state.single_delete.last_results[self.sid]={'state':'available','result':{'outcome':'failed','target':target,'diagnostic':{'http_status':403,'error_code':200}}}
        response=await self.client.post('/api/delete-diagnosis',headers=HEADERS,json={'last_result':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['request_count'],5)
        self.app.state.single_delete.last_results.clear()
        response=await self.client.post('/api/delete-diagnosis',headers=HEADERS,json={'last_result':True})
        self.assertEqual(response.status_code,404)
        self.assertIn('restart',response.json()['error']['message'])
        invalid=await self.client.post('/api/delete-diagnosis',headers=HEADERS,json={'last_result':1})
        self.assertEqual(invalid.status_code,400)
        self.assertEqual(len(self.calls),5)

    async def test_origin_missing_page_token_and_lost_result_no_graph(self):
        response=await self.client.post('/api/delete-diagnosis',headers={**HEADERS,'Origin':'https://evil.example'},json={'job_id':self.job.id,'key':'published_posts:11_123'})
        self.assertEqual(response.status_code,403)
        self.session.pages[0].access_token=''
        self.assertEqual((await self.diagnose()).json()['error']['code'],'page_token_missing')
        self.session.pages[0].access_token=PAGE
        self.app.state.bulk.jobs.clear()
        self.assertEqual((await self.diagnose()).json()['error']['code'],'diagnosis_unavailable')
        self.assertEqual(self.calls,[])

    async def test_duplicate_probe_and_disconnect_during_get(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def delayed(request):
            self.calls.append(request)
            self.assertEqual(request.method,'GET')
            entered.set()
            await release.wait()
            return httpx.Response(200,json={'data':[]})
        self.app.state.graph.http._transport=httpx.MockTransport(delayed)
        task=asyncio.create_task(self.diagnose())
        try:
            await asyncio.wait_for(entered.wait(),2)
            self.assertEqual((await self.diagnose()).json()['error']['code'],'diagnosis_busy')
            self.store.disconnect(self.bid,self.sid)
        finally:
            release.set()
            response=await task
        self.assertEqual(response.status_code,401)
        self.assertEqual(len(self.calls),1)
