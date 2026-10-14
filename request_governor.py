"""One process-wide spacing/concurrency gate, not a Meta quota."""
import asyncio
import time
import math
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from errors import AppError


class RequestGovernor:
    def __init__(self, store, settings):
        self.store,self.settings=store,settings
        self.interval=settings.graph_request_interval
        self.usage_known=False
        self.next_at=0
        self.last_dispatch_at=0
        self.lock=asyncio.Lock()
        self.slots=asyncio.Semaphore(settings.graph_request_concurrency)
        self.usage_states={}
        self.low_observations=0
        self.last_usage=None
        self.saturation_paused=set()

    def observe_usage(self, percent, *, bucket='direct', source='local_policy', metrics=None, adapt=True, complete=True):
        if type(percent) not in {int,float} or not math.isfinite(percent) or not 0<=percent<=10000: return
        self.usage_known=True
        now=time.monotonic()
        self.usage_states={key:value for key,value in self.usage_states.items() if value['seen']>now-300}
        self.saturation_paused.intersection_update(self.usage_states)
        if bucket not in self.usage_states and len(self.usage_states)>=self.settings.max_token_entries:
            # Unknown capacity is not permission to speed up.
            self.low_observations=0
            if percent>=self.settings.usage_threshold: self.interval=self.settings.graph_max_interval
            return
        self.usage_states[bucket]={'percent':percent,'seen':now,'source':source,'metrics':dict(metrics or {}),'complete':complete,'observed_at':datetime.now(timezone.utc).isoformat()}
        if percent<min(self.settings.usage_threshold,90): self.saturation_paused.discard(bucket)
        if not adapt: return
        peak=max(self.usage_states.values(),key=lambda value:value['percent'])
        self.last_usage={key:peak[key] for key in ('source','percent','metrics','observed_at')}
        worst=max(value['percent'] for value in self.usage_states.values())
        before=self.interval
        if worst>=self.settings.usage_threshold:
            self.low_observations=0
            self.interval=min(self.settings.graph_max_interval,max(before*1.25,self.settings.graph_request_interval*6))
        elif worst>=50:
            self.low_observations=0
            factor=4 if worst>=80 else 2
            self.interval=max(before,min(self.settings.graph_max_interval,self.settings.graph_request_interval*factor))
        elif all(value['complete'] for value in self.usage_states.values()):
            self.low_observations+=1
            if self.low_observations>=3:
                self.interval=max(self.settings.graph_request_interval,before*.8)
                self.low_observations=0
        else:
            self.low_observations=0
        if self.next_at>0: self.next_at+=self.interval-before

    def usage_unavailable(self):
        self.usage_known=False
        self.low_observations=0

    def rate_limited(self):
        before=self.interval
        self.interval=max(before,min(self.settings.graph_max_interval,self.settings.graph_request_interval*6))
        self.low_observations=0
        if self.next_at>0: self.next_at+=self.interval-before

    def pacing_summary(self):
        return {'interval_seconds':round(self.interval,6),'usage_known':self.usage_known,
                'last_usage':dict(self.last_usage) if self.last_usage else None}

    async def _acquire(self, primitive, deadline):
        if not primitive.locked():
            await primitive.acquire()
            return
        task=asyncio.create_task(primitive.acquire())
        try:
            done,_=await asyncio.wait({task},timeout=max(0,deadline-time.monotonic()))
            if not done:
                raise AppError('timeout',504)
            task.result()
        except BaseException:
            if not task.done(): task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            if not task.cancelled() and task.exception() is None and task.result():
                primitive.release()
            raise

    async def pace_dispatch(self, scope, deadline, before_send=None):
        """Recheck spacing after local preflight, immediately before the HTTP call."""
        await self._acquire(self.lock, deadline)
        try:
            while time.perf_counter() < self.last_dispatch_at + self.interval:
                wait=self.last_dispatch_at+self.interval-time.perf_counter()
                if time.monotonic()+wait >= deadline:
                    raise AppError('timeout',504)
                await asyncio.sleep(wait)
            self.store.check_cooldown(scope)
            if time.monotonic() >= deadline:
                raise AppError('timeout',504)
            value=before_send() if before_send is not None else None
            if time.monotonic() >= deadline:
                raise AppError('timeout',504)
            # Preflight can write a checkpoint synchronously. Measure after it completes.
            self.last_dispatch_at=time.perf_counter()
            self.next_at=max(self.next_at,self.last_dispatch_at+self.interval)
            return value
        finally:
            self.lock.release()

    @asynccontextmanager
    async def slot(self, scope, deadline):
        self.store.check_cooldown(scope)
        remaining=deadline-time.monotonic()
        if remaining <= 0:
            raise AppError('timeout',504)
        await self._acquire(self.slots,deadline)
        try:
            remaining=deadline-time.monotonic()
            if remaining <= 0:
                raise AppError('timeout',504)
            await self._acquire(self.lock,deadline)
            try:
                while time.perf_counter() < self.next_at:
                    wait=self.next_at-time.perf_counter()
                    if time.monotonic()+wait >= deadline:
                        raise AppError('timeout',504)
                    await asyncio.sleep(wait)
                self.store.check_cooldown(scope)
                if time.monotonic() >= deadline:
                    raise AppError('timeout',504)
                self.next_at=time.perf_counter()+self.interval
            finally:
                self.lock.release()
            yield
        finally:
            self.slots.release()
