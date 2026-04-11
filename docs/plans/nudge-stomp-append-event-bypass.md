---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-11
tracking: https://github.com/tomcounsell/ai/issues/898
last_comment_id:
---

# Nudge Stomp Follow-up: Close the `append_event` Save Bypass

## Problem

PM/SDLC sessions that reach `end_turn` on their first SDK turn are silently marked `completed` instead of continuing the nudge loop. The PM's output (often 2–3 KB of analysis) never reaches Telegram because PM output routes through `nudge_continue`, and the nudge is nuked by a stale-object save before the next turn can pick it up. Direct Redis inspection of sessions 529, 530, 532, 539, and 541 on chat `-1003449100931` confirms every affected session has `auto_continue_count=0`, `pm_sent_message_ids=[]`, and a `session_events` history missing the `running→pending: nudge re-enqueue` entry.

[PR #885](https://github.com/tomcounsell/ai/pull/885) added CAS guards to `finalize_session()` and `transition_status()`, but the stomping save doesn't go through either helper. It goes through `log_lifecycle_transition → append_event → _append_event_dict → self.save()` (a plain Popoto full-state save, no CAS fence), and through a second direct `agent_session.save()` inside `_execute_agent_session`'s nudge branch. The `log_lifecycle_transition` call in the worker's finally block was introduced by [`silent-session-death`](docs/plans/silent-session-death.md) Fix 4 (commit `6ccce56f5`); its "Why this is safe" rationale missed that `append_event` implicitly persists.

**Current behavior:**
- PM session runs one SDK turn, produces output, `_enqueue_nudge` correctly writes `status=pending` and `auto_continue_count=1` to Redis on a fresh re-read.
- `_execute_agent_session`'s inner finally at `agent/agent_session_queue.py:3234-3235` writes a cosmetic `updated_at` heartbeat via full-state `save()` on the stale local `agent_session`, clobbering the nudge back to `status=running`, `auto_continue_count=0`.
- `_execute_agent_session` returns. Worker loop's outer finally at `:2259` calls `log_lifecycle_transition("completed", "worker finally block")` on the stale outer `session`. That triggers `append_event → _append_event_dict → self.save()` — a second full-state stale save.
- The nudge guard at `:2294-2318` re-reads Redis, sees `fresh.status='running'` (clobbered, not `pending`), falls through to `_complete_agent_session` → `finalize_session`, which CAS-checks `running==running` (no conflict) and terminates the session.
- Result: session marked `completed`, orphan `pending` status-index entry, watchdog false-alarms for hours.

**Desired outcome:**
- PM session that gets nudged re-enters the queue in `pending` state with `auto_continue_count` correctly incremented, is picked up by the next worker loop, and runs its continuation turn.
- No orphan `pending` status-index entries. No false-alarm `LIFECYCLE_STALL` warnings.
- Crash path (`_execute_agent_session` raises) still gets its session finalized and diagnostic snapshot written.
- Future callers of `append_event` / `append_history` / `log_lifecycle_transition` on stale objects can at worst lose a `session_events` entry, never clobber `status` / `auto_continue_count` / `message_text`.

## Freshness Check

**Baseline commit:** `ab5b1b53333fcef568ef5a4274eb1401b13d7d86`
**Issue filed at:** 2026-04-11T07:24:55Z (~5 minutes before plan creation)
**Disposition:** Unchanged

**File:line references re-verified:** All five sites cited in issue #898 still present at the same lines (`agent/agent_session_queue.py:2179`, `:2259`, `:2294-2318`, `:3234-3235`; `models/agent_session.py:1197-1198`, `:1234-1261`, `:1171-1199`). Phase 1.5 spike-2 surfaced `:3234-3235` as a third stomp site not in the issue body. Popoto `save(update_fields=[...])` partial-save path confirmed at `.venv/.../popoto/models/base.py:1119-1194`. `complete_transcript` fresh-read path confirmed at `bridge/session_transcript.py:285-292`.

**Cited sibling issues/PRs re-checked:** #867 (CLOSED 2026-04-10, "structurally fixed" by #885 — incomplete), #872 (CLOSED 2026-04-10, "harmless no-op" — incorrect closure), PR #885 (MERGED 2026-04-10, added CAS to lifecycle module but not to `append_event`'s save path), `docs/plans/lifecycle-cas-authority.md` (status `Completed`, non-overlapping).

**Commits on main since issue was filed touching referenced files:** None.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** Plan revised after human feedback on open questions — scope trimmed to the minimum set of changes that structurally eliminate the race (no fresh-read reorder needed, see Technical Approach).

## Prior Art

- **#867** — *Race: nudge re-enqueue stomped by worker finally-block finalize_session()* (CLOSED 2026-04-10). Fixed by PR #885's CAS in `finalize_session`. Incomplete because the stomp also fires through saves that bypass the lifecycle module entirely.
- **#872** — *cleanup: worker finally-block runs redundant snapshot/log/finalize on happy path — creates #867 race window* (CLOSED 2026-04-10). Proposed a `finalized_by_execute` gate, closed as "harmless no-op after CAS lands." This plan promotes that gate from optional optimization to **the load-bearing correctness fix** and ships it here.
- **#626** — *Sessions die silently* (CLOSED 2026-04-02). Fix 4 introduced the regression. The lifecycle-log observability motivation is still valid; this plan preserves it on the crash path via the same call site, now protected by Layer 2's partial save.
- **PR #885** — *Lifecycle CAS authority* (MERGED 2026-04-10). Foundation for the fresh-read API; this plan does not need it structurally because `finalized_by_execute` eliminates the stale-save from the happy path entirely.
- **[`docs/plans/silent-session-death.md`](docs/plans/silent-session-death.md)** — will be amended with a regression note pointing to this plan.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #885 (`#875` lifecycle CAS authority) | Added CAS re-read in `finalize_session()` / `transition_status()` that raises `StatusConflictError` on mismatch. | The stomp happens *upstream* of the lifecycle module. `log_lifecycle_transition → append_event → self.save()` and `agent_session.save()` in `_execute_agent_session`'s nudge branch write to Redis WITHOUT routing through `finalize_session` or `transition_status`. By the time CAS runs, the earlier stale save has aligned the on-disk and in-memory statuses, so CAS passes. |
| #872 resolution (declared "harmless no-op") | Deferred the `finalized_by_execute` gate as a performance optimization. | The `log_lifecycle_transition("worker finally block")` call at :2259 runs BEFORE the finalize path and has its own implicit save. Even with `_complete_agent_session` as a no-op, the log call still clobbers. The `finalized_by_execute` gate is genuinely needed — it prevents the log call from running at all on the happy path. |
| `silent-session-death` Fix 4 (`6ccce56f5`, 2026-04-02) | Added `session.log_lifecycle_transition(target, "worker finally block")` unconditionally in the worker finally block for crash observability. | Correct motivation, wrong location. The call operates on the stale worker-loop `session` variable. Plan's "Why this is safe" rationale missed that `append_event` persists. The crash-path observability should remain; the happy-path firing is the bug. |

**Root cause pattern:** Two independent save paths can mutate Redis without routing through the lifecycle module's CAS fence (`append_event`'s implicit save, and direct `.save()` on session objects held by the worker loop). Any fix that only hardens the lifecycle module leaves these bypasses open. This plan closes both: `finalized_by_execute` keeps the stale save from firing on the happy path, and Layer 2's partial save makes any future stale-save at worst corrupt `session_events`.

