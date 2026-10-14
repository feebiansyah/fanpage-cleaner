"""State per proses. Mutasi tanpa await agar admission/replacement atomik."""

import hashlib
import hmac
import math
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import Settings
from errors import AppError
from content_cache import AuditCache
from request_governor import RequestGovernor


@dataclass
class Page:
    id: str
    name: str
    access_token: str = field(repr=False)


@dataclass
class Session:
    browser_id: str = field(repr=False)
    user_token: str = field(repr=False)
    pages: list[Page] = field(repr=False)
    expires_at: float

    def metadata(self):
        # Treat upstream names as untrusted, including reflected secrets.
        secrets_to_hide = [self.user_token] + [page.access_token for page in self.pages if page.access_token]
        result = []
        for page in self.pages:
            name = page.name
            for secret in secrets_to_hide:
                name = name.replace(secret, "[disamarkan]")
            result.append({"id": page.id, "name": name})
        return result


@dataclass
class Browser:
    expires_at: float
    attempts: deque = field(default_factory=deque)
    busy: bool = False
    session_id: str | None = field(default=None, repr=False)
    reserved_slot: bool = False
    load_active: bool = False
    load_task: object = field(default=None, repr=False)
    load_session_id: str | None = field(default=None, repr=False)


class MemoryStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions: dict[str, Session] = {}
        self.browsers: dict[str, Browser] = {}
        self.token_cooldowns: dict[str, float] = {}
        self.global_cooldown_until = 0.0
        self.cooldown_records = {}  # Bounded safe metadata; keys are internal HMAC scopes.
        self.global_attempt_times = deque()
        self.active_jobs = 0
        self.reserved_slots = 0
        self._salt = secrets.token_bytes(32)
        self.audits = AuditCache(settings)
        self.content_jobs = None
        self.bulk_queue = None
        self.single_delete = None
        self.bulk_delete = None
        self.governor = RequestGovernor(self,settings)

    def cleanup(self):
        now = time.monotonic()
        for key in [key for key, session in self.sessions.items() if session.expires_at <= now]:
            del self.sessions[key]
        if self.single_delete is not None:
            self.single_delete.cleanup()
        if self.bulk_delete is not None:
            self.bulk_delete.cleanup()
        if self.content_jobs is not None:
            self.content_jobs.cleanup()
        if self.bulk_queue is not None:
            self.bulk_queue.sync_sessions()
        self.audits.cleanup(self.sessions)
        for key, browser in list(self.browsers.items()):
            if browser.expires_at <= now and not browser.busy and not browser.load_active:
                self.sessions.pop(browser.session_id, None)
                del self.browsers[key]
            else:
                self._trim(browser.attempts, now)
        self._trim(self.global_attempt_times, now)
        for key in [key for key, expiry in self.token_cooldowns.items() if expiry <= now]:
            del self.token_cooldowns[key]

    def _trim(self, attempts, now):
        while attempts and attempts[0] <= now - self.settings.local_window:
            attempts.popleft()

    def browser(self, browser_id: str | None):
        self.cleanup()
        return self.browsers.get(browser_id)

    def bootstrap(self, browser_id: str | None):
        browser = self.browser(browser_id)
        if browser is not None:
            browser.expires_at = time.monotonic() + self.settings.browser_ttl
            return browser_id, browser, False
        if len(self.browsers) >= self.settings.max_browser_entries:
            raise AppError("limiter_capacity", 503)
        browser_id = secrets.token_urlsafe(32)
        browser = Browser(time.monotonic() + self.settings.browser_ttl)
        self.browsers[browser_id] = browser
        return browser_id, browser, True

    def session(self, browser_id: str | None, session_id: str | None):
        browser = self.browser(browser_id)
        session = self.sessions.get(session_id)
        if (browser is None or session is None or browser.session_id != session_id
                or session.browser_id != browser_id):
            return None
        return session

    def token_key(self, token: str):
        return hmac.new(self._salt, token.encode("ascii"), hashlib.sha256).hexdigest()

    def known_tokens(self):
        # Temporary caller-owned set only; never persist or expose these credentials.
        tokens=set()
        for session in self.sessions.values():
            tokens.add(session.user_token)
            tokens.update(page.access_token for page in session.pages)
        tokens.discard('')
        return tokens

    def check_cooldown(self, token_key: str):
        until = max(self.global_cooldown_until, self.token_cooldowns.get(token_key, 0))
        remaining = math.ceil(until - time.monotonic())
        if remaining > 0:
            raise AppError("cooldown", 429, remaining)

    def cooldown(self, token_key: str, seconds: float, global_scope: bool = False, *, reason='local_policy', source='local_policy', usage=None, error_code=None):
        self.cleanup()
        expiry = time.monotonic() + seconds
        if global_scope:
            self.global_cooldown_until = max(self.global_cooldown_until, expiry)
        elif token_key in self.token_cooldowns or len(self.token_cooldowns) < self.settings.max_token_entries:
            self.token_cooldowns[token_key] = max(self.token_cooldowns.get(token_key, 0), expiry)
        else:
            # Never evict an active cooldown; conservatively stop all Graph work.
            self.global_cooldown_until = max(self.global_cooldown_until, expiry)
            global_scope=True
            reason,source='local_scope_capacity','local_policy'
        if global_scope: scope='global'
        else: scope=token_key
        allowed_reasons={'local_policy','local_scope_capacity','local_usage_saturated','retry_after','meta_http_429','meta_rate_limit'}
        allowed_sources={'local_policy','x-app-usage','x-business-use-case-usage','retry-after','http_status','error_code'}
        if reason not in allowed_reasons: reason='local_policy'
        if source not in allowed_sources: source='local_policy'
        if scope not in self.cooldown_records and len(self.cooldown_records)>=self.settings.max_token_entries+1:
            # Evict only inactive diagnostic history, never a live gate.
            inactive=[key for key in self.cooldown_records if key!='global' and self.token_cooldowns.get(key,0)<=time.monotonic()]
            if inactive: self.cooldown_records.pop(inactive[0],None)
        if scope in self.cooldown_records or len(self.cooldown_records)<self.settings.max_token_entries+1:
            records=self.cooldown_records.setdefault(scope,{})
            key=(reason,source,error_code if type(error_code) is int else None)
            if key not in records and len(records)>=4:
                records.pop(min(records,key=lambda k:records[k]['until']),None)
            previous=records.get(key)
            if previous is None or expiry>previous['until']:
                numeric=usage if type(usage) in {int,float} and math.isfinite(usage) and 0<=usage<=10000 else None
                try: expires_at=datetime.fromtimestamp(time.time()+max(0,seconds),timezone.utc).isoformat()
                except (OverflowError,OSError,ValueError): expires_at=None
                records[key]={'until':expiry,'reason':reason,'source':source,'scope':'global' if global_scope else 'token',
                    'usage':numeric,'error_code':error_code if type(error_code) is int and 0<=error_code<=2147483647 else None,
                    'policy':'meta' if reason in {'retry_after','meta_http_429','meta_rate_limit'} else 'local',
                    'expires_at':expires_at}

    def cooldown_summary(self, scopes):
        now=time.monotonic()
        candidates=[]
        for key in {'global',*scopes}:
            gate=self.global_cooldown_until if key=='global' else self.token_cooldowns.get(key,0)
            if gate<=now: continue
            for record in self.cooldown_records.get(key,{}).values():
                if now<record['until']<=gate:
                    candidates.append(record)
        if not candidates: return None
        priority={'meta_http_429':3,'meta_rate_limit':2,'retry_after':1}
        chosen=max(candidates,key=lambda r:(r['until'],priority.get(r['reason'],0),r['error_code'] is not None))
        return {k:v for k,v in chosen.items() if k!='until'}|{'remaining_seconds':math.ceil(chosen['until']-now)}

    def last_cooldown_summary(self,scopes):
        records=[record for key in {'global',*scopes} for record in self.cooldown_records.get(key,{}).values()]
        if not records: return None
        chosen=max(records,key=lambda record:record['until'])
        active=self.cooldown_summary(scopes)
        return {k:v for k,v in chosen.items() if k!='until'}|{'active':active is not None,'remaining_seconds':active['remaining_seconds'] if active else 0}

    def begin(self, browser_id: str | None, session_id: str | None, token_key: str):
        browser = self.browser(browser_id)
        if browser is None:
            raise AppError("browser_required", 409)
        if browser.busy:
            raise AppError("connection_busy", 409)
        if browser.load_active:
            raise AppError("load_busy", 409)
        self.check_cooldown(token_key)
        now = time.monotonic()
        for attempts, maximum in [(browser.attempts, self.settings.local_attempts),
                                  (self.global_attempt_times, self.settings.global_attempts)]:
            if len(attempts) >= maximum:
                wait = math.ceil(attempts[0] + self.settings.local_window - now)
                raise AppError("local_rate_limit", 429, max(1, wait))
        if self.active_jobs >= self.settings.max_jobs:
            raise AppError("job_capacity", 503)
        replacing = self.session(browser_id, session_id) is not None
        # Existing sessions also reserve a slot: expiry during await cannot overfill memory.
        replacement_jobs = sum(b.busy and not b.reserved_slot and b.session_id not in self.sessions
                               for b in self.browsers.values())
        effective_sessions = len(self.sessions) + self.reserved_slots + replacement_jobs
        if not replacing and effective_sessions >= self.settings.max_sessions:
            raise AppError("session_capacity", 503)
        browser.attempts.append(now)
        self.global_attempt_times.append(now)
        browser.busy = True
        browser.reserved_slot = not replacing
        if browser.reserved_slot:
            self.reserved_slots += 1
        self.active_jobs += 1
        browser.expires_at = now + self.settings.browser_ttl

    def finish(self, browser_id: str):
        browser = self.browsers.get(browser_id)
        if browser and browser.busy:
            self.active_jobs -= 1
            if browser.reserved_slot:
                self.reserved_slots -= 1
            browser.reserved_slot = False
            browser.busy = False

    def replace(self, browser_id: str, user_token: str, pages: list[Page]):
        browser = self.browsers[browser_id]
        if self.content_jobs is not None:
            self.content_jobs.discard_session(browser.session_id)
        self.audits.discard(browser.session_id)
        self.sessions.pop(browser.session_id, None)
        session_id = secrets.token_urlsafe(32)
        session = Session(browser_id, user_token, pages, time.monotonic() + self.settings.session_ttl)
        self.sessions[session_id] = session
        browser.session_id = session_id
        browser.expires_at = time.monotonic() + self.settings.browser_ttl
        return session_id, session

    def disconnect(self, browser_id: str | None, session_id: str | None):
        browser = self.browser(browser_id)
        if browser and browser.busy:
            raise AppError("connection_busy", 409)
        if self.session(browser_id, session_id) is not None:
            if self.bulk_queue is not None:
                self.bulk_queue.pause_session(session_id,'session_disconnected')
            if self.content_jobs is not None:
                self.content_jobs.discard_session(session_id)
            if browser.load_task is not None:
                browser.load_task.cancel()
            self.audits.discard(session_id)
            del self.sessions[session_id]
            browser.session_id = None

    def require_page(self, browser_id, session_id, page_id):
        session = self.session(browser_id, session_id)
        if session is None:
            raise AppError("session_expired", 401)
        page = next((page for page in session.pages if page.id == page_id), None)
        if page is None:
            raise AppError("page_not_owned", 403)
        return session, page

    def begin_load(self, browser_id, session_id, token_key, limit, page_id=None):
        browser = self.browser(browser_id)
        if browser is None:
            raise AppError("session_expired", 401)
        if browser.busy:
            raise AppError("connection_busy", 409)
        if browser.load_active:
            raise AppError("load_busy", 409)
        self.check_cooldown(token_key)
        now = time.monotonic()
        for attempts, maximum in [(browser.attempts, self.settings.local_attempts),
                                  (self.global_attempt_times, self.settings.global_attempts)]:
            if len(attempts) >= maximum:
                wait = math.ceil(attempts[0] + self.settings.local_window - now)
                raise AppError("local_rate_limit", 429, max(1, wait))
        if self.active_jobs >= self.settings.max_jobs:
            raise AppError("job_capacity", 503)
        self.audits.reserve(session_id, limit, page_id)
        browser.attempts.append(now)
        self.global_attempt_times.append(now)
        browser.load_active = True
        browser.load_session_id = session_id
        self.active_jobs += 1
        return browser

    def finish_load(self, browser_id, session_id):
        browser = self.browsers.get(browser_id)
        if browser and browser.load_active and browser.load_session_id == session_id:
            browser.load_active = False
            browser.load_task = None
            browser.load_session_id = None
            self.active_jobs -= 1
        self.audits.release(session_id)

    def clear(self):
        if self.bulk_delete is not None:
            self.bulk_delete.confirmations.clear()
        if self.single_delete is not None:
            self.single_delete.confirmations.clear()
            self.single_delete.diagnostics.clear()
            self.single_delete.last_results.clear()
        if self.content_jobs is not None:
            self.content_jobs.clear()
        for browser in self.browsers.values():
            if browser.load_task is not None:
                browser.load_task.cancel()
        self.sessions.clear()
        self.browsers.clear()
        self.token_cooldowns.clear()
        self.cooldown_records.clear()
        self.global_attempt_times.clear()
        self.audits.clear()
