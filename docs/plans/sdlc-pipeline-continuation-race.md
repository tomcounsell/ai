---
status: Planning
type: bug
appetite: Small
owner: valorengels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/987
last_comment_id:
---

# SDLC Pipeline Continuation Race: `_handle_dev_session_completion` vs `_finalize_parent_sync`

## Problem

The SDLC pipeline halts after the first stage — PLAN+CRITIQUE completes, but BUILD is never dispatched.

**Current behavior:**
When a dev session completes, `_handle_dev_session_completion` is called **before** `complete_transcript` (line 3841 before line 3859 in `agent/agent_session_queue.py`). At that point the PM parent is still `running`, so the re-check guard at line 3142-3163 passes and logs "Steered parent PM session". Then `complete_transcript` runs, which calls `_finalize_parent_sync`, which transitions the PM from `running` → `waiting_for_children` → `completed` within 13ms. The steering message accepted earlier is now orphaned — the PM is terminal and will never consume it. No continuation PM is created because the guard already declared success.

There is a second, independent failure path: when `agent_session is None` at line 3053 (the `status="running"` filter returned nothing because the dev session is no longer `running` at that moment), `parent_id` is `None` and the function returns early — no continuation PM is created, no warning is logged at an actionable level.

**Desired outcome:**
Each dev session completion steers the PM or creates a continuation PM, and the pipeline progresses through all stages to MERGE.

## Freshness Check