## Spike Results

### spike-1: Does Popoto `save(update_fields=[...])` maintain IndexedField indices correctly?
- **Assumption**: Partial save with `update_fields=['session_events', 'updated_at']` only writes listed fields and only calls `on_save` for listed fields.
- **Method**: code-read (`.venv/lib/python3.14/site-packages/popoto/models/base.py:1119-1194`)
- **Finding**: CONFIRMED. Line 1131-1137 HSETs only listed fields. Line 1164-1173 iterates `update_fields` and calls `on_save` only for listed fields — status's IndexedField hook is NOT called. Line 1183-1184 updates `_saved_field_values` only for listed fields.
- **Confidence**: high
- **Impact on plan**: Layer 2 is mechanically safe. Stale callers can at worst corrupt `session_events`; they cannot clobber `status`, `auto_continue_count`, `message_text`, or any other field.

### spike-2: Does `_execute_agent_session` always finalize via `complete_transcript` on the happy path?
- **Assumption**: Non-crash paths already handle finalization via `complete_transcript → finalize_session` on a fresh re-read, so the outer finally block's finalize work is redundant.
- **Method**: code-read (`agent/agent_session_queue.py:3210-3260`)
- **Finding**: Partially true with an important exception. Non-nudge happy path calls `complete_transcript` (fresh re-read, safe). **Nudge happy path skips `complete_transcript` and instead does `agent_session.updated_at = now; agent_session.save()` at lines 3234-3235 on the stale local.** This is a third stomp site not mentioned in the issue. Crash path still calls `complete_transcript` with `status="failed"`.
- **Confidence**: high
- **Impact on plan**: The nudge branch's `agent_session.save()` at :3234-3235 must be deleted (it's a cosmetic `updated_at` heartbeat; the next worker pop refreshes it anyway). `finalized_by_execute` alone does not cover this site because it fires BEFORE `_execute_agent_session` returns.

## Data Flow

1. **Entry point**: Bridge receives a Telegram message, enqueues an `AgentSession` with `status=pending`.
2. **Worker pop**: `_worker_loop` pops the session — this Python object is the "outer session" that will outlive `_execute_agent_session`.
3. **Execution**: `await _execute_agent_session(session)` runs. Inside, line 2803 fetches a fresh `agent_session` filtered by `status=running` (a second Python object).
4. **SDK turn + nudge**: SDK emits `end_turn`, `send_to_chat` routes to `nudge_continue`, `_enqueue_nudge` calls `get_authoritative_session` (a third Python object — the fresh nudge session), mutates it, and `transition_status(..., "pending")` CAS-saves `status=pending`, `auto_continue_count=1`, nudge event. Redis is now in the authoritative post-nudge state.
5. **Inner finally** (`:3234-3235`): `agent_session.save()` writes stale full state. Clobber #1. **With this plan: this block is deleted.** No heartbeat, no clobber. Nudge state survives.
6. **`_execute_agent_session` returns normally**. **With this plan: `finalized_by_execute = True` is set immediately after the `await`.**
7. **Outer finally** (`:2255`): `if not session_completed and not finalized_by_execute:` — **gated off**. Neither `log_lifecycle_transition` nor `_complete_agent_session` fires. Nudge state survives.
8. **Worker loop pops the now-pending session** from the queue, runs the next turn. Nudge loop proceeds as designed.
9. **Crash path instead of step 6**: `_execute_agent_session` raises. `finalized_by_execute` stays `False`. Outer finally runs its diagnostic snapshot and `_complete_agent_session` on the stale outer `session`. Layer 2's partial save prevents the `log_lifecycle_transition` call from clobbering any field except `session_events`; since the nudge couldn't have fired on a crash path (SDK aborted before `end_turn`), the stale state is still correct for finalization.

## Appetite

**Size:** Small

**Team:** Solo dev, PM (plan critique gate)

**Interactions:**
- PM check-ins: 1 (post-plan critique via `/do-plan-critique`)
- Review rounds: 1 (code review on the implementation PR)

Five surgical edits, three of which are one-line changes. The whole fix is ~40 LOC across two files plus tests and a reflection pattern addition.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Popoto supports `save(update_fields=[...])` | `python -c "from popoto.models.base import Model; import inspect; assert 'update_fields' in inspect.signature(Model.save).parameters"` | Layer 2 partial save |
| Redis running for tests | `redis-cli ping \| grep -q PONG` | Integration tests hit real Popoto/Redis |

Run all checks: `python scripts/check_prerequisites.py docs/plans/nudge-stomp-append-event-bypass.md`

## Solution

### Key Elements

