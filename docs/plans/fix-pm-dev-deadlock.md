---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-16
tracking: https://github.com/tomcounsell/ai/issues/1004
last_comment_id:
---

# Fix PM Session Deadlock When Child Dev Session Can't Get a Worker Slot

## Problem

When a PM session creates a child dev session via `valor_session create --role dev`, the PM enters `waiting_for_children` status but the nudge loop keeps re-enqueueing it as `pending`. Each nudge cycle consumes a global semaphore slot, starving the child dev session of execution capacity.

**Current behavior:**

1. PM session acquires global semaphore slot, runs Claude, creates child dev session
2. PM calls `wait-for-children`, outputs "Dispatched BUILD. Waiting for completion."
3. Output router (`output_router.py:110`) sees PM+SDLC → returns `nudge_continue`
4. `_enqueue_nudge` re-enqueues PM as `pending` — PM will re-acquire a global slot
5. PM runs again, still in `waiting_for_children`, outputs another wait message → nudged again
6. This cycle repeats indefinitely. Each iteration holds a global semaphore slot for the PM's execution time
7. The child dev session sits in `pending`, competing for slots with the PM's continuous nudge cycle
8. With `MAX_CONCURRENT_SESSIONS=3` and other sessions running, the child may never get a slot
9. Observed: PM ran 30+ minutes with no productive output before manual intervention

**Desired outcome:**

When a PM session enters `waiting_for_children`, the nudge loop stops re-enqueueing it. The PM sits idle (no semaphore slot consumed) until the child completes and the worker steers the PM back to life via `_handle_dev_session_completion`.

## Freshness Check

**Baseline commit:** `0f5c1037b3d44c87d86328b5a1509fd81213a681`
**Issue filed at:** 2026-04-16T09:10:32Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `worker/__main__.py:178-191` — semaphore initialization — still holds (extends to line 192)
- `agent/agent_session_queue.py:2503-2530` — dev semaphore swap logic — still holds
- `agent/agent_session_queue.py:2358` — `_worker_loop` docstring — still holds (function signature at 2350)
- `agent/agent_session_queue.py:3198` — `_handle_dev_session_completion` — still holds

**Cited sibling issues/PRs re-checked:**
- #810 — CLOSED 2026-04-07: introduced global session semaphore — fix is in place
- #402 — CLOSED 2026-03-14: watchdog stall recovery — fix is in place

**Commits on main since issue was filed (touching referenced files):**
- None

**Active plans in `docs/plans/` overlapping this area:**
- `worktree-parallel-sdlc.md` — Shipped. Introduced `MAX_CONCURRENT_DEV_SESSIONS` semaphore (default 1). The dev semaphore swap logic it added is not the cause of this bug but is adjacent code.
- `worker_lifecycle_fixes.md` — Planning. Addresses restart flag TTL and zombie PID detection — different scope, no overlap.

**Notes:** No drift. All references are accurate.

## Prior Art

- **Issue #810 / PR #814**: "Worker runs sessions in parallel despite per-chat serialization" — Added the global `MAX_CONCURRENT_SESSIONS` semaphore. This is the mechanism that enables the deadlock (PM holds a slot while waiting), but #810 itself was about capping unbounded parallelism and was correct to add the semaphore.
- **Issue #402**: "Watchdog stall recovery for pending sessions" — Added detection for stuck pending sessions, but focused on watchdog recovery, not parent-child deadlocks. The watchdog could theoretically detect the stuck PM but doesn't address the root cause.

## Research

No relevant external findings — proceeding with codebase context and training data. This is a purely internal concurrency bug in the worker's nudge loop / semaphore interaction.

## Spike Results

### spike-1: Can PM safely release its global semaphore slot while waiting_for_children?

- **Assumption**: "Nothing in the codebase depends on a `waiting_for_children` session holding a global slot"
- **Method**: code-read
- **Finding**: Confirmed. No code reads `_global_session_semaphore._value` to derive PM counts — dashboard counts by querying Redis session statuses. However, releasing the semaphore from inside `_execute_agent_session` would cause a double-release bug: the `_worker_loop` finally block (line 2696) also releases via `_semaphore_acquired`, and `asyncio.Semaphore.release()` silently increments above cap (no error raised). The release MUST happen in `_worker_loop` itself, following the same pattern as the dev semaphore swap (lines 2514-2516).
- **Confidence**: high
- **Impact on plan**: The fix should NOT release the semaphore from inside execution. Instead, the output router should stop nudging the PM, letting `_execute_agent_session` return normally, which triggers the finally block's semaphore release correctly.

