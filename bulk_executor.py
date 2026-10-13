"""Local dry-run executor and pure future outcome design; no HTTP mutation code."""
import asyncio
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
