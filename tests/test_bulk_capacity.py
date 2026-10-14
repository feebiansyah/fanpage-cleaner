"""Large local batches: all outbound requests use the dummy MockTransport."""
import asyncio
import json
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_bulk_delete as fixture


class BulkCapacity(unittest.IsolatedAsyncioTestCase):
    max_targets = 5000
    asyncSetUp = fixture.BulkDelete.asyncSetUp
    asyncTearDown = fixture.BulkDelete.asyncTearDown
    browser = fixture.BulkDelete.browser
    seed = fixture.BulkDelete.seed
    prepare = fixture.BulkDelete.prepare
    confirm = fixture.BulkDelete.confirm
    start = fixture.BulkDelete.start

    async def get_snapshot(self, jid, **params):
        response = await self.client.get('/api/bulk-delete/jobs/'+jid,
                                         headers=fixture.HEADERS, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        return response.json()

    async def wait_terminal(self, jid, timeout=30):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            snapshot = await self.get_snapshot(jid, since=0)
            if snapshot['terminal']:
                return snapshot
            await asyncio.sleep(.005)
        self.fail('Dummy batch did not finish within its local test deadline')

    async def test_five_thousand_original_targets_and_bounded_get_only_polling(self):
        loop = asyncio.get_running_loop()
        previous_debug = loop.get_debug()
        loop.set_debug(False)
        self.addCleanup(loop.set_debug, previous_debug)
        items = [fixture.post(n) for n in range(1, 5001)]
        self.allowed.update(item['object_id'] for item in items)
        self.seed(items)
        prepared = await self.prepare(5000)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        preview = prepared.json()
        self.assertEqual(preview['supported'], 5000)
        self.assertEqual(len(preview['items']), 5000)
        self.assertEqual(self.calls, [])
        self.store.governor.interval = .000001
        # Revisions must still be published by persist; only disk IO is omitted.
        with patch.object(self.app.state.bulk.checkpoints, 'write', new=lambda record: None), \
             patch.object(self.app.state.bulk.checkpoints, 'write_progress', new=lambda record, chunk: None):
            jid = (await self.confirm(preview['confirmation_id'])).json()['job_id']
            done = await self.wait_terminal(jid, timeout=120)
        self.assertEqual(done['counts']['succeeded'], 5000)
        self.assertEqual(len(self.calls), 5000)
        self.assertEqual({request.url.path for request, _ in self.calls},
                         {'/v26.0/'+item['object_id'] for item in items})
        self.assertTrue(all(request.method == 'DELETE' for request, _ in self.calls))
        first = await self.get_snapshot(jid)
        self.assertEqual(len(first['items']), 50)
        self.assertEqual(first['total'], 5000)
        successes = first['changes']
        self.assertEqual(len(successes), 5000)
        self.assertTrue(all(row['status'] == 'succeeded' for row in successes))
        for row in successes:
            self.assertTrue({'key', 'page_id', 'delete_target', 'status', 'details'} <= row.keys())
        page = await self.get_snapshot(jid, offset=4950, since=first['revision'])
        self.assertEqual(len(page['items']), 50)
        self.assertEqual(page['items'][0]['delete_target'], '11_4951')
        self.assertEqual(page['changes'], [])
        self.assertFalse(page['has_more_changes'])
        seen, cursor = [], 0
        for _ in range(200):
            delta = await self.get_snapshot(jid, offset=0, since=cursor)
            self.assertLessEqual(len(delta['items']), 50)
            self.assertLessEqual(len(delta['changes']), 100)
            self.assertFalse(delta['reset_required'])
            seen.extend(delta['changes'])
            self.assertGreaterEqual(delta['next_revision'], cursor)
            cursor = delta['next_revision']
            if not delta['has_more_changes']:
                break
        else:
            self.fail('Delta cursor failed to drain bounded retained changes')
        self.assertEqual(cursor, first['revision'])
        self.assertEqual({row['delete_target'] for row in seen if row['status']=='succeeded'},
                         {item['object_id'] for item in items})
        self.assertEqual(len(self.calls), 5000, 'GET polling must never dispatch to Meta')
        rejected = await self.prepare(5001)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(len(self.calls), 5000)

    async def test_bulk_ttl_is_ten_minutes_capped_to_session_and_replay_bound_to_owner(self):
        data = (await self.prepare(1)).json()
        self.assertGreaterEqual(data['expires_in_seconds'], 598)
        self.assertLessEqual(data['expires_in_seconds'], 600)
        self.session.expires_at = time.monotonic()+90
        capped = (await self.prepare(1)).json()
        self.assertGreaterEqual(capped['expires_in_seconds'], 88)
        self.assertLessEqual(capped['expires_in_seconds'], 90)
        jid = (await self.confirm(data['confirmation_id'])).json()['job_id']
        await self.wait_terminal(jid)
        self.app.state.bulk_delete.confirmations[data['confirmation_id']].expires_at = time.monotonic()-1
        self.assertEqual((await self.confirm(data['confirmation_id'])).json()['job_id'], jid)
        bid, _, _ = self.store.bootstrap(None)
        sid, _ = self.store.replace(bid, 'dummy-other-user',
                                    [fixture.Page('11', 'Other', fixture.TOKEN)])
        other = self.browser(bid, sid)
        try:
            self.assertEqual((await self.confirm(data['confirmation_id'], client=other)).status_code, 404)
        finally:
            await other.aclose()
        self.assertEqual(len(self.calls), 1)

    async def test_prepare_body_over_one_mib_rejected_without_graph(self):
        # A valid JSON envelope exceeds the route's dedicated 1 MiB ceiling.
        body = json.dumps({'selections': [{'page_id': '11', 'keys': ['x'*160]*7000}]})
        self.assertGreater(len(body.encode()), 1048576)
        response = await self.client.post('/api/bulk-delete/prepare', headers={
            **fixture.HEADERS, 'Content-Type': 'application/json'}, content=body)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.calls, [])

    async def test_preview_capacity_fails_closed_and_expiration_reclaims_budget(self):
        self.store.settings = replace(self.store.settings, dry_max_targets=3)
        first = await self.prepare(2)
        self.assertEqual(first.status_code, 200, first.text)
        rejected = await self.prepare(2)
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(len(self.app.state.bulk_delete.confirmations), 1)
        self.app.state.bulk_delete.confirmations[first.json()['confirmation_id']].expires_at = time.monotonic()-1
        accepted = await self.prepare(2)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(len(self.app.state.bulk_delete.confirmations), 1)
        self.assertEqual(self.calls, [])

    async def test_preview_bytes_and_retained_job_share_capacity_budget(self):
        self.store.settings = replace(self.store.settings, dry_max_bytes=8192)
        response = await self.prepare(2)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.app.state.bulk_delete.confirmations, {})
        self.store.settings = replace(self.store.settings, dry_max_bytes=134217728)
        jid = await self.start(2)
        await self.wait_terminal(jid)
        self.store.settings = replace(self.store.settings, dry_max_targets=3)
        response = await self.prepare(2)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.calls), 2)

    async def test_dry_admission_counts_outstanding_bulk_preview_bytes_and_targets(self):
        from bulk_targets import from_audit
        from errors import AppError
        for max_bytes, max_targets in ((24576, 50000), (134217728, 6)):
            with self.subTest(max_bytes=max_bytes, max_targets=max_targets):
                self.app.state.bulk_delete.confirmations.clear()
                settings = replace(self.store.settings, dry_max_bytes=max_bytes,
                                   dry_max_targets=max_targets)
                self.store.settings = self.app.state.bulk.settings = settings
                prepared = await self.prepare(2)
                self.assertEqual(prepared.status_code, 200, prepared.text)
                dry_targets = [from_audit(fixture.post(number), '11') for number in range(3, 8)]
                # Preview bytes=16384; dry bytes=10240. Their sum exceeds24576.
                with self.assertRaises(AppError) as rejected:
                    self.app.state.bulk.add(self.bid, self.sid, dry_targets, 0, {'11':'First Page'})
                self.assertEqual(rejected.exception.code, 'dry_capacity')
                self.assertEqual(self.app.state.bulk.jobs, {})
                self.assertEqual(self.calls, [])

    async def test_confirm_transfers_preview_without_double_count_and_rejects_other_preview(self):
        from errors import AppError
        settings = replace(self.store.settings, dry_max_bytes=2*8192, dry_max_targets=2)
        self.store.settings = self.app.state.bulk.settings = settings
        prepared = await self.prepare(2)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        cid = prepared.json()['confirmation_id']
        entry = self.app.state.bulk_delete.confirmations[cid]
        owned_targets = entry.targets
        with patch.object(self.app.state.bulk.checkpoints, 'write', new=lambda record: None), \
             patch.object(self.app.state.bulk.checkpoints, 'write_progress', new=lambda record, chunk: None):
            confirmed = self.app.state.bulk_delete.confirm(self.bid, self.sid,
                {'confirmation_id':cid, 'manual_targets_confirmed':True})
            job = self.app.state.bulk.jobs[confirmed['job_id']]
            self.assertIs(job.targets, owned_targets)
            self.assertEqual(len(job.targets), 2)
            self.assertEqual(entry.targets, [])
            self.assertEqual(self.app.state.bulk_delete.preview_capacity(), (0, 0))
            self.assertEqual(self.calls, [], 'Atomic transfer happens before worker dispatch')
            with self.assertRaises(AppError) as rejected:
                self.app.state.bulk_delete.prepare(self.bid, self.sid,
                    {'selections':[{'page_id':'11', 'keys':[fixture.post(3)['key']]}]})
            self.assertEqual(rejected.exception.code, 'delete_capacity')
            self.assertEqual(len(self.app.state.bulk_delete.confirmations), 1)
            result = await self.wait_terminal(job.id)
            self.assertEqual(result['counts']['succeeded'], 2)
            self.assertEqual(len(self.calls), 2)

    async def test_confirmation_count_capacity_is_bounded_before_any_dispatch(self):
        from single_delete import MAX_PER_SESSION
        for _ in range(MAX_PER_SESSION):
            response = await self.prepare(1)
            self.assertEqual(response.status_code, 200, response.text)
        rejected = await self.prepare(1)
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(len(self.app.state.bulk_delete.confirmations), MAX_PER_SESSION)
        self.assertEqual(self.calls, [])

    async def test_retention_overflow_requires_one_resync_then_empty_incremental_poll(self):
        self.responses['11_2'] = fixture.httpx.Response(403, json={'error':{'code':200, 'message':'Dummy object denial'}})
        jid = await self.start(2)
        await self.wait_terminal(jid)
        job = self.app.state.bulk.jobs[jid]
        # Exercise the real publication path without dispatching additional requests.
        for number in range(10002):
            job.targets[0].status = 'failed' if number % 2 == 0 else 'succeeded'
            self.app.state.bulk.publish_changes(job, 0)
        self.assertEqual(len(job.changes), 10000)
        reset = await self.get_snapshot(jid, since=0)
        self.assertTrue(reset['reset_required'])
        self.assertFalse(reset['has_more_changes'])
        self.assertEqual(len(reset['changes']), 2)
        self.assertEqual({row['status'] for row in reset['changes']}, {'succeeded', 'failed'})
        self.assertEqual({row['delete_target'] for row in reset['changes']}, {'11_1', '11_2'})
        current = await self.get_snapshot(jid, since=reset['next_revision'])
        self.assertFalse(current['reset_required'])
        self.assertEqual(current['changes'], [])
        self.assertEqual(current['next_revision'], reset['revision'])
        self.assertEqual(len(self.calls), 2)

    async def test_cache_tombstone_hides_proven_alias_on_next_local_read(self):
        alias = fixture.post(2, key='video_reels:987', object_id='987', kind='Reel',
                             sources=[{'source':'video_reels', 'object_id':'987', 'post_id':'11_1'}])
        self.seed([fixture.post(1), alias, fixture.post(3)])
        with patch.object(self.store.audits, 'filter_removed', wraps=self.store.audits.filter_removed) as scan:
            self.store.audits.invalidate_post('11', '11_1')
            self.assertEqual(scan.call_count, 0, 'Dispatch records a tombstone without rescanning the audit')
            visible = self.store.audits.get(self.sid, '11')
            self.assertGreater(scan.call_count, 0)
        self.assertEqual([row['object_id'] for row in visible['items']], ['11_3'])
        self.assertEqual(self.calls, [])

    async def test_thousand_target_stop_and_expiry_keep_inflight_only(self):
        self.store.governor.interval = .000001
        self.allowed.update(page+'_'+str(number) for page in ('11', '22') for number in range(1, 1001))
        for expire in (False, True):
            with self.subTest(expire=expire):
                page_id = '22' if expire else '11'
                if expire:
                    self.bid, _, _ = self.store.bootstrap(None)
                    self.sid, self.session = self.store.replace(self.bid, 'dummy-expiry-user',
                        [fixture.Page('22', 'Second Page', fixture.OTHER_TOKEN)])
                    self.client.cookies.set('fanpage_browser', self.bid)
                    self.client.cookies.set('fanpage_session', self.sid)
                items = [fixture.post(number, page_id) for number in range(1, 1001)]
                self.seed(items, page_id)
                entered, release = asyncio.Event(), asyncio.Event()

                async def blocked(request):
                    entered.set()
                    await release.wait()
                    return fixture.httpx.Response(200, json={'success': True})

                self.response = blocked
                before = len(self.calls)
                with patch.object(self.app.state.bulk.checkpoints, 'write', new=lambda record: None), \
                     patch.object(self.app.state.bulk.checkpoints, 'write_progress', new=lambda record, chunk: None):
                    jid = await self.start(selections=[{'page_id':page_id, 'keys':[row['key'] for row in items]}])
                    await asyncio.wait_for(entered.wait(), 3)
                    job = self.app.state.bulk.jobs[jid]
                    if expire:
                        self.session.expires_at = time.monotonic()-1
                        self.store.cleanup()
                        denied = await self.client.get('/api/bulk-delete/jobs/'+jid, headers=fixture.HEADERS)
                        self.assertEqual(denied.status_code, 401)
                    else:
                        stopped = await self.client.post('/api/bulk-delete/jobs/'+jid+'/stop', headers=fixture.HEADERS)
                        self.assertEqual(stopped.status_code, 200, stopped.text)
                    sent = len(self.calls)-before
                    self.assertGreaterEqual(sent, 1)
                    self.assertLessEqual(sent, 2)
                    self.assertFalse(job.terminal)
                    await asyncio.sleep(.03)
                    self.assertEqual(len(self.calls)-before, sent)
                    release.set()
                    for _ in range(300):
                        if job.terminal:
                            break
                        await asyncio.sleep(.005)
                    self.assertTrue(job.terminal)
                    self.assertFalse(job.active)
                    self.assertEqual(sum(target.status=='succeeded' for target in job.targets), sent)
                    self.assertEqual(sum(target.status in {'cancelled', 'blocked'} for target in job.targets), 1000-sent)
                    self.assertEqual(len(self.calls)-before, sent)

    async def test_cross_browser_linked_reel_lock_blocks_alias_but_allows_other_post(self):
        merged = fixture.post(1, kind='Reel', sources=[
            {'source':'published_posts', 'object_id':'11_1'},
            {'source':'video_reels', 'object_id':'987', 'post_id':'11_1'}])
        self.allowed.add('987')
        self.seed([merged])
        bid, _, _ = self.store.bootstrap(None)
        sid, session = self.store.replace(bid, 'dummy-second-owner',
                                          [fixture.Page('11', 'Other Owner', fixture.TOKEN)])
        other = self.browser(bid, sid)
        b_items = [fixture.post(1), fixture.post(2)]
        self.store.audits.reserve(sid, 2, '11')
        self.store.audits.put(sid, {'page_id':'11', 'items':b_items, 'displayed':2,
                                  'found':2, 'safe_for_delete':False}, session.expires_at)
        a_entered, b_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def blocked(request):
            if request.url.path == '/v26.0/987':
                a_entered.set()
            elif request.url.path == '/v26.0/11_2':
                b_entered.set()
            else:
                self.fail('Linked post alias must never be sent after successful Reel deletion')
            await release.wait()
            return fixture.httpx.Response(200, json={'success':True})

        self.response = blocked
        self.store.governor.interval = .000001
        try:
            a_jid = await self.start(selections=[{'page_id':'11', 'keys':[merged['key']]}])
            await asyncio.wait_for(a_entered.wait(), 3)
            self.assertIn('post:11_1', self.app.state.bulk.locks)
            prepared = await other.post('/api/bulk-delete/prepare', headers=fixture.HEADERS,
                json={'selections':[{'page_id':'11', 'keys':[row['key'] for row in b_items]}]})
            self.assertEqual(prepared.status_code, 200, prepared.text)
            confirmed = await self.confirm(prepared.json()['confirmation_id'], client=other)
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            b_jid = confirmed.json()['job_id']
            await asyncio.wait_for(b_entered.wait(), 3)
            self.assertEqual(len(self.calls), 2)
            alias = self.app.state.bulk.jobs[b_jid].targets[0]
            self.assertEqual(alias.delete_target, '11_1')
            self.assertEqual(alias.status, 'queued')
            self.assertEqual(alias.attempts, 0)
            release.set()
            await self.wait_terminal(a_jid)
            for _ in range(300):
                snapshot = await other.get('/api/bulk-delete/jobs/'+b_jid, headers=fixture.HEADERS)
                self.assertEqual(snapshot.status_code, 200, snapshot.text)
                result = snapshot.json()
                if result['terminal']:
                    break
                await asyncio.sleep(.005)
            self.assertTrue(result['terminal'])
            self.assertEqual(result['counts']['succeeded'], 1)
            self.assertEqual(result['counts']['blocked'], 1)
            self.assertEqual(alias.status, 'blocked')
            self.assertIn('bulk_target_deleted', alias.reasons)
            self.assertEqual({request.url.path for request, _ in self.calls},
                             {'/v26.0/987', '/v26.0/11_2'})
            self.assertEqual(len(self.calls), 2)
        finally:
            release.set()
            await other.aclose()

    async def test_shared_story_post_and_linked_reel_alias_do_not_change_original_delete_id(self):
        shared = fixture.post(1, type='shared_story', status_type='shared_story',
                              post_status_type='shared_story')
        story = fixture.post(2, kind='Story')
        linked = fixture.post(3, key='video_reels:987', object_id='987', kind='Reel',
                              sources=[{'source':'video_reels', 'object_id':'987', 'post_id':'11_3'}],
                              permalink=None)
        self.allowed.add('987')
        self.seed([shared, story, fixture.post(3), linked])
        keys = [row['key'] for row in (shared, story, fixture.post(3), linked)]
        data = (await self.prepare(selections=[{'page_id':'11', 'keys':keys}])).json()
        self.assertEqual(data['supported'], 2)
        self.assertEqual(data['duplicates'], 1)
        self.assertEqual(data['unsupported'], 1)
        jid = (await self.confirm(data['confirmation_id'])).json()['job_id']
        done = await self.wait_terminal(jid)
        self.assertEqual(done['counts']['succeeded'], 2)
        self.assertEqual({request.url.path for request, _ in self.calls},
                         {'/v26.0/11_1', '/v26.0/987'})
        success = next(row for row in (await self.get_snapshot(jid))['changes']
                       if row['delete_target']=='987' and row['status']=='succeeded')
        self.assertEqual(success['details']['linked_post_id'], '11_3')


if __name__ == '__main__':
    unittest.main()
