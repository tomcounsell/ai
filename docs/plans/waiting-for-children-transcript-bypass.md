---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1156
last_comment_id:
revision_applied: true
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
The transition `waiting_for_children → completed` (or `→ failed`) via the transcript-end bypass and the Stop-hook bypass is eliminated. Sanctioned channels (`_finalize_parent_sync` after all children terminate, the completion runner, the worker's last-resort crash finalizer, and health-check/watchdog recovery paths) continue to operate unchanged.

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
- #721 — MERGED 2026-04-05. Consolidated lifecycle mutations into `models/session_lifecycle.py`. Establishes `finalize_session` as the single choke-point for terminal writes.
- #875 — CLOSED 2026-04-10. Promoted `session_lifecycle.py` to status authority with CAS. CAS validates status equality, not semantic transition validity — so the bug we're fixing sits in the gap CAS does not cover.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since="2026-04-24T07:17:55Z" -- bridge/session_transcript.py models/session_lifecycle.py agent/session_executor.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/reliable-pm-final-delivery.md` (status: Ready, the #1058 plan) — already merged as PR #1089. Describes the completion runner. No overlap: this plan does not modify the runner or its lock.
- `docs/plans/pm-session-child-fanout.md`, `docs/plans/pm-session-scope-and-wait.md` — describe the PM fan-out architecture; provide background context but do not overlap the fix.

**Notes:** No drift. Line numbers match the issue body verbatim. Plan proceeds on the premises stated in the issue.

## Prior Art

- **Issue #987 / merged PR** — Fixed a TOCTOU race where `_handle_dev_session_completion` steered the PM before `_finalize_parent_sync` ran. Re-ordered calls in `agent/session_executor.py:1578` so PM-steering happens AFTER `complete_transcript`. Did not add child-liveness gating.
- **Issue #1004 / PR #1008** — Added `waiting_for_children → deliver` output-routing branch in `agent/output_router.py:154`. Governs tick-time delivery; unrelated to terminal transitions.
- **Issue #1058 / PR #1089** — Introduced the completion runner (`agent/session_completion.py:411`) as sole owner of PM's final-delivery turn, guarded by `pipeline_complete_pending:{parent_id}` Redis lock. `_finalize_parent_sync` defers to the runner on the success path (`models/session_lifecycle.py:644-662`). The lock is read only inside `_finalize_parent_sync`; the `complete_transcript` path bypasses it.
- **Issue #875 / merged work** — Promoted `session_lifecycle.py` to status authority with CAS (compare-and-set on status equality). CAS detects concurrent *writers* but does not validate *semantic* transition validity.
- **PR #721** — Consolidated lifecycle mutations into `models/session_lifecycle.py`.
- **PR #903** — Added PM session child fan-out for multi-issue SDLC prompts. Created the conditions under which this bug is visible: before fan-out, PMs with non-trivial child lifetimes were rare.

## Research

No external research needed — the fix is entirely internal to the repo's state machine and session lifecycle. No external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

Tracing the buggy path from PM transcript end to the terminal state being set:

1. **Entry point**: PM's Claude transcript ends. Either (a) the SDK task naturally completes after the PM spawned children and said nothing further, or (b) the `.claude/hooks/stop.py` Stop hook fires (e.g., user interrupt, Claude Code stopping).
2. **`agent/session_executor.py:1487-1497`** (happy path): Worker computes `final_status = "completed" if not task.error else "failed"`, then calls `complete_transcript(session.session_id, status=final_status)`.
3. **`agent/session_executor.py:1517-1541`** (fallback path when `agent_session is None`): Same call with the same `final_status` semantics.
4. **`bridge/session_transcript.py:252-299`** (`complete_transcript`):
   - Writes `SESSION_END` marker to the transcript log (always happens, L268-277).
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

**The fix**: skip the `finalize_session` call when the session is in `waiting_for_children`, at the two entry points that create the bypass (the transcript boundary and the Stop hook). Non-bypass paths (`_finalize_parent_sync`, completion runner, crash-path `_complete_agent_session`, health-check recovery, watchdog) remain free to finalize.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR for #987 | Re-ordered `_handle_dev_session_completion` to run AFTER `complete_transcript` | Protected the PM-steering race, but did not add a child-liveness gate on the transcript-end path itself |
| PR #1008 (#1004) | Added `waiting_for_children → deliver` in `output_router.py` | Governs tick-time output routing (deliver vs nudge). Does not cover terminal transitions |
| PR #1089 (#1058) | Completion runner + `pipeline_complete_pending` Redis lock | Lock is read only inside `_finalize_parent_sync`. `complete_transcript → finalize_session` path bypasses the lock entirely |
| Issue #875 | Promoted lifecycle to status authority with CAS | CAS validates status equality (concurrent-writer detection), not semantic transition validity |

**Root cause pattern:** Each prior fix protected a specific channel (ordering, output routing, a lock inside one function, a concurrent-writer check). None covered the transcript-end path, which reaches `finalize_session` via a different route than `_finalize_parent_sync`.

## Architectural Impact

- **New dependencies**: None. Pure internal control-flow change.
- **Interface changes**: None. `complete_transcript` and the Stop hook helper gain an early return under a new condition; signatures unchanged.
- **Coupling**: Unchanged. The fix is local to two call sites; it does not alter `finalize_session`, `_finalize_parent_sync`, the completion runner, the output router, or any recovery path.
- **Data ownership**: Unchanged. `finalize_session` remains the sole writer of terminal statuses.
- **Reversibility**: High. The fix is two `if` blocks, each a plain early return. Removing them reverts to current behavior. No migrations, no schema changes, no data rewrites.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (plan is specific; no ambiguity to resolve)
- Review rounds: 1 (standard PR review to confirm the caller audit holds)

The fix is small in code but semantically important. Most of the effort is in test coverage, not implementation.

## Prerequisites

No prerequisites — this work has no external dependencies. The repo's existing Popoto/Redis test fixtures (`tests/unit/test_session_lifecycle*.py`, `tests/unit/test_agent_session_hierarchy.py`) provide the harness.

## Solution

### Design Choice: Transcript-Boundary Skip (Option A Only)

The original plan proposed Option A (transcript-boundary skip) PLUS Option B (allow-list guard inside `finalize_session`). The revision drops Option B.

**Why Option B was dropped (critique response):** A comprehensive audit of `finalize_session` callers (see Caller Audit below) found that Option B's two-reason allow-list would reject legitimate recovery transitions from:
- `agent/session_completion.py:160` (`_complete_agent_session` — worker crash path, reason `"agent session completed"`)
- `agent/session_health.py:810` (already-delivered recovery to `completed`, reason `"health check: already delivered"`)
- `agent/session_health.py:987` (exhausted retries to `failed`, reason `"health check: N recovery attempts, never progressed"`)
- `monitoring/session_watchdog.py:226` (stale-session reaper to `failed`, reason `"watchdog: stale session (...)"`)

Each of these is a legitimate escape hatch for a genuinely stuck session. Rejecting them creates the stuck-state trap acknowledged in Risks 3–4 of the original draft. Expanding the allow-list to 6+ reasons couples the state-machine invariant to specific call-site strings from subsystems that have their own change cadences — the drift risk grows linearly with the allow-list size.

The bug has a single mechanism: the transcript-end and Stop-hook paths both call `finalize_session` without checking whether the session is in `waiting_for_children`. Fixing those two call sites is sufficient and does not create the trap. **Option B (the `finalize_session` guard) is explicitly out of scope.**

### Key Elements

- **`complete_transcript` skip branch** (`bridge/session_transcript.py`): after the re-read, if `s.status == "waiting_for_children"` and `status` is `"completed"` or `"failed"`, log INFO and return. The SESSION_END transcript marker still writes (it was written earlier in the function, before the re-read).
- **Stop hook skip branch** (`.claude/hooks/stop.py`): after loading the `agent_session`, if its status is `waiting_for_children`, return before calling `finalize_session`.
- **No guard in `finalize_session`**: the state machine's existing idempotency check, CAS, and TERMINAL_STATUSES validation are retained unchanged. No new exception type. No allow-list constant.
- **Scope clarification**: the skip applies only when `status in ("completed", "failed")`. Kill/cancel/abandon targets are untouched (already handled correctly by existing code paths).

### Caller Audit (why two call-site fixes are sufficient)

A complete enumeration of `finalize_session` callers, classified by whether they can fire on a `waiting_for_children` session and by their target status:

| Call Site | Target | Reason | Hits waiting_for_children? | Handled by |
|-----------|--------|--------|--------------------------|------------|
| `bridge/session_transcript.py:292` | completed/failed | `"transcript completed: {status}"` | YES (the bug) | **Fix 1: Transcript skip** |
| `.claude/hooks/stop.py:159` | completed/failed | `"stop hook: {stop_reason}"` | YES (hook fires on PM transcript) | **Fix 2: Stop hook skip** |
| `models/session_lifecycle.py:680` (`_transition_parent`) | completed/failed | `"all children terminal"` | YES (the intended path) | Existing correct behavior |
| `agent/session_completion.py:699` (runner) | completed | `"pipeline complete: final summary delivered"` | YES (the intended path) | Existing correct behavior |
| `agent/session_completion.py:160` (`_complete_agent_session`) | completed/failed | `"agent session completed"` | YES (worker crash path) | **Legitimate recovery** — intentionally NOT blocked |
| `agent/session_health.py:810` | completed | `"health check: already delivered"` | YES | **Legitimate recovery** |
| `agent/session_health.py:987` | failed | `"health check: N recovery attempts..."` | YES | **Legitimate recovery** |
| `monitoring/session_watchdog.py:226` | failed | `"watchdog: stale session ..."` | YES | **Legitimate recovery** |
| `agent/session_health.py:369,966,1116` | abandoned | various | YES | Kill-class; not guarded |
| `agent/agent_session_queue.py:648` | cancelled | `"PM cancelled session ..."` | YES | Kill-class; not guarded |
| `monitoring/session_watchdog.py:797` | abandoned | various | YES | Kill-class; not guarded |
| `tools/valor_session.py:842,864` | killed | `"valor-session kill*"` | YES | Kill-class; not guarded |
| `tools/agent_session_scheduler.py:860,887` | killed | `"CLI kill"` | YES | Kill-class; not guarded |
| `bridge/telegram_bridge.py:1690` | completed | `"Acknowledged by {user}..."` | NO (dormant-only) | Pre-condition excludes |
| `scripts/update/run.py:233,293` | abandoned | deployment | YES | Kill-class; not guarded |

**Conclusion:** exactly two call sites need patching — `complete_transcript` and the Stop hook. All other `completed`/`failed` callers are legitimate recovery paths that must be permitted even for `waiting_for_children` PMs (otherwise sessions get stuck with no escape).

### Flow

Bug-fix plan — no user-facing flow. The corrected execution path:

**PM transcript ends (happy path)** → `complete_transcript(session_id, status="completed")` → **re-read session** → **s.status == "waiting_for_children"** → log INFO, return → SESSION_END marker already written, no status mutation → PM stays in `waiting_for_children`.

Later, when the last child terminates:

**Child `finalize_session`** → `_finalize_parent_sync(parent_id=pm_id)` → children all terminal → `_transition_parent(parent, "completed")` → `finalize_session(pm, "completed", reason="all children terminal", skip_parent=True)` → transition proceeds normally → PM becomes `completed` with lifecycle timestamp AFTER child's.

**PM crash path** — worker's finally block at `agent/agent_session_queue.py:1504` calls `_complete_agent_session`, which calls `finalize_session(pm, "completed", reason="agent session completed")`. The state machine accepts this as a best-effort recovery. This is the designed escape hatch for wedged sessions.

### Technical Approach

**Fix 1: Transcript skip branch in `complete_transcript`.**

In `bridge/session_transcript.py`, modify `complete_transcript` (L252):

1. After the re-read at L285-287, before the `if status in TERMINAL_STATUSES:` branch at L291, add:

   ```python
   # Issue #1156: If this session is in waiting_for_children, the terminal
   # transition must come from _finalize_parent_sync (after children finalize)
   # or the completion runner — NOT from the transcript-end call site. Skip the
   # finalize_session call; the SESSION_END marker has already been written
   # above (L268-277), which is the transcript-visible artifact we preserve.
   if s.status == "waiting_for_children" and status in ("completed", "failed"):
       logger.info(
           "[session-lifecycle] complete_transcript skipping terminal transition "
           "for %s — session is waiting_for_children; children will finalize "
           "via _finalize_parent_sync (issue #1156)",
           s.session_id,
       )
       return
   ```

2. The non-terminal branch (L293-297, `transition_status` for `dormant`) is untouched — non-terminal transitions out of `waiting_for_children` are not the bug.

3. Keep the existing outer `try/except` and `except Exception:` handler (L282-299) as-is — the new skip branch cannot raise.

**Fix 2: Stop hook skip branch.**

In `.claude/hooks/stop.py`, modify the finalization helper at L134-167:

1. After `agent_session = matches[0]` (L150), add:

   ```python
   # Issue #1156: If this PM is in waiting_for_children, do not collapse the
   # hierarchy from the Stop hook. Children will finalize the parent via
   # _finalize_parent_sync. The stop hook has no visibility into child liveness.
   if getattr(agent_session, "status", None) == "waiting_for_children":
       return
   ```

2. No other hook changes. The existing `except Exception: pass` at L166-167 remains for hook-local safety (unrelated to this fix).

**Fix 3: No changes to `finalize_session`.**

The state machine is unchanged. No new exception type. No allow-list constant. This is a deliberate departure from the original Option B proposal and is documented in the "Design Choice" block above.

**Fix 4: No changes to `agent/session_executor.py`.**

The `agent_session=None` fallback at L1517-1541 also calls `complete_transcript`, so Fix 1's skip branch covers it automatically.

**Fix 5: No changes to health check, watchdog, or `_complete_agent_session`.**

These are recovery paths for genuinely stuck sessions. Blocking them would create the stuck-state trap documented in Risks 3–4 of the original draft. They remain free to finalize `waiting_for_children` PMs to `completed`/`failed`/`abandoned`/`killed` with their respective reasons — that is correct behavior for a wedged hierarchy.

**Implementation ordering:**
1. Write the skip branch in `bridge/session_transcript.py`.
2. Write the skip branch in `.claude/hooks/stop.py`.
3. Add the unit/integration tests from "Test Impact → New tests to add".
4. Run `pytest tests/unit -k "lifecycle or transcript or hierarchy" -x` and `pytest tests/integration -k "parent_child or lifecycle" -x`.
5. Run `python -m ruff format bridge/session_transcript.py .claude/hooks/stop.py tests/unit/test_session_transcript.py tests/unit/test_stop_hook.py tests/integration/test_parent_child_round_trip.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/session_transcript.py:298-299` `except Exception:` block — the new skip branch is a plain early return and cannot raise. Add a test asserting the new INFO-level log line appears when a `waiting_for_children` session hits `complete_transcript`.
- [ ] `.claude/hooks/stop.py:166-167` `except Exception: pass` — the new `if status == "waiting_for_children": return` is a plain early return and cannot raise. Add a unit test that calls the hook's finalization helper with a `waiting_for_children` session and asserts the session remains in `waiting_for_children`.

### Empty/Invalid Input Handling
- [ ] `finalize_session(session=None, ...)` — unchanged; still raises `ValueError` at L261-262. No new validation.
- [ ] `finalize_session(session, status, reason=None, ...)` — unchanged. No new `reason` validation in this revision.
- [ ] `complete_transcript(session_id, status="completed", summary=None)` — already handles `summary=None`; unchanged. If a summary is passed for a `waiting_for_children` session, the summary-save at L288-290 still executes; only the `finalize_session` call is skipped. The final summary is later owned by `_finalize_parent_sync` or the completion runner.

### Error State Rendering
- [ ] The INFO log line `[session-lifecycle] complete_transcript skipping terminal transition for ... — session is waiting_for_children; children will finalize via _finalize_parent_sync (issue #1156)` must appear with the session_id populated. Test this with a log capture fixture.
- [ ] The Stop hook skip is silent (no log line), consistent with the hook's silent-failure policy. Add an inline comment at the skip branch explaining why no log is emitted. Operational audit trail is preserved by the lifecycle log written when the PM entered `waiting_for_children` earlier.

## Test Impact

Every test listed here has a deliberate disposition. The dispositions reflect the Option-A-only design (no `finalize_session` guard). The revised design changes `complete_transcript` and the Stop hook; it does NOT change `finalize_session`.

- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — UNAFFECTED: `_finalize_parent_sync` is unchanged. No edits required.
- [ ] `tests/unit/test_session_lifecycle.py` — UNAFFECTED: `finalize_session` is unchanged. No edits required. (Original draft proposed multiple UPDATEs here tied to the dropped Option B guard — none are needed.)
- [ ] `tests/unit/test_agent_session_hierarchy.py` — UNAFFECTED: parent-child finalization paths use `_finalize_parent_sync`, which is unchanged. No edits required.
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE: this test asserts the string `"transcript completed"` per issue evidence. If any fixture places the session in `waiting_for_children` before calling `complete_transcript`, update the assertion to expect the new skip log line instead of the lifecycle transition. If the fixture uses `running`, no change needed — read the file during build and apply the correct disposition.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — UPDATE (conditional): exercises `complete_transcript` in the `agent_session=None` fallback path. If any fixture constructs a `waiting_for_children` session, the new skip branch will suppress the terminal mutation; update the assertion to expect `s.status == "waiting_for_children"` after the call. If no such fixture exists, no change.
- [ ] `tests/unit/test_error_summary_enforcement.py` — UNAFFECTED: exercises the `"failed"` path with `task.error`. Grep the file during build to confirm no `waiting_for_children` fixture exists; if one does, apply the same skip-assertion update.
- [ ] `tests/integration/test_pm_final_delivery.py` — UNAFFECTED: tests the completion runner. Runner is unchanged, and its reason string is not validated against an allow-list in the revised design.
- [ ] `tests/integration/test_session_finalization_decoupled.py`, `tests/integration/test_session_finalize.py`, `tests/integration/test_parent_child_round_trip.py` — REVIEW: confirm no fixture puts a session in `waiting_for_children` before calling `complete_transcript`. If found, apply UPDATE as in `test_lifecycle_transition.py`.
- [ ] `tests/unit/test_stop_hook.py` — UPDATE: add a new test `test_stop_hook_skips_finalize_when_waiting_for_children` asserting that after the hook fires on a PM in `waiting_for_children`, the session remains in that state. If existing tests set up the hook for sessions in `running`, they pass unchanged.
- [ ] `tests/unit/test_completion_runner_two_pass.py`, `tests/unit/test_deliver_pipeline_completion.py` — UNAFFECTED: completion runner is unchanged.

**New tests to add:**

The transcript-focused tests need a home. The file `tests/unit/test_session_transcript.py` does NOT currently exist (verified via `ls tests/unit/test_session_transcript.py`). Plan decision: **create it as a new unit test file** rather than squeezing these tests into `test_session_lifecycle.py`, which is already large and thematically focused on `finalize_session` itself. The new file focuses on `bridge/session_transcript.py:complete_transcript` behaviors.

- [ ] **CREATE** `tests/unit/test_session_transcript.py` as a new file with:
  - [ ] `test_complete_transcript_skips_finalize_when_waiting_for_children` — construct a `waiting_for_children` session, call `complete_transcript(session_id, status="completed")`, assert (a) SESSION_END marker was written to the transcript file, (b) `s.status` remains `waiting_for_children` after the call, (c) the INFO log line appears (use `caplog` fixture with `propagate=True` on the `bridge.session_transcript` logger).
  - [ ] `test_complete_transcript_skips_finalize_when_waiting_for_children_with_failed_status` — same pattern with `status="failed"`. Both `"completed"` and `"failed"` targets are covered by the skip.
  - [ ] `test_complete_transcript_finalizes_when_running` — regression: a session in `running` → `completed` still transitions normally via `complete_transcript`.
  - [ ] `test_complete_transcript_passes_through_dormant_transition` — regression: non-terminal `dormant` transitions are unaffected by the skip (which only applies to `completed`/`failed` targets).
- [ ] **UPDATE** `tests/unit/test_stop_hook.py`:
  - [ ] `test_stop_hook_skips_finalize_when_waiting_for_children` — construct a PM in `waiting_for_children`, synthesize a hook input dict, call the stop hook's finalization helper, assert `s.status` remains `waiting_for_children` (the skip branch returned early; no exception raised).
- [ ] **UPDATE** `tests/integration/test_parent_child_round_trip.py`:
  - [ ] `test_pm_with_live_child_not_prematurely_finalized_by_transcript_end` — the end-to-end scenario from the issue: PM enters `waiting_for_children` with a child in `running`, PM's transcript ends (simulate worker call to `complete_transcript`), assert PM remains `waiting_for_children`. Then terminate the child; assert PM transitions to `completed` with reason `"all children terminal"`. Verify lifecycle timestamps: parent terminal transition happens AFTER child's.

## Rabbit Holes

- **Do not rework `_finalize_parent_sync`.** The gate inside it works correctly for its channel. The fix is upstream of it.
- **Do not change `pipeline_complete_pending` lock semantics** (#1058). The lock is read only inside `_finalize_parent_sync`; adding the lock check to `complete_transcript` is the wrong layer — the fix is a transcript-boundary skip, not a lock.
- **Do not touch `output_router.py`** (#1004). Its `waiting_for_children → deliver` rule is tick-time delivery, not terminal transitions.
- **Do not attempt to hold the PM's transcript open** by blocking the SDK. Wrong layer. The right answer is skipping the status mutation while still writing SESSION_END.
- **Do not install a guard inside `finalize_session` (Option B).** Explicitly considered and rejected during revision. The caller audit enumerates 6+ legitimate callers with unique reason strings; an allow-list approach couples the state-machine invariant to specific subsystems' reason-string conventions, creating drift risk and a stuck-state trap for genuinely wedged sessions.
- **Do not modify the `_complete_agent_session` crash path** (`agent/session_completion.py:160`). It is the worker's last-resort finalizer for crashed sessions; blocking it would strand sessions in `waiting_for_children` with no recovery. Intentionally untouched.
- **Do not modify health-check or watchdog recovery paths.** They are legitimate escape hatches for stuck sessions. If a PM is genuinely wedged in `waiting_for_children` (e.g., child session state is lost), these paths must still be able to finalize the parent.
- **Do not gate `killed`, `cancelled`, or `abandoned`.** Those are explicit human-intent overrides of the hierarchy. Kill from `valor-session kill`, cancel from shutdown, abandon from watchdog are all legitimate and unchanged by this plan.

## Risks

### Risk 1: The `agent_session=None` fallback at `agent/session_executor.py:1517-1541` also hits `complete_transcript`, and the skip will silently suppress what used to be a best-effort recovery
**Impact:** When the worker loses its `agent_session` reference (e.g., after a health-check recovery race), the fallback path used to unconditionally finalize via `complete_transcript`. After this fix, if the underlying session is in `waiting_for_children`, the fallback becomes a no-op. The session is left to be recovered by `_finalize_parent_sync` (when children complete) or the health-check watchdog.
**Mitigation:** This is the correct behavior — a PM in `waiting_for_children` should NOT be force-finalized by a stray fallback. The health-check watchdog already exists and will recover sessions genuinely wedged in `waiting_for_children` (see `agent/session_health.py:966` which finalizes to `abandoned`). The `test_health_check_recovery_finalization.py` review (in Test Impact) catches any surprising test fixture that assumed the old behavior.

### Risk 2: A PM's Claude transcript ends before children terminate, but the `_finalize_parent_sync` event for those children never arrives (e.g., worker restart, child session corrupted)
**Impact:** The PM is stuck in `waiting_for_children`. No child completion event triggers `_finalize_parent_sync`. Before this fix, `complete_transcript` would force-finalize the PM (which is the bug). After this fix, the PM stays `waiting_for_children` until the watchdog reaps it.
**Mitigation:** Acceptable behavior by design. The watchdog (`monitoring/session_watchdog.py:221`) finalizes stale sessions to `"failed"` with reason `"watchdog: stale session (...)"` — this is NOT blocked by the plan (the fix only touches `complete_transcript` and the Stop hook, not the watchdog's `finalize_session` call). The PM will reach a terminal state within the watchdog's TTL. Verify: run `pytest tests/unit -k watchdog -x` after the fix lands to confirm no regression in watchdog-driven recovery.

### Risk 3: Stop hook skip silently drops status mutation for hard-stopped Claude Code sessions
**Impact:** If Claude Code is hard-stopped (user interrupt, hook fires) on a PM in `waiting_for_children`, the hook's finalization call is suppressed. The PM stays `waiting_for_children`. If the user expected Claude Code's stop to finalize the session, they'll see it as "stuck."
**Mitigation:** (a) Document clearly in `docs/features/session-lifecycle.md` that Stop hook on a `waiting_for_children` PM is a no-op; the session will finalize when children terminate or the watchdog reaps. (b) The Stop hook's alternative behavior (finalizing the parent) is exactly the bug being fixed, so the "stuck" appearance is correct — the parent cannot be safely finalized without knowing child status. (c) If operational data shows users hitting this regularly, a follow-up issue could add a "kill children then finalize parent" helper — but that is out of scope for this bug fix.

### Risk 4: Existing test fixtures that set up `waiting_for_children` sessions and call `complete_transcript` expecting the old (buggy) behavior
**Impact:** Test suite breakage after the skip lands; initial PR noise.
**Mitigation:** Test Impact section lists each file explicitly with a disposition. The fix for each is mechanical (swap fixture status to `running`, or update the assertion to expect the skip). Run `pytest tests/unit -k "lifecycle or transcript or hierarchy" -x` and `pytest tests/integration -k "parent_child or lifecycle or pm_final" -x` early during build to surface breakage. The expected delta is 1-3 test cases per file, not wholesale rewrites.

### Risk 5: Silent skip makes post-hoc debugging harder — the skip branch is the second observable place where a PM's terminal transition can be "swallowed"
**Impact:** If a future bug causes a PM to never exit `waiting_for_children`, the INFO log line from the skip branch is one of several signals that must be correlated in logs. The Stop hook skip is silent, compounding this.
**Mitigation:** (a) The INFO log line at the transcript-skip site is explicit and names the issue number for searchability. (b) The lifecycle log already records when the PM *entered* `waiting_for_children`; that timestamp combined with the skip INFO log gives a complete audit trail. (c) Post-deploy: add a dashboard counter `session-lifecycle:transcript-skip-waiting-for-children` that increments on each skip, so operational volume is observable without log-scraping. This counter is a follow-up enhancement, not required for the fix.

## Race Conditions

### Race 1: Simultaneous `complete_transcript` (PM transcript end) and child finalization (Thread B)
**Location:** `bridge/session_transcript.py:291-292` and `models/session_lifecycle.py:611-627`.
**Trigger:** PM's Claude transcript ends at roughly the same time as the last child's SDK task completes.
**Data prerequisite:** Child must have written its terminal status to Redis before `_finalize_parent_sync` reads it.
**State prerequisite:** PM must be in `waiting_for_children`; children must be observable via `AgentSession.query`.
**Mitigation:** The transcript skip short-circuits Thread A before it reaches `finalize_session`. Thread B proceeds normally via `_finalize_parent_sync` and, when it sees all children terminal, calls `finalize_session(pm, "completed", reason="all children terminal")` via `_transition_parent`. If Thread A's `complete_transcript` is interleaved between the last child's terminal-status write and `_finalize_parent_sync`'s read, Thread A sees `waiting_for_children` and skips; Thread B's `_finalize_parent_sync` then observes all children terminal and finalizes. No conflict. The existing `pipeline_complete_pending:{parent_id}` Redis lock (#1058) continues to serialize `_finalize_parent_sync` and the completion runner on the success path.

### Race 2: PM transcript ends *after* `_finalize_parent_sync` already finalized the parent
**Location:** `bridge/session_transcript.py:285` (session re-read).
**Trigger:** The last child terminates first, `_finalize_parent_sync` finalizes the PM to `completed`. Then the PM's own transcript ends and `complete_transcript` is invoked.
**Mitigation:** The re-read at L285 returns the PM with `status == "completed"`. The skip branch compares `s.status == "waiting_for_children"` — FALSE. Control flows to `if status in TERMINAL_STATUSES: finalize_session(...)`. `finalize_session` sees `current_status == "completed"` and the target is also `"completed"` → idempotent no-op (early return at L272-278). No duplicate transition. If the PM is `completed` but `complete_transcript` is called with a different target (e.g., `"failed"`), the CAS check at L290 detects the mismatch.

### Race 3: Two sanctioned channels both firing for the same PM
**Location:** `_finalize_parent_sync` and `_deliver_pipeline_completion` both targeting the same PM after all children are terminal.
**Trigger:** Race between the last child's completion-triggered `_finalize_parent_sync` and the completion runner's final-delivery attempt.
**Mitigation:** Already handled by the `pipeline_complete_pending:{parent_id}` Redis lock (#1058): `_finalize_parent_sync` defers to the runner when the lock is held (`models/session_lifecycle.py:644-662`). This plan does not change that contract.

### Race 4: Worker crash path (`_complete_agent_session`) firing for a PM in `waiting_for_children`
**Location:** `agent/agent_session_queue.py:1504,1513` (`finally` block calling `_complete_agent_session`).
**Trigger:** Worker catches an exception mid-PM-execution. The PM is in `waiting_for_children`. The finally block calls `_complete_agent_session`, which calls `finalize_session(pm, "completed", reason="agent session completed")`.
**Mitigation:** **Intentionally permitted.** The worker's finally block is the last-resort finalizer. If the worker has decided to close out the session, forcing the PM terminal is correct behavior — the children may or may not complete, but the worker has already abandoned coordination. The plan does not block this path. The resulting PM lifecycle shows `waiting_for_children → completed (reason="agent session completed")`, which is distinguishable from the buggy `"transcript completed: completed"` reason in post-hoc analysis.

## No-Gos (Out of Scope)

- Refactoring `_finalize_parent_sync` or changing its reason string.
- Changing `pipeline_complete_pending` lock semantics.
- Output-router rules (`waiting_for_children → deliver`).
- SDK-level transcript-flow changes.
- Installing any guard inside `finalize_session`. Explicitly rejected during revision; see Rabbit Holes.
- Gating `killed`, `cancelled`, or `abandoned` transitions.
- Modifying `_complete_agent_session`, health-check recovery, or watchdog recovery paths — these are legitimate escape hatches.
- Adding a "kill children then finalize parent" helper for the Stop hook (possible follow-up issue, not required for this bug fix).
- Adding a dashboard counter for the skip branch (possible follow-up enhancement, not required for this bug fix).

## Update System

No update system changes required — this fix is purely internal to the worker, bridge, and state machine. Deployment is a standard `./scripts/valor-service.sh restart` on each machine after the fix merges. No new dependencies, no config files, no migrations.

## Agent Integration

No agent integration required — the bug lives entirely in `bridge/`, `agent/`, `models/`, and `.claude/hooks/`. No MCP server changes, no `.mcp.json` change, no bridge-external tool exposure. The fix is invisible to the agent: the agent's observable behavior is that the SDLC pipeline's final delivery reliably happens AFTER all children complete.

## Documentation

### Feature Documentation
- [x] Update `docs/features/session-lifecycle.md` "Parent Finalization" section (L101-110) to document the transcript-boundary skip: `complete_transcript` is a no-op status-wise for `waiting_for_children` PMs (the SESSION_END marker still writes; the `finalize_session` call is skipped). Name the two sanctioned finalization channels (`_finalize_parent_sync` with reason `"all children terminal"`, and the completion runner with reason `"pipeline complete: final summary delivered"`) as the intended paths.
- [x] Add a subsection "Transcript-Boundary Skip" under "State Transitions" describing why the skip exists, linking to issue #1156.
- [x] Add a note in `docs/features/pm-dev-session-architecture.md` describing why `complete_transcript` and the Stop hook are both no-ops for `waiting_for_children` PMs.
- [x] Add entry to `docs/features/README.md` index if session-lifecycle is not already listed (it is — no new entry needed).

### External Documentation Site
None — this repo does not use Sphinx, Read the Docs, or MkDocs. All documentation lives in `docs/`.

### Inline Documentation
- [x] Inline comment at the `complete_transcript` skip branch citing issue #1156 and summarizing the invariant in 1-2 lines.
- [x] Inline comment at the Stop hook skip branch citing issue #1156.
- [x] Updated docstring on `complete_transcript` mentioning the skip for `waiting_for_children`.

## Success Criteria

- [ ] Reproducing the evidence scenario (PM in `waiting_for_children`, live child, PM transcript ends with `status="completed"`) leaves the PM in `waiting_for_children` until the child finalizes (covered by `test_pm_with_live_child_not_prematurely_finalized_by_transcript_end`).
- [ ] When the child eventually finalizes, the PM transitions to `completed` with reason `"all children terminal"` via `_finalize_parent_sync` (assertion inside the same test).
- [ ] The transcript-end bypass is blocked — regression test `test_complete_transcript_skips_finalize_when_waiting_for_children` enforces.
- [ ] The Stop-hook bypass is blocked — regression test `test_stop_hook_skips_finalize_when_waiting_for_children` enforces.
- [ ] The sanctioned channels (`_finalize_parent_sync`, completion runner) still work: their tests pass unchanged.
- [ ] Legitimate recovery paths (`_complete_agent_session` crash path, health-check recovery, watchdog) still finalize `waiting_for_children` PMs to their respective terminal states — no regression.
- [ ] Lifecycle logs for all new PM sessions with children show the parent's terminal transition at-or-after the last child's terminal transition (property check can run against historical session records post-deploy).
- [ ] No regression in `_handle_dev_session_completion` steering, `_deliver_pipeline_completion` final delivery, continuation-PM creation, or watchdog recovery.
- [ ] Kill path from `valor-session kill` still finalizes `waiting_for_children` PMs without issue.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. Given the small size, a single builder + validator pair is sufficient; the documentarian handles doc updates.

### Team Members

- **Builder (transcript-boundary skip)**
  - Name: `transcript-skip-builder`
  - Role: Implement the two skip branches (transcript and Stop hook), create the new `tests/unit/test_session_transcript.py` file, add the hook test, and add the integration test. Update any test fixtures that break per the Test Impact review list.
  - Agent Type: builder
  - Resume: true

- **Validator (transcript-boundary skip)**
  - Name: `transcript-skip-validator`
  - Role: Verify: (1) skip branches trigger correctly for `waiting_for_children` sessions with `completed`/`failed` targets, (2) skip branches do NOT trigger for `running` sessions, (3) `finalize_session` is unchanged (no new exception, no new constant), (4) legitimate recovery paths (`_complete_agent_session`, health-check, watchdog) are untouched and still work, (5) all tests pass.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `transcript-skip-docs`
  - Role: Update `docs/features/session-lifecycle.md` and `docs/features/pm-dev-session-architecture.md` per the Documentation section.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build the transcript-boundary skip and tests
- **Task ID**: build-transcript-skip
- **Depends On**: none
- **Validates**: `tests/unit/test_session_transcript.py`, `tests/unit/test_stop_hook.py`, `tests/integration/test_parent_child_round_trip.py` (new/updated tests)
- **Informed By**: Issue #1156 Solution Sketch, revised caller audit in Solution section
- **Assigned To**: transcript-skip-builder
- **Agent Type**: builder
- **Parallel**: true
- Insert the skip branch in `bridge/session_transcript.py` after the re-read at L285-287 and before `if status in TERMINAL_STATUSES` at L291.
- Insert the skip branch in `.claude/hooks/stop.py` after `agent_session = matches[0]` at L150 and before `finalize_session` at L159.
- Do NOT modify `models/session_lifecycle.py`, `agent/session_completion.py` (including `_complete_agent_session`), `agent/session_health.py`, or `monitoring/session_watchdog.py`.
- Create new file `tests/unit/test_session_transcript.py` with the four new tests from "Test Impact → New tests to add".
- Add `test_stop_hook_skips_finalize_when_waiting_for_children` to `tests/unit/test_stop_hook.py`.
- Add `test_pm_with_live_child_not_prematurely_finalized_by_transcript_end` to `tests/integration/test_parent_child_round_trip.py`.
- For each file in the Test Impact review list: read the file, confirm whether any fixture puts a session in `waiting_for_children` before calling `complete_transcript`. If yes, apply UPDATE (swap fixture status to `running` OR update assertion to expect the skip). If no, no change.
- Run `pytest tests/unit -k "transcript or lifecycle or stop_hook or hierarchy" -x` and `pytest tests/integration -k "parent_child or lifecycle or pm_final" -x` to confirm.
- Run `python -m ruff format bridge/session_transcript.py .claude/hooks/stop.py tests/unit/test_session_transcript.py tests/unit/test_stop_hook.py tests/integration/test_parent_child_round_trip.py`.

### 2. Validate the transcript-boundary skip
- **Task ID**: validate-transcript-skip
- **Depends On**: build-transcript-skip
- **Assigned To**: transcript-skip-validator
- **Agent Type**: validator
- **Parallel**: false
- Read `bridge/session_transcript.py` and confirm:
  - Skip branch fires only when `s.status == "waiting_for_children"` AND `status in ("completed", "failed")`.
  - Skip branch returns before the `finalize_session` call; SESSION_END marker still writes (it is written earlier in the function).
  - INFO log line is emitted with session_id and issue #1156 reference.
- Read `.claude/hooks/stop.py` and confirm:
  - Skip branch fires only when `agent_session.status == "waiting_for_children"`.
  - Skip branch returns early before `finalize_session` call.
- Read `models/session_lifecycle.py` and confirm:
  - `finalize_session` is unchanged. No new constant, no new exception type, no new precondition check.
- Read `agent/session_completion.py`, `agent/session_health.py`, `monitoring/session_watchdog.py` and confirm:
  - No changes. All recovery paths still call `finalize_session` unchanged.
- Re-run the lifecycle test suite: `pytest tests/unit tests/integration -k "lifecycle or transcript or session or hierarchy or watchdog or health" -x`.
- Report pass/fail status. If any fixture in the Test Impact list was missed during build, flag it for patch.

### 3. Update documentation
- **Task ID**: document-feature
- **Depends On**: validate-transcript-skip
- **Assigned To**: transcript-skip-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-lifecycle.md` "Parent Finalization" section with the transcript-boundary skip description.
- Add "Transcript-Boundary Skip" subsection under "State Transitions" with the rationale and link to #1156.
- Add note in `docs/features/pm-dev-session-architecture.md` about `complete_transcript` and the Stop hook both being status-no-ops for `waiting_for_children` PMs.
- Verify docstrings on `complete_transcript` mention the skip.
- Verify inline comments at both skip branches cite issue #1156.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-transcript-skip, validate-transcript-skip, document-feature
- **Assigned To**: transcript-skip-validator
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
| Transcript tests pass | `pytest tests/unit/test_session_transcript.py -x -q` | exit code 0 |
| Lifecycle tests pass | `pytest tests/unit/test_session_lifecycle.py tests/unit/test_session_lifecycle_consolidation.py -x -q` | exit code 0 |
| Hierarchy tests pass | `pytest tests/unit/test_agent_session_hierarchy.py tests/integration/test_parent_child_round_trip.py -x -q` | exit code 0 |
| Stop hook tests pass | `pytest tests/unit/test_stop_hook.py -x -q` | exit code 0 |
| Full unit tests pass | `pytest tests/unit -x -q` | exit code 0 |
| Full integration tests pass | `pytest tests/integration -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/session_transcript.py .claude/hooks/stop.py` | exit code 0 |
| Transcript skip branch exists | `grep -c "waiting_for_children" bridge/session_transcript.py` | output > 0 |
| Transcript skip log line exists | `grep -c "complete_transcript skipping terminal transition" bridge/session_transcript.py` | output > 0 |
| Stop hook skip branch exists | `grep -c "waiting_for_children" .claude/hooks/stop.py` | output > 0 |
| `finalize_session` unchanged (no guard constant) | `grep -c "VALID_WAITING_FOR_CHILDREN_EXIT_REASONS" models/session_lifecycle.py` | output = 0 |
| `finalize_session` unchanged (no guard exception) | `grep -c "class WaitingForChildrenGuardError" models/session_lifecycle.py` | output = 0 |
| Existing parent-sync reason string stable | `grep -c '"all children terminal"' models/session_lifecycle.py` | output > 0 |
| Completion runner reason string stable | `grep -c "pipeline complete: final summary delivered" agent/session_completion.py` | output > 0 |
| New transcript skip test exists | `grep -c "test_complete_transcript_skips_finalize_when_waiting_for_children" tests/unit/test_session_transcript.py` | output > 0 |
| New stop hook skip test exists | `grep -c "test_stop_hook_skips_finalize_when_waiting_for_children" tests/unit/test_stop_hook.py` | output > 0 |
| End-to-end test exists | `grep -c "test_pm_with_live_child_not_prematurely_finalized_by_transcript_end" tests/integration/test_parent_child_round_trip.py` | output > 0 |

## Critique Results

The critique stored verdict `NEEDS REVISION` for this plan (artifact_hash `sha256:22a0aa5bf0e5f22fad9a2ac0b7e6c96c915f35e0935cb39a4bfce2c62b8c2b47`). The detailed critique findings were not persisted to a reviewable artifact, so the revision pass operated on a self-critique performed against the `do-plan-critique` rubric (structural checks + war-room lenses). The revision addresses the following findings:

**Blocker 1 — Incomplete allow-list (Option B):** The original `VALID_WAITING_FOR_CHILDREN_EXIT_REASONS` allow-list contained only 2 reasons (`"all children terminal"`, `"pipeline complete: final summary delivered"`). A full audit of `finalize_session` callers (see Caller Audit in Solution) found 4+ additional legitimate callers with unique reason strings that would be rejected by Option B, including `_complete_agent_session` worker-crash path (reason `"agent session completed"`), health-check recovery (`"health check: already delivered"`, `"health check: N recovery attempts..."`), and watchdog stale-reaper (`"watchdog: stale session ..."`). Blocking these creates a stuck-state trap. **Resolution:** Option B dropped entirely. The fix is scoped to the two actual bypass call sites (transcript boundary + Stop hook) via early returns. No changes to `finalize_session`, no allow-list, no new exception type.

**Blocker 2 — Referenced test file does not exist:** The original plan referenced `tests/unit/test_session_transcript.py` in Test Impact and Verification. Verified via `ls tests/unit/test_session_transcript.py`: the file does not exist. **Resolution:** Test Impact now explicitly specifies CREATE for this file, with a rationale for creating a new focused test file rather than adding the tests to `test_session_lifecycle.py`.

**Concern 1 — Risks 3 and 4 in the original draft acknowledged the stuck-state trap without resolving it:** Original Risk 3 said "health-check recovery might no longer complete" and Risk 4 said "if the watchdog targets a `waiting_for_children` PM, the guard rejects" — both were punted to "out of scope" or "verify scope". **Resolution:** By dropping Option B, the stuck-state trap is eliminated at design time. Recovery paths (`_complete_agent_session`, health check, watchdog) continue to operate unchanged and are explicitly preserved. Revised Risks reflect the narrower fix scope.

**Concern 2 — `_complete_agent_session` crash path (`agent/session_completion.py:160`) was not mentioned in the original plan:** The worker's `finally` block calls `_complete_agent_session` with reason `"agent session completed"`, which is a guarded reason under Option B's allow-list. This path was invisible in the original draft. **Resolution:** Added to the Caller Audit table and explicitly marked as a legitimate recovery path that is intentionally NOT blocked. Added as Race 4 in Race Conditions.

**Concern 3 — Silent Stop-hook skip compounds debugging difficulty:** Revised plan adds Risk 5 documenting this and proposes an operational counter as a follow-up enhancement (out of scope for the fix itself).

---

## Open Questions

All open questions from the issue body have been resolved inside the plan:

1. **"Should allow-listed reason strings be promoted to a named constant set?"** — Resolved: No longer applicable; the revision drops the allow-list entirely. The fix is scoped to two call-site early returns.
2. **"Does the `agent_session=None` fallback path at `agent/session_executor.py:1517-1541` need the same guard?"** — Resolved: The fallback path calls `complete_transcript` identically to the happy path, so Fix 1's skip branch covers it. The fallback test in `test_health_check_recovery_finalization.py` is listed in Test Impact for re-verification.
3. **"Does the Claude Code hook Stop path at `.claude/hooks/stop.py` run the same `complete_transcript → finalize_session` sequence?"** — Resolved: No, the Stop hook calls `finalize_session` directly (not via `complete_transcript`). Fix 2 patches the Stop hook directly.

No remaining open questions. The plan is Ready for build.
