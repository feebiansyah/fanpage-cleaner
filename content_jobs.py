"""Owner-scoped, bounded, resumable audit work. No token copies in job state."""

import asyncio
import json
import math
import secrets
import time
from contextlib import suppress

from content import ContentLoader, Source, LIMITATIONS, AUDIT_WARNING, WIB, identifier
from errors import AppError


class StepBoundary(Exception):
    def __init__(self, reason):
        self.reason = reason


class ContentJob:
    def __init__(self, browser_id, session_id, page_id, limit, session_expiry, settings):
        self.id = secrets.token_urlsafe(32)
        self.browser_id, self.session_id, self.page_id, self.limit = browser_id, session_id, page_id, limit
        now = time.monotonic()
        self.deadline = min(session_expiry, now + settings.posts_job_timeout)
        self.idle_expires_at = min(session_expiry, now + settings.content_job_idle_ttl)
        self.sources = [Source('published_posts')]
        self.story_ids = set()  # Explicit known IDs only; never acquired from a Story edge.
        self.limitations = []
        self.errors = []
        self.revision = 0
        self.steps = 0
        self.next_source = 0
        self.state = 'running'
        self.terminal = False
        self.task = None
        self.step_stop_reason = 'not_started'
        self.resume_at = 0
        self.interruptions = {}
        self.candidate_bytes = 0
        self.reserved_items = 2 * limit + 200
        self.reserved_bytes = self.reserved_items * 8192 + 65536

    @property
    def requests(self):
        return sum(source.requests for source in self.sources)


