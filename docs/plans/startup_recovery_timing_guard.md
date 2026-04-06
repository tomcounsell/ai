---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking:
last_comment_id:
---

# Startup Recovery Timing Guard

## Problem

When the bridge or worker restarts, `_recover_interrupted_agent_sessions_startup()` resets ALL `running` sessions to `pending` with no timing guard. If a worker picks up a session and transitions it to `running` before startup recovery fires (a 1-2 second race window), the recovery function treats it as an orphan and resets it back to `pending` — orphaning the SDK subprocess that was already spawned.

**Current behavior:**
1. Bridge/worker restarts, Telegram connects.
2. Worker picks up a pending session, transitions it `pending -> running`, spawns SDK subprocess.
3. ~1 second later, startup recovery fires, finds the session in `running`, resets it to `pending`.
4. SDK subprocess runs headlessly with no parent supervision. Session stalls until the periodic health check eventually kills it.

**Desired outcome:**
Startup recovery respects the same `AGENT_SESSION_HEALTH_MIN_RUNNING` (300s) timing guard that the periodic health check uses. Sessions that started within the last 5 minutes are skipped — they are not orphans from the previous process but active sessions from the current one.

## Prior Art

- **Issue #723**: Session recovery audit — completed audit of all 7 recovery mechanisms. This bug was discovered in production after #723 landed. The audit identified the inconsistency but the fix was not implemented.
- **Issue #700 / PR #703**: Completed sessions reverting to pending — different failure mode (zombie loop) but same recovery path. Fixed by adding terminal status guards, not timing guards.
- **PR #128**: Job health monitor — introduced the periodic health check with the timing guard. Startup recovery was not updated to match.

## Data Flow

1. **Bridge/worker starts**: `_recover_interrupted_agent_sessions_startup()` is called synchronously during initialization (before the event loop processes messages).
2. **Startup recovery**: Queries `AgentSession.query.filter(status="running")`, iterates all results, calls `transition_status(entry, "pending", reason="startup recovery")` for each.
3. **Worker loop** (concurrent): `_worker_loop()` dequeues pending sessions and transitions them `pending -> running` via the lifecycle module, then spawns the SDK subprocess.
4. **Race**: Steps 2 and 3 can overlap when the worker starts processing before startup recovery completes. The worker transitions a session to `running`, then startup recovery resets it.
5. **Orphan**: The SDK subprocess spawned in step 3 continues running with no parent tracking it. The session sits in `pending` with no worker aware it needs reprocessing.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #703 (#700) | Added terminal status guards to prevent completed sessions from reverting | Different bug class — addressed zombie loops, not timing races. Terminal status guards don't help because the session IS legitimately `running`, not terminal. |
| Issue #723 audit | Identified the inconsistency between startup recovery and health check timing guards | Audit-only — documented the gap but did not ship a fix for the startup path. |

**Root cause pattern:** The timing guard (`AGENT_SESSION_HEALTH_MIN_RUNNING`) was added to the periodic health check but never propagated to startup recovery, which was written earlier with the assumption that ALL running sessions at startup are orphans from a dead process.

## Architectural Impact

- **No new dependencies**: Uses the existing `AGENT_SESSION_HEALTH_MIN_RUNNING` constant.
- **No interface changes**: `_recover_interrupted_agent_sessions_startup()` keeps its signature and return value.
- **Coupling**: Reduces divergence between startup recovery and health check — both use the same guard constant.
- **Reversibility**: Trivial to revert — single function change.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Timing guard in startup recovery**: Filter out sessions whose `started_at` is within `AGENT_SESSION_HEALTH_MIN_RUNNING` seconds of now.
- **Logging for skipped sessions**: Log skipped sessions at INFO level so operators can see what was preserved.

### Flow

**Bridge/worker starts** → startup recovery queries running sessions → filters out sessions started < 300s ago → resets only stale sessions to pending → logs skip count

### Technical Approach

- Import `AGENT_SESSION_HEALTH_MIN_RUNNING` (already defined at line 126) into the startup recovery function.
- Before the recovery loop, compute a cutoff timestamp: `now - AGENT_SESSION_HEALTH_MIN_RUNNING`.
- Filter `running_sessions` to only include entries where `started_at` is `None` or `started_at < cutoff`.
- Log the number of skipped sessions.
- The `started_at` field is set to `None` by the recovery function itself (line 1000), and set to `datetime.now(UTC)` when a worker picks up the session. Sessions from the previous process that crashed mid-execution will have a `started_at` from before the restart — well beyond the 300s cutoff.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing `except Exception` block at line 1003 in `_recover_interrupted_agent_sessions_startup` already logs and deletes corrupted sessions — no change needed.
- [ ] No new exception handlers introduced.

