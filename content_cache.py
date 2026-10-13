"""Bounded last audit per session. Reserve memory before any Graph request."""

import json
import time
from dataclasses import dataclass, field

from errors import AppError


BYTES_PER_ITEM = 8192


def invalidate_confirmed_post(store, page_id, target_id):
    """Meta success stays success even if local cleanup fails."""
    try:
        store.audits.invalidate_post(page_id,target_id)
        return None
    except Exception:
        # Safe fallback: suppress old snapshots and discard affected cached Page results.
        # Do not retain exception strings, or convert a confirmed Meta success to uncertain.
        try:
            store.audits.removed_posts[(page_id,target_id)] = time.monotonic()+store.settings.session_ttl+store.settings.audit_ttl
            for key in [k for k in store.audits.pages if k[1]==page_id]:
                store.audits.pages.pop(key,None)
            for sid,entry in list(store.audits.entries.items()):
                if entry.data['page_id']==page_id: store.audits.entries.pop(sid,None)
        except Exception:
            pass
        return 'cache_cleanup_failed'


@dataclass
class CachedAudit:
    data: dict = field(repr=False)
    expires_at: float
    size: int


class AuditCache:
    def __init__(self, settings):
        self.settings = settings
        self.entries: dict[str, CachedAudit] = {}
        self.reservations: dict[str, int] = {}
        self.pages = {}
        self.removed_posts = {}  # Bounded tombstones: stale snapshots cannot restore successes.

    def cleanup(self, active_sessions):
        now = time.monotonic()
        self.removed_posts = {key: expiry for key, expiry in self.removed_posts.items() if expiry > now}
        for sid, entry in list(self.entries.items()):
            if sid not in active_sessions or entry.expires_at <= now:
                del self.entries[sid]
        for key,entry in list(self.pages.items()):
            if key[0] not in active_sessions or entry.expires_at<=now:
                del self.pages[key]

    def _all_entries(self):
        return {id(entry):entry for entry in [*self.entries.values(),*self.pages.values()]}.values()

    def reserve(self, sid, limit, page_id=None):
        previous=self.entries.pop(sid, None)
        replacing=page_id or (previous.data['page_id'] if previous else None)
        if replacing is not None:
            self.pages.pop((sid,replacing),None)
        count = len(self.entries) + len(self.reservations)
        rows = sum(entry.data["displayed"] for entry in self._all_entries()) + sum(self.reservations.values())
        size = sum(entry.size for entry in self._all_entries()) + sum(limit * BYTES_PER_ITEM + 8192 for limit in self.reservations.values())
        if (count >= self.settings.max_audit_entries or rows + limit > self.settings.max_audit_items
                or size + limit * BYTES_PER_ITEM + 8192 > self.settings.max_audit_bytes
                or len(self.pages)+len(self.reservations)>=self.settings.max_audit_pages):
            raise AppError("audit_capacity", 503)
        self.reservations[sid] = limit

    def put(self, sid, result, session_expiry):
        result = self.filter_removed(result)
        reservation = self.reservations.get(sid)
        if reservation is None or result["displayed"] > reservation:
            raise AppError("audit_capacity", 503)
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size > reservation * BYTES_PER_ITEM + 8192:
            raise AppError("audit_capacity", 503)
        key=(sid,result['page_id'])
        if key not in self.pages and len(self.pages)>=self.settings.max_audit_pages:
            raise AppError('audit_capacity',503)
        self.entries[sid] = CachedAudit(result, min(session_expiry, time.monotonic() + self.settings.audit_ttl), size)
        self.pages[key]=self.entries[sid]
        self.reservations.pop(sid, None)

    def get(self, sid, page_id):
        entry = self.pages.get((sid,page_id)) or self.entries.get(sid)
        if not entry or entry.expires_at <= time.monotonic() or entry.data["page_id"] != page_id:
            return None
        return {**self.filter_removed(entry.data), "cache_ttl_seconds": max(0, int(entry.expires_at - time.monotonic()))}

    def is_removed(self, item, page_id):
        identities = {item.get('object_id')}
        for source in item.get('sources', []):
            if isinstance(source, dict):
                if source.get('source') == 'published_posts': identities.add(source.get('object_id'))
                if source.get('source') == 'video_reels': identities.add(source.get('post_id'))
        now = time.monotonic()
        return any(self.removed_posts.get((page_id, value), 0) > now for value in identities if isinstance(value, str))

    def filter_removed(self, result):
        items = [item for item in result['items'] if not self.is_removed(item, result['page_id'])]
        if len(items) == len(result['items']):
            return result
        dated = sum(bool(item.get('published_at')) for item in items)
        return {**result, 'items': items, 'displayed': len(items),
                'found': max(len(items), result.get('found', len(result['items'])) - (len(result['items'])-len(items))),
                'dated_count': dated, 'undated_count': len(items)-dated}

    def invalidate_post(self, page_id, target_id):
        # Kept longer than any session/audit/load snapshot; bounded before dispatch.
        self.removed_posts[(page_id, target_id)] = time.monotonic()+self.settings.session_ttl+self.settings.audit_ttl
        for entry in self._all_entries():
            if entry.data['page_id'] == page_id:
                entry.data = self.filter_removed(entry.data)
                entry.size = len(json.dumps(entry.data, ensure_ascii=False).encode('utf-8'))

    def discard(self, sid):
        self.entries.pop(sid, None)
        for key in [key for key in self.pages if key[0]==sid]:
            del self.pages[key]

    def release(self, sid):
        self.reservations.pop(sid, None)

    def clear(self):
        self.entries.clear()
        self.reservations.clear()
        self.pages.clear()
        self.removed_posts.clear()
