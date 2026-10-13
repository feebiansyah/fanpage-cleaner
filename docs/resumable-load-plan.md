# Resumable Load Posts

Baseline: 0e78de3a4df8b6219577c4963b3bef0d6382c063. Work directly in the authorized project; no commit, install, deletion, or thumbnail changes.

## Evidence and policy

Meta reference pages were rechecked and returned HTTP 429. Official facebook-python-business-sdk Page, PagePost, AdVideo and Stories models confirm GET listing and requested fields, not Story exclusivity, chronological listing, video publication time, or a rule for changing post_id format. Preserve exact identity matching and unknown Reel dates. A bounded Story identity probe removes known matches; it does not prove all candidates are free of Story. Audit selection is independent and allowed even on partial/unverified candidates; safe_for_delete remains false.

## Implementation

1. Add mock tests for start/step/status/stop, cursor persistence, revision replay, capacity, time limits/cooldown, session ownership/cancellation and audit-only selection; run red first.
2. Add content_jobs.py using existing bounded Graph transport and normalization. State stores session/browser IDs, not token copies. Each step has six actual attempts including retry and 12 seconds; job total 600 seconds, 300 attempts, 200 steps, existing 50 pages/source, idle TTL 120 seconds. Reserve candidate memory before admission. Preserve atomic page commits and valid cursor. Step cancellation interrupts only the unfinished page. Meta request timeout exhausts existing two retries then marks its source failed; completed pages remain. Cooldown pauses the job without polling Meta. Repeated step-time interruptions are bounded.
3. Add owner/origin-protected endpoints: POST /api/posts/jobs, POST /api/posts/jobs/{id}/step with revision, GET /api/posts/jobs/{id}, POST /api/posts/jobs/{id}/stop. Replayed revisions return current state without acquisition; concurrent requests share the same step task. Lifecycle/disconnect/expiry cleanup cancels and discards work. Keep prior /api/posts/load as compatibility path for existing contract checks, unused by the UI.
4. Frontend starts once, automatically advances revisions, waits locally during cooldown, retries lost local step responses at most twice with the same revision. Stop/page change/disconnect invalidate the loop and cancel backend job. Progress and 50-row paging are local. Preserve search and selections still present between snapshots.
5. Run all old and new backend/frontend tests, syntax checks, actual localhost smoke with .venv, read-only code review, and update README with actual limits and unresolved Story evidence.

## Review focus

Expired sessions cannot retain jobs or tokens; duplicates cannot consume pages; request budgets include retries; cooldown waits consume job/session TTL; unknown coverage never becomes deletion eligibility; failure/stop must retain received pages without exposing cursor/token or raw Meta errors.
