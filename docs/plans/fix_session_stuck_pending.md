---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/342
---

# Fix Session Stuck in Pending After BUILD COMPLETED

## Problem

A session can get stuck in `pending` status indefinitely after the agent finishes building. The watchdog detects it as a `LIFECYCLE_STALL` but cannot recover it because stall retry logic only handles `active` sessions, not `pending` ones.

**Current behavior:**
Session stays in `pending` for 24+ minutes despite the last history entry showing `BUILD COMPLETED`. No worker picks it up and the user never receives a final reply.

**Desired outcome:**
Sessions that complete work always transition to `completed` or get picked up by a worker for the next stage. The watchdog can recover stalled pending sessions.

## Prior Art

- **Issue #127**: Job queue: detect and recover stuck running jobs -- Closed 2026-02-16. Addressed running jobs, not pending sessions.
- **PR #217**: Add session lifecycle diagnostics and stall detection (#216) -- Merged 2026-02-27. Added `check_stalled_sessions()` which DETECTS pending stalls but does not FIX them.
- **PR #128**: Job health monitor: detect and recover stuck running jobs -- Merged 2026-02-17. Recovery logic for running jobs only.

## Data Flow

1. **Entry point**: Agent finishes work, `send_to_chat()` callback is invoked in `_execute_job()`
2. **`send_to_chat()`**: Stage-aware auto-continue detects remaining stages -> calls `_enqueue_continuation()`
3. **`_enqueue_continuation()`**: Deletes current running session via `async_delete()`, creates new pending session via `async_create()`, calls `_ensure_worker()`
4. **`_ensure_worker()`**: Sees the current worker task is still alive (we're still inside `_execute_job`), returns without starting a new worker
5. **`_execute_job()` epilogue (line 1623)**: `agent_session.save()` is called on the stale in-memory reference -- this can **resurrect** the deleted running session as a ghost record in Redis
6. **`_complete_job()`**: Deletes `job._rj` (the original running session reference) -- may delete the ghost or be a no-op
7. **Worker loop**: Calls `_pop_job()` which queries `status="pending"` -- if Redis index corruption occurred from step 5, the pending continuation may be invisible to the query

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #217 | Added `check_stalled_sessions()` detection | Only logs LIFECYCLE_STALL warnings for pending sessions; never attempts recovery. Recovery logic in `fix_unhealthy_session()` only handles `active` sessions. |
| PR #128 | Added job health monitor | Only recovers `running` jobs, not `pending` ones stuck without a worker. |

**Root cause pattern:** All prior fixes addressed detection but not recovery for pending sessions. The root cause -- stale session references being saved after `_enqueue_continuation` deletes and recreates the session -- was never identified.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- all fixes are internal to existing functions
- **Coupling**: No change
- **Data ownership**: No change
- **Reversibility**: Fully reversible -- behavioral fix only

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Stale session save guard**: Prevent `agent_session.save()` from resurrecting deleted sessions after auto-continue
- **Pending stall recovery**: Extend watchdog to recover pending sessions that exceed their stall threshold
- **Worker restart on pending stall**: When the watchdog detects a stalled pending session, ensure a worker is spawned to process it

### Flow

**Auto-continue** -> `_enqueue_continuation()` deletes + recreates session -> **epilogue skips stale save** -> worker loop picks up pending continuation -> **session progresses**

**Stalled pending detected** -> watchdog `check_stalled_sessions()` -> **recovery: re-enqueue or spawn worker** -> session progresses

### Technical Approach

1. **Guard the stale save** (`agent/job_queue.py`, lines 1620-1624): When `chat_state.defer_reaction` is True, skip `agent_session.save()` entirely. The session was already deleted and recreated by `_enqueue_continuation()` -- saving the stale reference resurrects a ghost record. Replace with a no-op or at most a debug log.

2. **Add pending recovery to watchdog** (`monitoring/session_watchdog.py`): In `check_stalled_sessions()`, after detecting a pending stall, call `_ensure_worker()` for the session's `project_key`. This handles the case where the worker exited before picking up the pending continuation.

3. **Re-read session before epilogue save** (defensive): Even for non-deferred cases, re-read `agent_session` from Redis before calling `complete_transcript()` to avoid operating on stale data.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `except Exception` at line 1625 (`AgentSession update failed`) swallows errors. Add test asserting that when `defer_reaction=True`, no save is attempted at all.
- [ ] The watchdog's pending stall recovery must not crash if the session was already picked up by a worker between detection and recovery.

### Empty/Invalid Input Handling
- [ ] Test pending recovery when session has been deleted between stall detection and recovery attempt.
- [ ] Test `_ensure_worker` when called for a project with an active worker that already exited.

### Error State Rendering
- [ ] When a pending session is recovered by the watchdog, the user should eventually receive the agent's output (no silent drops).

## Rabbit Holes

- Refactoring the entire delete-and-recreate pattern in Popoto -- that's a library concern, not ours
- Adding distributed locking for session state transitions -- overkill for a single-worker-per-project architecture
- Redesigning the auto-continue flow to avoid stale references entirely -- scope creep; the guard is sufficient

## Risks

### Risk 1: Ghost session resurrection is not the only cause
**Impact:** Fix doesn't fully resolve the bug if there's another cause (e.g., Redis index corruption from Popoto)
**Mitigation:** The watchdog pending recovery acts as a safety net regardless of root cause

### Risk 2: Worker spawned by watchdog races with existing worker
**Impact:** Two workers processing the same project simultaneously
**Mitigation:** `_ensure_worker()` already checks for active workers and is a no-op if one exists

## Race Conditions

### Race 1: Stale save resurrects deleted session
**Location:** `agent/job_queue.py` lines 1620-1624
**Trigger:** `_enqueue_continuation()` deletes session, then epilogue calls `agent_session.save()` on the stale in-memory reference
**Data prerequisite:** `agent_session` must have been captured before `_enqueue_continuation()` deleted it
**State prerequisite:** `chat_state.defer_reaction` is True (auto-continue happened)
**Mitigation:** Skip the save entirely when `defer_reaction` is True. The continuation already has fresh state.

### Race 2: Worker exits before picking up continuation
**Location:** `agent/job_queue.py` `_worker_loop()` and `_enqueue_continuation()`
**Trigger:** Continuation is created after the worker's drain guard check
**Data prerequisite:** Pending continuation must exist in Redis
**State prerequisite:** Worker has already decided to exit (both `_pop_job` calls returned None)
**Mitigation:** Watchdog pending recovery spawns a new worker. Also, `_enqueue_continuation()` already calls `_ensure_worker()` which would start a new worker if the old one exited.

## No-Gos (Out of Scope)

- Fixing Popoto's KeyField index corruption -- work around it, don't fix the library
- Adding session locking/mutex -- the single-worker-per-project model is sufficient
- Redesigning the auto-continue flow -- the guard + recovery approach is proportional

## Update System

No update system changes required -- this is a bridge-internal bug fix with no new dependencies or config.

## Agent Integration

No agent integration required -- this is a bridge-internal change that fixes session lifecycle management.

## Documentation

- [ ] Update `docs/features/stall-retry.md` to document pending session recovery
- [ ] Update `docs/features/session-lifecycle-diagnostics.md` to document the stale save guard
- [ ] Add inline comments explaining why the stale save is skipped

## Success Criteria

- [ ] Sessions that auto-continue never leave ghost records in Redis
- [ ] Pending sessions stalled > 5 minutes are recovered by the watchdog
- [ ] Unit test: `agent_session.save()` is NOT called when `defer_reaction=True`
- [ ] Unit test: watchdog spawns worker for stalled pending sessions
- [ ] Integration test: full auto-continue flow completes without stuck sessions
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-fix)**
  - Name: session-fixer
  - Role: Fix the stale save guard and add pending recovery
  - Agent Type: builder
  - Resume: true