### spike-2: What happens when a PM in waiting_for_children gets steered back to life?

- **Assumption**: "The PM is re-enqueued as pending and goes through the normal `_pop_agent_session` path"
- **Method**: code-read
- **Finding**: `_handle_dev_session_completion` writes a steering message to `queued_steering_messages` via `steer_session()` and calls `_ensure_worker()`. But critically, `steer_session` does NOT change the PM status to `pending`. The PM transitions to `pending` via: (a) the `_agent_session_hierarchy_health_check` which detects stuck parents (line 1810), or (b) explicit recovery mechanisms. Once `pending`, the PM is popped by `_worker_loop` and re-acquires a global semaphore slot through the normal path — no special bypass.
- **Confidence**: high
- **Impact on plan**: Need to ensure that after the PM's nudge loop stops (fix from spike-1), the PM is correctly re-enqueued to `pending` when the child completes. The hierarchy health check already handles "all children terminal → re-enqueue parent as pending" (line 1836-1859). We need to verify this path works when the PM was never re-nudged.

## Data Flow

1. **Entry point**: PM session creates child dev session via `valor_session create --role dev`
2. **PM persona** (`config/personas/project-manager.md`): PM calls `wait-for-children`, outputs status message
3. **Output router** (`agent/output_router.py:110`): Sees PM+SDLC → `nudge_continue` (BUG: should detect `waiting_for_children` and deliver instead)
4. **`_enqueue_nudge`** (`agent_session_queue.py:2807`): Re-enqueues PM as pending
5. **`_worker_loop`** (`agent_session_queue.py:2364`): Pops PM, acquires global slot, runs again
6. **Repeat steps 2-5** indefinitely — PM wastes slots, child starves
7. **Desired**: At step 3, detect `waiting_for_children` → return `deliver` → PM exits cleanly → global slot released → child can start

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `determine_delivery_action` gains awareness of `waiting_for_children` status (already receives `session_status` parameter)
- **Coupling**: No change — the output router already receives session status
- **Data ownership**: No change
- **Reversibility**: Fully reversible — the change is a conditional branch in a pure function

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

- **Output router `waiting_for_children` guard**: When the PM is in `waiting_for_children` status, `determine_delivery_action` returns `deliver` instead of `nudge_continue`, allowing the PM's Claude process to exit and release its semaphore slot
- **Child priority boost in `_pop_agent_session`**: Sessions with `parent_agent_session_id` pointing to a running/waiting parent get a priority boost, ensuring they're popped before unrelated sessions at the same tier
- **Health check re-enqueue path verification**: Confirm `_agent_session_hierarchy_health_check` correctly handles the PM re-enqueue when all children are terminal

### Flow

**PM creates child** → PM calls `wait-for-children` → PM outputs status → **output router detects `waiting_for_children`** → delivers (no nudge) → PM exits → **global slot released** → child dev session gets slot → child runs → child completes → `_handle_dev_session_completion` steers PM → **hierarchy health check re-enqueues PM** → PM resumes with steering message

### Technical Approach

1. **In `determine_delivery_action`** (`agent/output_router.py:66`): Add early return for `session_status == "waiting_for_children"` — return `"deliver"`. This goes BEFORE the PM+SDLC nudge check at line 110, ensuring the PM's output is delivered and the session exits cleanly.

2. **In `_pop_agent_session`** (`agent/agent_session_queue.py:683`): Modify the sort key to boost sessions whose `parent_agent_session_id` is set (child sessions). Within the same priority tier, child sessions sort before parentless sessions. This is a tiebreaker only — it doesn't override the 4-tier priority system.

3. **In `_agent_session_hierarchy_health_check`** (`agent/agent_session_queue.py:1773`): Verify the existing "stuck parents: all children terminal → re-enqueue as pending" path at line 1836-1859 correctly handles this scenario. The PM must transition from `waiting_for_children` to `pending` with the steering message intact.

