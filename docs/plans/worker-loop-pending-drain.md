---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/543
last_comment_id:
---

# Worker Loop Pending Job Drain Fix

## Problem

When two Telegram messages arrive for the same chat within seconds, the second job gets stuck in `pending` forever. The worker processing the first job exits with "Queue empty" even though the second job exists in Redis.

**Current behavior:**
1. Message A arrives, job enqueued, worker starts, pops job A
2. Message B arrives 4s later, job enqueued as pending, `_ensure_worker()` sees existing worker and skips
3. Job A completes, worker calls `_pop_job(chat_id)` which queries `AgentSession.query.async_filter(chat_id=..., status="pending")` -- returns None despite job B existing
4. Drain guard: worker sleeps 0.1s, retries `_pop_job()` -- still None
5. Worker exits. Job B sits pending until watchdog or manual intervention

**Evidence from 2026-03-26 logs:**
- Issue #280 worker ran for chat `-5189826365`, issue #281 enqueued 4s later as pending, worker exited at 12:25:12 without picking up #281
- Same pattern on Valor chat: issue #538 ran, #539 stuck pending until worker exited

**Desired outcome:**
- Worker reliably picks up pending jobs after completing each job
- Zero jobs stuck in pending when a worker is active for that chat

## Prior Art

- **PR #95** (merged 2026-02-13): "Fix job queue losing enqueued jobs" -- Introduced the delete-and-recreate pattern in `_pop_job` and the 0.1s drain guard in `_worker_loop`. This fixed the KeyField index corruption on *writes* (status transitions) but the same index visibility problem now manifests on *reads* (the `async_filter` query misses recently-created pending jobs).
- **Issue #402** (closed 2026-03-14): "Watchdog stall recovery for pending sessions never kills stuck worker" -- Added stall detection but only addressed the case where the worker task itself is stuck. Did not fix the root cause of workers cleanly exiting while pending jobs exist.
- **Issue #501** (closed 2026-03-24): "Async job queue with branch-session mapping and dependency tracking" -- Introduced the current architecture. The `async_filter` and `async_create` both use `to_thread()`, creating a thread-pool race between index writes and reads.
- **PR #128** (merged 2026-02-17): "Job health monitor: detect and recover stuck running jobs" -- Safety net for stuck jobs, but does not prevent the premature-exit bug.

## Data Flow

1. **Entry point**: Telegram message arrives, `bridge/telegram_bridge.py` calls `enqueue_job()`
2. **`enqueue_job()`** (`agent/job_queue.py:1549`): Calls `_push_job()` then `_ensure_worker(chat_id)`
3. **`_push_job()`** (`agent/job_queue.py:277`): Calls `AgentSession.async_create()` which runs sync `create()` in a thread pool via `to_thread()`. The `create()` call writes the hash AND adds entries to KeyField index sets (e.g., `chat_id` index, `status` index) via Redis SADD
4. **`_ensure_worker()`** (`agent/job_queue.py:1586`): Checks `_active_workers` dict. If worker exists and not done, returns immediately (no-op)
5. **`_worker_loop()`** (`agent/job_queue.py:1600`): After job completes, calls `_pop_job(chat_id)`
6. **`_pop_job()`** (`agent/job_queue.py:627`): Calls `AgentSession.query.async_filter(chat_id=chat_id, status="pending")`. This runs `filter_for_keys_set()` in a thread pool, which intersects the `chat_id` and `status` KeyField index sets. **Bug**: The index sets from step 3 may not be visible to this intersection query due to thread-pool scheduling and Popoto's multi-step index write pattern
7. **Drain guard**: Sleeps 0.1s, retries `_pop_job()` once. If still None, worker exits

The race window: `async_create` (step 3) and `async_filter` (step 6) both use `to_thread()`. Redis operations themselves are atomic, but Popoto's `save()` performs multiple Redis commands (HSET for data, SADD for each KeyField index). If `filter_for_keys_set()` runs between these commands, or if the thread pool schedules the filter query before the create's index writes have all completed, the intersection returns empty.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #95 | Added delete-and-recreate for writes + 0.1s drain guard | Fixed index corruption on status *transitions* but didn't address index visibility on *reads*. 0.1s is too short -- Popoto `to_thread()` scheduling can delay index writes longer |
| Issue #402 | Added watchdog stall detection for pending sessions | Only addressed the case where worker is stuck, not where worker cleanly exits. The watchdog calls `_ensure_worker()` which is a no-op if no worker exists (it starts a new one, but by then the worker already exited) |