**Baseline commit:** `273722baacc59a32bab083f921c4d7a735b828af`
**Issue filed at:** 2026-04-15T07:02:58Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:3841` — `_handle_dev_session_completion` called before `complete_transcript` at line 3859 — still holds (line numbers exact)
- `agent/agent_session_queue.py:3142-3163` — re-check guard present but runs before `_finalize_parent_sync` — still holds
- `agent/agent_session_queue.py:3053-3059` — `agent_session is None` guard returns early with no continuation PM — still holds
- `models/session_lifecycle.py:554-640` — `_finalize_parent_sync` has zero steering logic, goes straight to `_transition_parent` — still holds at lines 554-640

**Cited sibling issues/PRs re-checked:**
- #934 — "PM session scope + wait: PM exits before dev session completes" — CLOSED 2026-04-13. Introduced `_create_continuation_pm` infrastructure. Confirmed: #987 is a narrower race that #934's fix did not close — the re-check guard was added but it runs before `_finalize_parent_sync`.
- #721 — Session lifecycle consolidation — CLOSED. Introduced `_finalize_parent_sync` as single path for parent completion. Still relevant as root of the ordering issue.

**Commits on main since issue was filed (touching referenced files):**
None — both `agent/agent_session_queue.py` and `models/session_lifecycle.py` are unchanged since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:**
- `pm-session-scope-and-wait.md` — already shipped (issue #934 closed 2026-04-13). The continuation PM infrastructure it built is the vehicle this fix relies on.
- `harness-failure-retry.md` — touches `_execute_agent_session` and error handling, but does not touch the completion/finalization ordering. No conflict.

**Notes:** The issue's line references for `_finalize_parent_sync` cite lines 554 (vs actual 554 in current code) — exact match confirmed.

## Prior Art

- **Issue #934** (closed 2026-04-13): "PM session scope + wait: PM exits before dev session completes" — Introduced `_create_continuation_pm` and checked the `steer_session` return value. Did not fix the ordering race (Path A) because the re-check guard was placed before `complete_transcript`.
- **PR #902**: "Harness abstraction" — Introduced `_handle_dev_session_completion`. Added PM steering without checking return value. Superseded by #934.
- **Issue #898** (closed 2026-04-11): "Nudge stomp regression via `log_lifecycle_transition`" — Related to lifecycle sequencing; confirmed that touching finalization order requires care around CAS fencing.

## Research

No relevant external findings — this is a pure internal concurrency fix. All relevant patterns (deferred completion, TOCTOU guards, continuation sessions) are already established in the codebase.

## Data Flow

1. **Dev session work completes** — `_execute_agent_session` finishes the `await task._task` at line 3826.
2. **`_handle_dev_session_completion` called (line 3841)** — At this point the PM parent is still `running` (or `waiting_for_children`). The steer call succeeds. Re-check guard reads the parent as non-terminal → logs "Steered parent PM session" → returns without creating a continuation PM.
3. **`complete_transcript` called (line 3851-3859)** — Finalizes the dev session. Inside, `finalize_session` calls `_finalize_parent_sync`.
4. **`_finalize_parent_sync` runs** — Sees PM is non-terminal → sets it `waiting_for_children` → checks all children are terminal → sets PM to `completed`. Takes ~13ms.
5. **Orphaned steering message** — The message pushed in step 2 is in `queued_steering_messages`. The PM is terminal and will never be popped from the queue again.
6. **No continuation PM created** — Because step 2's re-check guard saw the PM as non-terminal, `_create_continuation_pm` was never called.

**Path B variant (steps 1–6 alternate):**
- After step 1, the `status="running"` filter at line 3330 finds no session (the dev session was transitioned to a non-running status by a health-check recovery or fast finalization).
- `agent_session` is `None` at line 3328.
- At step 2, line 3053: `parent_id = getattr(agent_session, ...) if agent_session else None` → `None`.
- Guard at line 3055 returns early: "No parent_agent_session_id on dev session, skipping PM steering."
- No steering, no continuation PM.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #902 | Added PM steering call in `_handle_dev_session_completion` | Did not check `steer_session` return value; no fallback if PM terminal |
| Issue #934 | Added continuation PM fallback + re-check guard | Re-check guard placed *before* `complete_transcript`, so it always observes PM as non-terminal before `_finalize_parent_sync` runs |
| Issue #934 | Fixed `agent_session is None` path? | No — the early return at line 3055-3059 still returns silently with no continuation PM when `agent_session` is `None` |

**Root cause pattern:** Each fix addressed the symptom at the point it was observed (steer returns failure), but did not fix the ordering that causes the steer to be accepted and then orphaned. The re-check guard was added as a TOCTOU defense, but the window it was supposed to close is still open because `_finalize_parent_sync` runs after the guard.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:** `_handle_dev_session_completion` signature is unchanged. Behavior change: it now runs after `complete_transcript` instead of before.
- **Coupling:** No new coupling. The function already calls `_create_continuation_pm`; this fix ensures it is always called when appropriate.
- **Data ownership:** Unchanged.
- **Reversibility:** Easy — the ordering change is a one-line move. The Path B fix is a 3-line fallback.

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

- **Reorder `_handle_dev_session_completion`**: Move the call from before `complete_transcript` (line 3841) to after it (after line 3859). At that point `_finalize_parent_sync` has already run, so the re-check guard will correctly observe the PM's terminal status and create a continuation PM if needed.
- **Fix Path B (`agent_session is None`)**: When `agent_session` is `None` in `_handle_dev_session_completion`, fall back to `session.parent_agent_session_id` (the outer `session: AgentSession` param which is always populated from the queue). Use that to look up the parent and create a continuation PM.
- **Regression test**: A test that simulates the race — `_handle_dev_session_completion` is called with a PM that is already terminal (simulating post-`_finalize_parent_sync` state) — asserts a continuation PM is created.

### Flow

Dev session harness completes → `complete_transcript` runs → `_finalize_parent_sync` transitions PM to `completed` → `_handle_dev_session_completion` runs → re-check guard sees PM is terminal → `_create_continuation_pm` is called → continuation PM resumes pipeline

### Technical Approach

**Fix 1 — Move `_handle_dev_session_completion` call:**

In `_execute_agent_session`, the current call order is:

```python
# line ~3839 (CURRENT — wrong order)
if _session_type == "dev" and not task.error:
    await _handle_dev_session_completion(...)

# line ~3851
from bridge.session_transcript import complete_transcript
complete_transcript(session.session_id, status=final_status)
```

After the fix:

```python
# line ~3851
from bridge.session_transcript import complete_transcript
complete_transcript(session.session_id, status=final_status)

# AFTER complete_transcript (new order — correct)
if _session_type == "dev" and not task.error:
    await _handle_dev_session_completion(...)
```

This ensures `_finalize_parent_sync` has run by the time `_handle_dev_session_completion` executes its re-check. The re-check at lines 3142-3163 will now observe the PM as terminal and call `_create_continuation_pm` unconditionally.

**Fix 2 — Path B (`agent_session is None`) fallback:**

In `_handle_dev_session_completion` at line 3052-3059:

```python
# CURRENT — returns silently when agent_session is None
parent_id = (
    getattr(agent_session, "parent_agent_session_id", None) if agent_session else None
)
if not parent_id:
    logger.debug(
        "[harness] No parent_agent_session_id on dev session, skipping PM steering"
    )
    return