- **`finalized_by_execute` gate**: a boolean set to `True` immediately after `_execute_agent_session(session)` returns normally. The worker loop's finally block gates its entire finalize path on `not finalized_by_execute`, so the happy path (nudge or not) never runs the stale lifecycle log or `_complete_agent_session`. Crash and `CancelledError` paths leave the flag `False`, preserving existing finalization behavior.
- **Delete the redundant inner save**: `agent/agent_session_queue.py:3234-3235` writes a cosmetic `updated_at` heartbeat via full-state `save()`. Delete the two lines. `updated_at` gets refreshed on the next worker pop anyway.
- **Partial save in `append_event`**: change `_append_event_dict`'s implicit save to `save(update_fields=["session_events", "updated_at"])`. Defense-in-depth for every current and future caller of `append_event` / `append_history` / `log_lifecycle_transition`. Spike-1 confirmed this is mechanically safe.
- **Reflection addition (not a hook)**: extend `scripts/reflections.py::step_review_logs`'s existing log-review to count `"Stale index entry"` occurrences per day. If the count is non-zero, surface it as a finding. The reflection runs daily, reads logs, needs no new hooks or rules. Regression detection with zero new machinery.
- **Docs**: amend `docs/plans/silent-session-death.md` regression note; update `docs/features/session-lifecycle.md` with the stale-object hazard and the `finalized_by_execute` gate rationale; inline comments at the three touched call sites.

### Flow

```
Worker pops pending session → _execute_agent_session runs SDK
  → SDK end_turn → output_router returns nudge_continue
  → _enqueue_nudge writes pending/auto_continue_count=1 ✓
  → _execute_agent_session inner finally (nudge branch NOW DOES NOTHING)
  → _execute_agent_session returns normally
  → worker loop: finalized_by_execute = True
  → worker loop finally: gated off by finalized_by_execute ✓
  → Redis state preserved: status=pending, auto_continue_count=1
  → Worker loop pops the pending session → next turn ✓
```

### Technical Approach

**Change #1 — `finalized_by_execute` gate (`agent/agent_session_queue.py:2167-2327`)**

Add a local boolean next to `session_failed` / `session_completed`:
```python
session_failed = False
session_completed = False
finalized_by_execute = False  # NEW: True after _execute_agent_session returns normally
try:
    await _execute_agent_session(session)
    finalized_by_execute = True  # NEW: reached only on non-exceptional return
except asyncio.CancelledError:
    ...  # unchanged — leaves finalized_by_execute False
except Exception as e:
    ...  # unchanged — leaves finalized_by_execute False
finally:
    if not session_completed and not finalized_by_execute:  # CHANGED
        # Everything inside this block stays as it is today (crash/cancel path only).
        # log_lifecycle_transition, save_session_snapshot, and the existing nudge guard
        # + _complete_agent_session all remain — they now only fire on crash/cancel.
        try:
            target = "failed" if session_failed else "completed"
            session.log_lifecycle_transition(target, "worker finally block")
        except Exception:
            pass
        # ...existing snapshot + nudge guard + _complete_agent_session code unchanged...
```

**Why this is correct:**
- **Happy path (non-nudge)**: `_execute_agent_session` returns normally after `complete_transcript` already ran (fresh-read, CAS-guarded). `finalized_by_execute=True`. Finally block gated off. No double-finalize, no stale save.
- **Happy path (nudge)**: `_execute_agent_session` returns normally after `_enqueue_nudge` transitioned the session to `pending`. `finalized_by_execute=True`. Finally block gated off. **Nudge state survives.** This is the bug fix.
- **Crash path**: `_execute_agent_session` raises. `finalized_by_execute=False`. Finally block runs as it does today. On crash, `_enqueue_nudge` could not have fired (the raise aborted execution). The outer `session` is stale but valid for finalization — its pre-execution state is the only authoritative state. Layer 2's partial save protects `status`/`auto_continue_count`/`message_text` from being clobbered by the `log_lifecycle_transition` call.
- **CancelledError path**: `_execute_agent_session` raises `CancelledError`. `finalized_by_execute=False`. Existing handler at `:2171-2189` runs, calls `session.log_lifecycle_transition("running", ...)` on stale, sets `session_completed=True`, re-raises. With Layer 2 in place, the stale `log_lifecycle_transition` can only corrupt `session_events`, not `status`. Existing behavior preserved.

**Change #2 — Delete the redundant inner save (`agent/agent_session_queue.py:3233-3235`)**

Before:
```python
if not chat_state.defer_reaction:
    complete_transcript(session.session_id, status=final_status)
else:
    agent_session.updated_at = datetime.now(tz=UTC)
    agent_session.save()
```

After:
```python
if not chat_state.defer_reaction:
    complete_transcript(session.session_id, status=final_status)
# else: nudge path — _enqueue_nudge has already written the authoritative post-nudge
# state (status=pending, auto_continue_count, nudge event, new message_text). Do NOT
# save the stale `agent_session` local; it would clobber the nudge. updated_at will be
# refreshed on the next worker pop.
```

Two lines deleted, one comment added. This closes the third stomp site surfaced by spike-2.

**Change #3 — Partial save in `_append_event_dict` (`models/agent_session.py:1197`)**

Before:
```python
try:
    self.save()
except Exception as e:
    logger.warning(
        f"append_event save failed for session {self.session_id} "
        f"(event_type={event_dict.get('event_type')!r}): {e}"
    )
```

After:
```python
try:
    # Partial save: only persist session_events + updated_at. Prevents stale-object
    # callers from clobbering status/auto_continue_count/message_text (see #898).
    self.save(update_fields=["session_events", "updated_at"])
except Exception as e:
    logger.warning(
        f"append_event save failed for session {self.session_id} "
        f"(event_type={event_dict.get('event_type')!r}): {e}"
    )
```

One-line functional change + comment. Spike-1 confirmed Popoto's partial-save path writes only listed fields and only invokes `on_save` hooks for listed fields — `status`'s IndexedField hook is untouched.

**Change #4 — Reflection pattern (`scripts/reflections.py::step_review_logs`)**

