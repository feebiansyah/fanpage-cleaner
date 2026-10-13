# Stage 4: dry-run queue only

Baseline 97f13a1c2facf6af4947aadb47071275676d64d3. No database, token file, real deletion transport, installation, commit, or push.

## Evidence
Meta Page Post/Reels reference documents returned 429. Official PagePost api_delete demonstrates node-ID operation exists; AdVideo model/SDK method cannot prove Reel deletion semantics, Story exclusion, permissions or success response semantics. Existing Story probe is not exhaustive. Current loaded items therefore remain deletion-ineligible. Validate each identity separately, report blocked reasons, and still perform honest local dry_run_checked checks. Never infer eligibility from /me/accounts or safe_for_delete.

## Components and verification
1. bulk_targets.py: only keys from owner-scoped unexpired backend audits; preserve original IDs/provenance, canonical identity, per-target reasons; no arbitrary target/token input. Retain bounded audit per Page for multi-Page jobs.
2. bulk_queue.py: centralized per-Page round robin, canonical target locks (not Page locks), bounded jobs/targets/events/bytes, owner UUID distinct from session credentials. Checkpoint processing before executor, result after; cancel/pause on disconnect/expiry/shutdown.
3. request_governor.py + bulk_scheduler.py: two workers default (max 8), shared global attempt gate default one/second, no burst, cooldown through existing store, usage raises spacing conservatively, no automatic speedup. Graph GET transport uses same gate; dry-run scheduler tests use fake executor. Production executor is local only.
4. bulk_executor.py: local-only dry-run; pure future response classification treats unverified success/timeout after send as uncertain without blind retry, denies permanent-token/permission retries; bounded temporary retries. No actual deletion implementation or runtime enable flag.
5. bulk_checkpoint.py: fixed runtime/dry-run folder, UUID names, versioned allowlisted nonsecret schema, atomic fsync/replace, capacity/retention/path limits. Restart marks paused_needs_reconnect; processing becomes uncertain. Recovered records are not registered to live sessions and cannot be resumed/accessed in UI; no ownership recovery mechanism yet.
6. API: start with selections of Page ID + keys, status/stop bound to session+origin; paging local checkpoint/status, no Meta polling. UI adds one Dry Run button and small result area, separate Stop labels for load/dry run; real Delete stays disabled.
7. Tests first: denial of arbitrary/browser-owned/stale input, duplicates/locks/fairness/rate/concurrency/cooldown, bounded retries, cancel/expiry/shutdown, atomic secret-free checkpoint/restart quarantine, multi-Page validation, no outbound deletion; all existing suites. Syntax, localhost smoke, independent read-only review, README actual limits and blocked evidence.
