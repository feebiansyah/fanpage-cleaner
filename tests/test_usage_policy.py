"""Usage/cooldown policy with numeric dummy headers and MockTransport only."""
import json
import time
import unittest
import httpx
from config import Settings
from graph_api import GraphClient
from sessions import MemoryStore

class UsagePolicy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings=Settings(graph_request_interval=.001,graph_max_interval=.01,cooldown_seconds=.2)
        self.store=MemoryStore(self.settings)
        self.scope=self.store.token_key('dummy-secret')

    def graph(self):
        return GraphClient(None,self.settings,self.store)

    def header(self,percent):
        return {'x-app-usage':json.dumps({'call_count':percent,'total_time':percent/2,'total_cputime':1})}

    async def test_saturated_successes_pause_once_per_episode_then_evaluate_at_max_pacing(self):
        graph=self.graph()
        graph._usage(self.header(100),self.scope)
        first=self.store.token_cooldowns[self.scope]
        self.store.token_cooldowns[self.scope]=time.monotonic()-1
        for _ in range(5): graph._usage(self.header(100),self.scope)
        self.assertLess(self.store.token_cooldowns[self.scope],time.monotonic())
        self.assertEqual(self.store.governor.interval,self.settings.graph_max_interval)
        graph._usage(self.header(80),self.scope)
        graph._usage(self.header(100),self.scope)
        self.assertGreaterEqual(self.store.token_cooldowns[self.scope],first)

    async def test_repeated_success_high_usage_adapts_pacing_without_repeated_pause(self):
        calls=[]
        async def handle(request):
            calls.append(request)
            self.assertEqual(request.method,'DELETE')
            return httpx.Response(200,json={'success':True},headers=self.header(95))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            graph=GraphClient(client,self.settings,self.store)
            for n in range(12):
                result=await graph.delete_post_once('11_'+str(n),self.scope,time.monotonic()+10,lambda:'dummy-secret')
                self.assertEqual(result['outcome'],'succeeded')
        self.assertEqual(len(calls),12)
        self.assertGreater(self.store.governor.interval,self.settings.graph_request_interval)
        self.assertEqual(self.store.global_cooldown_until,0)
        self.assertEqual(self.store.token_cooldowns,{})

    async def test_improving_usage_lowers_gradually_missing_invalid_never_mean_zero(self):
        graph=self.graph()
        for _ in range(4): graph._usage(self.header(95),self.scope)
        high=self.store.governor.interval
        graph._usage({},self.scope)
        self.assertFalse(self.store.governor.usage_known)
        self.assertEqual(self.store.governor.interval,high)
        for value in ('bad','[]','{"call_count":true}','{"call_count":-1}','{"call_count":"0"}'):
            graph._usage({'x-app-usage':value},self.scope)
            self.assertEqual(self.store.governor.interval,high)
        for _ in range(3): graph._usage(self.header(10),self.scope)
        self.assertLess(self.store.governor.interval,high)
        self.assertGreaterEqual(self.store.governor.interval,self.settings.graph_request_interval)
        self.assertGreaterEqual(self.store.governor.interval,high*.79)

    async def test_buc_shape_and_scope_no_global_pause_from_unmapped_business(self):
        graph=self.graph()
        graph._usage({'x-business-use-case-usage':json.dumps({'12345':[{'type':'pages','call_count':100,'total_time':80,'total_cputime':20}]})},self.scope)
        detail=self.store.cooldown_summary([self.scope])
        self.assertEqual(detail['reason'],'local_usage_saturated')
        self.assertEqual(detail['source'],'x-business-use-case-usage')
        self.assertEqual(detail['usage'],100)
        self.assertEqual(detail['scope'],'token')
        self.assertEqual(self.store.global_cooldown_until,0)
        self.store.check_cooldown(self.store.token_key('unrelated-token'))
        self.assertNotIn(self.scope,json.dumps(detail))

    async def test_retry_after_not_shortened_or_overwritten_by_local_usage(self):
        async def handle(request):
            return httpx.Response(200,json={'success':True},headers={'Retry-After':'120',**self.header(100)})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result=await GraphClient(client,self.settings,self.store).delete_post_once('11_1',self.scope,time.monotonic()+10,lambda:'dummy-secret')
        self.assertEqual(result['outcome'],'succeeded')
        detail=self.store.cooldown_summary([self.scope])
        self.assertEqual(detail['reason'],'retry_after')
        self.assertEqual(detail['source'],'retry-after')
        self.assertGreaterEqual(detail['remaining_seconds'],119)
        self.graph()._usage(self.header(10),self.scope)
        self.assertGreaterEqual(self.store.cooldown_summary([self.scope])['remaining_seconds'],119)

    async def test_429_and_meta_code_distinguished_no_delete_retry_and_scope_shared(self):
        for response,reason in [(httpx.Response(429,json={},headers={'Retry-After':'2'}),'meta_http_429'),
                                (httpx.Response(400,json={'error':{'code':17}}),'meta_rate_limit')]:
            store=MemoryStore(self.settings); scope=store.token_key('dummy-secret'); calls=[]
            async def handle(request): calls.append(request);return response
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                graph=GraphClient(client,self.settings,store)
                result=await graph.delete_post_once('11_1',scope,time.monotonic()+10,lambda:'dummy-secret')
                self.assertEqual(result['reason'],'rate_limited')
                self.assertEqual(store.cooldown_summary([scope])['reason'],reason)
                with self.assertRaises(Exception):
                    await graph.delete_post_once('11_2',scope,time.monotonic()+10,lambda:'dummy-secret')
            self.assertEqual(len(calls),1)
            store.check_cooldown(store.token_key('unrelated-token'))

    async def test_malformed_buc_and_cross_bucket_low_observation_cannot_clear_high_usage(self):
        graph=self.graph()
        graph._usage(self.header(95),self.scope)
        before=self.store.governor.interval
        for _ in range(6):
            graph._usage({'x-business-use-case-usage':json.dumps({'12345':[{'type':'pages','call_count':1}]})},self.scope)
        self.assertGreaterEqual(self.store.governor.interval,before)
        graph._usage({'x-business-use-case-usage':json.dumps({'12345':{'type':'pages','call_count':0}})},self.scope)
        self.assertFalse(self.store.governor.usage_known)
        self.assertGreaterEqual(self.store.governor.interval,before)

    async def test_get_success_retry_after_is_honored_without_extra_get_and_long_wait_safe(self):
        for instruction in ('3','9999999999999999999999999'):
            store=MemoryStore(self.settings);scope=store.token_key('dummy-secret');calls=[]
            async def handle(request):
                calls.append(request)
                self.assertEqual(request.method,'GET')
                return httpx.Response(200,json={'data':[]},headers={'Retry-After':instruction})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                data=await GraphClient(client,self.settings,store)._get('dummy-secret',scope,'https://graph.facebook.com/v26.0/me/permissions',{},time.monotonic()+10)
            self.assertEqual(data,{'data':[]})
            self.assertEqual(len(calls),1)
            detail=store.cooldown_summary([scope])
            self.assertEqual(detail['reason'],'retry_after')
            self.assertGreaterEqual(detail['remaining_seconds'],int(instruction)-1)

    async def test_missing_header_breaks_recovery_streak_and_one_vote_per_multi_bucket_response(self):
        graph=self.graph()
        graph._usage(self.header(95),self.scope);high=self.store.governor.interval
        for _ in range(2): graph._usage(self.header(1),self.scope)
        graph._usage({},self.scope)
        graph._usage(self.header(1),self.scope)
        self.assertEqual(self.store.governor.interval,high)
        for _ in range(2): graph._usage(self.header(1),self.scope)
        self.assertAlmostEqual(self.store.governor.interval,high*.8)
        before=self.store.governor.interval
        combined={**self.header(1),'x-business-use-case-usage':json.dumps({'123':[{'type':'pages','call_count':1},{'type':'other','call_count':1}]})}
        graph._usage(combined,self.scope)
        self.assertEqual(self.store.governor.interval,before)

    async def test_buc_later_entry_is_not_silently_ignored(self):
        entries=[{'type':'pages','call_count':1} for _ in range(40)]+[{'type':'pages','call_count':100}]
        self.graph()._usage({'x-business-use-case-usage':json.dumps({'123':entries})},self.scope)
        self.assertEqual(self.store.cooldown_summary([self.scope])['usage'],100)
        until=self.store.token_cooldowns[self.scope]
        self.store.token_cooldowns[self.scope]=time.monotonic()-1
        self.graph()._usage({'x-business-use-case-usage':json.dumps({'123':entries})},self.scope)
        self.assertLess(self.store.token_cooldowns[self.scope],time.monotonic())

    async def test_partial_or_invalid_metric_never_votes_to_speed_up(self):
        graph=self.graph();graph._usage(self.header(95),self.scope)
        high=self.store.governor.interval
        for _ in range(5): graph._usage({'x-app-usage':'{"call_count":1}'},self.scope)
        self.assertEqual(self.store.governor.interval,high)
        for _ in range(5): graph._usage({'x-app-usage':'{"call_count":1,"total_cputime":false}'},self.scope)
        self.assertFalse(self.store.governor.usage_known)
        self.assertEqual(self.store.governor.interval,high)

    async def test_expired_cooldown_metadata_is_owner_scoped_safe_history(self):
        self.graph()._usage(self.header(100),self.scope)
        self.store.token_cooldowns[self.scope]=time.monotonic()-1
        detail=self.store.last_cooldown_summary([self.scope])
        self.assertFalse(detail['active'])
        self.assertEqual(detail['remaining_seconds'],0)
        self.assertEqual(detail['usage'],100)
        self.assertIsNone(self.store.last_cooldown_summary([self.store.token_key('another-token')]))
        self.assertNotIn('dummy-secret',json.dumps(detail))
        self.assertNotIn(self.scope,json.dumps(detail))

    async def test_usage_state_capacity_keeps_max_pacing_without_repeating_untracked_pause(self):
        governor=self.store.governor
        for i in range(self.settings.max_token_entries):
            governor.observe_usage(95,bucket=str(i),adapt=False)
        graph=self.graph()
        for _ in range(5): graph._usage(self.header(100),self.scope)
        self.assertEqual(len(governor.usage_states),self.settings.max_token_entries)
        self.assertEqual(governor.interval,self.settings.graph_max_interval)
        self.assertIsNone(self.store.cooldown_summary([self.scope]))