Inside the existing log-review step (around line 919-928 where `extract_structured_errors` is called), add one more pattern scan per log file:

```python
# Detect nudge-stomp regressions: issue #898. If fix regresses, stale-index
# warnings will start reappearing in bridge.log. No hook or rule needed —
# this reflection runs daily and surfaces the count as a finding.
with open(log_file) as f:
    content = f.read()
stale_index_count = content.count("Stale index entry")
if stale_index_count > 0:
    findings.append(
        f"{log_file.name}: {stale_index_count} 'Stale index entry' warnings "
        f"(regression marker for #898 — investigate)"
    )
```

Five lines in an existing reflection step. No new reflection unit, no new hook, no new rule. The existing daily reflection loop already tails logs and produces findings — this adds one more pattern.

**Change #5 — Docs**

- `docs/plans/silent-session-death.md`: add a `> **REGRESSION NOTE (2026-04-11):** ...` block at the top pointing to this plan. Do NOT change `status:`; the original plan shipped.
- `docs/features/session-lifecycle.md`: create if missing, or update the existing "Session states" section with a new "Stale object hazard" entry documenting the three-object pattern (outer worker `session`, inner `_execute_agent_session` `agent_session`, `_enqueue_nudge` fresh re-read), the `finalized_by_execute` gate that makes stale-save impossible on the happy path, and Layer 2's partial save as the crash-path safety net.
- `docs/plans/lifecycle-cas-authority.md`: add a backlink in the "Related Work" (or equivalent) section noting that CAS fenced the lifecycle module but did not cover `append_event`'s implicit save path; this plan closes the remaining bypass.
- Inline: comments at the three touched call sites explaining the #898 regression and the fix.

**Why not fresh-read reorder of the finally block (dropped from original plan)?**