- **Validator (session-fix)**
  - Name: session-validator
  - Role: Verify fixes work correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix stale session save guard
- **Task ID**: build-stale-save-guard
- **Depends On**: none
- **Assigned To**: session-fixer
- **Agent Type**: builder
- **Parallel**: true
- In `agent/job_queue.py` lines 1620-1624, when `chat_state.defer_reaction` is True, remove the `agent_session.save()` call. Replace with a debug log explaining why the save is skipped.
- Add a defensive re-read of `agent_session` from Redis before the `complete_transcript()` call in the non-deferred path.

### 2. Add pending stall recovery to watchdog
- **Task ID**: build-pending-recovery
- **Depends On**: none
- **Assigned To**: session-fixer
- **Agent Type**: builder
- **Parallel**: true
- In `monitoring/session_watchdog.py`, after `check_stalled_sessions()` detects a pending stall, call `_ensure_worker()` for the session's `project_key` to spawn a worker if none exists.
- Add a `_recover_stalled_pending()` function that is called from the watchdog loop alongside `check_stalled_sessions()`.

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-stale-save-guard, build-pending-recovery
- **Assigned To**: session-fixer
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit test: verify no save when `defer_reaction=True`
- Unit test: verify watchdog calls `_ensure_worker` for stalled pending sessions
- Integration test: auto-continue flow doesn't leave ghost sessions

### 4. Validate fix
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: session-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify success criteria met
- Check no regressions in existing lifecycle tests

## Validation Commands

- `pytest tests/test_lifecycle_transition.py -v` - Existing lifecycle tests still pass
- `pytest tests/unit/test_stall_detection.py -v` - Stall detection tests still pass
- `pytest tests/test_session_stuck_pending.py -v` - New tests for this fix
- `grep -n "agent_session.save" agent/job_queue.py` - Verify stale save is removed
