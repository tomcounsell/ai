---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-14
tracking: https://github.com/tomcounsell/ai/issues/402
last_comment_id:
---

# Fix pending session stall recovery to kill stuck workers

## Problem

On 2026-03-13, two sessions (7401 and 7405) stayed stuck in `pending` status for 4+ and 3+ hours respectively. The session watchdog detected `LIFECYCLE_STALL` every 5 minutes but recovery never worked because `_recover_stalled_pending()` only calls `_ensure_worker()`, which is a no-op when a worker task already exists — even if that worker is stuck processing a different job.

**Current behavior:**
Pending sessions stall → watchdog detects stall → calls `_ensure_worker()` → existing stuck worker is "alive" → no-op → session stays stuck forever. Watchdog logs the same warning every 5 minutes indefinitely.

**Desired outcome:**
Pending sessions stall → watchdog detects stall → kills the stuck worker → starts a fresh worker → pending job gets picked up. After `STALL_MAX_RETRIES` exhausted, session is abandoned with notification.

## Prior Art

- **Issue #360**: Smart stall detection via transcript mtime — improved detection accuracy for active sessions but did not touch the pending recovery path. Closed 2026-03-12.
- **PR #344**: Original implementation of `_recover_stalled_pending()` — introduced the function but only called `_ensure_worker()`. This was correct for Race 2 (worker exits before pickup) but insufficient when the worker is alive but stuck on another job. Merged 2026-03-10.

## Data Flow

1. **Entry**: `watchdog_loop()` runs every 5 minutes → calls `check_stalled_sessions()`
2. **Detection**: `check_stalled_sessions()` queries sessions with status=pending, checks if stalled > 300s → returns list of stalled session dicts
3. **Recovery (current, broken)**: `_recover_stalled_pending(stalled)` → filters for pending → calls `_ensure_worker(project_key)` → no-op if worker exists
4. **Recovery (desired)**: `_recover_stalled_pending(stalled)` → filters for pending → reads session from Redis → checks retry_count → kills stuck worker → re-enqueues with retry context → ensures fresh worker exists

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #344 | Added `_recover_stalled_pending()` calling `_ensure_worker()` | Only handles Race 2 (worker exited). When the worker is alive but stuck on another job, `_ensure_worker()` is a no-op because `existing and not existing.done()` returns True. |

**Root cause pattern:** The pending recovery path assumed "no worker = stuck". The actual failure mode is "worker alive but blocked by a stuck job".

## Architectural Impact

- **No new dependencies**: Uses existing `_kill_stalled_worker()`, `_enqueue_stall_retry()`, `_compute_stall_backoff()`, `_notify_stall_failure()` — all already implemented for the active session path.
- **Interface changes**: `_recover_stalled_pending()` signature stays the same. Internal logic changes.
- **Coupling**: No change — already coupled to `agent.job_queue`.
- **Reversibility**: Fully reversible. Revert = return to current `_ensure_worker()` only behavior.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The fix is well-understood: mirror what `fix_unhealthy_session()` already does for active sessions.

## Prerequisites

No prerequisites — this work has no external dependencies. All required functions already exist in the codebase.

## Solution

### Key Elements

- **Kill before spawn**: `_recover_stalled_pending()` kills the existing worker for the project before calling `_ensure_worker()`, mirroring the active session recovery path
- **Retry with backoff**: Use existing `_enqueue_stall_retry()` with exponential backoff and retry tracking
- **Abandon with notification**: After `STALL_MAX_RETRIES` exhausted, abandon and notify via `_notify_stall_failure()`

### Flow

**Pending stall detected** → Check retry_count → [retries remain] → Kill worker → Backoff → Re-enqueue → Ensure fresh worker → **Job resumes**

**Pending stall detected** → Check retry_count → [retries exhausted] → Abandon session → Notify human → **Session marked abandoned**

### Technical Approach

Rewrite `_recover_stalled_pending()` to:

1. For each pending stall, load the full `AgentSession` from Redis (currently only has dict from `check_stalled_sessions()`)
2. Check `retry_count < STALL_MAX_RETRIES`
3. If retries remain: call `_kill_stalled_worker(project_key)` → `asyncio.sleep(backoff)` → `_enqueue_stall_retry(session, reason)`
4. If retries exhausted: call `_safe_abandon_session()` → `_notify_stall_failure()`