**Root cause pattern:** All prior fixes assumed Redis index consistency -- that if a record is created, it will be found by index queries. Popoto's multi-command `save()` and the `to_thread()` async bridge create a visibility window where the hash exists but index entries haven't propagated to all KeyField sets.

## Architectural Impact

- **New dependencies**: None -- uses existing asyncio primitives
- **Interface changes**: New `_pop_job_with_fallback()` function (separate from `_pop_job()`). `_worker_loop()` drain uses Event-based wait. No external API changes
- **Coupling**: Slightly reduces coupling to Popoto's `to_thread()` by adding a sync fallback path
- **Process scope**: asyncio.Event is intra-process only. Multi-process deployment would require Redis pub/sub, explicitly out of scope
- **Data ownership**: No change
- **Reversibility**: Fully reversible -- remove the Event signaling and fallback scan, revert to current behavior

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work modifies existing internal code with no external dependencies.

## Solution

### Key Elements

- **asyncio.Event notification**: Signal the worker when new work is enqueued, eliminating the polling race entirely
- **Event-based drain with fallback**: Single clear drain strategy — Event wait with timeout, then sync Popoto fallback
- **Exit-time diagnostic**: Before worker exits, verify no pending jobs exist via sync query -- log warning if found

### Flow (single drain strategy)

After a job completes, the worker follows this exact sequence:

1. `event.clear()` — reset the signal
2. `await event.wait(timeout=1.5s)` — block until new work is signaled OR timeout
3. **If event fires**: call `_pop_job(chat_id)` → process job → loop back to step 1
4. **If timeout**: call `_pop_job_with_fallback(chat_id)` (sync Popoto query bypassing `to_thread()`) → if found, process → if empty, exit

There is NO separate polling retry loop. The Event wait subsumes the old drain guard — the 1.5s timeout provides the same "wait for late arrivals" behavior as the old 0.1s retry, but with the Event signal as the fast path.

### Technical Approach

1. **Add `asyncio.Event` per chat_id** in `_active_events` dict alongside `_active_workers`. `enqueue_job()` calls `event.set()` after `_push_job()`. The worker loop uses `event.wait()` + `event.clear()` as the primary "is there more work?" mechanism. asyncio.Event is level-triggered — `set()` during job execution latches until `clear()`.

2. **Add `_pop_job_with_fallback(chat_id)`**: A separate function (not modifying `_pop_job()`) that first calls `_pop_job()` (the normal async_filter path), and if that returns None, runs a **synchronous** `AgentSession.query.filter(chat_id=chat_id, status="pending")` call directly (bypassing `to_thread()` scheduling). This avoids the thread-pool race that is the root cause while using the same Popoto indexes. Only called from the drain timeout path and exit-time diagnostic — never on the hot `_pop_job()` path.

3. **Exit-time safety check**: Before the "Queue empty, worker exiting" log, run `_pop_job_with_fallback()`. If pending jobs are found, log a `WARNING` and process them. This is the last safety net.