4. **In `_handle_dev_session_completion`** (`agent/agent_session_queue.py:3198`): Verify that `steer_session()` correctly writes the steering message and `_ensure_worker()` wakes the relevant worker loop. If the PM is in `waiting_for_children`, the steering message sits in `queued_steering_messages` until the hierarchy health check re-enqueues the PM.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `determine_delivery_action` is a pure function with no exception handlers — no coverage needed
- [ ] `_pop_agent_session` has existing exception handling; the sort key change doesn't add new handlers

### Empty/Invalid Input Handling
- [ ] Test `determine_delivery_action` with `session_status=None` (default) — should not trigger `waiting_for_children` guard
- [ ] Test `_pop_agent_session` sort with sessions where `parent_agent_session_id` is None vs set

### Error State Rendering
- [ ] When PM delivers (instead of nudging) while in `waiting_for_children`, the output reaches the user as a status message — verify this renders correctly

## Test Impact

No existing tests affected — the output router and `_pop_agent_session` sort logic are tested in isolation, and the new conditions are additive branches that don't change existing behavior for non-`waiting_for_children` sessions.

## Rabbit Holes

- **Releasing the semaphore from inside `_execute_agent_session`**: Tempting but causes silent double-release bugs. The correct fix is to let the session exit normally (spike-1 confirmed).
- **Adding a dedicated "waiting" semaphore pool**: Over-engineering for a problem solved by simply stopping the nudge loop.
- **Changing `steer_session` to transition PM to `pending` directly**: Would bypass the hierarchy health check's safety logic and complicate the status machine. The existing health check path is correct.
- **Making PM sessions not count against the global semaphore**: Too broad — PM sessions DO consume compute while actively running Claude. They should only not hold a slot when idle in `waiting_for_children`.

## Risks

### Risk 1: PM never wakes up after child completes
**Impact:** PM stays in `waiting_for_children` forever, pipeline stalls
**Mitigation:** The hierarchy health check runs periodically and already handles this case (line 1810-1864). Additionally, `_handle_dev_session_completion` writes a steering message that the health check will process. Test this path explicitly.

### Risk 2: PM output not delivered to user when in waiting_for_children
**Impact:** User doesn't see "Dispatched BUILD. Waiting for completion." status message
**Mitigation:** Returning `"deliver"` from `determine_delivery_action` routes through the normal delivery path at `send_to_chat` line 3729, which calls `send_cb` to push the message to Telegram. This is the standard delivery path.

## Race Conditions

