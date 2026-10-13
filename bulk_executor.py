"""Local dry-run executor and pure future outcome design; no HTTP mutation code."""
import asyncio
import time
from bulk_targets import suppression_ids
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    temporary: bool = False
    retry_after: float = 0


class Executor(Protocol):
    async def check(self, target) -> Outcome: ...


class DryRunExecutor:
    async def check(self, target):
        await asyncio.sleep(0)
        return Outcome('dry_run_checked','local_identity_checked')


class PagePostExecutor:
    """Only explicitly confirmed queue snapshots; shares the one-send Graph implementation."""
    def __init__(self,graph,queue):
        self.graph,self.queue=graph,queue

    async def check(self,job,target,index):
        from content_cache import invalidate_confirmed_post
        from errors import AppError
        from graph_api import delete_internal_code
        from single_delete import OBSERVATION_FIELDS
        q=self.queue
        started=time.monotonic()
        observation={'phase':'graph_dispatch','request_started':False}
        target.diagnostic=observation
        result=None
        def token_at_dispatch():
            if job.stop_requested or job.terminal:
                raise AppError('bulk_dispatch_stopped',409)
            reason=q.block_reason(target)
            if reason: raise AppError('bulk_token_blocked' if reason=='token_invalid' else reason if reason in {'permission_circuit_open','permission_circuit_capacity'} else 'bulk_page_blocked',403)
            _,page=q.store.require_page(job.browser_id,job.session_id,target.page_id)
            if not page.access_token: raise AppError('page_token_missing',403)
            identities={target.delete_target,*target.details.get('related_video_ids',[])}
            if any(q.store.audits.removed_posts.get((target.page_id,node),0)>time.monotonic() for node in identities):
                raise AppError('bulk_target_deleted',409)
            target.attempts=1
            q.persist(job,index)  # Refuse dispatch if checkpoint cannot record processing.
            return page.access_token
        try:
            session,page=q.store.require_page(job.browser_id,job.session_id,target.page_id)
            if not page.access_token: raise AppError('page_token_missing',403)
            target.scope=q.store.token_key(page.access_token)
            deadline=min(job.expires_at,session.expires_at,started+q.settings.operation_timeout)
            del page,session
            result=await self.graph.delete_post_once(target.delete_target,target.scope,deadline,token_at_dispatch,observation)
            status,reason=result['outcome'],result['reason']
            if result.get('request_started'):
                q.record_delete_outcome(target,reason)
            if status=='succeeded':
                warnings=[invalidate_confirmed_post(q.store,target.page_id,node) for node in sorted(suppression_ids(target.delete_target,target.details))]
                target.cleanup_warning=next((warning for warning in warnings if warning),None)
                if target.cleanup_warning: target.reasons.append(target.cleanup_warning)
        except AppError as error:
            reason=q.block_reason(target) or error.code
            status='blocked' if reason in {'token_invalid','permission_denied','permission_circuit_open','permission_circuit_capacity','page_token_missing','bulk_target_deleted'} else 'cancelled' if job.stop_requested or reason in {'session_expired','bulk_dispatch_stopped'} else 'queued' if reason in {'cooldown','rate_limited'} and not observation.get('request_started') else 'failed'
            result={**observation,'outcome':status,'reason':reason,'retry_after':error.retry_after}
        except asyncio.CancelledError:
            reason=q.block_reason(target) or job.stop_reason or 'interrupted'
            status='uncertain' if observation.get('request_started') else 'blocked' if q.block_reason(target) else 'cancelled'
            result={**observation,'outcome':status,'reason':reason}
        except Exception as error:
            status,reason='uncertain','internal_error'
            result={**observation,'outcome':status,'reason':reason,'internal_code':delete_internal_code(error,observation['phase'])}
        finally:
            if result is not None:
                target.diagnostic={'operation':'delete_page_post','target_id':target.delete_target,
                    'outcome':result['outcome'],'reason':result['reason'],'duration_ms':round((time.monotonic()-started)*1000),
                    **{key:result.get(key,observation.get(key)) for key in ('http_status','error_code','error_subcode',*OBSERVATION_FIELDS)}}
        return Outcome(status,reason,False,result.get('retry_after') or 0)


def classify_future_response(status=None, error_code=None, *, timeout=False, sent=False, transient=False):
    """Never returns deletion success until official response semantics verified."""
    if timeout:
        return Outcome('uncertain','timeout_after_send') if sent else Outcome('failed','timeout_before_send',True)
    if error_code in {102,190} or status==401:
        return Outcome('failed','token_invalid')
    if error_code in {10,200} or status==403:
        return Outcome('failed','permission_denied')
    if status==429 or error_code in {4,17,32,613}:
        return Outcome('failed','rate_limited',True)
    if status in {500,502,503,504} or transient:
        return Outcome('uncertain','transient_after_send') if sent else Outcome('failed','temporary_before_send',True)
    if status is not None and 200<=status<300:
        return Outcome('uncertain','success_semantics_unverified')
    return Outcome('failed','response_unverified')