This is structurally identical to what `fix_unhealthy_session()` does for the `silence_duration > ABANDON_THRESHOLD` case.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_recover_stalled_pending()` has a try/except per session — verify it logs errors and continues to next session (existing behavior, add test)
- [ ] `_enqueue_stall_retry()` handles `ModelException` from Redis operations — already tested

### Empty/Invalid Input Handling
- [ ] Test with empty stalled list (should be no-op)
- [ ] Test with stalled session that has `project_key = "?"` (should skip with warning, existing behavior)
- [ ] Test with stalled session where `AgentSession.query.get()` returns None (session deleted between detection and recovery)

### Error State Rendering
- [ ] Verify `_notify_stall_failure()` sends notification with correct session ID and retry count
- [ ] No user-facing rendering in this change (internal watchdog behavior)

## Rabbit Holes

- **Adding per-job timeouts to `_worker_loop()`**: Tempting but large scope change. Would need careful design around sub-agent trees and legitimate long-running tasks. Separate issue.
- **Surfacing loop detection to the agent itself**: The secondary issue about exploration loops is valid but separate. Don't mix it into this bug fix.
- **Changing the watchdog interval**: 5 minutes is fine. The problem isn't detection frequency, it's the recovery action being ineffective.

## Risks

### Risk 1: Killing a healthy worker processing a legitimate long job
**Impact:** A legitimately long-running (but progressing) job gets killed when an unrelated pending session stalls.
**Mitigation:** This only triggers when a pending session has been stalled for 5+ minutes AND the retry threshold is hit. A healthy worker processing a long job would have its active session's `last_activity` or transcript mtime updating — meaning the active session wouldn't be in "stalled" state. The pending stall is the signal that something is wrong. Additionally, the killed job's active session enters the standard stall-retry flow and gets re-enqueued itself.

### Risk 2: Rapid worker churn from multiple pending stalls
**Impact:** If multiple pending sessions stall simultaneously, each triggers a kill+restart cycle.
**Mitigation:** The per-project worker model means kills are scoped to one project. The exponential backoff (10s, 20s, 40s) prevents rapid cycling. The 5-minute watchdog interval also naturally rate-limits recovery attempts.

## Race Conditions

### Race 1: Worker completes between stall detection and kill
**Location:** `_recover_stalled_pending()` between `check_stalled_sessions()` return and `_kill_stalled_worker()` call
**Trigger:** Worker finishes its current job and picks up the pending session naturally, just as the watchdog decides to kill it.
**Data prerequisite:** The pending session must still exist in Redis when re-enqueue is attempted.
**State prerequisite:** Worker must be alive at kill time.
**Mitigation:** `_kill_stalled_worker()` already handles the case where the worker is done (`worker.done()` returns True) — it returns False and logs "already dead/missing". The subsequent `_enqueue_stall_retry()` uses delete-and-recreate which is safe even if the session was already picked up (it would have transitioned out of pending status, so `AgentSession.query.get()` finds it in a different state or deleted).

### Race 2: Two watchdog cycles both try to recover the same pending session
**Location:** `_recover_stalled_pending()` — two consecutive 5-min cycles
**Trigger:** Backoff sleep (up to 40s for retry 2) delays recovery past the next watchdog cycle.
**Data prerequisite:** Session must still be in pending status.
**State prerequisite:** Previous recovery attempt must not have completed.
**Mitigation:** `_enqueue_stall_retry()` deletes the old session and creates a new one atomically. If the session was already deleted by a previous recovery attempt, `session.delete()` is a no-op or raises `ModelException` which is caught. The first recovery to complete wins; the second harmlessly fails.

## No-Gos (Out of Scope)

- Per-job timeout in `_worker_loop()` — separate issue, larger design needed
- Agent-level loop detection injection (system message when looping) — separate feature
- Changing watchdog interval or stall thresholds — current values are appropriate
- Modifying `_ensure_worker()` itself — the function is correct for its purpose; the caller was wrong

## Update System

No update system changes required — this is a monitoring/recovery logic change within `session_watchdog.py`. No new dependencies, configs, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal watchdog change. No MCP servers, tools, or bridge imports are affected.

## Documentation

- [ ] Update `docs/features/stall-retry.md` — rewrite the "Pending Session Recovery" section (lines 99-132) to document the new kill+retry flow instead of the ensure-only flow
- [ ] Update inline docstring of `_recover_stalled_pending()` to reflect new behavior

## Success Criteria

- [ ] `_recover_stalled_pending()` kills the stuck worker before spawning a new one
- [ ] Pending stall recovery uses exponential backoff via `_compute_stall_backoff()`
- [ ] Retry count is tracked and incremented per attempt
- [ ] After `STALL_MAX_RETRIES` exhausted, session is abandoned with Telegram notification
- [ ] Existing stall-retry tests pass (no regressions)
- [ ] New tests cover: kill+retry path, retry exhaustion path, missing session edge case
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (watchdog-fix)**
  - Name: watchdog-builder
  - Role: Implement the fix in `_recover_stalled_pending()` and write tests
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog-fix)**
  - Name: watchdog-validator
  - Role: Verify fix works, tests pass, docs updated
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement the fix
- **Task ID**: build-watchdog-fix
- **Depends On**: none
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `_recover_stalled_pending()` in `monitoring/session_watchdog.py` to:
  - Load full `AgentSession` from Redis for each stalled pending session
  - Check `retry_count < STALL_MAX_RETRIES`
  - If retries remain: `_kill_stalled_worker()` → `asyncio.sleep(_compute_stall_backoff())` → `_enqueue_stall_retry()`
  - If retries exhausted: `_safe_abandon_session()` → `_notify_stall_failure()`
  - Handle edge cases: session deleted between detection and recovery, missing project_key
- Write tests in `tests/unit/test_pending_stall_recovery.py`:
  - Test kill+retry path (mock `_kill_stalled_worker`, `_enqueue_stall_retry`)
  - Test retry exhaustion path (mock `_safe_abandon_session`, `_notify_stall_failure`)
  - Test session-not-found edge case
  - Test empty stalled list (no-op)
  - Test project_key="?" skip behavior

### 2. Validate the fix
- **Task ID**: validate-watchdog-fix
- **Depends On**: build-watchdog-fix
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_recover_stalled_pending()` calls `_kill_stalled_worker()` before `_ensure_worker()`
- Run `pytest tests/unit/test_pending_stall_recovery.py -v`
- Run `pytest tests/ -k stall -v` to check for regressions
- Verify `ruff check` and `ruff format --check` pass

### 3. Documentation
- **Task ID**: document-fix
- **Depends On**: validate-watchdog-fix
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/stall-retry.md` "Pending Session Recovery" section
- Update `_recover_stalled_pending()` docstring

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-fix
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Stall tests pass | `pytest tests/ -k stall -v` | exit code 0 |
| New tests exist | `test -f tests/unit/test_pending_stall_recovery.py` | exit code 0 |
