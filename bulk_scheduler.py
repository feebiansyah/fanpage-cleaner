"""Fixed workers and shared governor/cooldown for dry run and confirmed Page Posts."""
import asyncio
import time
import math
from contextlib import suppress
from bulk_executor import DryRunExecutor,Outcome
from errors import AppError


class BulkScheduler:
    def __init__(self,queue,executor=None):
        self.queue=queue
        self.executor=executor or DryRunExecutor()  # Test-only Python injection; no HTTP/config selector.
        self.workers=[]
        self.stopping=False
        self.real_executor=None

    async def start(self):
        self.stopping=False
        self.queue.stopping=False
        self.queue.recovered_count=len(self.queue.checkpoints.restore())
        self.workers=[asyncio.create_task(self.worker()) for _ in range(self.queue.settings.dry_workers)]

    async def worker(self):
        q=self.queue
        while not self.stopping:
            picked=q.claim()
            if picked is None:
                q.changed.clear()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(q.changed.wait(),.1)
                continue
            job,target,index=picked
            async def execute():
                if job.mode=='delete_page_posts':
                    return await self.real_executor.check(job,target,index)
                if job.terminal: return Outcome('cancelled','dispatch_cancelled')
                session,page=q.store.require_page(job.browser_id,job.session_id,target.page_id)
                scope=q.store.token_key(page.access_token)
                # Drop Page/session references before awaits; no token in executor/state.
                del page,session
                async with q.store.governor.slot(scope,min(job.expires_at,q.store.sessions[job.session_id].expires_at)):
                    if job.terminal: return Outcome('cancelled','dispatch_cancelled')
                    q.store.require_page(job.browser_id,job.session_id,target.page_id)
                    target.attempts+=1
                    q.persist(job,index)
                    outcome=await self.executor.check(target)
                    allowed_reasons={'local_identity_checked','timeout_after_send','timeout_before_send','token_invalid','permission_denied',
                        'rate_limited','transient_after_send','temporary_before_send','success_semantics_unverified','response_unverified','dispatch_cancelled'}
                    if (not isinstance(outcome,Outcome) or outcome.status not in {'dry_run_checked','failed','uncertain','cancelled'}
                            or outcome.reason not in allowed_reasons or type(outcome.temporary) is not bool
                            or type(outcome.retry_after) not in {int,float} or not math.isfinite(outcome.retry_after) or outcome.retry_after<0):
                        return Outcome('uncertain','executor_contract_invalid')
                    if outcome.reason=='rate_limited':
                        q.store.cooldown(scope,max(outcome.retry_after,q.settings.cooldown_seconds),True)
                    return outcome
            task=asyncio.create_task(execute())
            job.active[target.canonical]=task
            try:
                outcome=await task
            except asyncio.CancelledError:
                outcome=Outcome('uncertain','interrupted_processing') if job.mode!='delete_page_posts' or target.diagnostic.get('request_started') else Outcome('blocked' if q.block_reason(target) else 'cancelled',q.block_reason(target) or job.stop_reason or 'dispatch_cancelled')
            except AppError as error:
                if error.code in {'cooldown','rate_limited'}:
                    outcome=Outcome('failed',error.code,True,error.retry_after or 1)
                elif error.code=='session_expired':
                    q.pause(job,'session_expired')
                    outcome=Outcome('uncertain','session_expired')
                else: outcome=Outcome('failed','local_check_failed')
            except Exception:
                outcome=Outcome('uncertain','executor_error')
            q.release(job,target,index,outcome)

    async def shutdown(self):
        self.stopping=True
        self.queue.stopping=True
        for job in self.queue.jobs.values():
            if not job.terminal: self.queue.pause(job,'shutdown')
        for task in self.workers:
            with suppress(asyncio.CancelledError): await task
