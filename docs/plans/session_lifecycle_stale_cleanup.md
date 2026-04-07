---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/738
last_comment_id:
---

# Fix: Stale Session Cleanup Kills Live Sessions and Corrupts State on Forced Termination

## Problem

When the bridge restarts during an active SDLC session, two interacting bugs corrupt the session's final state in Redis:

**Bug 1 — Dead PID guard:** `_cleanup_stale_sessions` in `scripts/update/run.py` checks `getattr(s, "pid", None)` to decide whether a session has a live process. `AgentSession` has no `pid` field, so `getattr` always returns `None`. The guard at line 189 is dead code. Any `running` or `pending` session older than 30 minutes is killed unconditionally — including sessions whose workers are still actively executing.

**Bug 2 — Stale in-memory snapshot on cancellation:** When a bridge restart cancels the asyncio worker task (`asyncio.CancelledError` in `_worker_loop`), the handler calls `_complete_agent_session(session, failed=True)` on the in-memory `session` object. If `_cleanup_stale_sessions` ran during the session's lifetime and delete-and-recreated the Redis record (producing a new `id`), the in-memory `session` object holds the old `id` and — critically — the old `session_events` list from session creation time. Any SDLC stage transitions written to Redis during execution are absent from the in-memory copy. The final `finalize_session` call saves the stale snapshot, overwriting accumulated `stage_states` with the initial all-pending state.

**Bug 3 — Lifecycle bypass:** `_cleanup_stale_sessions` performs a raw delete-and-recreate (`s.delete()` / `AgentSession.create(**fields)`) instead of routing through `finalize_session`. This bypasses `log_lifecycle_transition`, `auto_tag_session`, `checkpoint_branch_state`, and parent finalization.

**Current behavior:** Session `tg_valor_-1003449100931_441` produced two Redis records — a `killed` record (`id=873411799eca`) with `PLAN=completed, REVIEW=completed, DOCS=completed, MERGE=ready`, and a subsequent `failed` record (`id=9a4823ca2db4`) with `stage_states` reset to all-pending — exactly the Bug 1+2 failure chain. The session's 26 minutes of productive SDLC work was lost from the final record.

**Desired outcome:** After a bridge restart during an active session, the surviving Redis record reflects the actual SDLC progress made before the restart. Lifecycle hooks fire for every terminal status transition, including stale cleanup.

## Prior Art

No prior issues or PRs found for "stale session cleanup", "cleanup kills live", or "stage_states corrupt". This is the first investigation of this failure mode.

## Spike Results

No spikes needed — the root cause is confirmed by code inspection and live session evidence. The fix approach is clear from reading the three affected code sites.

## Data Flow

**Bug 1+2 failure chain:**

1. **Session created** — `AgentSession` created with `id=9a4823ca2db4`, status=`running`; `_active_workers["chat_id"]` = asyncio Task
2. **Session executes** — SDK runs; `PipelineStateMachine` appends stage-change events to `session_events` in Redis
3. **`_cleanup_stale_sessions` fires** (30 min age threshold crossed) — reads in-memory `AgentSession` object, calls `s.delete()`, calls `AgentSession.create(**fields)` with status=`killed` → produces new record `id=873411799eca`. Old `id=9a4823ca2db4` is gone from Redis. The in-memory `session` object in the worker still holds `id=9a4823ca2db4` and the `session_events` snapshot from session start.
4. **Bridge restart** — asyncio task cancelled; `_worker_loop` catches `CancelledError`; calls `_complete_agent_session(session, failed=True)` with the stale in-memory object → `finalize_session` calls `session.save()` → writes `id=9a4823ca2db4` back to Redis with initial `session_events`, clobbering the enriched `killed` record.

**Bug 3 impact:**

At step 3, `auto_tag_session`, `checkpoint_branch_state`, `log_lifecycle_transition`, and parent finalization are all skipped because the raw delete-and-recreate bypasses `finalize_session`.

## Architectural Impact

- **Interface changes:** `finalize_session` already accepts `skip_checkpoint: bool` — no signature change needed for Bug 3. `_complete_agent_session` will add a Redis re-read before calling `finalize_session`.
- **New dependencies:** `_cleanup_stale_sessions` will import `_active_workers` from `agent.agent_session_queue` and `finalize_session` from `models.session_lifecycle`. Both are already imported in adjacent code.
- **Coupling:** Slightly increases coupling between `scripts/update/run.py` and `agent.agent_session_queue` (`_active_workers`), but this is the correct coupling — the update script runs in the same process as the queue.
- **Data ownership:** No change — Redis remains the source of truth; we are fixing the write path to respect that.
- **Reversibility:** All three fixes are surgical — revert individual lines or the whole commit cleanly.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment before build)
- Review rounds: 1 (code review + test validation)

