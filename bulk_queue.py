"""Bounded process-wide queue: Page fairness and canonical-target locks."""
import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from errors import AppError


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
    state: str = 'queued'
    events: deque = field(default_factory=deque)
    active: dict = field(default_factory=dict,repr=False)

    @property
    def terminal(self):
        return self.state in {'completed','cancelled','failed','paused_needs_reconnect'}


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

    def record(self,job):
        return {'version':1,'job_id':job.id,'owner_id':job.owner_id,'created_at':job.created_at,
                'state':job.state,'mode':'dry_run','targets':[t.public() for t in job.targets]}

    def persist(self,job,index=None):
        try:
            if index is None: self.checkpoints.write(self.record(job))
            else:
                first=(index//256)*256
                record={'version':1,'job_id':job.id,'owner_id':job.owner_id,'created_at':job.created_at,
                        'state':job.state,'mode':'dry_run','targets':[t.public() for t in job.targets[first:first+256]]}
                self.checkpoints.write_progress(record,index//256)
        except (ValueError,OSError,TypeError):
            job.state='failed'
            for target in job.targets:
                if target.status=='processing':
                    target.status='uncertain'
                    target.reasons=list(dict.fromkeys(target.reasons+['checkpoint_failed']))
            for task in job.active.values(): task.cancel()
            raise AppError('checkpoint_failed',503) from None

    def sync_sessions(self):
        now=time.monotonic()
        for job in list(self.jobs.values()):
            if not job.terminal and (job.session_id not in self.store.sessions or job.expires_at<=now):
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

    def add(self,browser_id,sid,targets,duplicates,pages):
        self.sync_sessions()
        if self.store.session(browser_id,sid) is None:
            raise AppError('session_expired',401)
        if self.stopping or not targets:
            raise AppError('invalid_selection')
        total=sum(len(j.targets) for j in self.jobs.values())+len(targets)
        if len(self.jobs)>=self.settings.dry_max_jobs or total>self.settings.dry_max_targets or total*2048>self.settings.dry_max_bytes:
            raise AppError('dry_capacity',503)
        owner=self.owners.setdefault(sid,uuid.uuid4().hex)
        job=DryJob(uuid.uuid4().hex,owner,browser_id,sid,time.time(),time.monotonic()+self.settings.dry_job_ttl,targets,duplicates,pages)
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
            if not items: continue
            self.rotation.append(page)
            for _ in range(len(items)):
                job_id,index=items.popleft()
                job=self.jobs[job_id]
                target=job.targets[index]
                if job.terminal or target.status!='queued': continue
                if target.canonical in self.locks or target.retry_at>time.monotonic():
                    items.append((job_id,index))
                    continue
                self.locks.add(target.canonical)
                target.status='processing'
                job.state='running'
                try: self.persist(job,index)
                except AppError:
                    self.locks.discard(target.canonical)
                    continue
                return job,target,index
        return None

    def release(self,job,target,index,outcome):
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
        self.locks.discard(target.canonical)
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

    def snapshot(self,job,offset=0):
        targets=job.targets
        return {'job_id':job.id,'mode':'dry_run','state':job.state,'terminal':job.terminal,
                'safe_for_delete':False,'selected':len(targets)+job.duplicates,'unique':len(targets),
                'valid':sum(t.eligible for t in targets),'ambiguous':sum(not t.eligible for t in targets),
                'duplicates':job.duplicates,'checked':sum(t.status=='dry_run_checked' for t in targets),
                'counts':{s:sum(t.status==s for t in targets) for s in ('queued','processing','dry_run_checked','failed','uncertain','cancelled')},
                'pages':[{'page_id':pid,'name':name} for pid,name in job.pages.items()],
                'items':[t.public() for t in targets[offset:offset+50]],'offset':offset,'total':len(targets),
                'events':list(job.events),'message':'Dry run hanya memeriksa metadata lokal; tidak ada konten dihapus.'}