```

After the fix:

```python
# NEW — falls back to outer session object
parent_id = (
    getattr(agent_session, "parent_agent_session_id", None)
    or getattr(session, "parent_agent_session_id", None)
)
if not parent_id:
    logger.debug(
        "[harness] No parent_agent_session_id on dev session or session object, skipping PM steering"
    )
    return
```

The outer `session` parameter is always the full `AgentSession` object (populated at enqueue time), so `session.parent_agent_session_id` is reliable even when the `status="running"` lookup returns `None`.

**Note on `complete_transcript` placement:** The move must respect the `chat_state.defer_reaction` guard that already gates `complete_transcript`. The `_handle_dev_session_completion` call should only be moved to after the `complete_transcript` block (inside the same `if agent_session:` branch or its equivalent), not after the entire `if agent_session / else` block. Specifically, the placement should be after the `complete_transcript` call at line 3859 but still guarded by `not task.error` (the same guard as the current call at line 3840).

**Handling the error path:** The current guard `if _session_type == "dev" and not task.error` should remain. When `task.error` is truthy, the dev session failed and `_handle_dev_session_completion` should still run — the PSM will classify it as `fail` and the continuation PM will carry that outcome. Consider whether to drop the `not task.error` guard entirely since the continuation PM path handles failures too. This is a scope question — the plan conservatively keeps the guard to match existing behavior; the builder can widen it in the next patch cycle.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_handle_dev_session_completion` wraps everything in `try/except Exception` at line 3195 — existing tests cover the steer-failure path. The new re-ordering does not add new exception handlers; coverage is unchanged.
- [ ] Path B fix adds no new exception handlers. The fallback to `session.parent_agent_session_id` can only fail if `session` itself is unexpectedly missing the field — treated as a debug-level non-event (returns early as before).

### Empty/Invalid Input Handling
- [ ] `session.parent_agent_session_id` may be `None` (non-child dev sessions). The existing `if not parent_id: return` guard handles this correctly after the fix.
- [ ] `agent_session` being `None` is now a non-fatal degraded path (creates continuation PM from `session` fields) rather than a silent skip.

### Error State Rendering
- [ ] The continuation PM creation is the user-visible outcome. Existing tests in `test_continuation_pm.py` cover message text and session creation. No new user-visible rendering.

## Test Impact

- [ ] `tests/unit/test_continuation_pm.py::TestHandleCompletionContinuationFallback::test_steer_success_no_continuation` — UPDATE: after the fix, steer still "succeeds" (accepted into queue) but the PM will be terminal by the time the re-check runs. The test must simulate the PM being terminal at re-check time to assert continuation PM IS created. New behavior: when steer is accepted but PM is terminal, continuation PM is created.
- [ ] `tests/unit/test_continuation_pm.py` — ADD new test class `TestHandleCompletionOrderingRace` with a test that calls `_handle_dev_session_completion` with a PM already in terminal status (simulating post-`_finalize_parent_sync` state) and asserts a continuation PM is created.
- [ ] `tests/unit/test_continuation_pm.py` — ADD new test `test_agent_session_none_uses_session_parent_id` asserting that when `agent_session=None`, the function uses `session.parent_agent_session_id` to look up the parent and create a continuation PM.

## Rabbit Holes

