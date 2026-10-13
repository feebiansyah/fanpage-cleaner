"""Bounded last audit per session. Reserve memory before any Graph request."""

import json
import time
from dataclasses import dataclass, field

from errors import AppError


BYTES_PER_ITEM = 8192


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

    def cleanup(self, active_sessions):
        now = time.monotonic()
        for sid, entry in list(self.entries.items()):
            if sid not in active_sessions or entry.expires_at <= now:
                del self.entries[sid]

    def reserve(self, sid, limit):
        self.entries.pop(sid, None)  # A new load replaces the previous audit for this browser.
        count = len(self.entries) + len(self.reservations)
        rows = sum(entry.data["displayed"] for entry in self.entries.values()) + sum(self.reservations.values())
        size = sum(entry.size for entry in self.entries.values()) + sum(limit * BYTES_PER_ITEM + 8192 for limit in self.reservations.values())
        if (count >= self.settings.max_audit_entries or rows + limit > self.settings.max_audit_items
                or size + limit * BYTES_PER_ITEM + 8192 > self.settings.max_audit_bytes):
            raise AppError("audit_capacity", 503)
        self.reservations[sid] = limit

    def put(self, sid, result, session_expiry):
        reservation = self.reservations.get(sid)
        if reservation is None or result["displayed"] > reservation:
            raise AppError("audit_capacity", 503)
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size > reservation * BYTES_PER_ITEM + 8192:
            raise AppError("audit_capacity", 503)
        self.entries[sid] = CachedAudit(result, min(session_expiry, time.monotonic() + self.settings.audit_ttl), size)
        self.reservations.pop(sid, None)

    def get(self, sid, page_id):
        entry = self.entries.get(sid)
        if not entry or entry.expires_at <= time.monotonic() or entry.data["page_id"] != page_id:
            return None
        return {**entry.data, "cache_ttl_seconds": max(0, int(entry.expires_at - time.monotonic()))}

    def discard(self, sid):
        self.entries.pop(sid, None)

    def release(self, sid):
        self.reservations.pop(sid, None)

    def clear(self):
        self.entries.clear()
        self.reservations.clear()
