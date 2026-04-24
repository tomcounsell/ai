---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1156
last_comment_id:
---

# waiting_for_children → terminal bypass via complete_transcript

## Problem

PM sessions can reach the terminal `completed` state while their children are still running. The bypass is at the transcript-completion boundary: when the PM's own Claude transcript ends, the worker calls `complete_transcript()` → `finalize_session()` on the PM itself, with no check of the PM's current status or the liveness of its children. The child-liveness gate exists only inside `_finalize_parent_sync`, which is only invoked when a *child* finalizes — never on the PM's own transcript end.

**Current behavior:**
- PM enters `waiting_for_children` after spawning child sessions.
- PM's transcript ends (Stop hook, or the worker's end-of-task code path).
- `complete_transcript(session_id, status="completed")` runs, calling `finalize_session(pm, "completed", reason="transcript completed: completed")`.
- `finalize_session` accepts the transition because the only validation is status equality (CAS) and "target must be terminal" — neither catches the semantic violation.
- PM is marked `completed` while child is still running.
- The still-running child subsequently finishes; its `_finalize_parent_sync` sees the parent already terminal and silently no-ops.
- The completion runner (#1058) sees the PM already terminal and skips final delivery — the user gets no summary.

**Desired outcome:**
The transition `waiting_for_children → completed` (or `→ failed`) fires **only** through sanctioned channels:
1. `_finalize_parent_sync` after every child has reached a terminal status (reason `"all children terminal"`).
2. The completion runner `_deliver_pipeline_completion` after it holds the `pipeline_complete_pending` Redis lock (reason `"pipeline complete: final summary delivered"`).
3. Human-intent overrides that explicitly collapse the hierarchy: kill, cancel, abandonment.

All other callers (the `complete_transcript` path, the Claude Code Stop hook, any future caller) must be rejected when attempting to transition a `waiting_for_children` PM to `completed` or `failed` with an unsanctioned reason.

## Freshness Check

**Baseline commit:** `46b2de03389dcdb38ad2c348e9b7e43365d3d8e9`
**Issue filed at:** 2026-04-24T07:17:55Z (~7 hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified on current main:**
- `models/session_lifecycle.py:217` — `finalize_session(session, status, reason, ...)` signature — still holds.
- `models/session_lifecycle.py:611-627` — `_finalize_parent_sync` non-terminal-children short-circuit — still holds.
- `bridge/session_transcript.py:291-292` — unconditional `finalize_session(s, status, reason=f"transcript completed: {status}")` when `status in TERMINAL_STATUSES` — still holds, no liveness check, no `s.status` check.
- `agent/session_executor.py:1487-1497` — happy-path `complete_transcript` call site — still holds.
- `agent/session_executor.py:1517-1541` — `agent_session=None` fallback `complete_transcript` call site — still holds.
- `.claude/hooks/stop.py:157-165` — Stop hook's direct `finalize_session(..., reason=f"stop hook: {stop_reason}", skip_auto_tag=True, skip_checkpoint=True)` — still holds; bypasses `complete_transcript` but still reaches `finalize_session` directly.

**Cited sibling issues/PRs re-checked:**
- #987 — CLOSED 2026-04-15. Reordered `_handle_dev_session_completion` after `complete_transcript`. Does not add child-liveness gating at the transcript boundary — does not fix this bug.
- #1004 / PR #1008 — CLOSED 2026-04-16. Added `waiting_for_children → deliver` output routing. Tick-time delivery only; not a terminal-transition gate.
- #1058 / PR #1089 — MERGED 2026-04-21. Introduced `pipeline_complete_pending` Redis lock, read only inside `_finalize_parent_sync`. `complete_transcript` path bypasses this lock entirely.
- #721 — MERGED 2026-04-05. Consolidated lifecycle mutations into `models/session_lifecycle.py`. This gives us a single choke-point (`finalize_session`) in which to install the Option B guard.
- #875 — CLOSED 2026-04-10. Promoted `session_lifecycle.py` to status authority with CAS. CAS validates status equality, not semantic transition validity — so the bug we're fixing sits in the gap CAS does not cover.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since="2026-04-24T07:17:55Z" -- bridge/session_transcript.py models/session_lifecycle.py agent/session_executor.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/reliable-pm-final-delivery.md` (status: Ready, the #1058 plan) — already merged as PR #1089. Describes the completion runner that our guard must permit. No overlap: our fix is upstream of the runner's lock.
- `docs/plans/pm-session-child-fanout.md`, `docs/plans/pm-session-scope-and-wait.md` — describe the PM fan-out architecture; provide background context but do not overlap the fix.

**Notes:** No drift. Line numbers match the issue body verbatim. Plan proceeds on the premises stated in the issue.

## Prior Art

- **Issue #987 / merged PR** — Fixed a TOCTOU race where `_handle_dev_session_completion` steered the PM before `_finalize_parent_sync` ran. Re-ordered calls in `agent/session_executor.py:1578` so PM-steering happens AFTER `complete_transcript`. Did not add child-liveness gating.
- **Issue #1004 / PR #1008** — Added `waiting_for_children → deliver` output-routing branch in `agent/output_router.py:154`. Governs tick-time delivery; unrelated to terminal transitions.
- **Issue #1058 / PR #1089** — Introduced the completion runner (`agent/session_completion.py:411`) as sole owner of PM's final-delivery turn, guarded by `pipeline_complete_pending:{parent_id}` Redis lock. `_finalize_parent_sync` defers to the runner on the success path (`models/session_lifecycle.py:644-662`). The lock is read only inside `_finalize_parent_sync`; the `complete_transcript` path bypasses it.
- **Issue #875 / merged work** — Promoted `session_lifecycle.py` to status authority with CAS (compare-and-set on status equality). CAS detects concurrent *writers* but does not validate *semantic* transition validity — so `waiting_for_children → completed` with an arbitrary reason passes CAS as long as the on-disk status hasn't drifted.
- **PR #721** — Consolidated lifecycle mutations into `models/session_lifecycle.py`. This consolidation is what makes Option B feasible: there is exactly one place in the codebase that writes terminal statuses (`finalize_session`), so a single guard covers every call site.
- **PR #903** — Added PM session child fan-out for multi-issue SDLC prompts. Created the conditions under which this bug is visible: before fan-out, PMs with non-trivial child lifetimes were rare.

## Research

No external research needed — the fix is entirely internal to the repo's state machine and session lifecycle. No external libraries, APIs, or ecosystem patterns are involved. Relevant patterns (state-machine invariants, allow-list guards on terminal transitions) are standard idioms with no library dependency.

## Data Flow

Tracing the buggy path from PM transcript end to the terminal state being set:

1. **Entry point**: PM's Claude transcript ends. Either (a) the SDK task naturally completes after the PM spawned children and said nothing further, or (b) the `.claude/hooks/stop.py` Stop hook fires (e.g., user interrupt, Claude Code stopping).
2. **`agent/session_executor.py:1487-1497`** (happy path): Worker computes `final_status = "completed" if not task.error else "failed"`, then calls `complete_transcript(session.session_id, status=final_status)`.
3. **`agent/session_executor.py:1517-1541`** (fallback path when `agent_session is None`): Same call with the same `final_status` semantics.
4. **`bridge/session_transcript.py:252-299`** (`complete_transcript`):
   - Writes `SESSION_END` marker to the transcript log (always happens).
   - Re-reads the session from Redis (L285) — critical, gives us a fresh `s.status`.
   - **L291-292**: If `status in TERMINAL_STATUSES`, calls `finalize_session(s, status, reason=f"transcript completed: {status}")` — **no check of `s.status`**, no liveness gate.
5. **`models/session_lifecycle.py:217-412`** (`finalize_session`):
   - L264: validates target `status` is terminal.
   - L271-278: idempotency check (skip if `current_status == status`).
   - L280-306: CAS — re-reads from Redis, compares on-disk status to `current_status`. **Passes** because on-disk and in-memory both show `waiting_for_children`.
   - **GAP**: no semantic transition validation. `waiting_for_children → completed` is accepted.
   - L308-346: lifecycle log, auto-tag, branch checkpoint, `_finalize_parent_sync(parent_id=...)` — the parent-finalize here refers to the *current session's* parent (not its children), and for a top-level PM, `parent_id` is None, so this is a no-op.
   - L357-361: sets `session.status = "completed"`, saves.
6. **Output**: PM is terminal. Meanwhile, the still-running child's `_finalize_parent_sync` will later see the parent already terminal and no-op (L593-599 early exit).

The **child-liveness gate** — `_finalize_parent_sync` non-terminal children check at L611-627 — is in the data-flow path only when a *child* finalizes. The PM's self-finalization via `complete_transcript` never passes through it.

**The fix**: insert the gate at two points (Options A and B are complementary):
- **Option A** (guard at transcript boundary): in `complete_transcript`, after the re-read (L285), skip the `finalize_session` call if `s.status == "waiting_for_children"`. Still write the `SESSION_END` marker — only the status mutation is suppressed.
- **Option B** (guard inside `finalize_session`): after the CAS block, reject the transition when `current_status == "waiting_for_children"` and the target status is `"completed"` or `"failed"` and the `reason` is not on an allow-list of sanctioned channels.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR for #987 | Re-ordered `_handle_dev_session_completion` to run AFTER `complete_transcript` | Protected the PM-steering race, but did not add a child-liveness gate on the transcript-end path itself |
| PR #1008 (#1004) | Added `waiting_for_children → deliver` in `output_router.py` | Governs tick-time output routing (deliver vs nudge). Does not cover terminal transitions |
| PR #1089 (#1058) | Completion runner + `pipeline_complete_pending` Redis lock | Lock is read only inside `_finalize_parent_sync`. `complete_transcript → finalize_session` path bypasses the lock entirely |
| Issue #875 | Promoted lifecycle to status authority with CAS | CAS validates status equality (concurrent-writer detection), not semantic transition validity |

**Root cause pattern:** Each prior fix protected a specific channel (ordering, output routing, a lock inside one function, a concurrent-writer check). None added a **state-machine invariant** enforced at the single choke-point for terminal writes. The consolidated `finalize_session` function (PR #721) has been sitting as the ideal place to install that invariant — the bug is a missed opportunity from that consolidation, not a regression.

## Architectural Impact

- **New dependencies**: None. Pure internal guard logic.
- **Interface changes**: None externally. Internally, `finalize_session` gains a new precondition (checked after CAS, before side effects). A new module-level constant `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` is introduced.
- **Coupling**: Marginally increases coupling between `finalize_session` and the specific reason strings used by `_finalize_parent_sync` and the completion runner. Mitigated by lifting those strings into shared constants.
- **Data ownership**: Unchanged. `finalize_session` remains the sole writer of terminal statuses.
- **Reversibility**: High. The guard is a single `if` block; removing it reverts to current behavior. No migrations, no schema changes, no data rewrites.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (plan is specific; no ambiguity to resolve)
- Review rounds: 1 (standard PR review to sanity-check the guard's allow-list)

The fix is small in code but semantically important. Most of the effort is in test coverage, not implementation.

## Prerequisites

No prerequisites — this work has no external dependencies. The repo's existing Popoto/Redis test fixtures (`tests/unit/test_session_lifecycle*.py`, `tests/unit/test_agent_session_hierarchy.py`) provide the harness.

## Solution

### Key Elements

- **`VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` constant** (in `models/session_lifecycle.py`): the allow-list of sanctioned reason strings under which a `waiting_for_children` session may transition to `completed` or `failed`. Populated with the two success-path reasons; extended as new sanctioned channels emerge.
- **`finalize_session` guard** (Option B, primary fix): after the CAS block in `models/session_lifecycle.py`, reject any `waiting_for_children → {completed, failed}` transition whose reason is not in `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS`. Raise a specific exception type so callers can distinguish it from `StatusConflictError`.
- **`complete_transcript` pre-emption** (Option A, belt-and-braces): in `bridge/session_transcript.py`, after the re-read, detect `s.status == "waiting_for_children"` and skip the `finalize_session` call entirely. Still write the `SESSION_END` transcript marker. Log the skip at INFO level so the bypass attempt is auditable.
- **Scope clarification**: the guard covers transitions to `completed` and `failed` only. Explicit human-intent terminal overrides (`killed`, `cancelled`, `abandoned`) remain unrestricted — these are legitimate overrides of the hierarchy.

### Flow

Bug-fix plan — no user-facing flow. The corrected execution path:

**PM transcript ends** → `complete_transcript(session_id, status="completed")` → **re-read session** → **s.status == "waiting_for_children"** → skip `finalize_session`, log INFO → SESSION_END marker written, no status mutation → PM stays in `waiting_for_children`.

Later, when the last child terminates:

**Child `finalize_session`** → `_finalize_parent_sync(parent_id=pm_id)` → children all terminal → `finalize_session(pm, "completed", reason="all children terminal", skip_parent=True)` → guard sees `reason in VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` → transition proceeds → PM becomes `completed` with lifecycle timestamp AFTER child's.

If a future caller attempts `finalize_session(pm_in_waiting, "completed", reason="some new path")`:

**`finalize_session`** → current_status is `waiting_for_children` → target is `completed` → reason not in allow-list → **raise `WaitingForChildrenGuardError`** (subclass of `ValueError`) → caller sees the exception; the transition does not occur.

### Technical Approach

**Fix 1: Option B guard inside `finalize_session`** (primary defense).

In `models/session_lifecycle.py`:

1. Add a module-level constant near the top of the file:
   ```python
   # Sanctioned reason strings for transitions from waiting_for_children to completed or failed.
   # Any other caller attempting such a transition will be rejected by finalize_session.
   # Kill/cancel/abandon paths are explicit human-intent overrides and are NOT guarded here.
   VALID_WAITING_FOR_CHILDREN_EXIT_REASONS = frozenset({
       "all children terminal",                            # _finalize_parent_sync
       "pipeline complete: final summary delivered",       # completion runner (#1058)
   })
   ```

2. Define a guard exception type (subclass of `ValueError` so existing broad `except Exception:` blocks still catch it, but code that wants to distinguish can):
   ```python
   class WaitingForChildrenGuardError(ValueError):
       """Raised when a caller attempts to finalize a waiting_for_children session
       via an unsanctioned reason. Used to prevent PM sessions from reaching a
       terminal state while their children are still running."""
   ```

3. Insert the guard after the CAS block (~L306) and before the lifecycle-log step (~L308):
   ```python
   # State-machine invariant (#1156): waiting_for_children sessions may only
   # transition to completed or failed via sanctioned channels. Kill/cancel/abandon
   # are explicit overrides and are not guarded.
   GUARDED_TARGET_STATUSES = {"completed", "failed"}
   if current_status == "waiting_for_children" and status in GUARDED_TARGET_STATUSES:
       if reason not in VALID_WAITING_FOR_CHILDREN_EXIT_REASONS:
           raise WaitingForChildrenGuardError(
               f"Cannot finalize session {session_id} from waiting_for_children "
               f"to {status!r} via reason {reason!r}: only _finalize_parent_sync "
               f"('all children terminal') or the completion runner "
               f"('pipeline complete: final summary delivered') may perform this "
               f"transition. See issue #1156."
           )
   ```

4. Update the module docstring to name the new invariant.

**Fix 2: Option A pre-emption in `complete_transcript`** (secondary defense + clean logging).

In `bridge/session_transcript.py`:

1. After the re-read at L285, before the `if status in TERMINAL_STATUSES` branch at L291, add:
   ```python
   if s.status == "waiting_for_children" and status in ("completed", "failed"):
       logger.info(
           "complete_transcript skipping terminal transition for %s — "
           "session is waiting_for_children; children will finalize via "
           "_finalize_parent_sync (issue #1156)",
           s.session_id,
       )
       return
   ```
   This early return runs AFTER the `SESSION_END` marker is written (which happens in the earlier try block at L268-277). It suppresses only the status mutation.

2. Keep the existing exception handler at L298-299 as-is — the Option A branch cannot raise.

**Fix 3: Caller update for Stop hook** (covered by Option B automatically).

`.claude/hooks/stop.py` at L159 calls `finalize_session(agent_session, status, reason=f"stop hook: {stop_reason}", ...)`. If the hook fires for a PM in `waiting_for_children`, the Option B guard raises `WaitingForChildrenGuardError`. The hook's outer `except Exception: pass` block (L166-167) already swallows it. This is the correct behavior — Stop hook should not be able to collapse a PM with live children. No hook code changes needed.

**Fix 4: Caller update for `agent_session=None` fallback** (covered by Option A automatically).

`agent/session_executor.py:1517-1541` calls `complete_transcript` exactly the same way as the happy path, so Option A's early return covers it. No executor code changes needed.

**Implementation ordering:**
1. Write `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` and `WaitingForChildrenGuardError` in `models/session_lifecycle.py`.
2. Write the guard in `finalize_session` (Option B).
3. Write the `complete_transcript` pre-emption (Option A).
4. Add the four unit tests from "Failure Path Test Strategy".
5. Run the full lifecycle test suite; fix any tests broken by the new guard (see Test Impact).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/session_transcript.py:298-299` `except Exception:` block — existing test `test_session_lifecycle*.py` should already cover the happy path; after Option A lands, add a test asserting the new INFO-level log line appears when a `waiting_for_children` session hits `complete_transcript`.
- [ ] `.claude/hooks/stop.py:166-167` `except Exception: pass` — add a unit test that calls the hook's finalization helper with a `waiting_for_children` session and asserts the session remains in `waiting_for_children` (the guard raised but was swallowed by the hook's outer handler).
- [ ] `models/session_lifecycle.py:305-306` `except Exception:` — the guard raises `WaitingForChildrenGuardError`, which is a `ValueError` and hence distinct from the `Exception` class caught in CAS's retry branch. Verify the guard error propagates out of `finalize_session` and is not swallowed by internal error handling.

### Empty/Invalid Input Handling
- [ ] `finalize_session(session=None, ...)` — unchanged; still raises `ValueError` at L261-262.
- [ ] `finalize_session(session, status, reason=None, ...)` — explicit test: `reason=None` evaluated against the allow-list. The guard test should use `reason=None` as one of its rejected inputs.
- [ ] `finalize_session(session, status, reason="", ...)` — empty-string reason should also be rejected (not in allow-list). Add to the same parameterized test.
- [ ] `complete_transcript(session_id, status="completed", summary=None)` — already handles `summary=None`; unchanged.

### Error State Rendering
- [ ] When the guard fires, `WaitingForChildrenGuardError` is raised. No user-facing output path — this is an internal state-machine error. Verify it is logged at ERROR level somewhere in the call chain (likely by the caller's `except` block). If no caller logs it, add a `logger.warning` at the guard site before the raise so the event is visible.
- [ ] The Option A INFO log line (`complete_transcript skipping terminal transition for ... — session is waiting_for_children`) must appear with the session_id populated; test this with a log capture fixture.

## Test Impact

Every test listed here needs a deliberate disposition. The dispositions are based on what the guard changes about observable behavior.

- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — REVIEW: existing `_finalize_parent_sync` coverage stays. No rewrites required. Add new test cases for the guard (see new tests below).
- [ ] `tests/unit/test_session_lifecycle.py` — REVIEW: any test that calls `finalize_session(s, "completed", reason="...")` on a session whose current status is `waiting_for_children` must use an allow-listed reason, or expect `WaitingForChildrenGuardError`. Likely 1–3 test cases to UPDATE (swap reason string to `"all children terminal"` or update the fixture so the session is `running` rather than `waiting_for_children`).
- [ ] `tests/unit/test_agent_session_hierarchy.py` — REVIEW: parent-child finalization paths. Likely unaffected because tests construct scenarios where children finalize first and the parent transitions via `_finalize_parent_sync` (which uses the allow-listed reason). Add a new test covering the guard: parent in `waiting_for_children`, child still `running`, call `finalize_session(parent, "completed", reason="transcript completed: completed")` and assert `WaitingForChildrenGuardError`.
- [ ] `tests/integration/test_lifecycle_transition.py` — REVIEW: emits the string `"transcript completed"` in at least one assertion per evidence in the issue. After the fix, sessions in `waiting_for_children` will NOT log this transition. Update the test to either (a) arrange the session in `running` (not `waiting_for_children`) before calling `complete_transcript`, or (b) assert the new `waiting_for_children`-skipped log line instead.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — REVIEW: exercises `complete_transcript` in the `agent_session=None` fallback path. If any fixture constructs a `waiting_for_children` session for this test, it needs either (a) fixture update (set status to `running`) or (b) assertion update (expect the Option A skip). Read the file; pick one.
- [ ] `tests/unit/test_error_summary_enforcement.py` — REVIEW: exercises `complete_transcript` on the failed-task path (`status="failed"`). Must re-check that no fixture constructs a `waiting_for_children` PM with `task.error` — if it does, the guard will reject `"transcript completed: failed"` for `waiting_for_children` PMs. Likely unaffected but must be re-verified once the guard lands.
- [ ] `tests/integration/test_pm_final_delivery.py` — REVIEW: tests the completion runner's success path. Must verify the runner's reason string `"pipeline complete: final summary delivered"` is EXACTLY what the allow-list contains. If the runner's string differs, either (a) update the allow-list constant, or (b) update the runner — the plan defaults to (a) because the runner is already in production.
- [ ] `tests/integration/test_session_finalization_decoupled.py`, `tests/integration/test_session_finalize.py`, `tests/integration/test_parent_child_round_trip.py` — REVIEW: same pattern. Fixtures that put the session in `waiting_for_children` before calling `finalize_session` or `complete_transcript` must either allow-list the reason or change the fixture status.
- [ ] `tests/unit/test_stop_hook.py` — REVIEW: if any test calls the stop hook's finalization helper on a `waiting_for_children` session, add an assertion that the session remains in `waiting_for_children` (guard suppressed the transition via the hook's outer exception swallow).
- [ ] `tests/unit/test_completion_runner_two_pass.py`, `tests/unit/test_deliver_pipeline_completion.py` — REVIEW: must still pass unchanged because the completion runner uses the allow-listed reason.

**New tests to add** (from issue's "Failure Path Test Strategy"):

- [ ] `tests/unit/test_session_lifecycle.py::test_finalize_session_rejects_waiting_for_children_with_non_allowlisted_reason` — construct a session in `waiting_for_children`, call `finalize_session(s, "completed", reason="transcript completed: completed")`, assert `WaitingForChildrenGuardError` is raised and `s.status` remains `waiting_for_children`.
- [ ] `tests/unit/test_session_lifecycle.py::test_finalize_session_accepts_waiting_for_children_with_all_children_terminal_reason` — construct a session in `waiting_for_children`, call `finalize_session(s, "completed", reason="all children terminal")`, assert the transition succeeds and `s.status == "completed"`.
- [ ] `tests/unit/test_session_lifecycle.py::test_finalize_session_accepts_waiting_for_children_with_completion_runner_reason` — same pattern, `reason="pipeline complete: final summary delivered"`, expect success.
- [ ] `tests/unit/test_session_lifecycle.py::test_finalize_session_rejects_waiting_for_children_with_empty_reason` — parameterized over `reason=None`, `reason=""`, `reason="   "`: assert `WaitingForChildrenGuardError`.
- [ ] `tests/unit/test_session_lifecycle.py::test_finalize_session_kill_path_allowed_from_waiting_for_children` — construct `waiting_for_children` session, call `finalize_session(s, "killed", reason="valor-session kill")`, assert transition succeeds. Kill is NOT guarded.
- [ ] `tests/unit/test_session_lifecycle.py::test_finalize_session_abandoned_path_allowed_from_waiting_for_children` — same pattern for `status="abandoned"`. Abandoned is NOT guarded.
- [ ] `tests/unit/test_session_transcript.py::test_complete_transcript_skips_finalize_when_waiting_for_children` — construct `waiting_for_children` session, call `complete_transcript(session_id, status="completed")`, assert (a) SESSION_END marker was written to the transcript file, (b) `s.status` remains `waiting_for_children`, (c) INFO log line appears.
- [ ] `tests/unit/test_session_transcript.py::test_complete_transcript_finalizes_when_not_waiting_for_children` — regression test: `running` → `completed` still works normally via `complete_transcript`.
- [ ] `tests/integration/test_parent_child_round_trip.py::test_pm_with_live_child_not_prematurely_finalized_by_transcript_end` — the end-to-end scenario from the issue: PM enters `waiting_for_children` with a child in `running`, PM's transcript ends (simulate worker call to `complete_transcript`), assert PM remains `waiting_for_children`. Then terminate the child; assert PM transitions to `completed` with reason `"all children terminal"`. Verify lifecycle timestamps: parent terminal transition happens AFTER child's.

## Rabbit Holes

- **Do not rework `_finalize_parent_sync`.** The gate inside it works correctly for its channel. The fix is upstream of it.
- **Do not change `pipeline_complete_pending` lock semantics** (#1058). The lock is read only inside `_finalize_parent_sync`; adding the lock check to `complete_transcript` is the wrong layer — the fix is a state-machine invariant, not a lock.
- **Do not touch `output_router.py`** (#1004). Its `waiting_for_children → deliver` rule is tick-time delivery, not terminal transitions.
- **Do not attempt to hold the PM's transcript open** by blocking the SDK. Wrong layer. The right answer is gating the status transition.
- **Do not promote the allow-list into a state-machine transition matrix** for all statuses. That would be a nice architectural refactor, but it is out of scope for a bug fix. A small `frozenset` of sanctioned reason strings is sufficient and easy to extend.
- **Do not attempt to catch the guard exception and retry.** If the guard fires, the caller has a bug (wrong channel, wrong reason). The exception should propagate; the caller's outer `except Exception:` block will swallow it, which is the correct behavior for the Stop hook and the health check (they should not be finalizing `waiting_for_children` PMs).
- **Do not gate `killed`, `cancelled`, or `abandoned`.** Those are explicit human-intent overrides of the hierarchy. Kill from `valor-session kill`, cancel from shutdown, abandon from watchdog are all legitimate and must not be blocked.

## Risks

### Risk 1: Reason strings drift and lock out the sanctioned channels
**Impact:** If `_finalize_parent_sync`'s reason string ever changes (e.g., to `"all children terminal (reconciled)"`) without updating `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS`, legitimate parent finalizations will be rejected and PMs will get stuck in `waiting_for_children` forever.
**Mitigation:** (a) Promote the reason strings to module-level constants in `models/session_lifecycle.py` and `agent/session_completion.py`, and have both the emit site and the allow-list reference the same constant. (b) Add a grep-based CI check: `grep -rn '"all children terminal"' models/ agent/ | wc -l` must equal 2 (one at the emit site, one in the allow-list). (c) A passing `test_finalize_session_accepts_waiting_for_children_with_all_children_terminal_reason` test catches drift at PR time.

### Risk 2: Existing test fixtures put sessions in `waiting_for_children` and expect `finalize_session` to succeed with arbitrary reasons
**Impact:** Test suite breakage after the guard lands; initial PR noise.
**Mitigation:** Test Impact section lists the specific files to re-verify. The fix for each is mechanical (swap reason string or change fixture status). Run `pytest tests/unit -k lifecycle -x` early during build to surface breakage.

### Risk 3: The `agent_session=None` fallback path was added to recover stuck sessions (see #917); the guard could regress that recovery
**Impact:** Health-check recovery for PMs stuck in `waiting_for_children` might no longer complete because `complete_transcript` now skips the transition.
**Mitigation:** Read `agent/session_health.py` to confirm whether health-check ever finalizes `waiting_for_children` PMs via `complete_transcript`. If it does, the health-check is itself a bug — a PM in `waiting_for_children` should wait for its children, not be force-completed. A separate "zombie parent" reaper (checking both parent stuck AND all children terminal) is a different concern; the guard does not obstruct that reaper because it would use `"all children terminal"` as the reason. Add a unit test simulating health-check recovery on a `waiting_for_children` session with live children, and assert the session stays `waiting_for_children`.

### Risk 4: Stop hook guard suppression surfaces as silent "ghost PMs" if children never terminate
**Impact:** If Claude Code is hard-stopped and the hook fires, the guard rejects the termination; the PM stays `waiting_for_children`. If the child session is also stopped but its `finalize_session` also fails, the PM is stranded.
**Mitigation:** This is the correct behavior — the hierarchy is in a consistent-but-stalled state, which is recoverable by the existing watchdog (`monitoring/session_watchdog.py`). The watchdog uses reason prefixes like `"watchdog: stale session (...)"`; these are NOT in the allow-list, so if the watchdog targets a `waiting_for_children` PM, the guard rejects. Verify watchdog scope: does the watchdog finalize `waiting_for_children` PMs directly? If yes, update watchdog to target children first (kill from the leaves), which is the correct order. This is in scope for the fix if the watchdog has this defect.

## Race Conditions

### Race 1: Simultaneous `complete_transcript` (PM transcript end) and child finalization (Thread B)
**Location:** `bridge/session_transcript.py:291-292` and `models/session_lifecycle.py:611-627`.
**Trigger:** PM's Claude transcript ends at roughly the same time as the last child's SDK task completes.
**Data prerequisite:** Child must have written its terminal status to Redis before `_finalize_parent_sync` reads it.
**State prerequisite:** PM must be in `waiting_for_children`; children must be observable via `AgentSession.query`.
**Mitigation:** Option A short-circuits Thread A before it reaches `finalize_session`. Thread B proceeds normally via `_finalize_parent_sync` and, when it sees all children terminal, calls `finalize_session(pm, "completed", reason="all children terminal")` — which the guard permits. If Thread A and B race at `_finalize_parent_sync`, the existing `pipeline_complete_pending` lock (#1058) serializes them. If Thread A somehow reaches `finalize_session` with an illegal reason (e.g., future caller not using `complete_transcript`), Option B rejects it — no status mutation occurs — and Thread B's later attempt with the allow-listed reason succeeds.

### Race 2: Guard rejection vs. legitimate retry
**Location:** `models/session_lifecycle.py` around the guard.
**Trigger:** A caller observes `WaitingForChildrenGuardError`, catches it, and retries with a different reason (unlikely, but possible for a misbehaving health check).
**Mitigation:** The guard is deterministic — a retry with the same unsanctioned reason still fails. To legitimately finalize, the caller must use an allow-listed reason, which is intentional. No mitigation needed; this is the designed behavior.

### Race 3: Two sanctioned channels both firing for the same PM
**Location:** `_finalize_parent_sync` and `_deliver_pipeline_completion` both targeting the same PM after all children are terminal.
**Trigger:** Race between the last child's completion-triggered `_finalize_parent_sync` and the completion runner's final-delivery attempt.
**Mitigation:** Already handled by the `pipeline_complete_pending:{parent_id}` Redis lock (#1058): `_finalize_parent_sync` defers to the runner when the lock is held (`models/session_lifecycle.py:644-662`). This plan does not change that contract.

## No-Gos (Out of Scope)

- Refactoring `_finalize_parent_sync` or changing its reason string.
- Changing `pipeline_complete_pending` lock semantics.
- Output-router rules (`waiting_for_children → deliver`).
- SDK-level transcript-flow changes.
- Promoting the allow-list into a general state-machine transition matrix.
- Gating `killed`, `cancelled`, or `abandoned` transitions.
- Changing the Stop hook's error-handling behavior (it already swallows the new exception correctly).

## Update System

No update system changes required — this fix is purely internal to the worker, bridge, and state machine. Deployment is a standard `./scripts/valor-service.sh restart` on each machine after the fix merges. No new dependencies, no config files, no migrations.

## Agent Integration

No agent integration required — the bug lives entirely in `bridge/`, `agent/`, `models/`, and `.claude/hooks/`. No MCP server changes, no `.mcp.json` change, no bridge-external tool exposure. The fix is invisible to the agent: the guard enforces an invariant that should have always held; the agent's observable behavior is that the SDLC pipeline's final delivery now reliably happens AFTER all children complete.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` "Parent Finalization" section (L101-110) to document the enforced invariant: `waiting_for_children → {completed, failed}` is only permitted from `_finalize_parent_sync` (reason `"all children terminal"`) or the completion runner (reason `"pipeline complete: final summary delivered"`). Kill/cancel/abandon are explicit overrides and remain unrestricted.
- [ ] Add a subsection "Waiting-for-Children Guard" under "State Transitions" with the guard exception name, the allow-list constant name, and a link to issue #1156.
- [ ] Add a note in `docs/features/pm-dev-session-architecture.md` describing why `complete_transcript` is a no-op status-wise for `waiting_for_children` PMs (the SESSION_END marker still writes; only the status mutation is suppressed).
- [ ] Add entry to `docs/features/README.md` index if session-lifecycle is not already listed (it is — no new entry needed).

### External Documentation Site
None — this repo does not use Sphinx, Read the Docs, or MkDocs. All documentation lives in `docs/`.

### Inline Documentation
- [ ] Docstring on `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` explaining its purpose and that kill/cancel/abandon are intentionally NOT in it.
- [ ] Docstring on `WaitingForChildrenGuardError` naming issue #1156 and explaining when it fires.
- [ ] Updated docstring on `finalize_session` mentioning the new precondition (the guard) and pointing at the allow-list constant.
- [ ] Inline comment at the guard site citing issue #1156 and summarizing the invariant in 1-2 lines.
- [ ] Inline comment at `complete_transcript`'s Option A skip branch citing issue #1156.

## Success Criteria

- [ ] Reproducing the evidence scenario (PM in `waiting_for_children`, live child, PM transcript ends with `status="completed"`) leaves the PM in `waiting_for_children` until the child finalizes (covered by `test_pm_with_live_child_not_prematurely_finalized_by_transcript_end`).
- [ ] When the child eventually finalizes, the PM transitions to `completed` with reason `"all children terminal"` via `_finalize_parent_sync` (assertion inside the same test).
- [ ] No `complete_transcript` / `finalize_session` call site can emit a `waiting_for_children → {completed, failed}` transition with an unsanctioned reason — regression test `test_finalize_session_rejects_waiting_for_children_with_non_allowlisted_reason` enforces.
- [ ] The sanctioned channels (`_finalize_parent_sync`, completion runner) still work: their tests pass unchanged.
- [ ] Lifecycle logs for all new PM sessions with children show the parent's terminal transition at-or-after the last child's terminal transition (property check can run against historical session records post-deploy).
- [ ] No regression in `_handle_dev_session_completion` steering, `_deliver_pipeline_completion` final delivery, continuation-PM creation, or watchdog recovery.
- [ ] Kill path from `valor-session kill` still finalizes `waiting_for_children` PMs without hitting the guard (test asserts this).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. Given the small size, a single builder + validator pair is sufficient; the documentarian handles doc updates.

### Team Members

- **Builder (state-machine guard)**
  - Name: `lifecycle-guard-builder`
  - Role: Implement the allow-list constant, the exception type, the `finalize_session` guard, and the `complete_transcript` pre-emption. Add the unit/integration tests listed in Test Impact.
  - Agent Type: builder
  - Resume: true

- **Validator (state-machine guard)**
  - Name: `lifecycle-guard-validator`
  - Role: Verify the guard behavior matches the plan: allow-list is correct, kill/cancel/abandon unrestricted, tests pass, watchdog and health-check paths not regressed.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `lifecycle-docs`
  - Role: Update `docs/features/session-lifecycle.md` and `docs/features/pm-dev-session-architecture.md` per the Documentation section.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build the guard and tests
- **Task ID**: build-lifecycle-guard
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle.py`, `tests/unit/test_session_transcript.py`, `tests/integration/test_parent_child_round_trip.py` (new tests created)
- **Informed By**: Issue #1156 Solution Sketch (Option A + Option B combined)
- **Assigned To**: lifecycle-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` constant and `WaitingForChildrenGuardError` exception to `models/session_lifecycle.py`.
- Insert the guard check in `finalize_session` after the CAS block.
- Insert the Option A skip branch in `bridge/session_transcript.py` after the re-read, before the `if status in TERMINAL_STATUSES` branch.
- Add the new unit tests listed under "Test Impact → New tests to add".
- Update any existing tests that break per the Test Impact review list (most likely 1-3 test cases in `test_session_lifecycle.py` and `test_lifecycle_transition.py`).
- Run `pytest tests/unit -k lifecycle -x` and `pytest tests/integration/test_parent_child_round_trip.py -x` to confirm.
- Run `python -m ruff format models/session_lifecycle.py bridge/session_transcript.py tests/unit/test_session_lifecycle.py tests/unit/test_session_transcript.py`.

### 2. Validate the guard behavior
- **Task ID**: validate-lifecycle-guard
- **Depends On**: build-lifecycle-guard
- **Assigned To**: lifecycle-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Read the implemented guard and confirm:
  - `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` contains exactly two strings: `"all children terminal"` and `"pipeline complete: final summary delivered"`.
  - Guard only fires for `current_status == "waiting_for_children"` AND target in `{"completed", "failed"}`.
  - Guard does NOT fire for `killed`, `cancelled`, `abandoned`.
  - Option A skip branch writes INFO log and returns before `finalize_session`.
  - SESSION_END transcript marker still writes in the Option A skip path.
- Confirm no changes were made to `_finalize_parent_sync`, `output_router.py`, `_deliver_pipeline_completion`, or the `pipeline_complete_pending` lock.
- Re-run the full lifecycle test suite: `pytest tests/unit tests/integration -k "lifecycle or session or hierarchy" -x`.
- Sanity-check `.claude/hooks/stop.py` and `monitoring/session_watchdog.py` behavior: if either finalizes `waiting_for_children` sessions, confirm the guard rejection is swallowed (hook) or confirm the watchdog targets children-first (if not, flag for follow-up but do NOT fix in this plan — it is out of scope per No-Gos).
- Report pass/fail status.

### 3. Update documentation
- **Task ID**: document-feature
- **Depends On**: validate-lifecycle-guard
- **Assigned To**: lifecycle-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-lifecycle.md` "Parent Finalization" section with the enforced invariant and the allow-list constant name.
- Add "Waiting-for-Children Guard" subsection with exception name, constant name, and link to #1156.
- Add note in `docs/features/pm-dev-session-architecture.md` about `complete_transcript` being a status-no-op for `waiting_for_children` PMs.
- Verify docstrings on `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS`, `WaitingForChildrenGuardError`, and `finalize_session` name issue #1156.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-lifecycle-guard, validate-lifecycle-guard, document-feature
- **Assigned To**: lifecycle-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all success-criteria checks.
- Run full pytest suite: `pytest tests/ -x -q`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Verify all Verification table commands return expected results.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lifecycle tests pass | `pytest tests/unit/test_session_lifecycle.py tests/unit/test_session_lifecycle_consolidation.py tests/unit/test_session_transcript.py -x -q` | exit code 0 |
| Hierarchy tests pass | `pytest tests/unit/test_agent_session_hierarchy.py tests/integration/test_parent_child_round_trip.py -x -q` | exit code 0 |
| Full unit tests pass | `pytest tests/unit -x -q` | exit code 0 |
| Full integration tests pass | `pytest tests/integration -x -q` | exit code 0 |
| Lint clean | `python -m ruff check models/session_lifecycle.py bridge/session_transcript.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/session_lifecycle.py bridge/session_transcript.py` | exit code 0 |
| Guard constant exists | `grep -c "VALID_WAITING_FOR_CHILDREN_EXIT_REASONS" models/session_lifecycle.py` | output > 1 |
| Guard exception exists | `grep -c "class WaitingForChildrenGuardError" models/session_lifecycle.py` | output > 0 |
| Option A skip branch exists | `grep -c "waiting_for_children" bridge/session_transcript.py` | output > 0 |
| Allow-list reason strings stable | `grep -c '"all children terminal"' models/session_lifecycle.py` | output > 1 |
| Runner reason string stable | `grep -c "pipeline complete: final summary delivered" agent/session_completion.py models/session_lifecycle.py` | output > 1 |
| New guard test exists | `grep -c "test_finalize_session_rejects_waiting_for_children" tests/unit/test_session_lifecycle.py` | output > 0 |
| New transcript skip test exists | `grep -c "test_complete_transcript_skips_finalize_when_waiting_for_children" tests/unit/test_session_transcript.py` | output > 0 |
| End-to-end test exists | `grep -c "test_pm_with_live_child_not_prematurely_finalized_by_transcript_end" tests/integration/test_parent_child_round_trip.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

All open questions from the issue body have been resolved inside the plan:

1. **"Should allow-listed reason strings be promoted to a named constant set?"** — Resolved: Yes, the plan introduces `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` as a module-level `frozenset`. A grep-based CI check (in the Verification table) enforces that the emit site and the allow-list both reference the same literal string, preventing drift.
2. **"Does the `agent_session=None` fallback path at `agent/session_executor.py:1517-1541` need the same guard?"** — Resolved: No direct change needed. The fallback path calls `complete_transcript` identically to the happy path, so Option A's skip branch covers it. The fallback test in `test_health_check_recovery_finalization.py` is listed in Test Impact for re-verification.
3. **"Does the Claude Code hook Stop path at `.claude/hooks/stop.py` run the same `complete_transcript → finalize_session` sequence?"** — Resolved: No, the Stop hook calls `finalize_session` directly (not via `complete_transcript`). However, because Option B installs the guard inside `finalize_session` itself, the Stop hook's unsanctioned reason (`"stop hook: {stop_reason}"`) is rejected automatically. The hook's outer `except Exception: pass` swallows the exception — which is the correct behavior (a Stop hook should not be able to collapse a PM with live children). A unit test on `test_stop_hook.py` asserts the `waiting_for_children` session remains in that state after the hook fires.

No remaining open questions. The plan is ready for critique.