### Race 1: Child completes before PM exits the nudge-delivery path
**Location:** `agent/agent_session_queue.py:3198` and `agent/output_router.py:110`
**Trigger:** Child dev session completes very quickly (before PM's current turn finishes). `_handle_dev_session_completion` writes a steering message, but the PM is still in its current execution (about to exit). The hierarchy health check hasn't run yet.
**Data prerequisite:** PM must be in `waiting_for_children` status before the child completes
**State prerequisite:** Steering message must be written to PM's `queued_steering_messages`
**Mitigation:** This is actually fine — the steering message persists in Redis. When the hierarchy health check re-enqueues the PM, the steering message will be consumed at the next turn boundary. The health check runs every 60s (startup recovery loop), so worst case there's a 60s delay.

## No-Gos (Out of Scope)

- Changing the global semaphore cap or default values
- Modifying the dev semaphore swap logic (lines 2503-2530)
- Adding new semaphore types or slot reservation mechanisms
- Changing the PM persona's `wait-for-children` behavior
- Modifying `_handle_dev_session_completion` beyond verification

## Update System

No update system changes required — this fix modifies internal worker behavior that is deployed via the standard git pull path. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a worker-internal change to the output router and session queue. No new MCP servers, bridge changes, or tool exposure needed.

## Documentation

- [ ] Update `docs/features/pm-dev-session-architecture.md` to document the `waiting_for_children` → deliver (no nudge) behavior
- [ ] Add entry to `docs/features/README.md` index if a new section is created

## Success Criteria

- [ ] A PM session that creates a child dev session does not deadlock when `MAX_CONCURRENT_DEV_SESSIONS=1` and `MAX_CONCURRENT_SESSIONS=3`
- [ ] Child dev sessions created by a running PM are executed within a bounded time (no indefinite pending state)
- [ ] Existing concurrency limits continue to function as caps for unrelated sessions
- [ ] The fix handles the case where multiple PMs each create child dev sessions simultaneously
- [ ] Unit test demonstrates the output router returns `deliver` (not `nudge_continue`) for `waiting_for_children` PM sessions
- [ ] Unit test demonstrates child sessions sort before parentless sessions at the same priority tier
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (deadlock-fix)**
  - Name: deadlock-fix-builder
  - Role: Implement output router guard and child priority boost
  - Agent Type: builder
  - Resume: true

- **Validator (deadlock-fix)**
  - Name: deadlock-fix-validator
  - Role: Verify fix correctness and test coverage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add waiting_for_children guard to output router
- **Task ID**: build-output-router
- **Depends On**: none
- **Validates**: tests/unit/test_output_router.py (update)
- **Informed By**: spike-1 (confirmed: must stop nudge, not release semaphore internally)
- **Assigned To**: deadlock-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/output_router.py`, add an early return in `determine_delivery_action`: if `session_status == "waiting_for_children"`, return `"deliver"`. Place this BEFORE the PM+SDLC check at line 110.
- Add unit tests in `tests/unit/test_output_router.py` for the new branch: PM+SDLC+waiting_for_children → deliver, PM+SDLC+running → nudge_continue (unchanged)

### 2. Add child priority boost in _pop_agent_session
- **Task ID**: build-child-priority
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue.py (create)
- **Informed By**: spike-2 (confirmed: child must be popped promptly for health check path to work)
- **Assigned To**: deadlock-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/agent_session_queue.py`, modify the `sort_key` function inside `_pop_agent_session` (line 773-775): add a third element to the sort tuple — `0` if `parent_agent_session_id` is set, `1` otherwise. This makes child sessions sort before parentless sessions within the same priority tier.
- Add a unit test verifying the sort order: given two pending sessions at normal priority (one with parent, one without), the child is popped first.

### 3. Verify hierarchy health check re-enqueue path
- **Task ID**: build-health-check-verify
- **Depends On**: build-output-router
- **Validates**: tests/unit/test_agent_session_queue.py (update or create)
- **Informed By**: spike-2 (confirmed: PM transitions to pending via health check)
- **Assigned To**: deadlock-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Read `_agent_session_hierarchy_health_check` (line 1773-1864) and confirm the "all children terminal → re-enqueue parent as pending" path (line 1836-1859) handles the case where PM has a steering message in `queued_steering_messages`.
- If the health check's re-enqueue path doesn't push the steering message through, add logic to ensure it does.
- Add a test verifying: PM in `waiting_for_children` with one completed child → health check transitions PM to `pending` with steering message preserved.

### 4. Validate full deadlock scenario
- **Task ID**: validate-deadlock
- **Depends On**: build-output-router, build-child-priority, build-health-check-verify
- **Assigned To**: deadlock-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all unit tests pass
- Verify the output router correctly handles all combinations: PM+SDLC+waiting_for_children, PM+SDLC+running, PM+SDLC+active, teammate+waiting_for_children (should not change)
- Verify child priority sort doesn't break existing priority ordering
- Run `python -m ruff check agent/output_router.py agent/agent_session_queue.py`
- Run `python -m ruff format --check agent/output_router.py agent/agent_session_queue.py`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-deadlock
- **Assigned To**: deadlock-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/pm-dev-session-architecture.md` to document the `waiting_for_children` delivery behavior
- Add a "Deadlock Prevention" subsection explaining: PM stops being nudged when in `waiting_for_children`, child gets priority boost, health check re-enqueues PM

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: deadlock-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify lint: `python -m ruff check .`
- Verify format: `python -m ruff format --check .`
- Verify documentation exists and is accurate
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/output_router.py agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/output_router.py agent/agent_session_queue.py` | exit code 0 |
| Output router guard | `pytest tests/unit/test_output_router.py -x -q -k waiting_for_children` | exit code 0 |
| Child priority sort | `pytest tests/unit/test_agent_session_queue.py -x -q -k child_priority` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | [agent-type] | [The concern raised] | [How/whether addressed] | [Guard condition or gotcha] |

---

## Open Questions

No open questions — the spike investigations resolved all verifiable assumptions. The fix is a straightforward conditional branch in a pure function plus a sort key tiebreaker. The hierarchy health check path is already implemented and handles the re-enqueue.
