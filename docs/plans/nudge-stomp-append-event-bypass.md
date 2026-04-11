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

PM/SDLC sessions that reach `end_turn` on their first SDK turn are silently marked `completed` instead of continuing the nudge loop. The PM's output (often 2–3 KB of analysis) never lands in Telegram because PM output routes through `nudge_continue`, and the nudge is nuked by a stale-object save before it can be picked up for the next turn. Direct Redis inspection of sessions 529, 530, 532, 539, and 541 on chat `-1003449100931` confirms every affected session has `auto_continue_count=0`, `pm_sent_message_ids=[]`, and a `session_events` history missing the `running→pending: nudge re-enqueue` entry.

[PR #885](https://github.com/tomcounsell/ai/pull/885) was supposed to make this race "structurally impossible" by adding Compare-And-Set (CAS) guards to `finalize_session()` and `transition_status()`. It doesn't catch this defect because the stomping save doesn't go through either helper — it goes through `log_lifecycle_transition → append_event → _append_event_dict → self.save()` (plain Popoto full-state save, no CAS fence). The `log_lifecycle_transition` call in the worker's happy-path finally block (introduced by [`silent-session-death`](docs/plans/silent-session-death.md) Fix 4, commit `6ccce56f5`) operates on the stale session object the worker popped and writes its entire pre-nudge snapshot back to Redis, clobbering the nudge's `pending` status, `auto_continue_count`, `message_text`, and event history. Phase 1.5 spikes surfaced a **third** stale-save site at `agent/agent_session_queue.py:3234-3235` (`agent_session.save()` inside `_execute_agent_session` on the nudge path) that the original issue report missed.

**Current behavior:**

- PM session is picked up, runs one SDK turn (~168s, ~12 bash tools, produces a ~2355-char response).
- Nudge path fires: `_enqueue_nudge` re-reads a fresh session object, transitions it to `pending`, increments `auto_continue_count` to 1, writes a `running→pending` event. Redis is now in the correct nudge state.
- `_execute_agent_session`'s inner finally hits `agent/agent_session_queue.py:3234-3235`: `agent_session.updated_at = now; agent_session.save()` on the stale local. Full-state save silently clobbers Redis back to `status='running'`, `auto_continue_count=0`, message_text=original.
- `_execute_agent_session` returns. Worker loop's outer finally at `agent/agent_session_queue.py:2259` runs `session.log_lifecycle_transition("completed", "worker finally block")` on the same stale outer `session` variable. Appends a bogus `running→completed: worker finally block` event and saves the full stale state again.
- Worker's nudge guard at `agent/agent_session_queue.py:2294-2318` re-reads Redis, sees `fresh.status='running'` (just clobbered, not `pending` anymore), falls through the `elif fresh.status == "pending":` branch, calls `_complete_agent_session` which finalizes to `completed` via `finalize_session`. `finalize_session`'s CAS check sees matching `running`-to-`running` in-memory and on-disk status, no conflict raised.
- Session ends up `status=completed`, orphan `pending` status-index entry left behind.
- Watchdog then spams `LIFECYCLE_STALL status=pending` for hours and the worker logs flood with `Skipping session ... index says pending but actual status='completed'. Stale index entry.`

**Desired outcome:**

- PM/SDLC session that gets nudged re-enters the worker queue in `pending` state, is picked up by the next worker loop iteration, and runs its continuation turn. `auto_continue_count` correctly increments across turns.
- Redis record's `session_events` preserves the full lifecycle history including `running→pending: nudge re-enqueue`.
- `status` index and record state are consistent after the nudge-completion cycle — no orphan `pending` index entries accumulate.
- `LIFECYCLE_STALL` watchdog does not fire false alarms on sessions that are actually `completed`.
- Crash path (`_execute_agent_session` raises an uncaught exception) still gets its session finalized exactly once with a diagnostic snapshot written.
- Future callers of `append_event` / `append_history` / `log_lifecycle_transition` that accidentally operate on a stale object can at worst corrupt `session_events`, never `status` / `auto_continue_count` / `message_text` / routing state.

## Freshness Check

**Baseline commit:** `ab5b1b53333fcef568ef5a4274eb1401b13d7d86`
**Issue filed at:** 2026-04-11T07:24:55Z (~5 minutes before plan creation)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:2259` — `session.log_lifecycle_transition(target, "worker finally block")` — still holds, confirmed in current main
- `agent/agent_session_queue.py:2179` — `session.log_lifecycle_transition("running", "worker cancelled — ...")` — still holds
- `agent/agent_session_queue.py:2294-2318` — nudge guard with `AgentSession.query.get(redis_key=...)` — still holds, positioned after :2259 as described
- `agent/agent_session_queue.py:3234-3235` — `agent_session.updated_at = now; agent_session.save()` on nudge branch — still holds (surfaced by Phase 1.5 spike, not in original issue body)
- `models/agent_session.py:1197-1198` — `append_event → _append_event_dict → self.save()` — still holds, unchanged since `0c810e71e` (2026-02-25)
- `models/agent_session.py:1234-1261` — `log_lifecycle_transition` appends event via `append_event` — still holds
- `models/agent_session.py:1171-1199` — `append_event` / `_append_event_dict` definition — still holds
- `models/session_lifecycle.py:270-361` — `finalize_session` with CAS check at 287-300 — still holds
- `agent/messenger.py:77` — `"Sent result message"` log from `BossMessenger.send` — still holds (secondary-bug site)

**Cited sibling issues/PRs re-checked:**
- #867 — CLOSED 2026-04-10T12:10:09Z as "structurally fixed by PR #885". This plan is the sibling defect that the CAS fix did not address.
- #872 — CLOSED 2026-04-10T12:10:12Z as "harmless no-op after CAS lands". That closure rationale is incorrect; the `log_lifecycle_transition` call at :2259 still clobbers via the implicit save even when `_complete_agent_session` is a no-op. `#872`'s proposed `finalized_by_execute` gate is re-promoted from "optimization" to "correctness requirement" by this plan.
- PR #885 — MERGED 2026-04-10T11:13:33Z. Shipped CAS to `finalize_session` / `transition_status`. This plan builds on it rather than changing it.
- `docs/plans/lifecycle-cas-authority.md` — status `Completed`. Related, non-overlapping.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-11T07:24:55Z" -- agent/agent_session_queue.py models/agent_session.py models/session_lifecycle.py agent/messenger.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:**
- None. `lifecycle-cas-authority.md` is `Completed`; no in-flight plans touch `agent/agent_session_queue.py`'s finally block or `models/agent_session.py`'s `append_event`.

**Notes:**
- Phase 1.5 spike surfaced `agent/agent_session_queue.py:3234-3235` as a third stale-save site. The issue body (#898) only called out two sites; this plan widens scope to three. No other drift.
- The `complete_transcript` happy path at `agent/agent_session_queue.py:3232` is safe — it uses a fresh re-read inside `bridge/session_transcript.py:285-292`. The unsafe branch is specifically the `defer_reaction=True` (nudge) branch on line 3233-3235.

## Prior Art

- **#867** — *Race: nudge re-enqueue stomped by worker finally-block finalize_session()* (CLOSED 2026-04-10). Same user-visible symptom. Fixed by PR #885's CAS fence in `finalize_session()`. **Incomplete**: the stomp also fires through save paths that bypass the lifecycle module entirely (`append_event`'s implicit save, and `agent_session.save()` in `_execute_agent_session`'s nudge branch). CAS catches the terminal write but not the earlier clobbering saves that erase the state mismatch before CAS looks at it.
- **#872** — *cleanup: worker finally-block runs redundant snapshot/log/finalize on happy path — creates #867 race window* (CLOSED 2026-04-10). Proposed a `finalized_by_execute` gate to make the finally-block's finalize path crash-only. Closed as "harmless no-op after CAS lands." **Incorrect closure**: the `log_lifecycle_transition` call at :2259 still clobbers via the implicit save even when `_complete_agent_session` is a no-op. `finalized_by_execute` is a genuine correctness fix, not an optimization.
- **#626** — *Sessions die silently* (CLOSED 2026-04-02 by PR that introduced the very call site that regresses #867 here). Fix 4's "Why this is safe" rationale claimed `log_lifecycle_transition` is idempotent logging. It isn't — it's idempotent logging plus an implicit full-state save via `append_event`. The observability motivation is still valid; the implementation needs to move to a fresh-read path.
- **PR #885** — *Lifecycle CAS authority* (MERGED 2026-04-10). Added CAS to `finalize_session` and `transition_status`. Did not audit `append_event`'s save path or `agent_session.save()` call sites outside the lifecycle module. Those are the bypasses this plan closes.
- **[`docs/plans/lifecycle-cas-authority.md`](docs/plans/lifecycle-cas-authority.md)** — shipped plan that established the `update_session()` / `get_authoritative_session()` CAS API used throughout this plan's solution.
- **[`docs/plans/silent-session-death.md`](docs/plans/silent-session-death.md)** — shipped plan whose Fix 4 introduced the regression. To be amended with a regression note pointing to this plan.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #885 (`#875` lifecycle CAS authority) | Added CAS re-read in `finalize_session()` / `transition_status()` that raises `StatusConflictError` when on-disk status differs from the caller's in-memory view. | The stomp happens *upstream* of the lifecycle module. `log_lifecycle_transition → append_event → self.save()` and `agent_session.save()` (in `_execute_agent_session`'s nudge branch) write to Redis WITHOUT routing through `finalize_session` or `transition_status`. By the time CAS runs, the on-disk and in-memory statuses both read `running` because the earlier stale save aligned them. CAS sees no conflict and passes. |
| #872 resolution (declared "harmless no-op") | Deferred the `finalized_by_execute` gate as a performance optimization, relying on #885's CAS as the load-bearing protection. | The `log_lifecycle_transition("worker finally block")` call at :2259 runs BEFORE the finalize path. Even if `_complete_agent_session` is a no-op, the log call's implicit save still clobbers. The `finalized_by_execute` gate (or equivalent fresh-read reorder) is genuinely needed, not optional. |
| `silent-session-death` Fix 4 (commit `6ccce56f5`, 2026-04-02) | Added `session.log_lifecycle_transition(target, "worker finally block")` unconditionally in the worker finally block to close an observability gap where crashed sessions had no lifecycle marker. | Correct motivation, wrong call site. The call operates on the worker-loop `session` variable, which is stale by the time the finally block runs (nudge has already transitioned the fresh re-read to `pending`). The implicit `append_event → save()` writes the stale snapshot back. Plan's "Why this is safe" rationale missed that `append_event` persists. |

**Root cause pattern:** Two completely independent save paths (`append_event`'s implicit full-state save, and direct `.save()` on session objects held by the worker loop) can mutate Redis without routing through the lifecycle module's CAS fence. Any fix that only hardens the lifecycle module leaves these bypasses open. The correct fix must close the bypasses (Layer 2) and/or ensure the worker loop only ever operates on freshly-read objects (Layer 1).

## Spike Results

### spike-1: Does Popoto `save(update_fields=[...])` maintain IndexedField indices correctly?
- **Assumption**: "Partial save with `update_fields=['session_events', 'updated_at']` writes only the listed fields to the Redis hash, does not call `on_save` hooks for unlisted fields, and therefore cannot corrupt the `status` IndexedField index."
- **Method**: code-read (`.venv/lib/python3.14/site-packages/popoto/models/base.py:993-1200`)
- **Finding**: CONFIRMED. Popoto's partial-save path at `base.py:1119-1194`:
  1. Line 1131-1137 encodes all fields but filters to `hset_mapping = {k: v for k, v in full_mapping.items() if k in update_field_names_bytes}` — only listed fields are written to the Redis hash via HSET.
  2. Line 1164-1173 iterates `for field_name in update_fields` and calls `field.on_save(...)` only for listed fields — status's IndexedField `on_save` is NOT called, so no `srem`/`sadd` on the status index set.
  3. Line 1183-1184 updates `_saved_field_values` only for listed fields, preserving the pre-save snapshot of untouched fields.
- **Confidence**: high
- **Impact on plan**: Layer 2 (`update_fields=["session_events", "updated_at"]` in `_append_event_dict`) is mechanically safe. A stale caller can at worst corrupt `session_events` and `updated_at` — it cannot clobber `status`, `auto_continue_count`, `message_text`, `pm_sent_message_ids`, or any other record field.

### spike-2: Does `_execute_agent_session` always finalize via `complete_transcript` on the happy path?
- **Assumption**: "On a successful SDK turn, `_execute_agent_session` calls `complete_transcript` → `finalize_session` (fresh re-read, CAS-guarded), and the outer worker finally block's finalize path is redundant on the happy path."
- **Method**: code-read (`agent/agent_session_queue.py:3210-3260`)
- **Finding**: PARTIALLY TRUE, and surfaced a **third stale-save site** the issue missed.
  - **Non-nudge happy path** (`chat_state.defer_reaction=False`): Line 3232 calls `complete_transcript(session_id, status="completed"|"failed")`. Inside `bridge/session_transcript.py:285-292`, a fresh `AgentSession.query.filter(session_id=...)` is read and passed to `finalize_session`. **Safe.**
  - **Nudge happy path** (`chat_state.defer_reaction=True`): Lines 3233-3235 skip `complete_transcript` and instead execute `agent_session.updated_at = datetime.now(tz=UTC); agent_session.save()` on the **stale local `agent_session`** obtained at line 2803 (filtered by `status="running"`). This is a plain full-state save that clobbers `status`, `auto_continue_count`, `message_text`, and `session_events` just like the outer finally block does. **Unsafe — this is a third stomp site not mentioned in issue #898.**
  - **Crash path** (`task.error` set): Line 3232 still fires with `status="failed"`; `complete_transcript` handles it. Outer finally block writes a diagnostic snapshot. **Safe for crash finalization.**
- **Confidence**: high
- **Impact on plan**: Scope expands from two stale-save sites (issue named `:2179` and `:2259`) to **three** (`:2179`, `:2259`, and `:3234-3235`). All three must be fixed together. Layer 1 must also reorder or replace the `:3234-3235` save to operate on a fresh re-read. Does not change appetite — still Small, still ~30 LOC of targeted fixes, but across three call sites instead of two.

## Data Flow

1. **Entry point**: Telegram message arrives, bridge enqueues `AgentSession` with `status="pending"`, pub-sub notifies worker.
2. **Worker pop**: `_worker_loop` calls `_pop_agent_session`, receives session object (the "outer session" — a **Python identity** that the finally block will eventually see).
3. **Execution**: `_worker_loop` calls `await _execute_agent_session(session)`. Inside:
   - Line 2803 does a fresh `AgentSession.query.filter(project_key=..., status="running")` to get `agent_session` (the "inner session" — a **different Python object** from the outer `session`).
   - Runs the Claude Agent SDK, produces text output and tool calls.
   - Routes output via `send_to_chat` / `output_router.route_session_output`.
4. **Nudge decision**: For PM/SDLC, `route_session_output` returns `nudge_continue`. `send_to_chat` calls `_enqueue_nudge`:
   - `_enqueue_nudge` calls `get_authoritative_session(session_id)` to get a **third Python object** — the fresh nudge session.
   - Mutates the fresh object: sets `message_text=<nudge feedback>`, `auto_continue_count=1`, `priority="high"`.
   - Calls `transition_status(fresh_nudge_session, "pending")` which CAS-saves `status='pending'` and appends `running→pending: nudge re-enqueue` to `session_events`.
   - Redis now has: `status='pending'`, `auto_continue_count=1`, `session_events=[..., running→pending: nudge re-enqueue]`.
   - The outer `session` (from step 2) and the inner `agent_session` (from step 3 line 2803) are BOTH still stale — their in-memory state is pre-nudge.
5. **First stomp — inner finally** (`agent/agent_session_queue.py:3234-3235`):
   - `_execute_agent_session`'s inner finally runs: `agent_session.updated_at = now; agent_session.save()`.
   - Full-state save writes `status='running'`, `auto_continue_count=0`, stale `session_events`, stale `message_text` back to Redis. **Clobber #1.** Popoto's `IndexedFieldMixin.on_save` sees `_saved_field_values["status"]='running'` → `running` (no change), so `status` index is NOT updated — the orphan `pending` index entry from step 4 persists.
6. **Second stomp — outer finally** (`agent/agent_session_queue.py:2259`):
   - `_execute_agent_session` returns.
   - `_worker_loop`'s finally block at :2255 checks `if not session_completed:`. True, falls through.
   - Line 2259: `session.log_lifecycle_transition("completed", "worker finally block")`. The stale outer `session` has `self.status='running'`, so `old_status='running'`. `log_lifecycle_transition` calls `append_event("lifecycle", "running→completed: worker finally block")`.
   - `append_event` calls `_append_event_dict`, which mutates `session.session_events` (the stale list) and calls `self.save()` (full-state, no `update_fields`).
   - **Clobber #2.** Redis now shows the stale `session_events` list with the bogus `running→completed: worker finally block` appended.
7. **Third stomp — fall-through finalize** (`agent/agent_session_queue.py:2299-2318`):
   - The nudge guard re-reads `fresh = AgentSession.query.get(redis_key=...)`. Reads `fresh.status='running'` (the clobbered value from step 5 or 6, NOT the nudge's `pending`).
   - `elif fresh.status == "pending":` branch is not taken.
   - Calls `_complete_agent_session(session, failed=False)` which re-reads `fresh_records` (still `running`), calls `finalize_session(s, "completed", ...)`.
   - `finalize_session` CAS check: in-memory `running`, on-disk `running`, no conflict. Proceeds. Sets `status='completed'`, saves. **Clobber #3 — terminal.**
8. **Orphan index**: The `pending` status-index entry from step 4 is never removed because none of the save calls after step 4 saw the old→new transition as `pending→something`. It sits in Redis pointing at a completed record.
9. **Downstream**: Watchdog's next pass reads the pending index, finds the orphan, raises `LIFECYCLE_STALL status=pending` for hours. Worker pop tries to claim the session, `_pop_agent_session` rebuilds indexes partially but the specific orphan survives until the next worker restart's full `rebuild_indexes()`.

## Architectural Impact

- **New dependencies**: None. Uses existing `get_authoritative_session()` / `update_session()` CAS API from PR #885.
- **Interface changes**:
  - `models/agent_session.py::_append_event_dict()` switches its internal `self.save()` call to `self.save(update_fields=["session_events", "updated_at"])`. Method signature unchanged.
  - `agent/agent_session_queue.py::_worker_loop` finally block reordered: re-read fresh BEFORE any save-triggering call. Public signature unchanged.
  - `agent/agent_session_queue.py::_execute_agent_session` nudge branch (line 3233-3235) switches from `agent_session.save()` (full) to either `update_session(expected_status="pending", updated_at=now)` or a partial `.save(update_fields=["updated_at"])`.
  - `agent/messenger.py::BossMessenger.send()` gains a parameter or caller-supplied context to differentiate "delivered to chat" from "routed to nudge" in the log line.
- **Coupling**: Reduced. The plan enforces the invariant "mutating a session means you hold a freshly-read instance of it" everywhere in the worker loop. Removes the current implicit coupling where the worker loop trusts its locally-held session to be in sync with Redis.
- **Data ownership**: Unchanged. Redis remains the source of truth.
- **Reversibility**: High. Layer 1 is a 3-site surgical fix — revert touches only the finally block and one line in `_execute_agent_session`. Layer 2 is a one-line change in `_append_event_dict` — revert is trivial. #872's gate is optional and independently reversible.

## Appetite

**Size:** Small

**Team:** Solo dev, PM (plan critique and review gate)

**Interactions:**
- PM check-ins: 1 (post-plan critique round via `/do-plan-critique`)
- Review rounds: 1 (code review on the implementation PR)

The fix is three surgical edits plus a partial-save change plus tests. The bottleneck is carefulness — every touched call site has a known race class — not coding volume.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #885 merged (CAS API available) | `grep -q "get_authoritative_session" models/session_lifecycle.py && grep -q "StatusConflictError" models/session_lifecycle.py` | Layer 1 depends on `get_authoritative_session()` from the lifecycle CAS authority |
| Popoto supports `save(update_fields=[...])` | `python -c "from popoto.models.base import Model; import inspect; sig = inspect.signature(Model.save); assert 'update_fields' in sig.parameters"` | Layer 2 relies on partial save semantics |
| Redis running for integration tests | `redis-cli ping \| grep -q PONG` | Integration tests hit real Popoto/Redis (required per this repo's testing philosophy) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/nudge-stomp-append-event-bypass.md`

## Solution

### Key Elements

- **Fresh-read invariant in the worker finally block**: Before any save-triggering call in `_worker_loop`'s finally block, re-read the session via `get_authoritative_session(session_id)`. If fresh is `None`, `pending`, or terminal, skip all mutating operations. If fresh is `running`, operate on the fresh object for every subsequent step (lifecycle log, snapshot, finalize).
- **Fresh-read invariant in `_execute_agent_session`'s nudge branch**: Replace the stale `agent_session.save()` at line 3234-3235 with either a CAS-aware `update_session()` call or a partial `save(update_fields=["updated_at"])` on a freshly-read object. The `updated_at` heartbeat is the only mutation needed there; the rest of the state was already correctly written by `_enqueue_nudge`.
- **Safe-by-default `append_event`**: Change `_append_event_dict` to use `save(update_fields=["session_events", "updated_at"])`. Defense-in-depth: any future caller that forgets to re-read cannot clobber `status`, `auto_continue_count`, `message_text`, `pm_sent_message_ids`, or other critical fields. Worst-case corruption is a stale `session_events` list instead of a total record wipe.
- **Correct messenger logging**: `BossMessenger.send` logs `"Sent result message"` only when the send_callback actually delivered to chat. For nudge-routed output, log `"Routed to nudge (not delivered)"` instead. Prevents future diagnostic confusion.
- **Regression note in `silent-session-death.md`**: Amend the shipped plan with a pointer to this plan so future readers know Fix 4's "safe because idempotent" rationale was incorrect.

### Flow

```
Worker pops session → _execute_agent_session runs SDK → SDK end_turn
  → send_to_chat → output_router returns nudge_continue
    → _enqueue_nudge transitions fresh to pending ✓

  → _execute_agent_session inner finally
    → re-read fresh via get_authoritative_session
    → fresh.status == "pending" → skip stale save ✓

  → _execute_agent_session returns
  → _worker_loop outer finally
    → re-read fresh via get_authoritative_session
    → fresh.status == "pending" → skip lifecycle log + snapshot + complete ✓
  → Worker loop picks up the now-pending session from the queue
  → Next turn runs
```

### Technical Approach

**Layer 1a — Worker finally block reorder (`agent/agent_session_queue.py:2254-2327`)**

Replace the current structure:
```python
finally:
    if not session_completed:
        # Current: log first, then guard
        try:
            target = "failed" if session_failed else "completed"
            session.log_lifecycle_transition(target, "worker finally block")  # ← stale save
        except Exception:
            pass
        try:
            save_session_snapshot(...)  # using stale session fields
        except Exception: ...
        try:
            fresh = AgentSession.query.get(redis_key=session.db_key.redis_key)
            if not fresh: ...
            elif fresh.status == "pending": ...
            else:
                await _complete_agent_session(session, failed=session_failed)  # ← stale reference
        except Exception:
            await _complete_agent_session(session, failed=session_failed)
```

With:
```python
finally:
    if not session_completed:
        # Fresh-read FIRST, before any save-triggering call.
        fresh = None
        try:
            from models.session_lifecycle import get_authoritative_session, TERMINAL_STATUSES
            fresh = get_authoritative_session(session.session_id)
        except Exception as guard_err:
            logger.warning(
                "[worker:%s] Fresh re-read failed for %s: %s — "
                "falling back to stale session for completion",
                worker_key, session.agent_session_id, guard_err,
            )

        if fresh is None:
            logger.info(
                "[worker:%s] Session %s no longer exists in Redis "
                "(recreated by nudge fallback) — skipping completion",
                worker_key, session.agent_session_id,
            )
        elif fresh.status in TERMINAL_STATUSES:
            logger.info(
                "[worker:%s] Session %s already terminal (%s) — "
                "skipping redundant finalize",
                worker_key, session.agent_session_id, fresh.status,
            )
        elif fresh.status == "pending":
            logger.info(
                "[worker:%s] Session %s has status 'pending' in Redis "
                "(nudge was enqueued) — skipping completion to preserve nudge",
                worker_key, session.agent_session_id,
            )
        else:
            # Fresh is still running. Operate on fresh for all downstream ops.
            target = "failed" if session_failed else "completed"
            try:
                fresh.log_lifecycle_transition(target, "worker finally block")
            except Exception:
                pass
            try:
                activity = get_activity(session.session_id)
                save_session_snapshot(
                    session_id=fresh.session_id,
                    event=("crash" if session_failed else "complete"),
                    project_key=fresh.project_key,
                    branch_name=_session_branch_name(fresh.session_id),
                    task_summary=(
                        f"Session {fresh.agent_session_id} "
                        f"{'failed' if session_failed else 'terminated'}"
                    ),
                    extra_context={
                        "agent_session_id": fresh.agent_session_id,
                        "tool_count": activity.get("tool_count", 0),
                        "trigger": "finally_block",
                    },
                    working_dir=str(
                        Path(fresh.working_dir)
                        if hasattr(fresh, "working_dir")
                        else Path(__file__).parent.parent
                    ),
                )
            except Exception as snap_err:
                logger.warning(
                    "Failed to save crash snapshot for %s: %s",
                    fresh.agent_session_id, snap_err,
                )
            await _complete_agent_session(fresh, failed=session_failed)
```

**Layer 1b — CancelledError branch (`agent/agent_session_queue.py:2171-2189`)**

Apply the same re-read-first pattern to the cancellation path. Current code calls `session.log_lifecycle_transition("running", "worker cancelled — startup recovery will re-queue")` on the stale outer session. Replace with: re-read fresh via `get_authoritative_session`, and call `fresh.log_lifecycle_transition(...)` only if fresh is still `running`. If fresh has moved to `pending`/terminal, just log the event to bridge.log without touching the stored record.

**Layer 1c — `_execute_agent_session` nudge branch (`agent/agent_session_queue.py:3222-3241`)**

Replace:
```python
if not chat_state.defer_reaction:
    complete_transcript(session.session_id, status=final_status)
else:
    agent_session.updated_at = datetime.now(tz=UTC)
    agent_session.save()  # ← stale full save
```

With:
```python
if not chat_state.defer_reaction:
    complete_transcript(session.session_id, status=final_status)
else:
    # Nudge path: DO NOT full-save the stale agent_session local.
    # _enqueue_nudge has already written the authoritative post-nudge state;
    # we only need to heartbeat updated_at, and only if the nudge left us in pending.
    try:
        from models.session_lifecycle import get_authoritative_session
        fresh_nudge = get_authoritative_session(session.session_id)
        if fresh_nudge is not None and fresh_nudge.status == "pending":
            fresh_nudge.updated_at = datetime.now(tz=UTC)
            fresh_nudge.save(update_fields=["updated_at"])
        # If fresh is not pending (race with another writer), skip the heartbeat.
    except Exception as e:
        logger.debug(
            f"[{session.project_key}] Nudge-path updated_at heartbeat "
            f"failed (non-fatal): {e}"
        )
```

**Layer 2 — Safe `append_event` implicit save (`models/agent_session.py:1197`)**

Change the one line:
```python
try:
    self.save()
except Exception as e:
    ...
```

To:
```python
try:
    self.save(update_fields=["session_events", "updated_at"])
except Exception as e:
    ...
```

Spike 1 confirmed Popoto's partial-save path writes only the listed fields to the hash and only calls `on_save` hooks for listed fields. `session_events` is not an IndexedField; `updated_at` is a sorted field but the worst-case corruption is a stale timestamp, not a status-index leak.

**Layer 3 — Messenger log accuracy (`agent/messenger.py:52-85`)**

Two options:
- **3a (minimal)**: Add a `message_type` hint to the caller so `BossMessenger.send` can log `"Routed to nudge"` vs `"Sent result message"` based on the callback's side effect. But this requires the callback to report back, which is a larger surgery.
- **3b (preferred)**: Change the log line to describe what `BossMessenger.send` actually does — "passed N chars to send_callback" — without claiming delivery. The actual delivery log should come from inside `send_to_chat` on the `action == "deliver"` path in `agent/agent_session_queue.py:3004-3009` (which already logs `"Output delivered (stop_reason=..., N chars)"`).

**Layer 4 — Documentation cascade**

Amend `docs/plans/silent-session-death.md` with a regression note at the top pointing to this plan. Update `docs/features/session-lifecycle.md` (create if missing) with a "Stale object hazard" rule: "Any caller of `append_event` / `append_history` / `log_lifecycle_transition` / direct `session.save()` on an `AgentSession` must hold a freshly re-read instance. The worker loop's `session` variable and any object obtained via `AgentSession.query.filter(status='running')` before an SDK run are considered stale after any nudge or concurrent mutation."

**Why not extract a `SessionRunner` class or collapse the dual-finalize path (#872's original proposal)?**

Tempting but out of scope for Small appetite. Layer 1's fresh-read pattern achieves the same correctness outcome without the restructuring risk. `finalized_by_execute` as a boolean gate is still optional; the plan can leave it as a follow-up optimization. Keeping scope tight is the deliberate choice.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/agent_session_queue.py:2260` (`except Exception: pass` around `log_lifecycle_transition`) — assert via test that a `log_lifecycle_transition` failure is caught and the finally block proceeds to the fresh-read guard without crashing the worker loop.
- [ ] `agent/agent_session_queue.py:2319-2327` (`except Exception as guard_err`) — refactored to become the fresh-read fallback. Test that if `get_authoritative_session` raises, the finally block logs a warning and defaults to a safe no-op (do NOT blindly call `_complete_agent_session` on stale data, even as a fallback — that's how the current bug survives).
- [ ] `agent/agent_session_queue.py:3237-3241` (`except Exception as e` around the nudge-path save) — test that a Redis failure on the `updated_at` heartbeat does not crash `_execute_agent_session`.
- [ ] `models/agent_session.py:1199-1203` (`except Exception as e` around `_append_event_dict`'s save) — test that a partial save failure is logged and does not raise. Unchanged behavior, but verify Layer 2 didn't break the catch-all.

### Empty/Invalid Input Handling
- [ ] `get_authoritative_session(session_id)` returning `None` — explicitly tested in the fresh-read-first path. Behavior: log "no longer exists in Redis", skip all mutating ops.
- [ ] `get_authoritative_session(session_id)` returning a session with unexpected status (e.g., `active`, `dormant`, or some future value) — test that the guard treats anything except `running` as "do not touch."
- [ ] `_enqueue_nudge` having already written `auto_continue_count > MAX_NUDGE_COUNT` — out of scope for this plan; covered by existing nudge cap logic.

### Error State Rendering
- [ ] No user-visible output from this change. The fix is entirely in the session-lifecycle plumbing. The user's visible symptom (silent PM death) goes away once the nudge is preserved. Confirmed via acceptance criterion 7 (production validation).

## Test Impact

- [ ] `tests/unit/test_session_completion_zombie.py::TestWorkerFinallyBlockNudgeGuard::test_skips_completion_when_session_is_pending` — UPDATE: current test passes because it mocks `AgentSession.get` to return `status="pending"`. It does not test the real-Popoto stale-save scenario. Replace the mock with a real Popoto setup where the outer `session` object is stale and the Redis record has been mutated to `pending`. Assert that the final state is `pending`, not `completed`.
- [ ] `tests/unit/test_session_completion_zombie.py::TestWorkerFinallyBlockNudgeGuard::test_completes_session_when_status_is_running` — UPDATE: ensure this test still passes with the reorder. The fresh-read-first path should finalize to `completed` when fresh is still `running`.
- [ ] `tests/unit/test_worker_cancel_requeue.py` — UPDATE: tests that verify `log_lifecycle_transition` is called in the CancelledError branch must now verify the call happens on a fresh-read object, not the stale outer session. Most of the existing tests use `MagicMock` so they won't regress mechanically, but add a real-Popoto test case for the stale scenario.
- [ ] `tests/unit/test_complete_agent_session_redis_reread.py` — UPDATE: verify `_complete_agent_session` is called with a fresh-read session (not the stale worker loop local). Tests should assert the argument type/identity.
- [ ] `tests/unit/test_crash_snapshot.py` — UPDATE: the crash-snapshot tests mock `log_lifecycle_transition` on the session. They need to assert `log_lifecycle_transition` is called on `fresh` (the re-read object), not `session` (the stale local). Currently `test_crash_snapshot.py:49` calls `session.log_lifecycle_transition(target, "worker finally block")` — update the mock setup to match the reorder.
- [ ] `tests/integration/test_agent_session_lifecycle.py` — UPDATE: extend with a new test class `TestNudgeStompPrevention` that exercises the real end-to-end race: worker pops, `_execute_agent_session` runs, nudge fires via `_enqueue_nudge`, finally block runs. Expected: record ends `status=pending`, `auto_continue_count >= 1`, nudge event in `session_events`. Must use real Popoto (not mocks). This is the load-bearing acceptance test.
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE: existing tests for `log_lifecycle_transition()` likely still pass (they test the method in isolation). Add a new case: verify that `log_lifecycle_transition` on a session object whose Redis counterpart has a different `status` does NOT clobber the on-disk status (now protected by Layer 2's `update_fields`).
- [ ] `tests/unit/test_recovery_respawn_safety.py::test_nudge_reread_guard_catches_late_terminal` — UPDATE: no change expected; this tests `_enqueue_nudge`'s own terminal guard, which is orthogonal to this fix. Verify it still passes.
- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — UPDATE: new test case that `finalize_session`'s CAS check still catches the scenario where the caller's in-memory status differs from on-disk (existing test). Verify Layer 1's reorder doesn't short-circuit CAS in cases where it should fire (happy path, concurrent finalize from a different worker).
- [ ] **NEW**: `tests/integration/test_append_event_partial_save.py` — CREATE: direct test that `_append_event_dict` calls `self.save(update_fields=["session_events", "updated_at"])`. Parametrized across a range of stale-caller scenarios. Assert non-listed fields (`status`, `auto_continue_count`, `message_text`) are preserved across the save.
- [ ] **NEW**: `tests/integration/test_nudge_stomp_regression.py` — CREATE: end-to-end test simulating issue #898's concrete incident. Two Python `AgentSession` instances for the same `session_id`; nudge on the fresh one; run the outer finally block with the stale one; assert the final Redis state matches the nudge's writes, not the stale snapshot.
- [ ] No deletes. No replaces. Three new test files, seven updates.

## Rabbit Holes

- **Extracting `SessionRunner` class** (proposed in #867's audit appendix). The worker loop's session execution and finalization logic is a genuinely tangled ball, and a class extraction would clean it up. It would also take 2-4× the appetite of this fix and introduce its own regression risk. Out of scope. Revisit in a dedicated refactor plan after this bug is shipped.
- **Collapsing `_complete_agent_session` and `complete_transcript` into one finalizer.** Same concern — worthwhile but too big. The two functions do overlapping re-read work and the plan could unify them. Defer to a follow-up.
- **Making `append_event` raise when called on a stale object.** Appealing invariant ("if you're going to corrupt the record, let me yell") but requires tracking "last seen authoritative version" on every `AgentSession` instance, which adds state to a model that's supposed to be a thin Redis view. Layer 2's partial save achieves the same practical safety with less complexity.
- **Rewriting the nudge path to not defer reaction at all.** Some of the fields the nudge manipulates (`message_text`, `priority`, `task_list_id`) feel like they shouldn't be mutable. Restructuring the nudge loop is the scope of #743 / `extract-nudge-to-pm.md` and is actively being planned elsewhere. Do not tangle scopes.
- **Auditing every `self.save()` call site in `models/agent_session.py`** for the stale-object hazard. There are 5-10 candidates. Most are fine (fresh reads, inside CAS flows, greenfield creates). Doing a full audit is worth doing but is its own plan — table it.
- **Adding Redis WATCH/MULTI/EXEC to replace Python-level CAS.** Correct engineering answer, large scope, interacts with Popoto's internals. Not in this appetite.

## Risks

### Risk 1: Layer 2's partial save breaks a test that relied on `append_event` writing all fields
**Impact:** Some test or production caller implicitly depends on `append_event` flushing other mutations (e.g., mutates `session.message_text` and `session.auto_continue_count` without calling `save()` explicitly, then calls `append_event` and expects those mutations to persist as a side effect).
**Mitigation:** Grep for every `append_event` / `append_history` call site in source and tests (already partially done in the investigation — only the `agent_session_queue.py:2811` site has field mutations before append, and those fields are already saved explicitly at line 2810). Audit remaining call sites in the plan's build step. Add a deprecation comment on `append_event` explaining it's events-only. If a legitimate caller is found that needs flushing, require it to call `save()` explicitly before `append_event`.

### Risk 2: Fresh re-read in the finally block introduces extra Redis calls and slows down happy-path finalization
**Impact:** Every session completion adds one `get_authoritative_session` round-trip (~1ms locally, plus CAS re-read inside `_complete_agent_session`). For a busy worker this is a measurable cost.
**Mitigation:** The CAS inside `_complete_agent_session` already does the same re-read — we're replacing one of two redundant reads with an earlier one, not adding a net read. Measure before/after via the existing `[lifecycle-cas] CAS overhead: X.Xms` debug log. If a regression shows, collapse the two reads into a single `get_authoritative_session` call passed through to `_complete_agent_session`.

### Risk 3: Layer 1c (the nudge-path heartbeat replacement) races with `_enqueue_nudge` itself
**Impact:** `_enqueue_nudge` sets `status='pending'` and returns. The new Layer 1c code re-reads fresh, checks `status == 'pending'`, and writes `updated_at`. If a worker in another project already picked up the session in those microseconds (pending→running), Layer 1c's status check would fail and skip — missing the heartbeat. Consequence: the watchdog might see a slightly stale `updated_at` on the next turn and raise a false stall alert.
**Mitigation:** Accept the miss. The heartbeat is best-effort observability; its absence for one cycle doesn't corrupt state. Add a comment explaining the tradeoff. If false-alert noise becomes a problem in production, revisit by threading the fresh object through from `_enqueue_nudge`.

### Risk 4: A future caller adds a new `append_event` site and forgets the fresh-read discipline
**Impact:** Layer 2's partial save limits blast radius but doesn't prevent the hazard from recurring. A stale `session_events` list can still stomp a fresh one (losing nudge events even though status / counters are preserved).
**Mitigation:** Add a docstring warning on `append_event` / `append_history` / `log_lifecycle_transition` documenting the stale-object hazard and linking to `docs/features/session-lifecycle.md`. Add a unit test that asserts the warning is present in the docstring (cheap future-proofing). Consider a lint rule in a follow-up.

### Risk 5: Layer 1 reorder accidentally disables the crash-path snapshot
**Impact:** `silent-session-death` Fix 3 relied on the finally block writing a crash snapshot unconditionally. If the reorder moves the snapshot save inside the `else: fresh is still running` branch, crashed sessions where `fresh` is `None` or `terminal` would miss their snapshot.
**Mitigation:** Keep the snapshot save at the TOP of the finally block (before the fresh re-read), using the stale session's identifiers only (session_id, agent_session_id, project_key — immutable fields that don't change between pop and finally). The snapshot is diagnostic; writing it from stale data is acceptable because it's for post-mortem debugging. The `log_lifecycle_transition` and `_complete_agent_session` calls move into the fresh-read branch; the snapshot stays outside.

## Race Conditions

### Race 1: Nudge re-enqueue vs. worker finally block (primary bug)
**Location:** `agent/agent_session_queue.py:2254-2327` (worker loop finally) and `agent/agent_session_queue.py:3222-3241` (`_execute_agent_session` nudge branch).
**Trigger:** `_enqueue_nudge` transitions the session to `pending` and writes updated fields on a fresh re-read object. Control returns to the inner/outer finally block, which holds a stale `session` object (or stale `agent_session` local) with the pre-nudge snapshot.
**Data prerequisite:** Redis record at `session_id` must be in the post-nudge state (`status=pending`, `auto_continue_count>=1`, `session_events` with nudge event appended) before the stale caller's finally block runs.
**State prerequisite:** The stale caller must NOT `save()` on its local session object until it has re-read fresh and confirmed the record is still in the state it expects (`running`).
**Mitigation:** Layer 1 — `get_authoritative_session` re-read at the top of the finally block and the nudge branch of `_execute_agent_session`. Check `fresh.status`; if `pending`/terminal, skip all mutating ops. Layer 2 — partial save in `append_event` so that if a stale caller slips through, it cannot clobber `status` / `auto_continue_count` / `message_text`.

### Race 2: Nudge re-enqueue vs. a worker in a different project key picking up the session immediately
**Location:** `agent/agent_session_queue.py:2560-2570` (`_enqueue_nudge` writes pending then calls `_ensure_worker`).
**Trigger:** Between `transition_status(session, "pending")` and the outer worker's finally block reading fresh, another worker could pop the session (now pending) and transition it to `running`. The outer finally block then reads `fresh.status=='running'` and — per Layer 1's logic — treats this as "safe to finalize."
**Data prerequisite:** Only one worker may be in `running` state for a given `session_id` at a time (enforced by the worker semaphore and the project-keyed serialization).
**State prerequisite:** The fresh re-read must distinguish "this is still MY running turn" from "a different worker has already picked this up for the next turn."
**Mitigation:** Out of scope for this plan. The `_worker_loop` is project-keyed and uses a global semaphore (via `678c67a1`), so cross-worker picks on the same `session_id` should not happen in practice. If the plan's integration test cannot reliably construct this race, document it in Rabbit Holes and move on. If it becomes a real problem, add a per-session `agent_session_id`-based CAS check that compares the running agent_session_id to the fresh record's agent_session_id. (Non-trivial; defer.)

### Race 3: `log_lifecycle_transition` called concurrently from two paths
**Location:** `models/agent_session.py:1234-1261`.
**Trigger:** `complete_transcript` (fresh read) calls `log_lifecycle_transition` at the same time that `_worker_loop`'s finally block (also fresh read post-Layer 1) calls it. Both try to append to `session_events` and save.
**Data prerequisite:** `session_events` list must be append-only and ordered; neither caller should lose the other's entry.
**State prerequisite:** Appending to `session_events` in-memory on one object does not merge with the other object's in-memory list, so the last save wins and the earlier append is lost.
**Mitigation:** Acceptable for now because `complete_transcript` runs BEFORE the outer finally block (inside `_execute_agent_session`'s happy path at line 3232, which runs before `_execute_agent_session` returns to the worker loop). Layer 1's fresh re-read in the outer finally block will observe `fresh.status='completed'` after `complete_transcript` ran, and will skip. The race is closed by sequencing, not by locking. Document this ordering requirement in the code comment at the finally block entry.

### Race 4: Layer 2 partial save of `session_events` races with a concurrent full save of `session_events`
**Location:** `models/agent_session.py:1182-1198` (`_append_event_dict`) and any other caller that writes `session_events` (primarily `transition_status` / `finalize_session` via `log_lifecycle_transition`, but also direct `session.session_events = [...]` paths if they exist).
**Trigger:** Two callers append to `session_events` concurrently. Both read the list, both append their entry, both save. One entry is lost.
**Data prerequisite:** `session_events` is append-only; all appends must land.
**State prerequisite:** Redis WATCH/MULTI or CAS for the list field (Popoto does not support this natively).
**Mitigation:** Out of scope. The production workload does not have concurrent writers to the same `session_events` list today (session execution is serialized per chat_id). If this becomes a problem, the fix is a list-field-level CAS in Popoto, which is a much larger scope. Flagged in Rabbit Holes for follow-up. Document in `docs/features/session-lifecycle.md` that `session_events` appends are best-effort under concurrent mutation.

## No-Gos (Out of Scope)

- **`SessionRunner` class extraction** (from #867's audit appendix). Larger refactor, separate plan.
- **Collapsing `_complete_agent_session` and `complete_transcript` into a single finalizer.** Overlapping work, separate plan.
- **Rewriting the nudge path end-to-end** (covered by #743 / `extract-nudge-to-pm.md`, actively being planned). Keep scope tight.
- **Adding Redis WATCH/MULTI to Popoto's save path.** Correct long-term answer, large scope, upstream dependency.
- **Auditing every `session.save()` call site in `models/agent_session.py`.** Small appetite bounds this plan to three call sites. A broader audit is a follow-up chore.
- **`finalized_by_execute` gate from #872.** Still optional. Layer 1's fresh-read achieves the same correctness outcome. Add as a follow-up optimization if `_complete_agent_session`'s redundant call costs measurable worker time.
- **Watchdog logic for orphan `pending` index entries.** The orphan stops being created once the root cause is fixed. Existing `rebuild_indexes()` will clean up remaining orphans on the next worker restart. No separate cleanup work needed.
- **Changing `agent/output_router.py`'s nudge_continue routing decision.** This plan only fixes the save-path bypass; the routing decision itself is correct.

## Update System

No update system changes required — this fix is purely internal to the worker loop and session model. Deploying is a standard `/update` pull + worker restart. No new config, no new dependencies, no migration steps.

After this fix deploys, existing orphan `pending` index entries from sessions 502, 516, 517, 529, 539, 540, 541, 866177c6 will be cleaned up on the next worker restart's `rebuild_indexes()` call. No manual intervention needed. Document this in the release note.

## Agent Integration

No agent integration required — this is an internal worker-loop fix. The PM/SDLC agent does not call these code paths directly; it submits tool calls that the worker loop manages. No `.mcp.json` changes, no new MCP server tools, no bridge imports.

Indirect agent impact (positive): PM sessions will correctly continue their work across nudge cycles, so PM agents will finish their pipelines instead of silently dying after turn 1. The agent's observable behavior changes from "goes silent after first reply" to "continues working until explicit completion signal."

## Documentation

### Feature Documentation
- [ ] Amend `docs/plans/silent-session-death.md` with a regression note at the top: "Fix 4's `log_lifecycle_transition("worker finally block")` call regressed as [#898](https://github.com/tomcounsell/ai/issues/898) — the `append_event → self.save()` chain clobbers the nudge re-enqueue when called on a stale session object. See `docs/plans/nudge-stomp-append-event-bypass.md` for the follow-up fix." Mark the plan's status as `Shipped-with-regression` → updated to `Shipped` once this plan lands.
- [ ] Create or update `docs/features/session-lifecycle.md` with a "Stale object hazard" section: explain the three-object pattern (outer worker session, inner `_execute_agent_session` agent_session, nudge fresh reread), document the rule "never `save()` a session object that was read before the last potentially-concurrent mutation," and list the three call sites this plan fixes as canonical examples.
- [ ] Amend `docs/plans/lifecycle-cas-authority.md`'s "Related Work" section with a backlink to this plan noting that CAS fenced the lifecycle module but not `append_event`'s implicit save.
- [ ] Add entry to `docs/features/README.md` index if the session-lifecycle doc is newly created.

### Inline Documentation
- [ ] Docstring on `models/agent_session.py::append_event` documenting the stale-object hazard and the Layer 2 partial-save protection. Point readers at `docs/features/session-lifecycle.md`.
- [ ] Docstring on `models/agent_session.py::log_lifecycle_transition` documenting that it implicitly saves via `append_event` and must be called on a fresh-read object.
- [ ] Inline comment block on `agent/agent_session_queue.py:2254` explaining the fresh-read-first invariant and the #898 regression it prevents.
- [ ] Inline comment on `agent/agent_session_queue.py:3233` explaining the Layer 1c replacement and why the simple `agent_session.save()` was unsafe.

### External Documentation Site
- This repo does not use Sphinx/Read the Docs/MkDocs. Docs are flat markdown in `docs/`. No external doc site update needed.

## Success Criteria

- [ ] Integration test `tests/integration/test_nudge_stomp_regression.py::test_nudge_survives_worker_finally` passes. Currently fails against unfixed main (TDD red).
- [ ] Integration test `tests/integration/test_nudge_stomp_regression.py::test_cancelled_worker_does_not_clobber_stale` passes (covers Layer 1b).
- [ ] Integration test `tests/integration/test_nudge_stomp_regression.py::test_execute_agent_session_nudge_branch_no_clobber` passes (covers Layer 1c).
- [ ] Integration test `tests/integration/test_append_event_partial_save.py::test_append_event_preserves_non_listed_fields` passes (covers Layer 2).
- [ ] After running the integration test 10 times in a loop, the log line `"Skipping session ... index says pending but actual status='completed'. Stale index entry."` does NOT appear in captured logs for nudge-continuation sessions.
- [ ] `monitoring/bridge_watchdog.py --check-only` returns zero stalled sessions after the integration test completes.
- [ ] Existing tests in `tests/unit/test_session_completion_zombie.py`, `tests/unit/test_worker_cancel_requeue.py`, `tests/unit/test_complete_agent_session_redis_reread.py`, `tests/integration/test_agent_session_lifecycle.py`, `tests/unit/test_crash_snapshot.py`, `tests/unit/test_session_lifecycle_consolidation.py`, `tests/unit/test_recovery_respawn_safety.py` all pass unchanged (or after the prescribed updates in the Test Impact section).
- [ ] `python -m ruff check .` exit 0, `python -m ruff format --check .` exit 0.
- [ ] `pytest tests/ -x -q` exit 0.
- [ ] Production validation: deploy to the dev environment, send a real PM/SDLC message (e.g., `"review PR 897"`), confirm via `python -m tools.valor_session status --id <id>` that during the nudge cycles `auto_continue_count` > 1 and `session_events` contains at least one `running→pending: nudge re-enqueue` entry. Final completion happens only after PM's explicit completion signal, not after turn 1.
- [ ] `docs/plans/silent-session-death.md` amended with regression note.
- [ ] `docs/features/session-lifecycle.md` created or updated with stale-object hazard section.
- [ ] No bug fix has xfail/xpass tests to convert — confirmed via `grep -rn "pytest.mark.xfail\|pytest.xfail(" tests/ | grep -i "nudge\|finally\|lifecycle_transition\|append_event\|stomp"` returning empty.

## Team Orchestration

### Team Members

- **Builder (layer-1-worker-finally)**
  - Name: `finally-block-builder`
  - Role: Rewrite the outer worker finally block and the CancelledError branch in `agent/agent_session_queue.py` to re-read fresh before any save-triggering call. Apply Layer 1a and Layer 1b.
  - Agent Type: `builder`
  - Resume: true

- **Builder (layer-1c-execute-nudge-branch)**
  - Name: `execute-session-builder`
  - Role: Replace `_execute_agent_session`'s nudge-branch stale save at `:3233-3235` with a fresh-read `updated_at` heartbeat. Apply Layer 1c.
  - Agent Type: `builder`
  - Resume: true

- **Builder (layer-2-append-event)**
  - Name: `append-event-builder`
  - Role: Change `_append_event_dict`'s implicit save to partial with `update_fields=["session_events", "updated_at"]`. Update docstrings. Apply Layer 2.
  - Agent Type: `builder`
  - Resume: true

- **Builder (layer-3-messenger-log)**
  - Name: `messenger-log-builder`
  - Role: Fix the misleading `"Sent result message"` log in `agent/messenger.py:77`. Apply Layer 3b (change log copy to describe callback handoff, not delivery).
  - Agent Type: `builder`
  - Resume: true

- **Test Engineer (regression-tests)**
  - Name: `regression-test-engineer`
  - Role: Create `tests/integration/test_nudge_stomp_regression.py` and `tests/integration/test_append_event_partial_save.py`. Update the existing tests in the Test Impact section. Tests must use real Popoto, not MagicMock.
  - Agent Type: `test-engineer`
  - Resume: true

- **Validator (layer-1-and-1c)**
  - Name: `finally-validator`
  - Role: Verify both finally block rewrites operate on fresh reads, skip appropriately on `pending`/terminal, and do not introduce net-new Redis reads beyond the replaced ones.
  - Agent Type: `validator`
  - Resume: true

- **Validator (layer-2)**
  - Name: `append-event-validator`
  - Role: Verify `_append_event_dict` uses partial save and that the `status` / `auto_continue_count` / `message_text` fields are preserved across stale-caller scenarios.
  - Agent Type: `validator`
  - Resume: true

- **Validator (regression-tests)**
  - Name: `test-validator`
  - Role: Verify the new regression tests fail against unfixed main (TDD red check before Layer 1/2 builds land) and pass after they land (green check). Run the full failing-test loop 10 times to catch flakes.
  - Agent Type: `validator`
  - Resume: true

- **Documentarian**
  - Name: `lifecycle-doc-writer`
  - Role: Amend `docs/plans/silent-session-death.md`, create/update `docs/features/session-lifecycle.md`, update `docs/plans/lifecycle-cas-authority.md` backlink, update inline docstrings on `append_event` / `log_lifecycle_transition` / `_append_event_dict` and comment blocks at `agent/agent_session_queue.py:2254` and `:3233`.
  - Agent Type: `documentarian`
  - Resume: true

- **Lead Validator**
  - Name: `lead-validator`
  - Role: Run full verification table, confirm all success criteria, produce final report.
  - Agent Type: `validator`
  - Resume: true

### Available Agent Types

Using Tier 1 (`builder`, `validator`, `test-engineer`, `documentarian`) throughout. No Tier 2 specialists needed — this is a surgical fix in a well-mapped area.

## Step by Step Tasks

### 1. Write failing regression tests (TDD red)
- **Task ID**: build-regression-tests-red
- **Depends On**: none
- **Validates**: `tests/integration/test_nudge_stomp_regression.py` (create), `tests/integration/test_append_event_partial_save.py` (create)
- **Informed By**: spike-2 (surfaced the third stomp site at `:3234-3235`; test must cover it)
- **Assigned To**: regression-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/integration/test_nudge_stomp_regression.py` with three test cases: `test_nudge_survives_worker_finally`, `test_cancelled_worker_does_not_clobber_stale`, `test_execute_agent_session_nudge_branch_no_clobber`. Each must use real Popoto, construct two distinct Python `AgentSession` instances for the same `session_id`, simulate the nudge via `transition_status`, run the relevant finally-block code path on the stale instance, and assert the final Redis state matches the nudge's writes.
- Create `tests/integration/test_append_event_partial_save.py` with `test_append_event_preserves_non_listed_fields`: construct an `AgentSession`, save, mutate `status` directly in Redis via Popoto, then call `append_event` on the stale in-memory object. Assert that after the save, Redis still has the out-of-band status change (because partial save didn't touch `status`).
- Run `pytest tests/integration/test_nudge_stomp_regression.py tests/integration/test_append_event_partial_save.py -v` and confirm ALL tests FAIL against unfixed main. Report the failure signatures.
- **Done when**: three new `test_nudge_stomp_regression.py` cases and one `test_append_event_partial_save.py` case are red.

### 2. Layer 2: partial save in `append_event`
- **Task ID**: build-layer-2
- **Depends On**: build-regression-tests-red
- **Validates**: `tests/integration/test_append_event_partial_save.py`, `tests/integration/test_lifecycle_transition.py`
- **Informed By**: spike-1 (confirmed partial save is mechanically safe and does not corrupt IndexedField indices)
- **Assigned To**: append-event-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `models/agent_session.py:1197` from `self.save()` to `self.save(update_fields=["session_events", "updated_at"])`.
- Update the docstring on `append_event` (line 1171) and `_append_event_dict` (line 1182) documenting the partial-save behavior, the stale-object hazard it mitigates, and the hazard it does NOT mitigate (stale `session_events` list still possible).
- Update the docstring on `log_lifecycle_transition` (line 1234) with a warning that it implicitly persists via `append_event` and must be called on a fresh-read object.
- Run `pytest tests/integration/test_append_event_partial_save.py tests/integration/test_lifecycle_transition.py -v` and confirm Layer 2's test passes.
- **Done when**: `test_append_event_partial_save.py::test_append_event_preserves_non_listed_fields` passes, `tests/integration/test_lifecycle_transition.py` still passes, no other tests regress.

### 3. Layer 1a: worker finally block reorder
- **Task ID**: build-layer-1a
- **Depends On**: build-regression-tests-red
- **Validates**: `tests/integration/test_nudge_stomp_regression.py::test_nudge_survives_worker_finally`, `tests/unit/test_session_completion_zombie.py`, `tests/unit/test_complete_agent_session_redis_reread.py`, `tests/unit/test_crash_snapshot.py`
- **Informed By**: spike-2 (confirmed `complete_transcript` covers happy path on non-nudge; finally block must keep crash-snapshot write outside the fresh-read branch)
- **Assigned To**: finally-block-builder
- **Agent Type**: builder
- **Parallel**: false (conflicts with Layer 1b in the same file region)
- Rewrite `agent/agent_session_queue.py:2254-2327` per Technical Approach Layer 1a. Keep `save_session_snapshot` at the top of the finally block (crash-path diagnostic, safe to write from stale identifiers). Move `log_lifecycle_transition`, snapshot detail, and `_complete_agent_session` INTO the `else: fresh is still running` branch. Use `get_authoritative_session` for the fresh re-read and `TERMINAL_STATUSES` from the lifecycle module.
- Update the tests listed in Test Impact that assert on the old ordering (e.g., `tests/unit/test_crash_snapshot.py:49` which calls `session.log_lifecycle_transition(target, "worker finally block")` — update the mock setup to match the reorder).
- Run `pytest tests/integration/test_nudge_stomp_regression.py::test_nudge_survives_worker_finally tests/unit/test_session_completion_zombie.py tests/unit/test_complete_agent_session_redis_reread.py tests/unit/test_crash_snapshot.py -v` and confirm all pass.
- **Done when**: `test_nudge_survives_worker_finally` is green; no regressions in the listed existing tests.

### 4. Layer 1b: CancelledError branch
- **Task ID**: build-layer-1b
- **Depends On**: build-layer-1a (same file, same region — must land sequentially to avoid merge conflicts)
- **Validates**: `tests/integration/test_nudge_stomp_regression.py::test_cancelled_worker_does_not_clobber_stale`, `tests/unit/test_worker_cancel_requeue.py`
- **Informed By**: spike-2 (CancelledError path also uses the stale outer session — same pattern)
- **Assigned To**: finally-block-builder
- **Agent Type**: builder
- **Parallel**: false
- Apply the fresh-read pattern to `agent/agent_session_queue.py:2171-2189` (CancelledError handler). Re-read fresh, log lifecycle transition on fresh (not the stale outer `session`) only if `fresh.status == "running"`. Preserve the existing behavior of NOT calling `_complete_agent_session` in the cancellation path (startup recovery owns re-queueing).
- Update `tests/unit/test_worker_cancel_requeue.py` tests per Test Impact section — tests using MagicMock may not regress mechanically, but at least one test must use real Popoto to validate the stale-scenario.
- Run `pytest tests/integration/test_nudge_stomp_regression.py::test_cancelled_worker_does_not_clobber_stale tests/unit/test_worker_cancel_requeue.py -v` and confirm all pass.
- **Done when**: `test_cancelled_worker_does_not_clobber_stale` is green; no regressions in `test_worker_cancel_requeue.py`.

### 5. Layer 1c: `_execute_agent_session` nudge branch
- **Task ID**: build-layer-1c
- **Depends On**: build-regression-tests-red
- **Validates**: `tests/integration/test_nudge_stomp_regression.py::test_execute_agent_session_nudge_branch_no_clobber`, `tests/integration/test_agent_session_lifecycle.py`
- **Informed By**: spike-2 (this is the third stomp site surfaced during planning, not mentioned in issue #898)
- **Assigned To**: execute-session-builder
- **Agent Type**: builder
- **Parallel**: true (different file region than Layer 1a/1b)
- Replace `agent/agent_session_queue.py:3233-3235` per Technical Approach Layer 1c. Use `get_authoritative_session` to re-read, check `fresh_nudge.status == "pending"`, and use `fresh_nudge.save(update_fields=["updated_at"])` for the heartbeat. Wrap in try/except with `logger.debug` on failure (non-fatal).
- Add an inline comment block explaining the stale-save regression and the Layer 1c fix.
- Run `pytest tests/integration/test_nudge_stomp_regression.py::test_execute_agent_session_nudge_branch_no_clobber tests/integration/test_agent_session_lifecycle.py -v` and confirm pass.
- **Done when**: `test_execute_agent_session_nudge_branch_no_clobber` is green; `test_agent_session_lifecycle.py` still passes.

### 6. Layer 3: messenger log accuracy
- **Task ID**: build-layer-3
- **Depends On**: none
- **Validates**: grep confirmation + manual log inspection
- **Informed By**: issue #898 secondary-bug note
- **Assigned To**: messenger-log-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `agent/messenger.py:77-80` log message from `"Sent {message_type} message ({len(message)} chars) to chat {self.chat_id}"` to something that accurately describes what `BossMessenger.send` actually did (e.g., `"Passed {len(message)} chars to send_callback for chat {self.chat_id}"`). This is a log-copy-only change.
- Confirm via grep that no tests or dashboards parse the old log message string.
- **Done when**: the log line no longer claims delivery, and no consumers of the old string exist.

### 7. Validate Layer 1a + 1b (worker finally region)
- **Task ID**: validate-layer-1a-1b
- **Depends On**: build-layer-1b
- **Assigned To**: finally-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify both finally block rewrites in `agent/agent_session_queue.py` operate on fresh reads from `get_authoritative_session` before any save-triggering call.
- Verify the crash-snapshot save at the top of the finally block still runs on stale identifiers (so crash diagnostics still fire).
- Count net Redis reads per finalization cycle: expect ≤ current (currently: 1 `AgentSession.query.get(redis_key=...)` in the guard + 1 re-read inside `_complete_agent_session` = 2 reads; after fix: 1 `get_authoritative_session` at entry + 1 re-read inside `_complete_agent_session` = 2 reads — no regression).
- Verify no Redis read is added for the happy-path `complete_transcript` case (that path returns before the outer finally block's mutating ops).
- Run the full unit/integration suite for the touched files.
- Report pass/fail status.

### 8. Validate Layer 1c (execute-session nudge branch)
- **Task ID**: validate-layer-1c
- **Depends On**: build-layer-1c
- **Assigned To**: finally-validator
- **Agent Type**: validator
- **Parallel**: true (different file region from step 7)
- Verify the replacement at `agent/agent_session_queue.py:3233-3241` re-reads via `get_authoritative_session`, checks `fresh_nudge.status == "pending"`, and uses `save(update_fields=["updated_at"])`.
- Verify the non-nudge branch (`complete_transcript` path) is untouched.
- Verify the try/except wraps the new code so Redis failures don't crash `_execute_agent_session`.
- Report pass/fail status.

### 9. Validate Layer 2 (`append_event` partial save)
- **Task ID**: validate-layer-2
- **Depends On**: build-layer-2
- **Assigned To**: append-event-validator
- **Agent Type**: validator
- **Parallel**: true
- Verify `models/agent_session.py:1197` uses `save(update_fields=["session_events", "updated_at"])`.
- Verify the docstrings on `append_event`, `_append_event_dict`, and `log_lifecycle_transition` document the stale-object hazard.
- Run `pytest tests/integration/test_append_event_partial_save.py tests/integration/test_lifecycle_transition.py tests/e2e/test_session_lifecycle.py -v`.
- Verify that `_saved_field_values` still tracks the listed fields after partial save (spike-1 confirmed this; validate via test).
- Report pass/fail status.

### 10. Run the regression test loop (flake check)
- **Task ID**: validate-regression-loop
- **Depends On**: validate-layer-1a-1b, validate-layer-1c, validate-layer-2
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_nudge_stomp_regression.py -v --count=10` (or equivalent loop). All three tests must pass all 10 iterations.
- Run `pytest tests/integration/test_append_event_partial_save.py -v --count=10`. Must pass all iterations.
- Confirm no `"Skipping session ... index says pending but actual status='completed'. Stale index entry."` log lines appear in captured test logs.
- Confirm no `LIFECYCLE_STALL` watchdog alerts fire for nudge-continuation sessions during the test run.
- Report pass/fail with any flake signatures.

### 11. Documentation cascade
- **Task ID**: document-feature
- **Depends On**: validate-regression-loop
- **Assigned To**: lifecycle-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Amend `docs/plans/silent-session-death.md` with the regression note at the top. Do not change the plan's `status:` field — the original plan still shipped; this plan is the regression fix.
- Create or update `docs/features/session-lifecycle.md` with the "Stale object hazard" section. Include the three-object pattern, the rule, and the three call sites as canonical examples.
- Update `docs/plans/lifecycle-cas-authority.md`'s related-work section with a backlink to this plan.
- Add an entry to `docs/features/README.md` index if session-lifecycle.md is newly created.
- Commit all doc changes.

### 12. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full verification table from the Verification section.
- Verify all success criteria are met.
- Produce the final report: pass/fail per success criterion, any deviations from plan, any deferred items.
- If the plan shipped clean, report ready for PR. If anything is off, report the specific deviations for a quick patch cycle.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Regression tests pass | `pytest tests/integration/test_nudge_stomp_regression.py tests/integration/test_append_event_partial_save.py -v` | exit code 0 |
| Full test suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale index warnings in test logs | `pytest tests/integration/test_nudge_stomp_regression.py -v 2>&1 \| grep -c "Stale index entry"` | output contains 0 |
| Layer 2 applied | `grep -q "update_fields=\[.session_events., .updated_at.\]" models/agent_session.py` | exit code 0 |
| Layer 1a applied | `grep -q "get_authoritative_session" agent/agent_session_queue.py && grep -B2 "log_lifecycle_transition.*worker finally block" agent/agent_session_queue.py \| grep -q "fresh\."` | exit code 0 |
| Layer 1c applied | `grep -A3 "chat_state.defer_reaction" agent/agent_session_queue.py \| grep -q "update_fields=\[.updated_at.\]"` | exit code 0 |
| Layer 3 applied (messenger log no longer claims delivery) | `grep -c "Sent.*message.*to chat" agent/messenger.py` | output contains 0 |
| No xfail regressions | `grep -rn "pytest.mark.xfail\|pytest.xfail(" tests/ \| grep -i "nudge\|append_event\|lifecycle_transition\|stomp"` | exit code 1 |
| Silent-session-death plan amended | `grep -q "#898" docs/plans/silent-session-death.md` | exit code 0 |
| Session-lifecycle doc exists | `test -f docs/features/session-lifecycle.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Layer 3 scope**: the secondary-bug fix in `agent/messenger.py:77` is small, but "change the log copy" feels less valuable than "make the log match reality." Option 3a (threaded callback result back into the log) is larger surgery but produces a genuinely informative log line. Which do you prefer — ship Layer 3b now (log copy change) and defer 3a, or fold 3a into this plan and accept the slightly larger appetite?

2. **#872's `finalized_by_execute` gate**: the plan treats it as a follow-up optimization (No-Gos). But if we're already rewriting the finally block in Layer 1a, the marginal cost of adding the gate is small and it closes the entire stale-save class at this site. Want to bundle it in, or keep scope tight and revisit after this ships?

3. **Integration test harness**: `tests/integration/test_nudge_stomp_regression.py` needs to construct two distinct Python `AgentSession` objects for the same `session_id` and exercise the worker finally block against the stale one. I don't see a helper for this pattern in existing tests. Is there one I missed (e.g., in `tests/integration/test_agent_session_lifecycle.py` or a fixture in `conftest.py`), or should the test-engineer build a small fixture from scratch?

4. **Docstring warning enforcement**: the plan adds warnings to `append_event` / `log_lifecycle_transition` docstrings about the stale-object hazard, but nothing mechanically enforces that callers read them. Would you be open to a small custom `ruff` rule or a unit test that greps the codebase for new `append_event` / `append_history` call sites and requires a comment justifying the call site is fresh-read? Small follow-up, tangential to the main fix, but cheap insurance against the bug recurring.
