---
status: Shipped
type: bug
appetite: Small
owner: Valor
created: 2026-04-08
tracking: https://github.com/tomcounsell/ai/issues/822
last_comment_id:
---

# Worker Restart: Session Recovery (Pending Preserved, Running Re-queued)

## Problem

When the standalone worker (`python -m worker`) is restarted mid-execution — either by `/update` or by `./scripts/valor-service.sh restart` — sessions are left in the wrong terminal state and permanently lost.

Two failure modes observed after a batch of 5 SDLC sessions were queued and the worker restarted:

**Failure mode 1 — `pending` sessions killed by stale cleanup:**
`_cleanup_stale_sessions()` at `scripts/update/run.py:194` iterates both `"running"` and `"pending"` statuses. A `pending` session has never run and has no stale process to clean up, but if its `updated_at` heartbeat is old (e.g., it was enqueued a while ago and the worker hadn't started it yet), the cleanup logic treats it as stale and kills it.

**Failure mode 2 — `running` sessions marked `completed` after worker kill:**
When the worker process is killed while a session is executing, the `asyncio.CancelledError` handler at `agent/agent_session_queue.py:1971` logs "worker cancelled" and calls `_complete_agent_session(session, failed=True)` — marking the session `failed`. However, the issue body documents `completed` being observed, which comes from the `finally` block at line 2001: when `session_completed=False` and `session_failed=False` (i.e., the outer `CancelledError` was not caught by the inner handler), `target = "failed" if session_failed else "completed"` evaluates to `"completed"`. Either path is wrong — the session was interrupted, not finished.

**Current behavior:** Sessions that were `pending` or `running` at restart time end up in terminal states (`killed`, `failed`, `completed`) and are permanently lost — the work is never retried.

**Desired outcome:** A worker restart is a non-destructive operation. `pending` sessions are left untouched. `running` sessions are transitioned back to `pending` so the new worker picks them up and retries the work.

## Prior Art

- **Issue #738 / PR #739** (fix: session lifecycle stale cleanup and state corruption) — Established `updated_at` heartbeat as primary liveness signal for stale cleanup; added `_active_workers` registry guard; routed terminal transitions through `finalize_session()`. Merged 2026-04-06. This fix correctly addressed live-session detection but did not fix the `pending` status over-reach or the `running`→`completed` path on worker interrupt.

## Data Flow

### Failure mode 1: `pending` session killed

1. **Entry**: `/update` or restart script invokes `_cleanup_stale_sessions(project_dir)` before starting new worker
2. **`scripts/update/run.py:194`**: loops `for status in ("running", "pending")` — iterates all `pending` sessions
3. **Liveness check**: `updated_at` recency check — a `pending` session that was enqueued long ago but not yet started may have no `updated_at` (or a stale one), failing the recency check
4. **`finalize_session(s, "killed", ...)`**: session transitions to terminal `killed` state — permanently lost
5. **New worker starts**: does not see the session because it is no longer in the `pending` index

### Failure mode 2: `running` session marked `completed` after interrupt

1. **Entry**: Worker process receives SIGTERM or SIGKILL during `_execute_agent_session(session)`
2. **`asyncio.CancelledError` inner handler** (`agent_session_queue.py:1971`): catches cancel, calls `_complete_agent_session(session, failed=True)`, sets `session_completed=True`
3. **`finally` block** (`agent_session_queue.py:2001`): if `session_completed` is False (cancel bypassed inner handler), evaluates `target = "failed" if session_failed else "completed"` → `"completed"` since `session_failed` is also False
4. **`_complete_agent_session(session, failed=False)`**: transitions to `"completed"` — session appears done
5. **New worker starts**: does not see the session because it is in terminal `completed` state

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #739 (#738) | Added `updated_at` heartbeat liveness guard; `_active_workers` registry guard; routed cleanup through `finalize_session()` | Addressed live-session detection (a different bug) but did not fix semantic correctness: (1) `pending` sessions are never stale and should never be in the cleanup loop; (2) interrupted `running` sessions should be re-queued, not terminated |

**Root cause pattern:** Each fix addressed a liveness-detection symptom. The underlying semantic issue — that `pending` means "never started" and `running` interrupted by restart means "needs retry" — was never encoded in the cleanup logic.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — both fix sites are self-contained in the existing codebase.

## Solution

### Key Elements

- **Remove `"pending"` from cleanup loop**: `_cleanup_stale_sessions()` should iterate only `"running"` sessions. A `pending` session has no process to clean up; leaving it in place allows the new worker to pick it up naturally.
- **Re-queue interrupted `running` sessions**: In the `CancelledError` path in `_worker_loop`, call `transition_status(session, "pending", reason="worker restart")` instead of `_complete_agent_session(session, failed=True)`. Since `running` is non-terminal, `transition_status()` accepts this directly.
- **Detect restart vs. genuine failure**: The `CancelledError` path currently conflates "cancelled because the worker is shutting down" with other cancellations. Add a `session_interrupted` flag (analogous to `session_failed` and `session_completed`) to distinguish restart-interrupted sessions from sessions that genuinely failed.

### Technical Approach

**Fix 1 — `scripts/update/run.py`:**
- Change `for status in ("running", "pending"):` → `for status in ("running",):`
- Update the docstring to reflect that `pending` sessions are intentionally excluded
- The `killed_count` / `skipped_live` return tuple is unchanged

**Fix 2 — `agent/agent_session_queue.py`:**
- Add `session_interrupted = False` alongside `session_failed = False` / `session_completed = False`
- In the `asyncio.CancelledError` inner handler (line 1971): set `session_interrupted = True`, call `transition_status(session, "pending", reason="worker restart")` instead of `_complete_agent_session(session, failed=True)`, set `session_completed = True` (to suppress the `finally` block from double-processing)
- In the `finally` block (line 2002): the guard `if not session_completed` already suppresses double-processing — no change needed there if `session_completed = True` is set in the cancel handler
- Log the re-queue clearly: `"[chat:%s] Session %s re-queued to pending (worker restart)"`

**Fix 3 — `_cleanup_stale_sessions` `pending` guard comment:**
- Add an explicit comment in the cleanup function: `# pending sessions are never stale — they were never started; only running sessions can be orphaned`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `transition_status(session, "pending", ...)` call in the cancel handler must not raise — `running` → `pending` is a valid non-terminal transition. Add a test asserting the call succeeds.
- [ ] If `transition_status` raises (e.g., session already in terminal state from a concurrent finalize), the except clause should log and not re-raise, allowing the cancel to propagate normally.

### Empty/Invalid Input Handling
- [ ] `_cleanup_stale_sessions` with an empty `pending` list — should no-op gracefully (already handled by `list()` iteration)
- [ ] Cancel handler with a session that was already finalized by stale cleanup — `transition_status` will raise `ValueError` (terminal→non-terminal); catch and log

### Error State Rendering
- Not applicable — this is a background worker path with no user-visible output beyond logs.

## Test Impact

- [ ] `tests/unit/test_stale_cleanup.py` — UPDATE: all tests currently set `sessions_by_status = {"running": [stale_session], "pending": []}` with an empty `pending` list. After Fix 1, `pending` sessions are never passed to the cleanup loop. Add a new test: `test_pending_sessions_never_killed` — pass a stale `pending` session and verify `finalize_session` is NOT called.
- [ ] `tests/unit/test_stale_cleanup.py::test_finalize_exception_does_not_abort_loop` — UPDATE: adjust `sessions_by_status` to confirm `pending` sessions are excluded before even reaching `finalize_session`

## Rabbit Holes

- **Re-implementing a PID-based liveness check**: Already rejected in issue #822 recon. Semantic logic (`pending` = never started, interrupted `running` = retry) is cleaner and more correct than age/PID heuristics.
- **Generalized session recovery / checkpointing**: Tempting to add a checkpoint before cancellation so the session can resume mid-execution. Out of scope — restart-triggered retries re-run the full session from the start, which is correct for the SDLC pipeline (idempotent by design).
- **Distributed lock / "who owns this session" tracking**: The `_active_workers` registry is process-local. Cross-process liveness tracking would require a Redis-based lock. Out of scope — the existing `updated_at` heartbeat is sufficient for the cleanup use case.
- **Changing the SIGTERM handler in `worker/__main__.py`**: The worker already has graceful shutdown logic. The issue is in how interrupted sessions are classified, not in the shutdown signal handling.

## Risks

### Risk 1: Re-queued session retried with stale context
**Impact:** A session that was 90% complete re-runs from scratch, potentially producing duplicate side effects (duplicate Telegram messages, duplicate Git commits).
**Mitigation:** The SDLC pipeline is designed to be idempotent — `stage_states` prevents re-executing completed stages. For non-SDLC conversational sessions, re-running from scratch is acceptable (the user's original message is replayed).

### Risk 2: `transition_status` raises because session was already finalized
**Impact:** The re-queue fails silently if the stale cleanup ran concurrently and already killed the session.
**Mitigation:** Wrap the `transition_status` call in a try/except that logs the failure and suppresses the error. The session is already terminal — no double-finalization occurs.

## Race Conditions

### Race 1: Stale cleanup runs while worker is shutting down
**Location:** `scripts/update/run.py:194` and `agent/agent_session_queue.py:1971`
**Trigger:** Update script kills the worker → `CancelledError` fires → cancel handler calls `transition_status(session, "pending")` → cleanup script simultaneously reads the session as `"running"` and kills it
**Data prerequisite:** The session's status must be written to Redis (`pending`) before the cleanup loop reads it
**State prerequisite:** The cleanup loop must see the updated status index, not a cached snapshot
**Mitigation:** `transition_status()` calls `session.save()` which updates the Redis index atomically. However, the cleanup loop iterates a snapshot from `AgentSession.query.filter(status="running")` — sessions that transition to `pending` during iteration are already excluded from the snapshot. This race is self-resolving: if the snapshot already included this session as `running`, the cleanup will kill it — but by then the cancel handler has set `session_completed=True`, which means the `finally` block skips `_complete_agent_session`. The session is killed by cleanup (already re-queued as `pending` then immediately killed) — this is a real race. Mitigation: in the cancel handler, after `transition_status(session, "pending")`, log a warning if the session is later found in `killed` state. Acceptable risk for the Small appetite; a full solution requires a Redis lock.

## No-Gos (Out of Scope)

- Session checkpointing / mid-execution resume — sessions re-run from the start on retry
- Cross-process liveness tracking beyond `updated_at` heartbeat
- Changes to the SIGTERM handler or watchdog behavior
- Retrying sessions that genuinely `failed` (non-cancellation exceptions)

## Update System

Fix 1 (`scripts/update/run.py`) modifies the update script itself. The change is backward-compatible — removing `"pending"` from the cleanup loop is safe on all existing installations. No migration needed. No new config. The `/update` skill (`scripts/remote-update.sh`) does not need changes.

## Agent Integration

No agent integration required — this is an internal worker lifecycle fix. No MCP server changes, no `.mcp.json` changes, no bridge changes.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` — add a note to the "Session Lifecycle" or "Worker Restart" section explaining that `pending` sessions survive restarts and interrupted `running` sessions are re-queued, not terminated.
- [ ] Add entry to `docs/features/README.md` if a new feature doc is created (no new doc needed here — this is an update to an existing doc).

## Success Criteria

- [ ] `_cleanup_stale_sessions()` no longer iterates `"pending"` sessions — confirmed by reading `scripts/update/run.py:194` after the fix
- [ ] After a simulated worker restart, a `pending` session remains `pending` in Redis
- [ ] After a simulated worker cancellation, an interrupted `running` session is transitioned to `pending` (not `completed`, `failed`, or `killed`) and is available for the new worker to pop
- [ ] `tests/unit/test_stale_cleanup.py::test_pending_sessions_never_killed` — new test passes
- [ ] All existing stale cleanup tests still pass
- [ ] Ruff lint and format clean

## Team Orchestration

### Team Members

- **Builder (session-recovery)**
  - Name: session-recovery-builder
  - Role: Implement both code fixes (cleanup loop and cancel handler)
  - Agent Type: builder
  - Resume: true

- **Test Engineer (session-recovery)**
  - Name: session-recovery-tester
  - Role: Write new unit tests covering the two fixed paths
  - Agent Type: test-engineer
  - Resume: true

- **Validator (session-recovery)**
  - Name: session-recovery-validator
  - Role: Verify all success criteria, run full test suite
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix stale cleanup loop to exclude `pending` sessions
- **Task ID**: build-cleanup-fix
- **Depends On**: none
- **Validates**: `tests/unit/test_stale_cleanup.py`
- **Assigned To**: session-recovery-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/update/run.py`: change `for status in ("running", "pending"):` to `for status in ("running",):`
- Update the `_cleanup_stale_sessions` docstring: remove references to `pending` from the list of iterated statuses; add a note that `pending` sessions are never stale
- Confirm the `killed_count`/`skipped_live` return tuple is unchanged

### 2. Fix cancel handler to re-queue interrupted sessions
- **Task ID**: build-cancel-fix
- **Depends On**: none
- **Validates**: (new unit test — see task 3)
- **Assigned To**: session-recovery-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/agent_session_queue.py`, in the `asyncio.CancelledError` inner handler (line ~1971):
  - Replace `_complete_agent_session(session, failed=True)` with `transition_status(session, "pending", reason="worker restart")`
  - Wrap the `transition_status` call in try/except — on error, log a warning and fall back to `_complete_agent_session(session, failed=True)`
  - Keep `session_completed = True` so the `finally` block skips double-processing
  - Log: `"[chat:%s] Session %s re-queued to pending after worker cancellation"`
  - Remove the `session.log_lifecycle_transition("failed", "worker cancelled")` call immediately before (or change it to `"pending"`)
- Import `transition_status` at the top of the cancel handler's try block (lazy import to avoid circular imports if needed)

### 3. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-cleanup-fix, build-cancel-fix
- **Validates**: `tests/unit/test_stale_cleanup.py`, new `tests/unit/test_worker_cancel_requeue.py`
- **Assigned To**: session-recovery-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_pending_sessions_never_killed` to `tests/unit/test_stale_cleanup.py`: create a stale `pending` session (old `updated_at`, old `created_at`) and verify `finalize_session` is NOT called
- Add `test_pending_sessions_excluded_from_loop` to `tests/unit/test_stale_cleanup.py`: verify that even when `pending` sessions are present in Redis, they are never passed to `finalize_session`
- Create `tests/unit/test_worker_cancel_requeue.py`: mock `transition_status` and verify the cancel handler calls `transition_status(session, "pending", ...)` and sets `session_completed=True`
- Verify all existing `test_stale_cleanup.py` tests still pass

### 4. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-cleanup-fix, build-cancel-fix
- **Assigned To**: session-recovery-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md`: add a paragraph or bullet in the session lifecycle section explaining the worker restart recovery semantics

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: session-recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_stale_cleanup.py tests/unit/test_worker_cancel_requeue.py -v`
- Run `python -m ruff check . && python -m ruff format --check .`
- Confirm all success criteria are met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_stale_cleanup.py tests/unit/test_worker_cancel_requeue.py -v` | exit code 0 |
| Cleanup loop fix | `grep -n '"pending"' scripts/update/run.py \| grep 'for status'` | exit code 1 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| high | Skeptic | Fix 2 overlaps with startup recovery — `_recover_interrupted_agent_sessions_startup()` in `worker/__main__.py` already re-queues stale `running` sessions to `pending` on new-worker boot. If the cancel handler also calls `transition_status(session, "pending")`, both paths fire on the same record; if `transition_status` is not idempotent for `pending→pending` it will error. | Simplify Fix 2 | Remove `transition_status` call from cancel handler; rely entirely on startup recovery. Cancel handler only needs to suppress `_complete_agent_session(failed=True)` and set `session_completed=True`. |
| high | Adversary | Race condition analysis is incorrect — `session_completed=True` only guards the coroutine stack in the dying worker; it does not stop the update-script subprocess from calling `finalize_session()` on the same Redis record after the cancel handler sets status to `pending`. | Verify before build | Confirm whether `finalize_session` re-reads current status from Redis before writing, or operates on the stale passed-in object. |
| high | Archaeologist | `"pending"` was added to the cleanup loop deliberately in PR #739 — plan should document why it was wrong from the start, not just remove it. | Add comment | Add inline comment: `# pending sessions are never stale — they were never started; "pending" was added in PR #739 by mistake` |
| medium | Skeptic | Failure Mode 2 data flow conflates two sub-cases: inner-handler cancel (during `_execute_agent_session`) vs. outer cancel (between sessions). Tests must cover both. | Test authorship | `test_worker_cancel_requeue.py` must test both cancel scenarios with different mocking strategies. |
| medium | Operator | "SDLC sessions are idempotent by design" is asserted, not verified. `stage_states` init code exists but enforcement (skipping completed stages on retry) is not confirmed. | Verify in build | Read `_execute_agent_session` to confirm it checks `stage_states` before each stage. |
| medium | User | Re-queued conversational sessions will send a duplicate Telegram reply with no user notification. | Acceptable risk | Log a warning in session output: "Session interrupted by worker restart — retrying." |
| low | Simplifier | Fix 2 simplification: remove `_complete_agent_session(failed=True)` from cancel handler; leave session in `running`; startup recovery on new worker re-queues it. Avoids the new race entirely. | Adopt | This is the preferred Fix 2 implementation. |
| low | Archaeologist | Task 2 prose says `log_lifecycle_transition` call is "immediately before" but it is inside the `except asyncio.CancelledError:` block. | Prose fix | Clarify: "inside the `except asyncio.CancelledError:` block, before `_complete_agent_session`". |

---

## Open Questions

None — both fix sites are confirmed by recon and the solution is unambiguous.
