import time
import unittest
from errors import AppError
from graph_api import GraphClient
from config import Settings
from sessions import MemoryStore

class DisabledReel(unittest.IsolatedAsyncioTestCase):
    async def test_reel_transport_is_disabled_without_dispatch(self):
        graph=GraphClient(None,Settings(),MemoryStore(Settings()))
        with self.assertRaises(AppError):
            await graph.delete_reel_once('123','dummy-scope',time.monotonic()+5,lambda:self.fail('No dispatch'))

    async def test_direct_reel_endpoints_never_dispatch(self):
        import httpx
        from app import create_app
        app=create_app(Settings())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
                for endpoint in ('prepare','execute'):
                    response=await client.post('/api/reels/delete/'+endpoint,json={'page_id':'11','keys':['video_reels:123']},headers={'Origin':'http://127.0.0.1:8000','X-Fanpage-Client':'1'})
                    self.assertEqual(response.status_code,409)
                    self.assertEqual(response.json()['error']['code'],'post_scope_unsupported')

    async def test_old_listing_edges_are_rejected_before_http(self):
        graph=GraphClient(None,Settings(),MemoryStore(Settings()))
        for edge in ('video_reels','stories'):
            with self.assertRaises(AppError):
                await graph.content_page('11',edge,'id','dummy-token','dummy-scope',10,None,time.monotonic()+5)