class ContentJobs:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.entries = {}
        self.loader = None  # Bound to the one lifespan-owned Graph client.

    def bind(self, graph):
        self.loader = ContentLoader(graph, self.store, self.settings)

    def cleanup(self):
        now = time.monotonic()
        for job in list(self.entries.values()):
            if job.session_id not in self.store.sessions:
                self.discard(job)
            elif not job.terminal and job.deadline <= now and (job.task is None or job.task.done()):
                self._finish(job, 'partial', 'job_timeout')
                self._save(job)
                job.idle_expires_at = min(self.store.sessions[job.session_id].expires_at, now+self.settings.content_job_idle_ttl)
            elif job.idle_expires_at <= now and (job.task is None or job.task.done()):
                self.discard(job)

    def discard(self, job):
        job.terminal = True
        if job.task is not None and not job.task.done():
            job.task.cancel()
        self.store.finish_load(job.browser_id, job.session_id)
        self.entries.pop(job.id, None)
        for source in job.sources:
            source.candidates.clear()
            source.cursor = None
            source.seen_cursors.clear()
            source.seen_pages.clear()
        job.story_ids.clear()

    def discard_session(self, sid):
        for job in list(self.entries.values()):
            if job.session_id == sid:
                self.discard(job)

    def clear(self):
        for job in list(self.entries.values()):
            self.discard(job)

    def owned(self, browser_id, session_id, job_id):
        self.store.session(browser_id, session_id)  # Runs expiry cleanup without extending TTL.
        job = self.entries.get(job_id)
        if job is None or job.browser_id != browser_id or job.session_id != session_id:
            raise AppError('job_not_found', 404)
        self.store.require_page(browser_id, session_id, job.page_id)
        if len(job.sources) != 1 or job.sources[0].edge != 'published_posts':
            self.discard(job)
            raise AppError('audit_scope_incompatible', 409)
        return job

    def start(self, browser_id, session_id, page_id, limit):
        session, page = self.store.require_page(browser_id, session_id, page_id)
        if not page.access_token:
            raise AppError('page_token_missing', 403)
        for job in self.entries.values():
            if job.session_id == session_id and not job.terminal:
                if job.page_id == page_id and job.limit == limit:
                    return job  # Lost start response / double submission: one admission.
                raise AppError('load_busy', 409)
        for old in list(self.entries.values()):
            if old.session_id == session_id:
                self.discard(old)
        job = ContentJob(browser_id, session_id, page_id, limit, session.expires_at, self.settings)
        if (len(self.entries) >= self.settings.max_content_jobs
                or sum(j.reserved_items for j in self.entries.values()) + job.reserved_items > self.settings.max_content_job_items
                or sum(j.reserved_bytes for j in self.entries.values()) + job.reserved_bytes > self.settings.max_content_job_bytes):
            raise AppError('audit_capacity', 503)
        self.store.begin_load(browser_id, session_id, self.store.token_key(page.access_token), limit, page_id)
        self.entries[job.id] = job
        return job

    def _items(self, job):
        items, unlinked = self.loader._merge(job.sources)
        return [item for item in items if item['object_id'] not in job.story_ids
                and not self.store.audits.is_removed(item, job.page_id)], unlinked

    def snapshot(self, job):
        items, unlinked = self._items(job)
        items.sort(key=lambda i: (i['_date'] is None, -i['_date'].timestamp() if i['_date'] else 0, i['key']))
        selected = items[:job.limit]
        for item in selected:
            date = item.pop('_date')
            item.pop('_linked_post', None)
            item.pop('_story_ids', None)
            item['published_at'] = date.isoformat() if date else None
            item['published_wib'] = date.astimezone(WIB).strftime('%d/%m/%Y %H:%M:%S WIB') if date else 'Tanggal tidak tersedia'
            item['selectable'] = True  # Audit only, independent of Story/delete eligibility.
        summaries = [s.summary() for s in job.sources]
        for summary in summaries:
            if summary['status'] is None:
                summary['status'] = 'running'
        limitations = list(dict.fromkeys(LIMITATIONS + job.limitations + (['cross_source_identity_unverified'] if unlinked else [])))
        dated = sum(item['published_at'] is not None for item in selected)
        result = {'job_id': job.id, 'revision': job.revision, 'terminal': job.terminal,
                  'page_id': job.page_id, 'limit': job.limit, 'state': job.state,
                  'step_stop_reason': job.step_stop_reason, 'request_count': job.requests, 'steps': job.steps,
                  'resume_after_seconds': max(0, math.ceil(job.resume_at-time.monotonic())),
                  'job_remaining_seconds': max(0, int(job.deadline-time.monotonic())),
                  'found': len(items), 'displayed': len(selected), 'items': selected,
                  'dated_count': dated, 'undated_count': len(selected)-dated,
                  'scanned': sum(s.received for s in job.sources),
                  'excluded_stories': sum(s.excluded for s in job.sources),
                  'sources': summaries, 'errors': list(job.errors),
                  'sources_exhausted': all(s.exhausted for s in job.sources),
                  'limit_reached': len(items) >= job.limit, 'retrieval_complete': job.terminal and not job.errors and job.state not in {'partial','cancelled'},
                  'coverage_complete': False, 'latest_guaranteed': False, 'safe_for_delete': False,
                  'bulk_selectable': bool(selected), 'selection_purpose': 'audit_only',
                  'story_exclusion': 'scope_filtered',
                  'limitations': limitations,
                  'warning': AUDIT_WARNING + ' Pilihan untuk audit; tindakan hapus memerlukan pemeriksaan terpisah.'}
        if job.errors:
            result['error'] = job.errors[0]
        if job.terminal:
            cached = self.store.audits.get(job.session_id, job.page_id)
            session = self.store.sessions.get(job.session_id)
            result['cache_ttl_seconds'] = cached['cache_ttl_seconds'] if cached else max(0, min(int(session.expires_at-time.monotonic()),int(self.store.settings.audit_ttl))) if session else 0
        return result

    def _save(self, job):
        self.store.audits.put(job.session_id,self.snapshot(job),self.store.sessions[job.session_id].expires_at)
        self.store.finish_load(job.browser_id,job.session_id)

    def _finish(self, job, state, reason):
        job.terminal, job.state, job.step_stop_reason = True, state, reason
        for source in job.sources:
            if source.status in {None, 'cooldown'}:
                source.status = 'limit_reached' if state == 'limit_reached' else 'cancelled' if state in {'partial','cancelled'} else 'not_needed'
                source.stop_reason = reason

    def _failure(self, job, source, error):
        reason = getattr(error, 'stop_reason', 'meta_request_timeout' if error.code == 'timeout' else error.code)
        source.status, source.stop_reason = 'failed', reason
        diagnostic = {**error.payload()['error'], 'source': source.edge, 'stop_reason': reason}
        diagnostic['message'] = {'timeout':'Request sumber Meta timeout setelah retry terbatas; halaman diterima tetap tersedia.',
                                 'pagination_incomplete':'Pagination sumber terhenti; halaman diterima tetap tersedia.'}.get(error.code, diagnostic['message'])
        job.errors.append(diagnostic)
        # Actual failed source is terminal; no infinite retry on next step.
        if error.code == 'token_invalid':
            self._finish(job, 'partial', 'token_invalid')

    def _choose(self, job):
        items, _ = self._items(job)
        if len(items) >= job.limit:
            self._finish(job, 'partial' if job.errors else 'limit_reached', 'unique_limit')
            return None
        source = job.sources[0]
        if source.status is None and not source.exhausted:
            return source
        self._finish(job, 'partial' if job.errors else 'empty' if not items else 'source_exhausted', 'source_exhausted')
        return None

    @staticmethod
    def _candidate_size(candidate):
        return len(json.dumps(candidate,default=str,ensure_ascii=False).encode())+128

    def _admit_candidate(self, job, source, candidate):
        if candidate['object_id'] in source.candidates:
            return
        size=self._candidate_size(candidate)
        if sum(len(s.candidates) for s in job.sources) >= job.reserved_items or job.candidate_bytes+size > job.reserved_bytes:
            raise AppError('audit_capacity',503)
        source.candidates[candidate['object_id']]=candidate
        job.candidate_bytes+=size

    async def _page(self, job, source, attempts):
        session, page = self.store.require_page(job.browser_id, job.session_id, job.page_id)
        hidden = [session.user_token] + [p.access_token for p in session.pages if p.access_token]
        token_key = self.store.token_key(page.access_token)
        def guard():
            self.store.require_page(job.browser_id, job.session_id, job.page_id)
            if job.terminal:
                raise AppError('load_cancelled', 409)
        def attempt():
            if job.requests >= self.settings.posts_job_requests:
                raise StepBoundary('job_request_limit')
            if job.requests-attempts >= self.settings.posts_step_requests:
                raise StepBoundary('step_request_limit')
            if getattr(source,'attempts_on_page',0) >= 3:
                raise StepBoundary('page_retry_limit')
            source.attempts_on_page = getattr(source,'attempts_on_page',0)+1
        found = len(self._items(job)[0])
        remaining = max(1, job.limit-found)
        share = remaining
        size = min(self.settings.page_size, share)
        data, next_error = await self.loader._fetch(source, page, token_key, hidden, size, job.deadline, guard, attempt)
        source.attempts_on_page = 0
        job.interruptions.pop(source.edge, None)
        for item in data:
            candidate, skip_reason = self.loader._normalize_with_reason(item, source.edge, job.page_id, hidden)
            if candidate is None:
                source.skipped += 1
                source.skip_reasons[skip_reason] = source.skip_reasons.get(skip_reason, 0) + 1
                if skip_reason == 'identified_story':
                    source.excluded += 1
            else:
                self._admit_candidate(job, source, candidate)
        if source.exhausted:
            source.status, source.stop_reason = 'source_exhausted', 'source_exhausted'
        if next_error:
            raise next_error

    async def _run_step(self, job):
        attempts = job.requests
        step_deadline = min(job.deadline, time.monotonic()+self.settings.posts_step_timeout)
        job.steps += 1
        job.state, job.step_stop_reason = 'running', 'step_request_limit'
        try:
            if job.steps > self.settings.posts_job_steps:
                raise StepBoundary('job_step_limit')
            while not job.terminal:
                if time.monotonic() >= job.deadline:
                    raise StepBoundary('job_timeout')
                source = self._choose(job)
                if source is None:
                    break
                remaining = step_deadline-time.monotonic()
                if remaining <= 0:
                    raise StepBoundary('step_timeout')
                try:
                    await asyncio.wait_for(self._page(job, source, attempts), remaining)
                except StepBoundary as boundary:
                    if boundary.reason != 'page_retry_limit':
                        raise
                    error=AppError('upstream_error',502)
                    error.stop_reason='request_retry_limit'
                    self._failure(job,source,error)
                except asyncio.TimeoutError:
                    job.interruptions[source.edge] = job.interruptions.get(source.edge, 0)+1
                    source.stop_reason = 'step_timeout'
                    if job.interruptions[source.edge] >= 3:
                        error=AppError('timeout',504)
                        error.stop_reason='step_timeout_limit'
                        self._failure(job, source, error)
                    raise StepBoundary('job_timeout' if time.monotonic() >= job.deadline else 'step_timeout') from None
                except AppError as error:
                    if error.code in {'session_expired','load_cancelled'}:
                        raise
                    if error.code in {'cooldown','rate_limited'}:
                        job.state, job.step_stop_reason = 'cooldown', error.code
                        job.resume_at = max(time.monotonic()+max(1,error.retry_after or 1), self.store.global_cooldown_until,
                                            self.store.token_cooldowns.get(self.store.token_key(self.store.require_page(job.browser_id, job.session_id, job.page_id)[1].access_token),0))
                        source.status, source.stop_reason = 'cooldown', error.code
                        break
                    self._failure(job, source, error)
                if job.requests-attempts >= self.settings.posts_step_requests:
                    # Finish locally if the last page already satisfied the policy.
                    self._choose(job)
                    break
        except StepBoundary as boundary:
            job.step_stop_reason = boundary.reason
            if boundary.reason.startswith('job_'):
                self._finish(job,'partial',boundary.reason)
        except (AppError,asyncio.CancelledError):
            raise
        except Exception:
            self._finish(job,'partial','internal_error')
            job.errors.append({'code':'internal_error','message':'Kegagalan internal pekerjaan; halaman diterima tetap tersedia.',
                               'source':'local_job','stop_reason':'internal_error'})
        finally:
            job.revision += 1
            job.idle_expires_at = min(self.store.sessions[job.session_id].expires_at if job.session_id in self.store.sessions else time.monotonic(), max(time.monotonic(),job.resume_at)+self.settings.content_job_idle_ttl)
        self.store.require_page(job.browser_id,job.session_id,job.page_id)
        if job.terminal:
            self._save(job)
        return self.snapshot(job)

    async def step(self, job, revision):
        if len(job.sources) != 1 or job.sources[0].edge != 'published_posts':
            self.discard(job)
            raise AppError('audit_scope_incompatible', 409)
        # Body reading awaited after route ownership lookup: recheck revocation.
        if self.entries.get(job.id) is not job:
            raise AppError('load_cancelled',409)
        self.store.require_page(job.browser_id,job.session_id,job.page_id)
        if type(revision) is not int or revision < 0 or revision > job.revision:
            raise AppError('invalid_revision',409)
        if job.terminal or revision < job.revision:
            return self.snapshot(job)
        session,page=self.store.require_page(job.browser_id,job.session_id,job.page_id)
        if job.task is None or job.task.done():
            try:
                self.store.check_cooldown(self.store.token_key(page.access_token))
            except AppError as error:
                job.state='cooldown'
                job.step_stop_reason='cooldown'
                job.resume_at=time.monotonic()+error.retry_after
                job.idle_expires_at=min(session.expires_at,job.resume_at+self.settings.content_job_idle_ttl)
                return self.snapshot(job)
            for source in job.sources:
                if source.status == 'cooldown':
                    source.status, source.stop_reason = None,None
            job.resume_at=0
            job.task=asyncio.create_task(self._run_step(job))
            self.store.browsers[job.browser_id].load_task=job.task
        try:
            return await asyncio.shield(job.task)
        except asyncio.CancelledError:
            if job.id not in self.entries:
                raise AppError('session_expired' if session.expires_at <= time.monotonic() else 'load_cancelled',401 if session.expires_at <= time.monotonic() else 409) from None
            if job.terminal:
                return self.snapshot(job)
            raise
        except AppError:
            raise
        except Exception:
            self._finish(job,'partial','internal_error')
            self.store.finish_load(job.browser_id,job.session_id)
            raise AppError('internal_error',500) from None

    async def stop(self, job):
        if not job.terminal:
            self._finish(job,'cancelled','user_stopped')
            if job.task is not None and not job.task.done():
                job.task.cancel()
                with suppress(asyncio.CancelledError,AppError):
                    await job.task
            self.store.require_page(job.browser_id,job.session_id,job.page_id)
            job.revision += 1
            self._save(job)
        return self.snapshot(job)