- **Fixing `_finalize_parent_sync` to be PM-type-aware**: Tempting to make `_finalize_parent_sync` skip finalization for PM sessions in SDLC pipelines. This would require PM sessions to know about SDLC state — wrong abstraction layer, opens a can of worms.
- **Adding a lock/mutex around the steer + finalize window**: The 13ms race window could theoretically be closed with a distributed lock, but this introduces distributed lock overhead for every session completion and is far more complex than moving two lines.
- **Widening `not task.error` guard**: Handling failed dev sessions through the same continuation PM path is a separate improvement (#988 territory). Do not widen the guard here.
- **Refactoring `_handle_dev_session_completion` into `complete_transcript`**: The function has different concerns (pipeline steering) from transcript writing. Merging them would reduce cohesion.

## Risks

### Risk 1: `complete_transcript` side effects change PM state before `_handle_dev_session_completion` reads it
**Impact:** The re-check at line 3143 would read a slightly different parent state than expected. However, this is the desired behavior — we want the re-check to see post-finalization state.
**Mitigation:** `_create_continuation_pm` uses Redis SETNX dedup to handle duplicate creation. Re-reading the parent after `_finalize_parent_sync` is exactly the invariant we want to enforce.

### Risk 2: `defer_reaction` (nudge path) interacts with the reordering
**Impact:** On the nudge path, `complete_transcript` is skipped (line 3858 guard). `_handle_dev_session_completion` must still run.
**Mitigation:** The reordering places `_handle_dev_session_completion` after the `complete_transcript` block. On the nudge path, `_finalize_parent_sync` still runs (via `finalize_session` inside the nudge path). The re-check guard remains correct. The call is guarded by `_session_type == "dev"`, not by `defer_reaction`, so it runs on both paths. Verify this in the implementation.

### Risk 3: `agent_session is None` fallback introduces a duplicate steer+continuation on fast paths
**Impact:** If both `agent_session` and `session.parent_agent_session_id` point to the same parent, two continuation PMs could be created.
**Mitigation:** This scenario is impossible — if `agent_session is None`, there is no steering call at all. The fallback creates one continuation PM via `session.parent_agent_session_id`. The dedup key prevents a second creation from any concurrent path.

## Race Conditions

### Race 1: Re-check guard observes non-terminal PM, but PM finalizes before steering message is consumed
**Location:** `agent/agent_session_queue.py:3142-3163` (re-check) and `models/session_lifecycle.py:607-640` (`_finalize_parent_sync`)
**Trigger:** `_handle_dev_session_completion` → re-check → "non-terminal" → `complete_transcript` → `_finalize_parent_sync` → PM terminal → steering message orphaned
**Data prerequisite:** PM must be in `running` or `waiting_for_children` status at the time of the re-check
**State prerequisite:** `_finalize_parent_sync` must run after the re-check passes
**Mitigation:** Fix 1 — move `_handle_dev_session_completion` to after `complete_transcript`. At that point `_finalize_parent_sync` has already completed; the re-check reads the post-finalization state.

### Race 2: `agent_session` lookup returns `None` due to status filter timing
**Location:** `agent/agent_session_queue.py:3328-3336` (`status="running"` filter) and `agent_session_queue.py:3053`
**Trigger:** Dev session transitions out of `running` before the `status="running"` filter at line 3330 runs
**Data prerequisite:** Dev session `status` must be `"running"` at the moment of the filter
**State prerequisite:** Health-check recovery or any other transition can move the session before the filter executes
**Mitigation:** Fix 2 — fall back to `session.parent_agent_session_id` when `agent_session is None`. The outer `session` is populated from the queue entry (pre-execution) and is reliable.

## No-Gos (Out of Scope)

- Fixing the `not task.error` guard (dev session failures bypassing continuation PM) — separate issue
- Refactoring `_finalize_parent_sync` to be SDLC-aware — wrong abstraction layer
- Adding distributed locks around the steer/finalize window
- Addressing multi-dev fan-out races (Race Condition 2 from pm-session-scope-and-wait) — already handled by SETNX dedup in `_create_continuation_pm`
- Any changes to the PM persona, SDLC skill, or bridge

## Update System

No update system changes required — this is a pure internal worker fix. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is an internal worker fix to `agent/agent_session_queue.py` and its test suite. No MCP changes, no bridge changes, no `.mcp.json` changes.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` — add a note in the "Dev session completion" section describing the correct ordering: `complete_transcript` runs first, then `_handle_dev_session_completion`, ensuring `_finalize_parent_sync` has completed before the steering re-check.
- [ ] Update inline docstring on `_handle_dev_session_completion` to document the ordering invariant: "Must be called after `complete_transcript` to ensure `_finalize_parent_sync` has run before the re-check guard executes."

## Success Criteria

- [ ] Running SDLC on an issue progresses all the way through PLAN → CRITIQUE → BUILD → TEST → REVIEW → DOCS → MERGE without stopping after the first dev session
- [ ] When a PM is finalized by `_finalize_parent_sync` between the steer call and the re-check, a continuation PM is created and the pipeline resumes
- [ ] When `agent_session` is `None` in `_handle_dev_session_completion`, a continuation PM is still created (not a silent no-op)
- [ ] `test_steer_success_no_continuation` updated to reflect new behavior (steer accepted + PM terminal → continuation PM created)
- [ ] New test `TestHandleCompletionOrderingRace` passes: calling handler with already-terminal PM creates continuation PM
- [ ] New test `test_agent_session_none_uses_session_parent_id` passes
- [ ] `pytest tests/unit/test_continuation_pm.py` — all tests pass
- [ ] `pytest tests/unit/ -x -q` — all unit tests pass
- [ ] `python -m ruff check . && python -m ruff format --check .` — clean

## Team Orchestration

### Team Members

- **Builder (queue-reorder)**
  - Name: queue-builder
  - Role: Implement Fix 1 (reorder) and Fix 2 (Path B fallback) in `agent/agent_session_queue.py`
  - Agent Type: builder
  - Resume: true

- **Test Engineer (continuation-race)**
  - Name: test-engineer
  - Role: Update `test_steer_success_no_continuation` and add `TestHandleCompletionOrderingRace` and `test_agent_session_none_uses_session_parent_id` in `tests/unit/test_continuation_pm.py`
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Update `docs/features/bridge-worker-architecture.md` and `_handle_dev_session_completion` docstring
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Run full test suite and lint, confirm all success criteria met
  - Agent Type: validator
  - Resume: true

### Available Agent Types

builder, test-engineer, documentarian, validator

## Step by Step Tasks

### 1. Implement Fix 1 and Fix 2 in agent_session_queue.py
- **Task ID**: build-queue-reorder
- **Depends On**: none
- **Validates**: `tests/unit/test_continuation_pm.py`, `tests/unit/test_agent_session_queue.py`
- **Informed By**: Technical Approach section — Fix 1 (move call after `complete_transcript`) and Fix 2 (Path B fallback)
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_execute_agent_session`, move the `_handle_dev_session_completion` call block (currently at line 3840-3845) to after the `complete_transcript` call block (currently lines 3851-3870). Keep the `if _session_type == "dev" and not task.error` guard.
- In `_handle_dev_session_completion` at the `parent_id` extraction (line 3052-3059), change the fallback: `parent_id = getattr(agent_session, "parent_agent_session_id", None) or getattr(session, "parent_agent_session_id", None)`. Update the debug log message accordingly.
- Ensure the `_handle_dev_session_completion` call still runs on the nudge (`defer_reaction`) path (verify the placement is correct).

### 2. Update and add tests in test_continuation_pm.py
- **Task ID**: build-tests
- **Depends On**: none
- **Validates**: `tests/unit/test_continuation_pm.py`
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Update `TestHandleCompletionContinuationFallback::test_steer_success_no_continuation`: the PM must be in a terminal status at the time the re-check runs. Adjust the test so `steer_session` returns `success: True` but the `get_by_id` re-read returns a terminal PM. Assert a continuation PM IS created (the new behavior).
- Add class `TestHandleCompletionOrderingRace` with test `test_pm_terminal_at_recheck_creates_continuation`: call `_handle_dev_session_completion` with `steer_session` returning `success: True` and `get_by_id` returning a PM in `completed` status. Assert a continuation PM is created and logged.
- Add test `test_agent_session_none_uses_session_parent_id`: call `_handle_dev_session_completion` with `agent_session=None` but `session.parent_agent_session_id` set to a valid terminal PM. Assert a continuation PM is created.

### 3. Update documentation
- **Task ID**: document-fix
- **Depends On**: build-queue-reorder
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- In `docs/features/bridge-worker-architecture.md`, add a note describing the completion ordering: `complete_transcript` → `_finalize_parent_sync` → `_handle_dev_session_completion` (re-check guard reads post-finalization state).
- Update the docstring on `_handle_dev_session_completion` in `agent/agent_session_queue.py` to document the ordering invariant.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-queue-reorder, build-tests, document-fix
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_continuation_pm.py -v` — all tests must pass.
- Run `pytest tests/unit/ -x -q` — all unit tests must pass.
- Run `python -m ruff check . && python -m ruff format --check .` — must be clean.
- Confirm `test_steer_success_no_continuation` was updated (not deleted).
- Confirm new test classes exist in `test_continuation_pm.py`.
- Report pass/fail for each criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_continuation_pm.py -v` | exit code 0 |
| All unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Race test present | `grep -r "TestHandleCompletionOrderingRace" tests/` | output contains TestHandleCompletionOrderingRace |
| Path B test present | `grep -r "test_agent_session_none_uses_session_parent_id" tests/` | output contains test_agent_session_none_uses_session_parent_id |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the fix is fully specified by the issue's solution sketch and the freshness check confirmed no drift.