## Prerequisites

No prerequisites — this work modifies existing Python files, no new dependencies or environment changes required.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | AgentSession reads/writes |
| Pytest available | `pytest --version` | Running unit tests |

## Solution

### Key Elements

- **Bug 1 — `_active_workers` cross-reference:** Before killing a session, check if `chat_id` has a live entry in `_active_workers`. If a live asyncio Task exists for the session's `chat_id`, skip it. As a fallback for sessions whose `chat_id` is missing or not in the registry, raise the default `age_minutes` threshold from 30 to 120 minutes — reflecting that truly orphaned sessions (dead process, no record in registry) are rare and warrant a longer grace period.
- **Bug 2 — Redis re-read in `_complete_agent_session`:** Before calling `finalize_session`, query Redis for the current `running` record matching the session's `session_id`. If found, use the fresh record instead of the stale in-memory object. This captures all `session_events` accumulated during execution (including SDLC stage transitions).
- **Bug 3 — Route through `finalize_session`:** Replace the raw delete-and-recreate in `_cleanup_stale_sessions` with `finalize_session(s, "killed", reason="stale cleanup (no live process)", skip_checkpoint=True)`. `finalize_session` already handles Popoto's save pattern via `session.save()` — but for the `status` KeyField constraint, it may need to delegate to a wrapper that does the delete-and-recreate internally while still firing all hooks. Investigate whether `finalize_session` → `session.save()` is sufficient or if a `force_recreate` path is needed.

### Flow

Session running >30 min → `_cleanup_stale_sessions` fires → check `_active_workers[chat_id]` → live worker found → **skip** → session continues running

Session running >120 min (fallback) → `_cleanup_stale_sessions` fires → no live worker found → `finalize_session(s, "killed", reason="stale cleanup", skip_checkpoint=True)` → lifecycle hooks fire → single clean `killed` record in Redis

Bridge restart → `CancelledError` fires → `_complete_agent_session(session, failed=True)` → **re-read session from Redis** → use fresh record → `finalize_session(fresh, "failed", ...)` → final record preserves accumulated `stage_states`

### Technical Approach

**Bug 1 fix in `scripts/update/run.py:_cleanup_stale_sessions`:**
```python
from agent.agent_session_queue import _active_workers

# Before age check:
chat_id = getattr(s, "chat_id", None)
if chat_id and chat_id in _active_workers:
    continue  # live worker exists, skip
```
Also update default `age_minutes` from 30 to 120.

**Bug 2 fix in `agent/agent_session_queue.py:_complete_agent_session`:**
```python
async def _complete_agent_session(session: AgentSession, *, failed: bool = False) -> None:
    from models.agent_session import AgentSession as AS
    # Re-read to capture stage events written during execution
    try:
        running = list(AS.query.filter(session_id=session.session_id, status="running"))
        if running:
            session = running[0]
    except Exception:
        pass  # fall back to in-memory object
    status = "failed" if failed else "completed"
    finalize_session(session, status, reason="agent session completed")
```

**Bug 3 fix in `scripts/update/run.py:_cleanup_stale_sessions`:**
Replace lines 196–200 (raw delete-and-recreate) with:
```python
from models.session_lifecycle import finalize_session
finalize_session(s, "killed", reason="stale cleanup (no live process)", skip_checkpoint=True)
```
Verify that `finalize_session` → `session.save()` is sufficient for the Popoto KeyField constraint, or extend `finalize_session` to support `force_recreate=True` if the KeyField mutation requires it.

## Failure Path Test Strategy

### Exception Handling Coverage
- `_cleanup_stale_sessions` wraps its loop in try/except per session — existing pattern. New `finalize_session` call may raise `ValueError` for invalid status; ensure the caller catches and logs rather than halting the loop.
- `_complete_agent_session` Redis re-read is wrapped in try/except — falls back to in-memory object, preventing a hard crash during cleanup.
- Test: verify that a `finalize_session` exception inside `_cleanup_stale_sessions` does not abort cleanup of the remaining sessions.

### Empty/Invalid Input Handling
- `session_id` may be None on very old sessions — the re-read in `_complete_agent_session` should handle `None` gracefully (skip re-read, fall through to in-memory).
- `chat_id` may be None on system sessions — `_active_workers` check must guard against `None` key lookups.