The original plan proposed re-reading `fresh = get_authoritative_session(session_id)` at the top of the finally block and operating on the fresh object for all downstream ops. That fix works but it's larger, touches more lines, and duplicates re-read work that `_complete_agent_session` already does internally. With `finalized_by_execute` preventing the finally block from running on the happy path at all, AND Layer 2 making stale saves non-destructive, the fresh-read reorder is redundant. Shipped scope is strictly smaller. Human reviewer chose this direction via OQ#1 ("minimum possible").

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/agent_session_queue.py:2260` (`except Exception: pass` around `log_lifecycle_transition`) — still present, still catches. Add a test that asserts a `log_lifecycle_transition` RuntimeError on the crash path does not crash the worker loop. This test exists in similar form at `tests/unit/test_crash_snapshot.py:117`; extend it to cover the `finalized_by_execute=False` branch.
- [ ] `agent/agent_session_queue.py:2319-2327` (`except Exception as guard_err`) — unchanged. Still fires on the crash path, still falls back to `_complete_agent_session`. Existing tests cover this.
- [ ] `models/agent_session.py:1199-1203` (`except Exception as e` around `_append_event_dict`'s save) — unchanged structure, now with `update_fields`. Test that a partial-save failure is caught and logged (existing test coverage in `tests/integration/test_lifecycle_transition.py`; add assertion for partial-save call signature).

### Empty/Invalid Input Handling
- [ ] `_execute_agent_session` returning `None` or raising an unexpected subtype — unchanged. `finalized_by_execute` only becomes `True` on clean return; any exception subtype leaves it `False`.
- [ ] `append_event` called with empty string or `None` text — unchanged (existing behavior in `_append_event_dict`).

### Error State Rendering
- [ ] No user-visible output changes from this plan. The user's visible symptom (silent PM death after turn 1) disappears once the nudge is preserved. Validated via Success Criterion #7 (production validation).

## Test Impact

- [ ] `tests/unit/test_session_completion_zombie.py::TestWorkerFinallyBlockNudgeGuard` — UPDATE: add one test case that exercises the `finalized_by_execute=True` path and asserts `_complete_agent_session` is NOT called. Existing three test cases remain valid (they cover the crash path where `finalized_by_execute=False`).
- [ ] `tests/unit/test_worker_cancel_requeue.py` — UPDATE: add assertion that `finalized_by_execute` remains `False` in the `CancelledError` branch (since the raise bypasses the assignment). Existing tests should continue to pass.
- [ ] `tests/unit/test_crash_snapshot.py` — UPDATE: tests mock `log_lifecycle_transition` on the session and assert it's called with `"worker finally block"`. Still valid on the crash path. Add one test case where `finalized_by_execute=True` and assert `log_lifecycle_transition` is NOT called.
- [ ] `tests/unit/test_complete_agent_session_redis_reread.py` — UPDATE: verify `_complete_agent_session` still fires on the crash path. No change to its internals.
- [ ] `tests/integration/test_agent_session_lifecycle.py` — UPDATE: add a new integration test (see NEW below).
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE: add one test asserting `_append_event_dict` passes `update_fields=["session_events", "updated_at"]` to `save()`. Use `unittest.mock.patch.object(AgentSession, "save")` — no real Popoto needed for this specific assertion.
- [ ] `tests/unit/test_recovery_respawn_safety.py::test_nudge_reread_guard_catches_late_terminal` — UPDATE: no change expected; tests `_enqueue_nudge`'s own guard, orthogonal to this fix.
- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — UPDATE: no change expected; tests `finalize_session`'s CAS in isolation.
- [ ] **NEW**: `tests/integration/test_nudge_stomp_regression.py` — CREATE one test file with two cases. **Both tests use second Popoto instances calling the authoritative APIs (`transition_status`, `finalize_session`, instance `.save()`) to represent concurrent writes — NEVER raw Redis commands or direct `r.hset`/`r.delete` calls.**
  1. `test_finalized_by_execute_gates_happy_path`: create a real `AgentSession` via `AgentSession.create(status="running", ...)`. Obtain a second Popoto instance via `AgentSession.query.get(id=...)` or `get_authoritative_session(session_id)`. Call `transition_status(second_instance, "pending", reason="simulated nudge")` with `second_instance.auto_continue_count=1` and a manually-appended nudge event — this is the sanctioned transition API and matches exactly what `_enqueue_nudge` does in production. Keep the ORIGINAL in-memory object untouched (it is now stale). Call the outer finally-block code path directly — either by invoking the post-Change-#1 `if not session_completed and not finalized_by_execute:` branch with `finalized_by_execute=True`, or by running the worker loop with a mocked `_execute_agent_session` that just returns. Fresh-query Redis via a third `AgentSession.query.filter(...)`. Assert: `status='pending'`, `auto_continue_count=1`, `session_events` contains the nudge event. (Per OQ3: simpler than the original two-object harness — uses sanctioned APIs only, one direct call to the finally block logic.)
  2. `test_layer_2_partial_save_preserves_fields`: create an `AgentSession.create(status="running", auto_continue_count=0, ...)`. Obtain a second Popoto instance via `AgentSession.query.get(id=...)` and mutate it via its own setters, then call `second_instance.save()` — this is the sanctioned full-save API on an instance. Set `second_instance.status='completed'`, `second_instance.auto_continue_count=5` (via `_saved_field_values` backfill pattern if needed to satisfy the index update, mirroring what `finalize_session` does internally), save. On the ORIGINAL stale in-memory instance, call `append_event("lifecycle", "test event")`. Fresh-query Redis via `AgentSession.query.get(id=...)`. Assert: `status='completed'`, `auto_continue_count=5`, `session_events` contains the new "test event". If the `_saved_field_values` dance feels fragile, use `finalize_session(second_instance, "completed", reason="simulated concurrent finalize")` instead — same effect, sanctioned API.
- [ ] No deletes. One new test file with two cases; six updates to existing test files.

## Rabbit Holes

- **Fresh-read reorder of the finally block.** The original plan proposed this. With `finalized_by_execute` + Layer 2, it's unnecessary. Skipped.
- **Extracting a `SessionRunner` class.** Correct long-term refactor, too big for this appetite. Out of scope.
- **Collapsing `_complete_agent_session` and `complete_transcript` into one finalizer.** Same.
- **Per-list CAS for `session_events`.** Race 4 in the Race Conditions section is real but out of scope. Popoto would need list-field-level CAS which is an upstream change.
- **Making `log_lifecycle_transition` refuse to save when called on a stale object.** Would require tracking "last seen authoritative version" on every instance. Layer 2 achieves the same practical safety with a one-line change.
- **Rewriting the nudge path end-to-end.** Covered by #743 / `extract-nudge-to-pm.md`, actively being planned elsewhere.
- **Messenger log copy fix** (`agent/messenger.py:77` — "Sent result message" claimed even for nudge-routed output). Per OQ1 "minimum possible", dropped from this plan. Noted as a diagnostic ergonomics issue but not shipped here.

## Risks

### Risk 1: `finalized_by_execute=True` is set on a path where the session state is actually inconsistent
**Impact:** If `_execute_agent_session` returns cleanly but leaves Redis in a half-mutated state (e.g., `_enqueue_nudge` succeeded but the inner finally's heartbeat-delete introduced a different bug), the worker loop would skip finalization on a record that should have been finalized. The session would sit in Redis forever.
**Mitigation:** `_execute_agent_session`'s return path is well-defined: non-nudge → `complete_transcript` runs and finalizes; nudge → `_enqueue_nudge` transitions to `pending`. Both are authoritative writes. Add an invariant assertion at the `finalized_by_execute = True` assignment: `assert session_id matches a Redis record in {completed, failed, pending}` — if this ever fires, it's a real bug. Gate the assertion on a DEBUG flag to avoid production cost.

### Risk 2: Layer 2's partial save silently drops a field mutation that a caller expected to flush
**Impact:** If any existing caller mutates non-`session_events` fields on an `AgentSession` and relies on a subsequent `append_event` / `append_history` to flush those mutations, Layer 2 breaks that caller.
**Mitigation:** Audit every `append_event` / `append_history` / `log_lifecycle_transition` call site as part of the build task (see Step 2 below). Grep reveals only ~15 call sites total in source + tests. Each call site gets a one-line inspection: does the caller mutate any non-event fields before the `append_*` call without calling `.save()` explicitly? If yes, update the caller to call `.save()` explicitly. Preliminary scan shows `agent/agent_session_queue.py:2805-2811` is the only site that mutates `updated_at`/`branch_name`/`task_list_id` before `append_history` — but it already calls `.save()` at line 2810 before the `append_history` call, so it's safe.

### Risk 3: The reflection catches the regression only once per day
**Impact:** If the fix regresses post-deploy, the reflection runs daily, so a regression could live up to 24 hours before being surfaced.
**Mitigation:** Accept the latency. A 24-hour detection window is still a huge improvement over the current state (no detection at all until users notice silent PM death). The reflection's finding surfaces in the daily reflection report with a `#898 regression marker` tag; a human can investigate the same day.

