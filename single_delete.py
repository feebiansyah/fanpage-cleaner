"""One manually confirmed Page Post. No bulk transport and no automatic retry."""
import asyncio
import copy
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

from content import identifier, safe_url
from errors import AppError, MESSAGES


CONFIRMATION_TTL = 60
MAX_CONFIRMATIONS = 200
MAX_PER_SESSION = 10
MAX_DIAGNOSTICS = 200


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

    def cleanup(self):
        now = time.monotonic()
        for cid, entry in list(self.confirmations.items()):
            if not entry.active and (entry.expires_at <= now or entry.session_id not in self.store.sessions):
                del self.confirmations[cid]

    def resolve(self, browser_id, sid, page_id, key):
        session, page = self.store.require_page(browser_id, sid, page_id)
        if not page.access_token:
            raise AppError('page_token_missing', 403)
        audit = self.store.audits.get(sid, page_id)
        if audit is None:
            raise AppError('audit_expired', 409)
        matches = [item for item in audit['items'] if item.get('key') == key]
        if not matches:
            raise AppError('invalid_single_delete')
        if len(matches) != 1:
            raise AppError('post_identity_ambiguous', 409)
        item = matches[0]
        sources = item.get('sources')
        if item.get('page_id') != page_id:
            raise AppError('page_not_owned',403)
        if item.get('kind') == 'Story' or item.get('identified_story') is True:
            raise AppError('identified_story',409)
        if not isinstance(sources,list) or any(not isinstance(s,dict) for s in sources):
            raise AppError('post_identity_ambiguous',409)
        if any(s.get('source') == 'stories' for s in sources):
            raise AppError('identified_story',409)
        if any(s.get('source') not in {'published_posts','video_reels'} or not identifier(s.get('object_id')) for s in sources):
            raise AppError('post_identity_ambiguous',409)
        posts = [s for s in sources if s['source'] == 'published_posts']
        if not posts:
            raise AppError('page_post_missing',409)
        nodes = {s['object_id'] for s in posts}
        if len(nodes) != 1:
            raise AppError('post_identity_ambiguous',409)
        node = next(iter(nodes))  # Never synthesize or reformat an ID.
        reels = [s for s in sources if s['source'] == 'video_reels']
        if (any(s.get('post_id') != node for s in reels)
                or item.get('object_id') not in {s['object_id'] for s in sources}):
            raise AppError('post_identity_ambiguous',409)
        story_ids = {s['object_id'] for s in sources} | {node}
        for job in self.store.content_jobs.entries.values():
            if job.page_id == page_id and story_ids.intersection(job.story_ids):
                raise AppError('identified_story', 409)
        link = safe_url(item.get('permalink'), False, [session.user_token, page.access_token])
        if link is None:
            raise AppError('post_permalink_missing', 409)
        name = next(p['name'] for p in session.metadata() if p['id'] == page_id)
        target = {'key': key, 'target_id': node, 'page_id': page_id, 'page_name': name,
                  'kind': item.get('kind') or 'Jenis tidak tersedia', 'text': item.get('text', '')[:500],
                  'operation_scope':'page_post', 'media_deletion_guaranteed':False,
                  'published_wib': item.get('published_wib') or 'Tanggal tidak tersedia', 'permalink': link}
        # Snapshot identity/provenance/visible metadata, not tokens or supplied delete IDs.
        identity = (node, tuple(sorted((s['source'], s['object_id'], s.get('post_id') or '') for s in sources)),
                    target['kind'], target['text'], target['published_wib'], target['permalink'])
        return session, page, target, identity

    def prepare(self, browser_id, sid, payload):
        if (not isinstance(payload, dict) or set(payload) != {'page_id', 'keys'}
                or not isinstance(payload['page_id'], str) or not payload['page_id'].isdigit()
                or len(payload['page_id']) > 40 or not isinstance(payload['keys'], list) or len(payload['keys']) != 1
                or not isinstance(payload['keys'][0], str) or len(payload['keys'][0]) > 160):
            raise AppError('invalid_single_delete')
        session, _, target, identity = self.resolve(browser_id, sid, payload['page_id'], payload['keys'][0])
        self.cleanup()
        if (len(self.confirmations) >= MAX_CONFIRMATIONS
                or sum(c.session_id == sid for c in self.confirmations.values()) >= MAX_PER_SESSION):
            raise AppError('delete_capacity', 503)
        cid = secrets.token_urlsafe(32)
        expiry = min(session.expires_at, time.monotonic()+CONFIRMATION_TTL)
        self.confirmations[cid] = Confirmation(browser_id, sid, copy.deepcopy(target), identity, expiry)
        return {'confirmation_id': cid, 'expires_in_seconds': max(0, int(expiry-time.monotonic())),
                'target': target, 'story_automatically_verified': False,
                'message': 'Periksa permalink: pastikan target adalah Page Post yang ingin dihapus, bukan Facebook Story. Operasi menargetkan Page Post, bukan jaminan menghapus objek foto/video/Reel secara terpisah. Post ini akan dihapus dari Fanpage. Penghapusan tidak dapat dibatalkan.'}

    async def execute(self, graph, browser_id, sid, payload):
        if (not isinstance(payload, dict) or set(payload) != {'confirmation_id', 'manual_target_confirmed'}
                or payload['manual_target_confirmed'] is not True
                or not isinstance(payload['confirmation_id'], str) or len(payload['confirmation_id']) > 100):
            raise AppError('invalid_single_delete')
        entry = self.confirmations.get(payload['confirmation_id'])
        if self.store.session(browser_id, sid) is None:
            raise AppError('session_expired', 401)
        if entry is None or entry.browser_id != browser_id or entry.session_id != sid:
            raise AppError('confirmation_not_found', 404)
        self.store.require_page(browser_id, sid, entry.target['page_id'])
        if entry.used:
            raise AppError('confirmation_used', 409)
        if time.monotonic() >= entry.expires_at:
            raise AppError('confirmation_expired', 409)
        session, page, _, identity = self.resolve(browser_id, sid, entry.target['page_id'], entry.target['key'])
        if identity != entry.identity:
            raise AppError('post_delete_unsupported', 409)
        canonical = 'post:'+entry.target['target_id']
        if canonical in self.queue.locks:
            raise AppError('target_busy', 409)
        if len(self.store.audits.removed_posts)+sum(c.active for c in self.confirmations.values()) >= self.store.settings.max_audit_items:
            raise AppError('delete_capacity', 503)
        entry.used, entry.active = True, True  # Atomic single-use + canonical lock before any await.
        self.queue.locks.add(canonical)
        started = time.monotonic()
        deadline = min(session.expires_at, entry.expires_at, started+self.store.settings.operation_timeout)
        scope = self.store.token_key(page.access_token)
        del session, page  # No token retained while waiting for global governor.
        outcome = None
        def token_at_dispatch():
            if time.monotonic() >= entry.expires_at:
                raise AppError('confirmation_expired', 409)
            _, current_page, _, current_identity = self.resolve(browser_id, sid, entry.target['page_id'], entry.target['key'])
            if current_identity != entry.identity:
                raise AppError('post_delete_unsupported', 409)
            return current_page.access_token
        try:
            outcome = await graph.delete_post_once(entry.target['target_id'], scope, deadline, token_at_dispatch)
            if outcome['outcome'] == 'succeeded':
                self.store.audits.invalidate_post(entry.target['page_id'], entry.target['target_id'])
        except AppError as error:
            outcome = {'outcome': 'failed', 'reason': error.code, 'http_status': None,
                       'error_code': None, 'error_subcode': None, 'retry_after': error.retry_after}
        except asyncio.CancelledError:
            # Client disconnect/cancellation never authorizes retry of this confirmation.
            outcome = {'outcome':'uncertain', 'reason':'interrupted', 'http_status':None,
                       'error_code':None, 'error_subcode':None}
            raise
        except Exception:
            outcome = {'outcome':'uncertain', 'reason':'internal_error', 'http_status':None,
                       'error_code':None, 'error_subcode':None}
        finally:
            entry.active = False
            self.queue.locks.discard(canonical)
            self.queue.changed.set()
            diagnostic = {'operation':'delete_one_page_post', 'target_id':entry.target['target_id'],
                          'http_status':outcome.get('http_status') if outcome else None,
                          'error_code':outcome.get('error_code') if outcome else None,
                          'error_subcode':outcome.get('error_subcode') if outcome else None,
                          'duration_ms':round((time.monotonic()-started)*1000),
                          'outcome':outcome['outcome'] if outcome else 'uncertain'}
            self.diagnostics.append(diagnostic)
        reason = outcome['reason']
        message = ('Post berhasil dihapus berdasarkan respons success=true dari Meta.' if outcome['outcome']=='succeeded'
                   else 'Hasil penghapusan belum dapat dipastikan. Post tetap ditampilkan. Jangan ulang otomatis; periksa Facebook secara manual.' if outcome['outcome']=='uncertain'
                   else 'Batas waktu lokal habis sebelum pengiriman DELETE. Tidak ada request penghapusan dikirim.' if reason == 'timeout'
                   else MESSAGES.get(reason, 'Percobaan penghapusan gagal. Post tetap ditampilkan.'))
        return {**outcome, 'target':entry.target, 'diagnostic':diagnostic, 'message':message,
                'manual_target_confirmed':True, 'story_automatically_verified':False, 'safe_for_bulk_delete':False}