### Error State Rendering
- No user-visible output — both functions are internal. Errors surface in bridge logs; verify `logger.warning` fires when re-read fails or live-worker check is skipped.

## Test Impact

- [ ] `tests/unit/test_crash_snapshot.py::test_cancelled_error_handler_logs_lifecycle_transition` — UPDATE: assert that re-read from Redis occurs before `finalize_session`; verify `stage_states` from fresh record are preserved
- [ ] `tests/unit/test_session_registry.py` — No direct impact (tests `cleanup_stale` from a different module); review to confirm no naming collision with new `_cleanup_stale_sessions` tests

## Rabbit Holes

- **Adding a `heartbeat_at` field to `AgentSession`:** Tempting but requires a Popoto schema change, migration concerns, and adds ongoing maintenance. The `_active_workers` registry already solves the problem in-process.
- **Rewriting `_cleanup_stale_sessions` as async:** The function runs in a sync context during `/update`. Keep it sync; the `_active_workers` dict read is thread-safe for reads.
- **Making `finalize_session` async:** `finalize_session` is intentionally sync to be callable from both sync and async contexts. Don't async-ify it.
- **Global fix for Popoto KeyField mutation:** The delete-and-recreate pattern is a Popoto limitation. Fixing it globally is a separate project (`models/` refactor). This plan only routes the specific call in `_cleanup_stale_sessions` through the existing lifecycle layer.

## Risks

### Risk 1: `finalize_session` cannot handle Popoto KeyField status mutation
**Impact:** `finalize_session(s, "killed", ...)` calls `session.save()` which may fail if `status` is a KeyField requiring delete-and-recreate.
**Mitigation:** Investigate `finalize_session`'s save path before building. If `session.save()` is insufficient, add `force_recreate=True` parameter to `finalize_session` that internally does the delete-and-recreate before firing hooks on the new record.

### Risk 2: `_active_workers` is not accessible from `scripts/update/run.py`
**Impact:** Import of `_active_workers` from `agent.agent_session_queue` may fail if the update script runs in a subprocess context where the queue module is not initialized.
**Mitigation:** Wrap the import in try/except. If import fails, fall back to the raised age threshold (120 min) rather than killing all sessions. Log a warning when the registry is not accessible.

### Risk 3: Re-read in `_complete_agent_session` finds multiple running records
**Impact:** If two `running` records exist for the same `session_id` (unlikely but possible during a race), taking `running[0]` may use the wrong record.
**Mitigation:** Log a warning if multiple running records found; take the most recently created one (sort by `created_at` descending).

## Race Conditions

### Race 1: `_cleanup_stale_sessions` and `_complete_agent_session` run concurrently
**Location:** `scripts/update/run.py:196–200` and `agent/agent_session_queue.py:887–903`
**Trigger:** Bridge restart fires cancellation while `_cleanup_stale_sessions` is mid-execution (between `s.delete()` and `AgentSession.create()`).
**Data prerequisite:** The new Redis record from `_cleanup_stale_sessions` must be fully created before `_complete_agent_session` re-reads it.
**State prerequisite:** `session_id` must be stable (not reassigned) across the delete-and-recreate.
**Mitigation:** The re-read in `_complete_agent_session` queries by `session_id` (not `id`), so it finds the new record regardless of which `id` was assigned. If the re-read finds `status="killed"` (already finalized), `finalize_session` is idempotent and will log + return without double-writing.

### Race 2: `_active_workers` read races with worker removal
**Location:** `scripts/update/run.py:_cleanup_stale_sessions` and `agent/agent_session_queue.py:1643`
**Trigger:** Worker completes and removes itself from `_active_workers` between the age check and the registry check in `_cleanup_stale_sessions`.
**Mitigation:** False negative (session removed from registry before cleanup checks it) results in the session being correctly cleaned up by `_cleanup_stale_sessions` — this is the desired behavior for genuinely completed sessions that weren't finalized.

## No-Gos (Out of Scope)

- Persisting `pid` or `heartbeat_at` to `AgentSession` — out of scope; `_active_workers` solves Bug 1 without schema changes
- Fixing the Popoto KeyField delete-and-recreate pattern globally — separate project
- Adding distributed locking around `_cleanup_stale_sessions` — not needed; the function already runs at most once per `/update`
- Monitoring or alerting on duplicate Redis records — separate observability concern

## Update System

`_cleanup_stale_sessions` is called by `scripts/update/run.py` which is part of the update pipeline. The fix modifies behavior of this function (raises age threshold to 120 min, routes through `finalize_session`). The update script itself does not need structural changes — the fix is within the existing function. No new config files, dependencies, or migration steps required.

