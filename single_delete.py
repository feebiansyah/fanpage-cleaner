"""One manually confirmed Page Post. No bulk transport and no automatic retry."""
import asyncio
import copy
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

from content import identifier, safe_url
from post_scope import SCOPE_VERSION, scope_decision
from errors import AppError, MESSAGES
from graph_api import delete_internal_code
from content_cache import invalidate_confirmed_post
from bulk_targets import identity_locks, suppression_ids


CONFIRMATION_TTL = 60
MAX_CONFIRMATIONS = 200
MAX_PER_SESSION = 10
MAX_DIAGNOSTICS = 200
OBSERVATION_FIELDS = ('content_type','json_parsed','json_type','success_type','has_error_field',
                      'response_issue','internal_code','phase','request_started','error_message','error_user_msg')


@dataclass
class Confirmation:
    browser_id: str = field(repr=False)
    session_id: str = field(repr=False)
    target: dict
    identity: tuple
    expires_at: float
    used: bool = False
    active: bool = False


class SingleDelete:
    def __init__(self, store, queue):
        self.store, self.queue = store, queue
        self.confirmations = {}
        self.diagnostics = deque(maxlen=MAX_DIAGNOSTICS)
        self.last_results = {}  # One bounded last attempt per live session; never persisted.

    def cleanup(self):
        now = time.monotonic()
        for cid, entry in list(self.confirmations.items()):
            if not entry.active and (entry.expires_at <= now or entry.session_id not in self.store.sessions):
                del self.confirmations[cid]
        for sid in list(self.last_results):
            if sid not in self.store.sessions:
                del self.last_results[sid]

    def last(self, browser_id, sid):
        if self.store.session(browser_id,sid) is None:
            raise AppError('session_expired',401)
        record=self.last_results.get(sid)
        if record is None:
            return {'state':'not_available','message':'Hasil percobaan terakhir tidak tersedia pada sesi/proses ini. Setelah restart, hasil memori lama tidak dapat dipulihkan. Jangan ulang DELETE untuk diagnosis.'}
        self.store.require_page(browser_id,sid,record['result']['target']['page_id'])
        return copy.deepcopy(record)

    def resolve(self, browser_id, sid, page_id, key, operation_scope='page_post', *, audit_context=None):
        if operation_scope != "page_post":
            raise AppError("reel_delete_unsupported",409)
        session, page = self.store.require_page(browser_id, sid, page_id)
        if not page.access_token:
            raise AppError('page_token_missing', 403)
        audit = audit_context[0] if audit_context is not None else self.store.audits.get(sid, page_id)
        if audit is None:
            raise AppError('audit_expired', 409)
        matches = audit_context[1].get(key,[]) if audit_context is not None else [item for item in audit['items'] if item.get('key') == key]
        if not matches:
            raise AppError('invalid_single_delete')
        if len(matches) != 1:
            raise AppError('post_identity_ambiguous', 409)
        item = matches[0]
        if item.get('page_id') != page_id:
            raise AppError('page_not_owned',403)
        reason=scope_decision(item)
        if reason:
            raise AppError('identified_story' if item.get('kind')=='Story' or item.get('identified_story')
                else 'post_identity_ambiguous' if reason in {'ambiguous_provenance','post_identity_ambiguous'}
                else 'audit_scope_incompatible' if reason in {'unknown_scope','unknown_attachments','truncated_attachments'}
                else 'post_media_unsupported',409)
        sources=item['sources']
        posts=sources
        reels=[]
        linked_post=None
        node=posts[0]['object_id']
        story_ids = {s['object_id'] for s in sources} | {node,linked_post}
        for job in self.store.content_jobs.entries.values():
            if job.page_id == page_id and story_ids.intersection(job.story_ids):
                raise AppError('identified_story', 409)
        link = safe_url(item.get('permalink'), False, [session.user_token, page.access_token])
        if link is None:
            raise AppError('post_permalink_missing', 409)
        name = next(p['name'] for p in session.metadata() if p['id'] == page_id)
        target = {'key': key, 'target_id': node, 'page_id': page_id, 'page_name': name,
                  'kind': item.get('kind') or 'Jenis tidak tersedia', 'text': item.get('text', '')[:500],
                  'operation_scope':operation_scope, 'media_deletion_guaranteed':False,
                  'related_video_ids':sorted({s['object_id'] for s in reels}),
                  'linked_post_id':linked_post,
                  'scope_version':SCOPE_VERSION, 'scope_item':scope_snapshot(item),
                  'thumbnail':safe_url(item.get('thumbnail'),True,list(self.store.known_tokens())),
                  'published_wib': item.get('published_wib') or 'Tanggal tidak tersedia', 'permalink': link}
        # Snapshot identity/provenance/visible metadata, not tokens or supplied delete IDs.
        identity = (node, tuple(sorted((s['source'], s['object_id'], s.get('post_id') or '') for s in sources)),
                    target['kind'], target['text'], target['published_wib'], target['permalink'],target['thumbnail'],operation_scope,repr(target['scope_item']))
        return session, page, target, identity

    def prepare(self, browser_id, sid, payload, operation_scope='page_post'):
        if (not isinstance(payload, dict) or set(payload) != {'page_id', 'keys'}
                or not isinstance(payload['page_id'], str) or not payload['page_id'].isdigit()
                or len(payload['page_id']) > 40 or not isinstance(payload['keys'], list) or len(payload['keys']) != 1
                or not isinstance(payload['keys'][0], str) or len(payload['keys'][0]) > 160):
            raise AppError('invalid_single_delete')
        session, _, target, identity = self.resolve(browser_id, sid, payload['page_id'], payload['keys'][0],operation_scope)
        self.cleanup()
        if (len(self.confirmations) >= MAX_CONFIRMATIONS
                or sum(c.session_id == sid for c in self.confirmations.values()) >= MAX_PER_SESSION):
            raise AppError('delete_capacity', 503)
        cid = secrets.token_urlsafe(32)
        expiry = min(session.expires_at, time.monotonic()+CONFIRMATION_TTL)
        self.confirmations[cid] = Confirmation(browser_id, sid, copy.deepcopy(target), identity, expiry)
        return {'confirmation_id': cid, 'expires_in_seconds': max(0, int(expiry-time.monotonic())),
                'experimental':False,'contract_verified':True,
                'target': target, 'story_automatically_verified': False,
                'message': 'Periksa permalink: pastikan target adalah Page Post teks, status, tautan, atau foto yang ingin dihapus, bukan Story, video, atau Reel. Penghapusan tidak dapat dibatalkan.'}

    async def execute(self, graph, browser_id, sid, payload, operation_scope='page_post'):
        if operation_scope != 'page_post':
            raise AppError('reel_delete_unsupported',409)
        if (not isinstance(payload, dict) or set(payload) != {'confirmation_id', 'manual_target_confirmed'}
                or payload['manual_target_confirmed'] is not True
                or not isinstance(payload['confirmation_id'], str) or len(payload['confirmation_id']) > 100):
            raise AppError('invalid_single_delete')
        entry = self.confirmations.get(payload['confirmation_id'])
        if self.store.session(browser_id, sid) is None:
            raise AppError('session_expired', 401)
        if entry is None or entry.browser_id != browser_id or entry.session_id != sid:
            raise AppError('confirmation_not_found', 404)
        if entry.target['operation_scope']!=operation_scope:
            raise AppError('confirmation_not_found',404)
        self.store.require_page(browser_id, sid, entry.target['page_id'])
        validate_scope_snapshot(entry.target)
        if entry.used:
            raise AppError('confirmation_used', 409)
        if time.monotonic() >= entry.expires_at:
            raise AppError('confirmation_expired', 409)
        session, page, _, identity = self.resolve(browser_id, sid, entry.target['page_id'], entry.target['key'],operation_scope)
        if identity != entry.identity:
            raise AppError('post_delete_unsupported', 409)
        canonical = 'post:'+entry.target['target_id']
        locks=identity_locks(canonical,entry.target)
        removal_ids=suppression_ids(entry.target['target_id'],entry.target)
        if locks.intersection(self.queue.locks):
            raise AppError('target_busy', 409)
        if len(self.store.audits.removed_posts)+sum(self.reserved_slots(c) for c in self.confirmations.values())+len(self.queue.success_reservations)+len(removal_ids) > self.store.settings.max_audit_items:
            raise AppError('delete_capacity', 503)
        entry.used, entry.active = True, True  # Atomic single-use + canonical lock before any await.
        self.queue.locks.update(locks)
        started = time.monotonic()
        deadline = min(session.expires_at, entry.expires_at, started+self.store.settings.operation_timeout)
        scope = self.store.token_key(page.access_token)
        del session, page  # No token retained while waiting for global governor.
        outcome = None
        observation = {'phase':'graph_dispatch','request_started':False}
        pending_record = {'state':'processing','result':{'outcome':'processing','reason':'request_in_progress',
            'target':copy.deepcopy(entry.target),'diagnostic':observation,
            'message':'Percobaan masih diproses. Pembacaan ini hanya lokal; jangan mengirim ulang DELETE.'}}
        self.last_results[sid] = pending_record
        def token_at_dispatch():
            validate_scope_snapshot(entry.target)
            if time.monotonic() >= entry.expires_at:
                raise AppError('confirmation_expired', 409)
            _, current_page, _, current_identity = self.resolve(browser_id, sid, entry.target['page_id'], entry.target['key'],operation_scope)
            current_scope=self.store.token_key(current_page.access_token)
            if current_scope in self.queue.blocked_scopes: raise AppError('token_invalid',401)
            if self.queue.permission_capacity_until>time.monotonic(): raise AppError('permission_circuit_capacity',429)
            if (entry.target['page_id'],current_scope) in self.queue.blocked_pages: raise AppError('permission_denied',403)
            if (entry.target['page_id'],current_scope) in self.queue.permission_circuits: raise AppError('permission_circuit_open',429)
            if current_identity != entry.identity:
                raise AppError('post_delete_unsupported', 409)
            return current_page.access_token
        try:
            send=graph.delete_post_once
            outcome = await send(entry.target['target_id'], scope, deadline, token_at_dispatch, observation)
            if outcome.get('request_started'):
                from types import SimpleNamespace
                self.queue.record_delete_outcome(SimpleNamespace(page_id=entry.target['page_id'],scope=scope),outcome['reason'])
            if outcome['outcome'] == 'succeeded':
                observation['phase']='cache_invalidation'
                warnings=[invalidate_confirmed_post(self.store,entry.target['page_id'],node) for node in sorted(removal_ids)]
                outcome['cleanup_warning']=next((warning for warning in warnings if warning),None)
        except AppError as error:
            outcome = {**observation,'outcome': 'uncertain' if observation.get('request_started') else 'failed',
                       'reason': 'internal_error' if observation.get('request_started') else error.code,
                       'retry_after': error.retry_after}
            if observation.get('request_started'):
                outcome['internal_code']=observation['phase']+'.app_error'
        except asyncio.CancelledError:
            # Client disconnect/cancellation never authorizes retry of this confirmation.
            outcome = {**observation,'outcome':'uncertain', 'reason':'interrupted'}
            raise
        except Exception as error:
            outcome = {**observation,'outcome':'uncertain', 'reason':'internal_error',
                       'internal_code':delete_internal_code(error,observation['phase'])}
        finally:
            entry.active = False
            self.queue.locks.difference_update(locks)
            self.queue.changed.set()
            diagnostic = {'operation':'delete_one_page_post', 'target_id':entry.target['target_id'],
                          'http_status':outcome.get('http_status') if outcome else None,
                          'error_code':outcome.get('error_code') if outcome else None,
                          'error_subcode':outcome.get('error_subcode') if outcome else None,
                          'duration_ms':round((time.monotonic()-started)*1000),
                          'outcome':outcome['outcome'] if outcome else 'uncertain',
                          'reason':outcome['reason'] if outcome else 'interrupted',
                          'cleanup_warning':outcome.get('cleanup_warning') if outcome else None,
                          **{key:outcome.get(key,observation.get(key)) if outcome else observation.get(key) for key in OBSERVATION_FIELDS}}
            self.diagnostics.append(diagnostic)
            reason = outcome['reason']
            message = ('Post berhasil dihapus berdasarkan respons success=true dari Meta.' if outcome['outcome']=='succeeded'
                   else 'Hasil penghapusan belum dapat dipastikan. Post tetap ditampilkan. Jangan ulang otomatis; periksa Facebook secara manual.' if outcome['outcome']=='uncertain'
                   else 'Batas waktu lokal habis sebelum pengiriman DELETE. Tidak ada request penghapusan dikirim.' if reason == 'timeout'
                   else MESSAGES.get(reason, 'Percobaan penghapusan gagal. Post tetap ditampilkan.'))
            result = {**outcome, 'target':copy.deepcopy(entry.target), 'diagnostic':diagnostic, 'message':message,
                      'experimental':False,'contract_verified':True,
                      'manual_target_confirmed':True, 'story_automatically_verified':False, 'safe_for_bulk_delete':False}
            if sid in self.store.sessions and self.store.sessions[sid].expires_at > time.monotonic():
                # Latest start wins; an older completion must not replace a newer attempt.
                if self.last_results.get(sid) is pending_record:
                    self.last_results[sid]={'state':'available','result':copy.deepcopy(result)}
            else:
                self.last_results.pop(sid,None)
        return result

    @staticmethod
    def reserved_slots(entry):
        if not entry.active: return 0
        return len(suppression_ids(entry.target['target_id'],entry.target))


def scope_snapshot(item):
    """Bounded classification/provenance only; no tokens or raw Graph payload."""
    return copy.deepcopy({key:item[key] for key in ('page_id','object_id','sources','kind','identified_story','post_status_type','scope_metadata') if key in item})


def validate_scope_snapshot(details, target_id=None, page_id=None):
    item=details.get('scope_item')
    if (details.get('operation_scope') != 'page_post' or details.get('scope_version') != SCOPE_VERSION
        or not isinstance(item,dict) or scope_decision(item)
        or item.get('object_id') != details.get('target_id')
        or item.get('page_id') != details.get('page_id')
        or (target_id is not None and target_id != details.get('target_id'))
        or (page_id is not None and page_id != details.get('page_id'))):
        raise AppError('audit_scope_incompatible',409)