### Risk 4: `test_finalized_by_execute_gates_happy_path` is a partial simulation (not a full end-to-end worker loop)
**Impact:** Per OQ3, the test uses a second Popoto instance calling the sanctioned `transition_status` API (not two Python objects constructed through the real worker loop). The test might pass while the real worker loop has some subtle scheduling issue the simulation doesn't reproduce.
**Mitigation:** Complement the integration test with production validation (Success Criterion #7): after deploy, send a real PM/SDLC message and verify the session actually runs multiple turns. The reflection in Change #4 also catches any real-world regression within 24 hours.

## Race Conditions

### Race 1: Nudge re-enqueue vs. worker finally block (primary bug)
**Location:** `agent/agent_session_queue.py:2254-2327` (outer worker finally) and `:3222-3241` (`_execute_agent_session` nudge branch).
**Trigger:** `_enqueue_nudge` writes the authoritative post-nudge state on a fresh re-read object, then the inner finally at `:3234-3235` and the outer finally at `:2259` both fire `.save()` on stale locals that have the pre-nudge snapshot.
**Data prerequisite:** Redis record must be in the post-nudge state after `_enqueue_nudge` returns.
**State prerequisite:** The stale local caller must NOT `.save()` on its object after the nudge has written.
**Mitigation:** Change #1 (`finalized_by_execute` gate) prevents the outer finally from running the stale save on the happy path. Change #2 (delete the inner save) eliminates the `:3234-3235` clobber entirely. Change #3 (partial save) makes any residual stale-save hazard non-destructive for `status` / `auto_continue_count` / `message_text`.

### Race 2: Nudge re-enqueue vs. a worker in a different project key picking up the session immediately
**Location:** `agent/agent_session_queue.py:2560-2570` (`_enqueue_nudge → _ensure_worker`).
**Trigger:** Another worker pops the (now pending) session before the current worker's finally block checks it.
**Mitigation:** Out of scope. Project-keyed serialization (`678c67a1`) and the global semaphore already prevent cross-worker picks on the same `session_id`. If it does happen, `finalized_by_execute=True` on the current worker still makes the finally block a no-op, so no damage.

### Race 3: `append_event` called concurrently from two paths on the same `session_id`
**Location:** `models/agent_session.py:1171-1199`.
**Trigger:** `complete_transcript` (fresh read) calls `append_event` while `_worker_loop`'s finally block (now gated on `finalized_by_execute=False`) also calls it on the crash path.
**Mitigation:** Sequenced in practice: `complete_transcript` runs INSIDE `_execute_agent_session` before it returns. By the time the outer finally could run `append_event`, `_execute_agent_session` has either returned (and set `finalized_by_execute=True`, gating off the finally) or raised (in which case `complete_transcript` already handled the transcript and the finally block is only doing crash diagnostics). Document this ordering requirement in the inline comment at `:2255`.

### Race 4: Layer 2 partial save of `session_events` races with a concurrent full save
**Location:** `models/agent_session.py:1182-1198`.
**Trigger:** Two concurrent writers append to `session_events`. One entry is lost.
**Mitigation:** Out of scope. Per-chat session serialization prevents concurrent writers today. If this becomes a problem, fix requires list-field-level CAS in Popoto (large upstream change). Document in the `append_event` docstring that appends are best-effort under concurrent mutation.

## No-Gos (Out of Scope)

- **Fresh-read reorder of the finally block.** Explicitly dropped in favor of `finalized_by_execute` + Layer 2 (smaller, same correctness outcome).
- **Messenger log copy fix** at `agent/messenger.py:77`. Diagnostic ergonomics, not correctness.
- **`SessionRunner` class extraction.** Separate plan.
- **Collapsing `_complete_agent_session` and `complete_transcript`.** Separate plan.
- **Full audit of every `.save()` call site in `models/agent_session.py`.** The targeted audit in Risk 2 covers `append_event` callers; a general audit is a separate chore.
- **Redis WATCH/MULTI for atomic session updates.** Upstream Popoto change.
- **Nudge path refactor.** Covered by #743.
- **New hook or lint rule for stale-object detection.** Per OQ4, replaced by the reflection addition in Change #4.

## Update System

No update system changes required — this fix is internal to the worker loop and session model. Deploying is a standard `/update` pull + bridge/worker restart. After deploy, existing orphan `pending` status-index entries from sessions 502, 516, 517, 529, 539, 540, 541 will be cleaned up on the next worker restart's `rebuild_indexes()` call. No manual intervention. Document this in the release note.

## Agent Integration

No agent integration required — this is an internal worker-loop fix. The PM/SDLC agent does not invoke these code paths directly. `.mcp.json` untouched, no new MCP tools, no bridge changes.

Indirect agent impact (positive): PM sessions that previously went silent after their first reply will now correctly continue their SDLC pipelines until they hit an explicit completion signal. Observable behavior changes from "PM responds once, then session dies" to "PM continues through the pipeline as designed."

## Documentation

### Feature Documentation
- [ ] Amend `docs/plans/silent-session-death.md` with a regression note at the top: `> **REGRESSION NOTE (2026-04-11):** Fix 4's \`log_lifecycle_transition("worker finally block")\` call regressed as [#898](https://github.com/tomcounsell/ai/issues/898) — the \`append_event → self.save()\` chain clobbers the nudge re-enqueue when called on a stale session object. Follow-up fix in \`docs/plans/nudge-stomp-append-event-bypass.md\`.` Do NOT change the `status:` field; the original plan still shipped.
- [ ] Create or update `docs/features/session-lifecycle.md` with a "Stale object hazard" section documenting the three-object pattern (outer worker `session`, inner `agent_session`, nudge fresh re-read), the `finalized_by_execute` gate, and Layer 2's partial save as the safety net.
- [ ] Add a backlink in `docs/plans/lifecycle-cas-authority.md`'s "Related Work" section noting that CAS fenced the lifecycle module but not `append_event`'s save path; this plan closes the remaining bypass.
- [ ] Add entry to `docs/features/README.md` index if `session-lifecycle.md` is newly created.

### Inline Documentation
- [ ] Docstring on `models/agent_session.py::append_event` documenting the stale-object hazard and Layer 2's partial-save protection. Link to `docs/features/session-lifecycle.md`.
- [ ] Docstring on `models/agent_session.py::log_lifecycle_transition` documenting that it implicitly saves via `append_event`.
- [ ] Inline comment at `agent/agent_session_queue.py:2170` (new `finalized_by_execute = False` declaration) explaining the #898 regression and the gate's purpose.
- [ ] Inline comment at `agent/agent_session_queue.py:3233` (the deleted `agent_session.save()` site) explaining why it's gone and pointing at `_enqueue_nudge`.
- [ ] Inline comment at `models/agent_session.py:1197` explaining the `update_fields` rationale.

## Success Criteria

- [ ] `tests/integration/test_nudge_stomp_regression.py::test_finalized_by_execute_gates_happy_path` passes. Currently fails against unfixed main (TDD red).
- [ ] `tests/integration/test_nudge_stomp_regression.py::test_layer_2_partial_save_preserves_fields` passes. Currently fails against unfixed main.
- [ ] All updated existing tests in the Test Impact section still pass: `tests/unit/test_session_completion_zombie.py`, `tests/unit/test_worker_cancel_requeue.py`, `tests/unit/test_crash_snapshot.py`, `tests/unit/test_complete_agent_session_redis_reread.py`, `tests/integration/test_agent_session_lifecycle.py`, `tests/integration/test_lifecycle_transition.py`, `tests/unit/test_recovery_respawn_safety.py`, `tests/unit/test_session_lifecycle_consolidation.py`.
- [ ] After running the regression tests 10× in a loop, no `"Stale index entry"` log lines appear in captured test logs.
- [ ] `pytest tests/ -x -q` → exit 0. `python -m ruff check .` → exit 0. `python -m ruff format --check .` → exit 0.
- [ ] `grep -q "finalized_by_execute" agent/agent_session_queue.py` → exit 0. `grep -q 'update_fields=\["session_events", "updated_at"\]' models/agent_session.py` → exit 0. `grep -c "agent_session.save()" agent/agent_session_queue.py` returns one fewer occurrence than on main.
- [ ] `scripts/reflections.py` log-review step includes the `Stale index entry` pattern count — verified by `grep -q "Stale index entry" scripts/reflections.py`.
- [ ] Production validation: deploy to dev, send a real PM/SDLC message (e.g., `"review PR 897"`). Via `python -m tools.valor_session status --id <id>`: during nudge cycles `auto_continue_count > 1`, `session_events` contains at least one `running→pending: nudge re-enqueue` entry, final completion happens only after the PM's explicit completion signal (not after turn 1).
- [ ] `docs/plans/silent-session-death.md` has the regression note at the top.
- [ ] `docs/features/session-lifecycle.md` exists (or is updated) with the stale-object hazard section.
- [ ] No bug fix has xfail/xpass tests to convert (confirmed: `grep -rn "pytest.mark.xfail\|pytest.xfail(" tests/ | grep -i "nudge\|append_event\|lifecycle_transition\|stomp"` returns empty).

## Team Orchestration

### Team Members

- **Builder (primary fix)**
  - Name: `nudge-stomp-builder`
  - Role: Apply Changes #1, #2, #3, #4 across `agent/agent_session_queue.py`, `models/agent_session.py`, and `scripts/reflections.py`. Single builder because the five edits are tightly coupled and must land together.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `regression-test-engineer`
  - Role: Create `tests/integration/test_nudge_stomp_regression.py` with two test cases. Update the existing tests flagged in Test Impact.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `nudge-stomp-validator`
  - Role: Verify all five changes landed correctly. Run the regression tests 10× loop. Audit `append_event` / `append_history` / `log_lifecycle_transition` call sites for Risk 2 compliance.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `lifecycle-doc-writer`
  - Role: Amend `silent-session-death.md`, create/update `session-lifecycle.md`, update `lifecycle-cas-authority.md` backlink, update inline docstrings and comments.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Using Tier 1 only: `builder`, `test-engineer`, `validator`, `documentarian`. No specialists needed.

## Step by Step Tasks

### 1. Write failing regression tests (TDD red)
- **Task ID**: build-regression-tests-red
- **Depends On**: none
- **Validates**: `tests/integration/test_nudge_stomp_regression.py` (create)
- **Informed By**: spike-2 (third stomp site at `:3234-3235`), OQ3 (simpler test harness: second Popoto instance using sanctioned APIs, not raw Redis writes — matches the `feedback_never_raw_delete_popoto` rule)
- **Assigned To**: regression-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/integration/test_nudge_stomp_regression.py`. **Both tests MUST use sanctioned Popoto APIs** (`AgentSession.create(...)`, `AgentSession.query.get(...)`, instance `.save()`, `transition_status(instance, ...)`, `finalize_session(instance, ...)`). **NEVER** raw Redis commands like `r.hset` / `r.delete` / `redis-cli DEL`.
  - `test_finalized_by_execute_gates_happy_path`: `AgentSession.create(status="running", ...)` → obtain a second instance via `get_authoritative_session(session_id)` → `transition_status(second_instance, "pending", reason="simulated nudge")` with `second_instance.auto_continue_count=1` set before the call (this is exactly what `_enqueue_nudge` does). The original in-memory instance is now stale. Call the worker finally-block code path directly with `finalized_by_execute=True` (or run the worker loop with a mocked `_execute_agent_session`). Fresh-query Redis via a third `AgentSession.query.get(id=...)`. Assert `status='pending'`, `auto_continue_count=1`, nudge event present in `session_events`.
  - `test_layer_2_partial_save_preserves_fields`: `AgentSession.create(status="running", auto_continue_count=0, ...)` → second instance via `AgentSession.query.get(id=...)` → `finalize_session(second_instance, "completed", reason="simulated concurrent finalize")` with `second_instance.auto_continue_count=5` set before the call. Original stale instance calls `append_event("lifecycle", "test event")`. Fresh-query via a third `AgentSession.query.get(id=...)`. Assert `status='completed'`, `auto_continue_count=5`, `session_events` contains the new event.
- Run `pytest tests/integration/test_nudge_stomp_regression.py -v` and confirm BOTH tests FAIL against unfixed main. Report the failure signatures.
- **Done when**: both new test cases are red.

### 2. Apply the five code changes
- **Task ID**: build-primary-fix
- **Depends On**: build-regression-tests-red
- **Validates**: `tests/integration/test_nudge_stomp_regression.py` (both cases), existing tests in Test Impact
- **Informed By**: spike-1 (partial save is safe), spike-2 (three stomp sites, not two)
- **Assigned To**: nudge-stomp-builder
- **Agent Type**: builder
- **Parallel**: false
- **Change #1**: Add `finalized_by_execute = False` declaration in `_worker_loop` alongside `session_failed`/`session_completed`. Set `finalized_by_execute = True` immediately after `await _execute_agent_session(session)` returns without exception. Change the finally block's guard from `if not session_completed:` to `if not session_completed and not finalized_by_execute:`. Inside the guarded block, leave everything as-is — the existing `log_lifecycle_transition`, `save_session_snapshot`, nudge guard, and `_complete_agent_session` calls are now crash/cancel-path only and are safe under Layer 2.
- **Change #2**: Delete `agent_session.updated_at = datetime.now(tz=UTC); agent_session.save()` at `agent/agent_session_queue.py:3234-3235`. Replace with an explanatory comment pointing at `_enqueue_nudge` as the authoritative writer. Keep the surrounding `if not chat_state.defer_reaction: complete_transcript(...)` branch unchanged.
- **Change #3**: In `models/agent_session.py:1197`, change `self.save()` to `self.save(update_fields=["session_events", "updated_at"])`. Add a comment referencing this plan.
- **Change #4**: In `scripts/reflections.py::step_review_logs`, add the `Stale index entry` pattern count after the existing `extract_structured_errors` call. Surface as a finding when count > 0 with the `#898 regression marker` tag.
- **Audit for Risk 2**: grep every `append_event` / `append_history` / `log_lifecycle_transition` call site in source. For each, verify the caller does not rely on `append_*` to flush non-event field mutations. Document the audit results in the PR description. Known safe sites: `agent/agent_session_queue.py:2805-2811` (explicitly saves at :2810 before `append_history` at :2811).
- Run `pytest tests/integration/test_nudge_stomp_regression.py -v` and confirm both tests PASS.
- Run `pytest tests/unit/test_session_completion_zombie.py tests/unit/test_worker_cancel_requeue.py tests/unit/test_crash_snapshot.py tests/unit/test_complete_agent_session_redis_reread.py tests/integration/test_agent_session_lifecycle.py tests/integration/test_lifecycle_transition.py -v` and confirm no regressions.
- **Done when**: regression tests green, existing tests green, grep audit clean.

### 3. Update existing tests to cover `finalized_by_execute`
- **Task ID**: build-test-updates
- **Depends On**: build-primary-fix
- **Validates**: test files listed in Test Impact
- **Assigned To**: regression-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add a test case to `tests/unit/test_session_completion_zombie.py::TestWorkerFinallyBlockNudgeGuard` asserting the finally block's finalize path is NOT entered when `finalized_by_execute=True`.
- Add a test case to `tests/unit/test_crash_snapshot.py` asserting `log_lifecycle_transition` is NOT called when `finalized_by_execute=True`.
- Add a test case to `tests/unit/test_worker_cancel_requeue.py` asserting `finalized_by_execute` remains `False` in the `CancelledError` branch (raise bypasses the assignment).
- Add one test to `tests/integration/test_lifecycle_transition.py` asserting `_append_event_dict` passes `update_fields=["session_events", "updated_at"]` to `save()` (via `unittest.mock.patch.object`).
- Run `pytest tests/unit/test_session_completion_zombie.py tests/unit/test_crash_snapshot.py tests/unit/test_worker_cancel_requeue.py tests/integration/test_lifecycle_transition.py -v` and confirm all pass.
- **Done when**: four new test cases green.

### 4. Validate everything
- **Task ID**: validate-all
- **Depends On**: build-test-updates
- **Assigned To**: nudge-stomp-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_nudge_stomp_regression.py -v --count=10` (or equivalent loop). Both tests must pass all 10 iterations.
- Run the full suite: `pytest tests/ -x -q`. Must exit 0.
- Run `python -m ruff check .` and `python -m ruff format --check .`. Must exit 0.
- Run the verification table from the Verification section.
- Audit `append_event` / `append_history` / `log_lifecycle_transition` call site grep output. Verify the builder's Risk 2 audit matches current source.
- Inspect captured test logs for any `"Stale index entry"` warnings in nudge-continuation scenarios — expected count: 0.
- Report pass/fail.

### 5. Documentation cascade
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: lifecycle-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Amend `docs/plans/silent-session-death.md` with the regression note at the top (do not change `status:`).
- Create or update `docs/features/session-lifecycle.md` with the "Stale object hazard" section, `finalized_by_execute` gate explanation, and Layer 2 partial-save rationale.
- Update `docs/plans/lifecycle-cas-authority.md` with a backlink.
- Update `docs/features/README.md` index if `session-lifecycle.md` is new.
- Verify inline comments at the three code-change sites (`:2170`, `:3233`, `models/agent_session.py:1197`) landed and are accurate.
- Verify docstrings on `append_event` and `log_lifecycle_transition` are updated.
- Commit all doc changes.

### 6. Final verification
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: nudge-stomp-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the verification table after doc changes.
- Confirm all success criteria are met.
- Produce final report: pass/fail per success criterion, ready for PR.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Regression tests pass | `pytest tests/integration/test_nudge_stomp_regression.py -v` | exit code 0 |
| Full test suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Change #1 applied (`finalized_by_execute` gate) | `grep -q "finalized_by_execute" agent/agent_session_queue.py` | exit code 0 |
| Change #1 applied (guard updated) | `grep -q "not session_completed and not finalized_by_execute" agent/agent_session_queue.py` | exit code 0 |
| Change #2 applied (inner save deleted) | `awk '/if not chat_state.defer_reaction/,/save_session_snapshot/' agent/agent_session_queue.py \| grep -c "agent_session.save()"` | output contains 0 |
| Change #3 applied (`append_event` partial save) | `grep -q 'update_fields=\["session_events", "updated_at"\]' models/agent_session.py` | exit code 0 |
| Change #4 applied (reflection pattern) | `grep -q "Stale index entry" scripts/reflections.py` | exit code 0 |
| No stale-index warnings in regression tests | `pytest tests/integration/test_nudge_stomp_regression.py -v 2>&1 \| grep -c "Stale index entry"` | output contains 0 |
| Silent-session-death plan amended | `grep -q "#898" docs/plans/silent-session-death.md` | exit code 0 |
| Session-lifecycle doc exists | `test -f docs/features/session-lifecycle.md` | exit code 0 |
| No xfail regressions | `grep -rn "pytest.mark.xfail\|pytest.xfail(" tests/ \| grep -iE "nudge\|append_event\|lifecycle_transition\|stomp"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---