## Agent Integration

No agent integration required — all three bugs are in bridge-internal session management code. No MCP servers, `.mcp.json`, or bridge routing changes needed.

## Documentation

- [ ] Update `docs/features/session-lifecycle.md` (if it exists) to document that `_cleanup_stale_sessions` now uses `_active_workers` for live-session detection and routes through `finalize_session`
- [ ] Update docstring for `_cleanup_stale_sessions` in `scripts/update/run.py` to describe the new behavior (age threshold, registry check, lifecycle routing)
- [ ] Update docstring for `_complete_agent_session` in `agent/agent_session_queue.py` to document the Redis re-read before finalization

## Success Criteria

- [ ] `_cleanup_stale_sessions` skips sessions whose `chat_id` has a live entry in `_active_workers`
- [ ] `_cleanup_stale_sessions` calls `finalize_session("killed", ..., skip_checkpoint=True)` instead of raw delete-and-recreate
- [ ] `_complete_agent_session` re-reads the session from Redis before calling `finalize_session`
- [ ] After a bridge restart on a 35+ minute session, the surviving Redis record's `stage_states` reflects actual SDLC progress (not initial all-pending state)
- [ ] No duplicate Redis records (two different `id` values for the same `session_id`) after stale-cleanup + cancellation sequence
- [ ] Unit test: `_cleanup_stale_sessions` does not kill a session when `_active_workers[chat_id]` contains a live Task
- [ ] Unit test: `_complete_agent_session` uses the fresh Redis record's `session_events` when a more recent record exists
- [ ] Tests pass (`/do-test`)
- [ ] Linting clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (session-lifecycle-bugs)**
  - Name: lifecycle-bug-builder
  - Role: Fix all three bugs — `_cleanup_stale_sessions` live-worker check, `finalize_session` routing, and `_complete_agent_session` Redis re-read
  - Agent Type: builder
  - Resume: true

- **Test Engineer (session-lifecycle-bugs)**
  - Name: lifecycle-test-engineer
  - Role: Write unit tests for the three bug fixes
  - Agent Type: test-engineer
  - Resume: true

- **Validator (final)**
  - Name: lifecycle-validator
  - Role: Run full test suite, verify success criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: lifecycle-documentarian
  - Role: Update docstrings and feature docs
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Build: Fix all three bugs
- **Task ID**: build-lifecycle-bugs
- **Depends On**: none
- **Validates**: `tests/unit/test_stale_cleanup.py` (create), `tests/unit/test_complete_agent_session.py` (create or update)
- **Assigned To**: lifecycle-bug-builder
- **Agent Type**: builder
- **Parallel**: true
- Fix Bug 1: add `_active_workers` cross-reference in `_cleanup_stale_sessions`; raise default `age_minutes` to 120
- Fix Bug 2: add Redis re-read in `_complete_agent_session` before calling `finalize_session`; handle multiple-record case
- Fix Bug 3: replace raw delete-and-recreate in `_cleanup_stale_sessions` with `finalize_session(s, "killed", reason="stale cleanup (no live process)", skip_checkpoint=True)`; verify Popoto KeyField compatibility
- If `finalize_session → session.save()` is insufficient for KeyField mutation, add `force_recreate` support to `finalize_session`

### 2. Write Tests
- **Task ID**: write-tests
- **Depends On**: build-lifecycle-bugs
- **Assigned To**: lifecycle-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Write `tests/unit/test_stale_cleanup.py`: test that `_cleanup_stale_sessions` skips session with live `_active_workers` entry; test that it calls `finalize_session` not raw delete-and-recreate
- Write/update test for `_complete_agent_session`: verify Redis re-read preserves accumulated `stage_states`
- Update `tests/unit/test_crash_snapshot.py` if affected

### 3. Documentation
- **Task ID**: document-lifecycle-bugs
- **Depends On**: write-tests
- **Assigned To**: lifecycle-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update docstrings for `_cleanup_stale_sessions` and `_complete_agent_session`
- Update `docs/features/session-lifecycle.md` if it exists, or create a note in the relevant doc

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-lifecycle-bugs
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` and verify all new tests pass
- Run `python -m ruff check .` and verify lint clean
- Verify success criteria checklist

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No raw delete-and-recreate in cleanup | `grep -n "s.delete()" scripts/update/run.py` | exit code 1 |
| finalize_session used in cleanup | `grep -n "finalize_session" scripts/update/run.py` | output contains "finalize_session" |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

---

## Open Questions

None — the root cause is confirmed by live session evidence and code inspection. Solution approach is clear. Ready to build.
