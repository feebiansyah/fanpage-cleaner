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
    async def test_reel_routes_reject_supported_and_forged_inputs_without_delete(self):
        for payload in [None,{'page_id':'11','keys':[reel()['key']],'target_id':'987'},
                        {'page_id':'11','keys':[reel()['key']],'access_token':TOKEN}]:
            response=await self.prepare(payload)
            self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(response.json()['error']['code'],'post_scope_unsupported')
        response=await self.execute('old-reel-confirmation')
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['error']['code'],'post_scope_unsupported')
        self.assertEqual(self.calls,[])

    async def test_reel_bulk_preview_is_unsupported_and_cannot_confirm(self):
        response=await self.client.post('/api/bulk-delete/prepare',headers=HEADERS,
            json={'selections':[{'page_id':'11','keys':[reel()['key']]}]})
        data=response.json()
        self.assertEqual((data['supported'],data['unsupported']),(0,1))
        confirmed=await self.client.post('/api/bulk-delete/confirm',headers=HEADERS,
            json={'confirmation_id':data['confirmation_id'],'manual_targets_confirmed':True})
        self.assertEqual(confirmed.status_code,409,confirmed.text)
        self.assertEqual(self.calls,[])

    async def test_foreign_origin_cannot_dispatch_disabled_routes(self):
        response=await self.client.post('/api/reels/delete/execute',
            headers={**HEADERS,'Origin':'https://foreign.example'},
            json={'confirmation_id':'old','manual_target_confirmed':True})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.calls,[])

    async def test_reel_execute_does_not_consume_post_confirmation(self):
        from test_single_delete import item
        self.audit['items']=[item()]
        self.seed()
        prepared=await self.client.post('/api/posts/delete/prepare',headers=HEADERS,
            json={'page_id':'11','keys':[item()['key']]})
        cid=prepared.json()['confirmation_id']
        self.assertEqual((await self.execute(cid)).status_code,409)
        self.assertFalse(self.app.state.single_delete.confirmations[cid].used)
        result=await self.client.post('/api/posts/delete/execute',headers=HEADERS,
            json={'confirmation_id':cid,'manual_target_confirmed':True})
        self.assertEqual(result.json()['outcome'],'succeeded')
        self.assertEqual([r.url.path for r in self.calls],['/v26.0/11_123'])
