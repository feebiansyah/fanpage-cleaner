"""One process-wide spacing/concurrency gate, not a Meta quota."""
import asyncio
import time
from contextlib import asynccontextmanager
from errors import AppError


class RequestGovernor:
    def __init__(self, store, settings):
        self.store,self.settings=store,settings
        self.interval=settings.graph_request_interval
        self.usage_known=False
        self.next_at=0
        self.lock=asyncio.Lock()
        self.slots=asyncio.Semaphore(settings.graph_request_concurrency)

    def observe_usage(self, percent):
        self.usage_known=True
        if percent >= 50:
            factor=4 if percent >= 80 else 2
            before=self.interval
            self.interval=max(self.interval,min(self.settings.graph_max_interval,self.settings.graph_request_interval*factor))
            if self.next_at>0:
                self.next_at+=self.interval-before

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