**Note**: asyncio.Event is intra-process only. Multi-process deployment would require Redis pub/sub or similar, which is explicitly out of scope for this fix.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The sync Popoto fallback in `_pop_job_with_fallback()` must have try/except -- if query fails, fall through to normal exit (don't crash the worker)
- [ ] Event.set() in `enqueue_job()` must not raise if no event exists yet (worker not started)

### Empty/Invalid Input Handling
- [ ] `_pop_job_with_fallback()` with empty chat_id should return None gracefully
- [ ] Sync query returning stale records (deleted between query and processing) must handle gracefully

### Error State Rendering
- [ ] No user-visible output -- this is internal infrastructure
- [ ] Warning log when exit-time scan finds orphaned pending jobs is the observable error signal

## Test Impact

- [ ] `tests/integration/test_job_queue_race.py::TestDrainGuard::test_drain_guard_double_check_finds_late_job` -- REPLACE: existing test only validates `_pop_job` isolation, not `_worker_loop` integration. Rewrite to test Event-based drain in `_worker_loop`
- [ ] `tests/integration/test_job_queue_race.py::TestDrainGuard::test_drain_guard_exits_when_truly_empty` -- REPLACE: rewrite for Event-based drain with timeout exit behavior

## Rabbit Holes

- **Upstream Popoto fix for atomic index writes**: Tempting but out of scope. Filing an upstream issue is fine, but don't block this fix on ORM changes
- **Replacing Popoto with raw Redis**: The entire model layer depends on it. Way too much scope
- **Worker-per-job instead of worker-per-chat**: Would eliminate the drain problem but violates the sequential-per-chat guarantee needed to prevent git conflicts

## Risks

### Risk 1: asyncio.Event missed due to timing
**Impact:** Worker exits before event is set (same as current bug)
**Mitigation:** The aggressive drain guard + raw Redis fallback are independent safety nets. Even if the Event is missed, the multi-retry drain guard with 1.5s total wait (3 x 0.5s) is much more likely to catch the index than the current 0.1s single retry.

### Risk 2: Sync Popoto query blocking the event loop
**Impact:** The `_pop_job_with_fallback()` sync query runs directly (not via `to_thread()`), briefly blocking the event loop
**Mitigation:** This only runs on the drain timeout path (after 1.5s with no Event signal) — a cold path. The sync Popoto filter is a single Redis SINTER + HGETALL, taking microseconds. Acceptable tradeoff for correctness.

## Race Conditions

### Race 1: Index visibility gap between async_create and async_filter
**Location:** `agent/job_queue.py` L338 (create) and L648 (filter)
**Trigger:** Job B created via `async_create` (thread A writes hash + index), worker calls `async_filter` (thread B reads index intersection) before all SADD commands from thread A complete
**Data prerequisite:** The KeyField index sets for both `chat_id` and `status` must contain the new job's key before `filter_for_keys_set()` intersects them
**State prerequisite:** Redis index sets are consistent with the hash data
**Mitigation:** asyncio.Event bypasses index queries entirely for the "is there new work?" signal. Raw Redis fallback bypasses index sets by scanning hashes directly.

### Race 2: Event.set() before worker is awaiting
**Location:** `enqueue_job()` sets event, `_worker_loop()` awaits event
**Trigger:** Job enqueued while worker is executing a job (not awaiting the event)
**Data prerequisite:** Event must be in set state when worker next checks it
**State prerequisite:** asyncio.Event is level-triggered (stays set until cleared)
**Mitigation:** asyncio.Event is level-triggered by design. `event.set()` latches the event until `event.clear()` is called. Even if set() happens while the worker is busy, the event will be set when the worker next calls `event.wait()`.

## No-Gos (Out of Scope)

- Upstream Popoto changes to make index writes atomic
- Changing the worker-per-chat architecture to worker-per-job
- Adding Redis pub/sub notification channel (asyncio.Event is simpler and sufficient for single-process)
- Fixing the watchdog stall recovery path (separate issue, already has its own fix)

## Update System

No update system changes required -- this is a bridge-internal bug fix with no new dependencies or config.

## Agent Integration

No agent integration required -- this is internal job queue infrastructure. No new MCP tools, no bridge import changes. The fix is entirely within `agent/job_queue.py`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/job-queue.md` with the new drain guard behavior, Event-based notification, and raw Redis fallback
- [ ] Add entry to `docs/features/README.md` index table if not already present

### Inline Documentation
- [ ] Docstring updates for `_pop_job()`, `_worker_loop()`, `_ensure_worker()`
- [ ] Comment explaining the raw Redis fallback and when it triggers

## Success Criteria

- [ ] Two jobs enqueued for the same chat_id within 5 seconds: both complete without manual intervention
- [ ] Worker loop logs a warning if exit-time scan finds pending jobs (raw Redis check)
- [ ] Integration test: enqueue job A, start worker, enqueue job B while A runs, assert B is picked up after A completes
- [ ] Drain guard retry count and interval are configurable constants (not hardcoded)
- [ ] No xfail markers related to this bug
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (job-queue)**
  - Name: queue-fixer
  - Role: Implement Event notification, aggressive drain guard, raw Redis fallback, and exit diagnostics
  - Agent Type: async-specialist
  - Resume: true

- **Validator (job-queue)**
  - Name: queue-validator
  - Role: Verify the fix resolves the race condition and all tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement asyncio.Event notification and Event-based drain
- **Task ID**: build-event-drain
- **Depends On**: none
- **Validates**: tests/integration/test_job_queue_race.py (replace), tests/integration/test_worker_drain.py (create)
- **Assigned To**: queue-fixer
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_active_events: dict[str, asyncio.Event]` alongside `_active_workers`
- In `_ensure_worker()`: create Event if not exists, pass to `_worker_loop()`
- In `enqueue_job()`: call `event.set()` after `_push_job()` succeeds
- In `_worker_loop()`: after job completes, `event.clear()` then `await asyncio.wait_for(event.wait(), timeout=1.5)`. If event fires → `_pop_job()`. If timeout → `_pop_job_with_fallback()`. If empty → exit
- Extract `DRAIN_TIMEOUT = 1.5` as module constant (replaces old 0.1s drain guard)
- Remove the old sleep-and-retry drain guard entirely

### 2. Add `_pop_job_with_fallback()` and exit diagnostics
- **Task ID**: build-fallback
- **Depends On**: none
- **Validates**: tests/integration/test_worker_drain.py (create)
- **Assigned To**: queue-fixer
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_pop_job_with_fallback(chat_id)` as a **separate function** (do NOT modify `_pop_job()`)
- First calls `_pop_job()` (normal path). If None, runs synchronous `AgentSession.query.filter(chat_id=chat_id, status="pending")` directly (bypassing `to_thread()`)
- Add exit-time diagnostic: before "Queue empty, worker exiting", call `_pop_job_with_fallback()`. If found, log WARNING and process. If empty, exit
- Wrap sync Popoto query in try/except with graceful fallthrough

### 3. Replace existing tests and create integration test
- **Task ID**: build-tests
- **Depends On**: build-event-drain, build-fallback
- **Validates**: tests/integration/test_job_queue_race.py, tests/integration/test_worker_drain.py
- **Assigned To**: queue-fixer
- **Agent Type**: test-engineer
- **Parallel**: false
- REPLACE `TestDrainGuard` tests — existing tests only validate `_pop_job` isolation, need to test `_worker_loop` Event-based drain
- Create `test_worker_drain.py` with end-to-end test: enqueue A, start worker, enqueue B during A execution, assert B picked up
- Test Event notification path: verify event.set() during job execution wakes worker after completion
- Test sync Popoto fallback: mock `async_filter` to return empty, verify `_pop_job_with_fallback()` finds the job via sync query
- Test exit diagnostic: verify WARNING log when pending jobs exist at exit time

### 4. Validate and document
- **Task ID**: validate-and-document
- **Depends On**: build-tests
- **Assigned To**: queue-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Run format: `python -m ruff format --check .`
- Verify no regressions in existing job queue tests
- Confirm sequential-per-chat guarantee is preserved
- Update `docs/features/job-queue.md` with Event-based drain behavior
- Update docstrings for modified functions
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Drain constant exists | `grep -c 'DRAIN_TIMEOUT' agent/job_queue.py` | output > 0 |
| Event dict exists | `grep -c '_active_events' agent/job_queue.py` | output > 0 |
| Fallback function exists | `grep -c '_pop_job_with_fallback' agent/job_queue.py` | output > 0 |
| Exit diagnostic exists | `grep -c 'pending jobs.*exit\|exit.*pending' agent/job_queue.py` | output > 0 |

## Critique Results

| Severity | Finding | Resolution |
|----------|---------|------------|
| BLOCKER | Event vs polling drain overlap creates ambiguous control flow | Resolved: defined single drain strategy — Event wait with 1.5s timeout, no separate polling loop |
| CONCERN | `redis.keys()` in fallback is problematic | Resolved: replaced with sync Popoto `query.filter()` bypassing `to_thread()` |
| CONCERN | `_pop_job()` contract change affects all callers | Resolved: created separate `_pop_job_with_fallback()` function |
| CONCERN | asyncio.Event single-process limitation undisclosed | Resolved: added note in Architectural Impact and Solution sections |
| CONCERN | Test Impact dispositions should be REPLACE not UPDATE | Resolved: changed both to REPLACE with justification |
| NIT | Tasks 4 and 6 duplicate full test suite runs | Resolved: merged into single validate-and-document task |

---

## Open Questions

No open questions -- the root cause is well-understood from the recon, the fix is scoped to a single file, and the approach uses standard asyncio primitives with multiple independent safety nets.