### Empty/Invalid Input Handling
- [ ] Test behavior when `started_at` is `None` (legacy sessions without timestamps) — these should still be recovered.
- [ ] Test behavior when `started_at` is a float vs datetime (the function must handle both, matching `_agent_session_health_check`'s `_ts()` helper pattern).

### Error State Rendering
- No user-visible output — this is internal session management.

## Test Impact

- [ ] `tests/integration/test_agent_session_queue_race.py::TestRecoverInterruptedJobsStartup::test_no_stale_running_after_recovery` — UPDATE: Must set `started_at` to a timestamp older than 300s for the session to be recovered, or the test will fail because the session gets skipped.
- [ ] `tests/integration/test_agent_session_queue_race.py::TestRecoverInterruptedJobsStartup::test_recover_multiple_running_jobs` — UPDATE: Same — set `started_at` to old timestamps.
- [ ] `tests/unit/test_recovery_respawn_safety.py::test_startup_recovery_only_recovers_running` — UPDATE: Set `started_at` to old timestamps on the running session fixture.
- [ ] `tests/unit/test_agent_session_scheduler_kill.py::test_recover_interrupted_agent_sessions_startup_filters_running` — UPDATE: Set `started_at` to old timestamps.

## Rabbit Holes

- Refactoring startup recovery and health check into a shared function — tempting but unnecessary for a Small fix. The two paths have different async/sync constraints and different caller contexts.
- Adding a lock/mutex around status transitions — overkill for this race. The timing guard eliminates the window without adding concurrency primitives.
- Killing orphaned SDK subprocesses retroactively — separate concern (issue #727 warning #4). The fix here prevents the orphan from being created in the first place.

## Risks

### Risk 1: Sessions from crashed process with recent `started_at` are not recovered
**Impact:** A session that was genuinely running when the process crashed (within the last 5 minutes) will not be recovered at startup. It will remain in `running` status until the periodic health check picks it up ~5 minutes later.
**Mitigation:** This is acceptable — the periodic health check runs every 5 minutes with the same guard. The worst case is a 5-minute delay before recovery, not a missed recovery.

## Race Conditions

### Race 1: Worker picks up session before startup recovery completes
**Location:** `agent/agent_session_queue.py` lines 972-1015 (startup recovery) vs worker loop
**Trigger:** Worker dequeues and transitions a session to `running` in the 1-2 seconds between Telegram connection and startup recovery execution.
**Data prerequisite:** A `pending` session exists in Redis when the worker starts.
**State prerequisite:** The session's `started_at` is set to `datetime.now(UTC)` by the worker.
**Mitigation:** The timing guard skips sessions with `started_at` within the last 300 seconds. A session started 1-2 seconds ago will have `started_at ≈ now`, which is well within the guard window and will be skipped.

## No-Gos (Out of Scope)

- Refactoring startup recovery and health check into a unified recovery function.
- Adding subprocess tracking to detect/kill orphaned SDK processes.
- Changing the `AGENT_SESSION_HEALTH_MIN_RUNNING` value (300s is appropriate for both paths).
- Adding Redis-level locking for status transitions.

## Update System

No update system changes required — this is a bug fix in internal session management code. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to the session recovery mechanism. No MCP servers, tools, or bridge imports affected.

## Documentation

- [ ] Update `docs/features/session-recovery-mechanisms.md` — add note about timing guard in startup recovery section
- [ ] Update inline docstring for `_recover_interrupted_agent_sessions_startup()` to document the timing guard

## Success Criteria

- [ ] `_recover_interrupted_agent_sessions_startup()` skips sessions with `started_at` within the last `AGENT_SESSION_HEALTH_MIN_RUNNING` seconds
- [ ] Sessions with `started_at=None` (legacy/corrupt) are still recovered
- [ ] Skipped sessions are logged at INFO level with count
- [ ] New test: session started 10 seconds ago is NOT recovered by startup recovery
- [ ] New test: session started 600 seconds ago IS recovered by startup recovery
- [ ] New test: session with `started_at=None` IS recovered by startup recovery
- [ ] Existing tests updated and passing
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (timing-guard)**
  - Name: guard-builder
  - Role: Implement timing guard in startup recovery and update tests
  - Agent Type: builder
  - Resume: true

- **Validator (timing-guard)**
  - Name: guard-validator
  - Role: Verify the fix prevents the race condition
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement timing guard
- **Task ID**: build-timing-guard
- **Depends On**: none
- **Validates**: tests/integration/test_agent_session_queue_race.py, tests/unit/test_recovery_respawn_safety.py
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add timing guard to `_recover_interrupted_agent_sessions_startup()` in `agent/agent_session_queue.py`: compute cutoff from `AGENT_SESSION_HEALTH_MIN_RUNNING`, filter sessions by `started_at`, log skipped count
- Update docstring to document the guard
- Update existing tests in `test_agent_session_queue_race.py` and `test_recovery_respawn_safety.py` to set `started_at` to old timestamps
- Add new tests: recently-started session skipped, old session recovered, None `started_at` recovered

### 2. Validate fix
- **Task ID**: validate-timing-guard
- **Depends On**: build-timing-guard
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_recover_interrupted_agent_sessions_startup()` uses `AGENT_SESSION_HEALTH_MIN_RUNNING`
- Verify all existing tests pass with updated fixtures
- Verify new tests cover the three key scenarios (recent, old, None)
- Run full test suite

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-timing-guard
- **Assigned To**: guard-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-recovery-mechanisms.md` with timing guard note
- Verify inline docstring is updated

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_recovery_respawn_safety.py tests/integration/test_agent_session_queue_race.py tests/unit/test_agent_session_scheduler_kill.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py` | exit code 0 |
| Guard constant used | `grep -c 'AGENT_SESSION_HEALTH_MIN_RUNNING' agent/agent_session_queue.py` | output > 2 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions — the issue is thoroughly diagnosed with production logs, the fix is well-defined (apply existing guard constant to the unguarded path), and the approach mirrors the existing periodic health check implementation.
