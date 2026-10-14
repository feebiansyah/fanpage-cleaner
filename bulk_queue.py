"""Bounded process-wide queue: Page fairness and canonical-target locks."""
import asyncio
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from errors import AppError
from bulk_targets import identity_locks, suppression_reservations


@dataclass
class DryJob:
    id: str
    owner_id: str
    browser_id: str = field(repr=False)
    session_id: str | None = field(repr=False)
    created_at: float
    expires_at: float
    targets: list
    duplicates: int
    pages: dict
    mode: str = 'dry_run'
    unsupported: int = 0
    stop_requested: bool = False
    stop_reason: str = ''
    final_stop_state: str = 'cancelled'
    state: str = 'queued'
    events: deque = field(default_factory=deque)
    active: dict = field(default_factory=dict,repr=False)
    revision: int = 0
    changes: deque = field(default_factory=lambda:deque(maxlen=10000),repr=False)
    published_status: list = field(default_factory=list,repr=False)

    @property
    def terminal(self):
        return self.state in {'completed','cancelled','failed','paused_needs_reconnect'} and (self.mode!='delete_page_posts' or not self.active)


class BulkQueue:
    def __init__(self,store,settings,checkpoints):
        self.store,self.settings,self.checkpoints=store,settings,checkpoints
        self.jobs={}
        self.owners={}
        self.buckets={}
        self.rotation=deque()
        self.locks=set()
        self.changed=asyncio.Event()
        self.stopping=False
        self.recovered_count=0
        self.blocked_pages={}
        self.blocked_scopes={}
        self.success_reservations=set()
        self.permission_denials={}
        self.permission_circuits={}
        self.permission_capacity_until=0

    def record(self,job):
        return {'version':1,'job_id':job.id,'owner_id':job.owner_id,'created_at':job.created_at,
                'state':job.state,'mode':job.mode,'targets':[t.public() for t in job.targets]}

    def persist(self,job,index=None):
        try:
            if index is None: self.checkpoints.write(self.record(job))
            else:
                first=(index//256)*256
                record={'version':1,'job_id':job.id,'owner_id':job.owner_id,'created_at':job.created_at,
                        'state':job.state,'mode':job.mode,'targets':[t.public() for t in job.targets[first:first+256]]}
                self.checkpoints.write_progress(record,index//256)
        except (ValueError,OSError,TypeError):
            if job.mode=='delete_page_posts':
                job.state='failed'
                job.stop_requested=True
                job.stop_reason='checkpoint_failed'
                self._remove_pending(job.id)
                for target in job.targets:
                    if target.status=='queued' or (target.status=='processing' and not target.diagnostic.get('request_started')):
                        target.status='blocked'
                        if 'checkpoint_failed' not in target.reasons: target.reasons.append('checkpoint_failed')
                self._cancel_unsent(job)
                self.publish_changes(job)
                raise AppError('checkpoint_failed',503) from None
            job.state='failed'
            for target in job.targets:
                if target.status=='processing':
                    target.status='uncertain'
                    target.reasons=list(dict.fromkeys(target.reasons+['checkpoint_failed']))
            for task in job.active.values(): task.cancel()
            raise AppError('checkpoint_failed',503) from None
        self.publish_changes(job,index)

    def publish_changes(self,job,index=None):
        if job.mode!='delete_page_posts': return
        if not job.published_status: job.published_status=['queued']*len(job.targets)
        indices=range(len(job.targets)) if index is None else (index,)
        for number in indices:
            status=job.targets[number].status
            if status!=job.published_status[number]:
                job.revision+=1
                job.changes.append((job.revision,number))
                job.published_status[number]=status

    def sync_sessions(self):
        now=time.monotonic()
        self.blocked_pages={k:v for k,v in self.blocked_pages.items() if v>now}
        self.blocked_scopes={k:v for k,v in self.blocked_scopes.items() if v>now}
        self.permission_circuits={k:v for k,v in self.permission_circuits.items() if v>now}
        self.permission_denials={k:v for k,v in self.permission_denials.items() if v and v[-1]>now-self.settings.delete_permission_window}
        for job in list(self.jobs.values()):
            if not job.terminal and not job.stop_requested and (job.session_id not in self.store.sessions or job.expires_at<=now):
                self.pause(job,'session_expired' if job.session_id not in self.store.sessions else 'queue_duration_limit')
            if job.terminal and job.expires_at<=now and not job.active:
                self._remove_pending(job.id)
                self.jobs.pop(job.id,None)
        for sid in [sid for sid in self.owners if sid not in self.store.sessions]:
            del self.owners[sid]

    def pause_session(self,sid,reason):
        for job in self.jobs.values():
            if job.session_id==sid:
                if not job.terminal: self.pause(job,reason)
                else: job.session_id=None
        self.owners.pop(sid,None)

    def pause(self,job,reason):
        if job.mode=='delete_page_posts':
            self.stop_real(job,reason,'paused_needs_reconnect')
            return
        job.state='paused_needs_reconnect'
        for target in job.targets:
            if target.status=='processing':
                target.status='uncertain'
                target.reasons=list(dict.fromkeys(target.reasons+[reason]))
        for task in job.active.values(): task.cancel()
        job.session_id=None
        self._remove_pending(job.id)
        try: self.persist(job)
        except AppError: pass
        self.changed.set()

    def add(self,browser_id,sid,targets,duplicates,pages,mode='dry_run',unsupported=0):
        self.sync_sessions()
        if self.store.session(browser_id,sid) is None:
            raise AppError('session_expired',401)
        if self.stopping or not targets:
            raise AppError('invalid_selection')
        total=sum(len(j.targets) for j in self.jobs.values())+len(targets)
        size=sum(len(j.targets)*(8192 if j.mode=='delete_page_posts' else 2048) for j in self.jobs.values())+len(targets)*(8192 if mode=='delete_page_posts' else 2048)
        preview_capacity=getattr(self,'preview_capacity',None)
        if preview_capacity is not None:
            preview_count,preview_bytes=preview_capacity(targets if mode=='delete_page_posts' else None)
            total+=preview_count
            size+=preview_bytes
        if len(self.jobs)>=self.settings.dry_max_jobs or total>self.settings.dry_max_targets or size>self.settings.dry_max_bytes:
            raise AppError('dry_capacity',503)
        owner=self.owners.setdefault(sid,uuid.uuid4().hex)
        job=DryJob(uuid.uuid4().hex,owner,browser_id,sid,time.time(),time.monotonic()+self.settings.dry_job_ttl,targets,duplicates,pages)
        job.mode=mode
        job.unsupported=unsupported
        self.persist(job)  # No job admitted/dispatched if atomic checkpoint failed.
        self.jobs[job.id]=job
        for index,target in enumerate(targets):
            if target.page_id not in self.buckets:
                self.buckets[target.page_id]=deque()
                self.rotation.append(target.page_id)
            self.buckets[target.page_id].append((job.id,index))
        self.changed.set()
        return job

    def owned(self,browser_id,sid,job_id):
        self.store.session(browser_id,sid)  # Cleanup, never session renewal.
        job=self.jobs.get(job_id)
        if job is None or job.browser_id!=browser_id or job.session_id!=sid or self.owners.get(sid)!=job.owner_id:
            raise AppError('dry_job_not_found',404)
        if self.store.session(browser_id,sid) is None:
            raise AppError('dry_job_not_found',404)
        return job

    def _remove_pending(self,job_id):
        for page,items in list(self.buckets.items()):
            self.buckets[page]=deque(pair for pair in items if pair[0]!=job_id)
            if not self.buckets[page]:
                del self.buckets[page]
        self.rotation=deque(p for p in self.rotation if p in self.buckets)

    def claim(self):
        self.store.cleanup()
        if self.stopping: return None
        for _ in range(len(self.rotation)):
            page=self.rotation.popleft()
            items=self.buckets.get(page)
            if not items:
                self.buckets.pop(page,None)
                continue
            self.rotation.append(page)
            for _ in range(len(items)):
                job_id,index=items.popleft()
                job=self.jobs[job_id]
                target=job.targets[index]
                if job.terminal or job.stop_requested or target.status!='queued': continue
                if job.mode=='delete_page_posts':
                    reason=self.block_reason(target)
                    if reason:
                        target.status='blocked'
                        target.reasons.append(reason)
                        try: self.persist(job,index)
                        except AppError: pass
                        self._complete_real(job)
                        continue
                    try: self.store.check_cooldown(target.scope)
                    except AppError:
                        job.state='cooldown'
                        items.append((job_id,index))
                        continue
                lock_keys=identity_locks(target.canonical,target.details)
                if lock_keys.intersection(self.locks) or target.retry_at>time.monotonic():
                    items.append((job_id,index))
                    continue
                if job.mode=='delete_page_posts':
                    singles=sum(self.store.single_delete.reserved_slots(c) for c in self.store.single_delete.confirmations.values()) if self.store.single_delete else 0
                    reservations=suppression_reservations(target.page_id,target.delete_target,target.details)
                    if len(self.store.audits.removed_posts)+len(self.success_reservations)+singles+len(reservations)>self.settings.max_audit_items:
                        target.status='blocked'
                        target.reasons.append('audit_capacity')
                        try: self.persist(job,index)
                        except AppError: pass
                        self._complete_real(job)
                        continue
                    self.success_reservations.update(reservations)
                self.locks.update(lock_keys)
                target.status='processing'
                job.state='running'
                try: self.persist(job,index)
                except AppError:
                    self.locks.difference_update(lock_keys)
                    self.success_reservations.difference_update(suppression_reservations(target.page_id,target.delete_target,target.details))
                    continue
                return job,target,index
        return None

    def release(self,job,target,index,outcome):
        if job.mode=='delete_page_posts':
            self.release_real(job,target,index,outcome)
            return
        if target.status=='processing':
            if outcome.status=='failed' and outcome.temporary and target.attempts<3 and not job.terminal:
                target.status='queued'
                target.retry_at=time.monotonic()+max(.1,outcome.retry_after)
                self.buckets.setdefault(target.page_id,deque()).append((job.id,index))
                if target.page_id not in self.rotation: self.rotation.append(target.page_id)
            else:
                target.status=outcome.status
            if outcome.reason not in target.reasons: target.reasons.append(outcome.reason)
            while len(target.reasons)>12: target.reasons.pop()
        self.locks.difference_update(identity_locks(target.canonical,target.details))
        self.success_reservations.difference_update(suppression_reservations(target.page_id,target.delete_target,target.details))
        job.active.pop(target.canonical,None)
        job.events.append({'status':target.status,'reason':outcome.reason})
        while len(job.events)>self.settings.dry_max_events: job.events.popleft()
        if not job.terminal and all(t.status not in {'queued','processing'} for t in job.targets): job.state='completed'
        for page in list(self.buckets):
            if not self.buckets[page]: del self.buckets[page]
        self.rotation=deque(p for p in self.rotation if p in self.buckets)
        try: self.persist(job,index)
        except AppError: pass
        self.changed.set()

    def cancel(self,job):
        if job.mode=='delete_page_posts':
            self.stop_real(job,'user_stopped')
            return
        if job.terminal: return
        job.state='cancelled'
        for target in job.targets:
            if target.status=='queued': target.status='cancelled'
            elif target.status=='processing':
                target.status='uncertain'
                target.reasons=list(dict.fromkeys(target.reasons+['cancelled_processing']))
        for task in job.active.values(): task.cancel()
        self._remove_pending(job.id)
        self.persist(job)
        self.changed.set()

    def snapshot(self,job,offset=0,*,since=None):
        if job.mode=='delete_page_posts':
            counts={s:sum(t.status==s for t in job.targets) for s in ('queued','processing','succeeded','failed','uncertain','blocked','cancelled')}
            def compact(target):
                return {'page_id':target.page_id,'key':target.key,'delete_target':target.delete_target,'status':target.status,
                        'details':{k:target.details.get(k) for k in ('operation_scope','related_video_ids','linked_post_id')}}
            reset=since is not None and bool(job.changes) and since<job.changes[0][0]-1
            if since is None or reset:
                changes=[compact(t) for t in job.targets if t.status in {'succeeded','failed','uncertain','blocked','cancelled'}]
                next_revision=job.revision
                more=False
            else:
                updates=[(revision,index) for revision,index in job.changes if revision>since][:100]
                changes=[compact(job.targets[index]) for _,index in updates]
                next_revision=updates[-1][0] if updates else job.revision
                more=next_revision<job.revision
            scopes={t.scope for t in job.targets}
            cooldown=max(0,self.store.global_cooldown_until-time.monotonic(),
                max((self.store.token_cooldowns.get(t.scope,0)-time.monotonic() for t in job.targets if t.status in {'queued','processing'}),default=0))
            return {'job_id':job.id,'mode':job.mode,'state':job.state,'terminal':job.terminal,
                    'total':len(job.targets),'selected':len(job.targets)+job.duplicates+job.unsupported,'duplicates':job.duplicates,'unsupported':job.unsupported,
                    'counts':counts,'completed':len(job.targets)-counts['queued']-counts['processing'],
                    'succeeded':counts['succeeded'],'manual_targets_confirmed':True,'story_automatically_verified':False,
                    'pages':[{'page_id':pid,'name':name} for pid,name in job.pages.items()],
                    'items':[{**t.public(),'details':dict(t.details),'diagnostic':dict(t.diagnostic),'cleanup_warning':t.cleanup_warning} for t in job.targets[offset:offset+50]],
                    'offset':offset,'page_size':50,'result_total':len(job.targets),
                    'revision':job.revision,'next_revision':next_revision,'changes':changes,'has_more_changes':more,'reset_required':reset,
                    'cooldown_seconds':math.ceil(cooldown),
                    'cooldown':self.store.cooldown_summary(scopes),
                    'last_cooldown':self.store.last_cooldown_summary(scopes),
                    'pacing':self.store.governor.pacing_summary(),
                    'stop_reason':job.stop_reason,'message':'Operasi menargetkan ID Post atau video/Reel asli sesuai snapshot; sukses mensyaratkan respons Meta yang ketat.'}
        targets=job.targets
        return {'job_id':job.id,'mode':'dry_run','state':job.state,'terminal':job.terminal,
                'safe_for_delete':False,'selected':len(targets)+job.duplicates,'unique':len(targets),
                'valid':sum(t.eligible for t in targets),'ambiguous':sum(not t.eligible for t in targets),
                'duplicates':job.duplicates,'checked':sum(t.status=='dry_run_checked' for t in targets),
                'counts':{s:sum(t.status==s for t in targets) for s in ('queued','processing','dry_run_checked','failed','uncertain','cancelled')},
                'pages':[{'page_id':pid,'name':name} for pid,name in job.pages.items()],
                'items':[t.public() for t in targets[offset:offset+50]],'offset':offset,'total':len(targets),
                'events':list(job.events),'message':'Dry run hanya memeriksa metadata lokal; tidak ada konten dihapus.'}

    def block_reason(self,target):
        if target.scope in self.blocked_scopes: return 'token_invalid'
        if self.permission_capacity_until>time.monotonic(): return 'permission_circuit_capacity'
        if (target.page_id,target.scope) in self.blocked_pages: return 'permission_denied'
        if (target.page_id,target.scope) in self.permission_circuits: return 'permission_circuit_open'
        return None

    def record_delete_outcome(self,target,reason):
        key=(target.page_id,target.scope)
        if reason=='token_invalid':
            self.block_related(target,reason)
        elif reason=='success_boolean_true':
            self.permission_denials.pop(key,None)
        elif reason=='permission_denied':
            now=time.monotonic()
            if key not in self.permission_denials and len(self.permission_denials)>=self.settings.max_token_entries:
                self.block_related(target,'permission_circuit_capacity')
                return
            denials=self.permission_denials.setdefault(key,deque())
            while denials and denials[0]<=now-self.settings.delete_permission_window:
                denials.popleft()
            denials.append(now)
            if len(denials)>=self.settings.delete_permission_threshold:
                self.permission_denials.pop(key,None)
                self.block_related(target,'permission_circuit_open')

    def _cancel_unsent(self,job):
        for target in job.targets:
            task=job.active.get(target.canonical)
            if task is not None and not target.diagnostic.get('request_started'):
                task.cancel()

    def _complete_real(self,job):
        if all(t.status not in {'queued','processing'} for t in job.targets) and not job.active:
            if job.state!='failed':
                job.state=job.final_stop_state if job.stop_requested else 'completed'
            if job.state=='paused_needs_reconnect': job.session_id=None

    def stop_real(self,job,reason,final_state='cancelled'):
        if job.terminal: return
        job.stop_requested=True
        job.stop_reason=reason
        job.final_stop_state=final_state
        job.state='stopping'
        for target in job.targets:
            if target.status=='queued':
                target.status='cancelled'
                target.reasons.append(reason)
        self._remove_pending(job.id)
        self._cancel_unsent(job)
        self._complete_real(job)
        try: self.persist(job)
        except AppError: pass
        self.changed.set()

    def block_related(self,target,reason):
        if reason not in {'token_invalid','permission_circuit_open','permission_circuit_capacity'}:
            return  # Generic target permission failures never establish a Page-wide denial.
        expiry=time.monotonic()+self.settings.dry_job_ttl
        if reason=='token_invalid': self.blocked_scopes[target.scope]=expiry
        elif reason=='permission_circuit_open':
            key=(target.page_id,target.scope)
            if key not in self.permission_circuits and len(self.permission_circuits)>=self.settings.max_token_entries:
                reason='permission_circuit_capacity'
                self.permission_capacity_until=time.monotonic()+self.settings.delete_permission_window
            else: self.permission_circuits[key]=time.monotonic()+self.settings.delete_permission_window
        elif reason=='permission_circuit_capacity': self.permission_capacity_until=time.monotonic()+self.settings.delete_permission_window
        for job in self.jobs.values():
            if job.mode!='delete_page_posts' or job.terminal: continue
            for other in job.targets:
                if self.block_reason(other):
                    if other.status=='queued':
                        other.status='blocked'
                        other.reasons.append(reason)
                    task=job.active.get(other.canonical)
                    if other is not target and task is not None and not other.diagnostic.get('request_started'):
                        task.cancel()
            self._complete_real(job)
            try: self.persist(job)
            except AppError: pass

    def release_real(self,job,target,index,outcome):
        if target.status=='processing':
            target.status=outcome.status
            if outcome.reason not in target.reasons: target.reasons.append(outcome.reason)
            if outcome.status=='queued' and not job.stop_requested:
                self.buckets.setdefault(target.page_id,deque()).append((job.id,index))
                if target.page_id not in self.rotation: self.rotation.append(target.page_id)
                job.state='cooldown'
            elif outcome.status=='queued': target.status='cancelled'
        self.locks.difference_update(identity_locks(target.canonical,target.details))
        self.success_reservations.difference_update(suppression_reservations(target.page_id,target.delete_target,target.details))
        job.active.pop(target.canonical,None)
        self._complete_real(job)
        try: self.persist(job,index)
        except AppError: pass
        self.changed.set()
