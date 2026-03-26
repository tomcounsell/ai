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
- **Interface changes**: `_pop_job()` gains a fallback scan path. `_worker_loop()` drain guard becomes configurable. No external API changes
- **Coupling**: Slightly reduces coupling to Popoto indexes by adding a bypass path
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
- **Aggressive drain guard**: Replace single 0.1s retry with configurable multi-retry (3 attempts, 0.5s spacing)
- **Raw Redis fallback scan**: When indexed query returns empty, do a direct SCAN/KEYS check as safety net
- **Exit-time diagnostic**: Before worker exits, verify no pending jobs exist via raw Redis -- log warning if found

### Flow

**Job enqueued** -> `_push_job()` creates record -> `_notify_worker(chat_id)` sets Event -> Worker `await event.wait()` wakes -> `_pop_job()` finds job -> Process -> Loop

**Fallback** (if Event not set or missed): Worker finishes job -> `_pop_job()` returns None -> Drain guard: retry 3x at 0.5s intervals -> If still None: raw Redis scan for pending jobs with this chat_id -> If found: log warning + retry `_pop_job()` -> If truly empty: exit

### Technical Approach

1. **Add `asyncio.Event` per chat_id** in `_active_events` dict alongside `_active_workers`. `enqueue_job()` calls `event.set()` after `_push_job()`. The worker loop `await event.wait()` + `event.clear()` between jobs instead of relying solely on `_pop_job()` polling.

2. **Make drain guard configurable**: Extract `DRAIN_RETRIES = 3` and `DRAIN_INTERVAL = 0.5` as module-level constants. Replace the single `await asyncio.sleep(0.1)` + one retry with a loop.

3. **Add raw Redis fallback in `_pop_job()`**: When `async_filter` returns empty, do a direct `redis.keys()` or `redis.scan()` for keys matching the AgentSession pattern with the target chat_id, then check each for `status=pending`. This bypasses Popoto indexes entirely.

4. **Exit-time safety check**: Before the "Queue empty, worker exiting" log, run the raw Redis scan. If pending jobs are found, log a `WARNING` and retry `_pop_job()` one final time.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The raw Redis fallback scan must have try/except -- if Redis SCAN fails, fall through to normal exit (don't crash the worker)
- [ ] Event.set() in `enqueue_job()` must not raise if no event exists yet (worker not started)

### Empty/Invalid Input Handling
- [ ] `_pop_job()` with empty chat_id should return None gracefully
- [ ] Raw Redis scan returning keys that no longer exist (deleted between SCAN and GET) must handle KeyError

### Error State Rendering
- [ ] No user-visible output -- this is internal infrastructure
- [ ] Warning log when exit-time scan finds orphaned pending jobs is the observable error signal

## Test Impact

- [ ] `tests/integration/test_job_queue_race.py::TestDrainGuard::test_drain_guard_double_check_finds_late_job` -- UPDATE: adjust to new retry count/interval constants
- [ ] `tests/integration/test_job_queue_race.py::TestDrainGuard::test_drain_guard_exits_when_truly_empty` -- UPDATE: adjust for multi-retry drain guard timing

## Rabbit Holes

- **Upstream Popoto fix for atomic index writes**: Tempting but out of scope. Filing an upstream issue is fine, but don't block this fix on ORM changes
- **Replacing Popoto with raw Redis**: The entire model layer depends on it. Way too much scope
- **Worker-per-job instead of worker-per-chat**: Would eliminate the drain problem but violates the sequential-per-chat guarantee needed to prevent git conflicts

## Risks

### Risk 1: asyncio.Event missed due to timing
**Impact:** Worker exits before event is set (same as current bug)
**Mitigation:** The aggressive drain guard + raw Redis fallback are independent safety nets. Even if the Event is missed, the multi-retry drain guard with 1.5s total wait (3 x 0.5s) is much more likely to catch the index than the current 0.1s single retry.

### Risk 2: Raw Redis SCAN performance
**Impact:** SCAN on large Redis databases could be slow
**Mitigation:** The SCAN is only triggered as a last resort (after indexed query returns empty AND drain guard retries exhausted). Scope the SCAN with a pattern match on the chat_id to limit results. In practice, the total number of AgentSession keys is small (dozens, not millions).

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

### 1. Implement asyncio.Event notification and aggressive drain guard
- **Task ID**: build-event-drain
- **Depends On**: none
- **Validates**: tests/integration/test_job_queue_race.py (update), tests/integration/test_worker_drain.py (create)
- **Assigned To**: queue-fixer
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_active_events: dict[str, asyncio.Event]` alongside `_active_workers`
- In `_ensure_worker()`: create Event if not exists, pass to `_worker_loop()`
- In `enqueue_job()`: call `event.set()` after `_push_job()` succeeds
- In `_worker_loop()`: after job completes, `event.clear()` then `await asyncio.wait_for(event.wait(), timeout=DRAIN_INTERVAL * DRAIN_RETRIES)` before exiting
- Extract `DRAIN_RETRIES = 3` and `DRAIN_INTERVAL = 0.5` as module constants
- Replace single 0.1s drain guard with multi-retry loop using these constants

### 2. Add raw Redis fallback scan and exit diagnostics
- **Task ID**: build-redis-fallback
- **Depends On**: none
- **Validates**: tests/integration/test_worker_drain.py (create)
- **Assigned To**: queue-fixer
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_raw_pending_check(chat_id)` function that bypasses Popoto indexes by scanning Redis keys
- Call from `_pop_job()` when `async_filter` returns empty as a fallback
- Add exit-time diagnostic: before "Queue empty, worker exiting", run `_raw_pending_check()` and log WARNING if pending jobs found
- Ensure all Redis operations have try/except with graceful fallthrough

### 3. Update existing tests and create integration test
- **Task ID**: build-tests
- **Depends On**: build-event-drain, build-redis-fallback
- **Validates**: tests/integration/test_job_queue_race.py, tests/integration/test_worker_drain.py
- **Assigned To**: queue-fixer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `TestDrainGuard` tests for new retry count/interval
- Create `test_worker_drain.py` with end-to-end test: enqueue A, start worker, enqueue B during A execution, assert B picked up
- Test Event notification path: verify event.set() during job execution wakes worker after completion
- Test raw Redis fallback: mock `async_filter` to return empty, verify fallback finds the job
- Test exit diagnostic: verify WARNING log when pending jobs exist at exit time

### 4. Validate fix
- **Task ID**: validate-fix
- **Depends On**: build-tests
- **Assigned To**: queue-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Run format: `python -m ruff format --check .`
- Verify no regressions in existing job queue tests
- Confirm sequential-per-chat guarantee is preserved

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fix
- **Assigned To**: queue-fixer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/job-queue.md` with new drain guard behavior
- Update docstrings for modified functions

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: queue-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Drain constants exist | `grep -c 'DRAIN_RETRIES\|DRAIN_INTERVAL' agent/job_queue.py` | output > 1 |
| Event dict exists | `grep -c '_active_events' agent/job_queue.py` | output > 0 |
| Raw fallback exists | `grep -c '_raw_pending_check' agent/job_queue.py` | output > 0 |
| Exit diagnostic exists | `grep -c 'pending jobs.*exit\|exit.*pending' agent/job_queue.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the root cause is well-understood from the recon, the fix is scoped to a single file, and the approach uses standard asyncio primitives with multiple independent safety nets.
