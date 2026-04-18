---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-18
tracking: https://github.com/valorengels/ai/issues/1036
last_comment_id:
---

# Heartbeat-Aware No-Progress Guard + Orphan Reap + Terminal Preservation

## Problem

The `AgentSession` queue runs a periodic health check every 5 minutes and recovers "stuck" running sessions using a guard based on three own-progress fields (`turn_count`, `log_path`, `claude_session_uuid`) plus an active-children probe. The SDK subprocess has its own liveness signal — the messenger heartbeat in `agent/messenger.py:194-220` — that logs `SDK heartbeat: running Ns, communicated=...` every 60 seconds. The heartbeat is NOT written to the AgentSession record, so the health check is blind to it. This split observability causes three distinct failures when an initial prompt takes longer than 300s to produce its first turn:

**Current behavior** (observed 2026-04-17, session `0_1776422754682`, ~4000-char PM prompt):

1. **False kill**: A session with a live `SDK heartbeat: running 360s, communicated=False` is recovered at t=358s because `_has_progress()` sees all three own-progress fields empty. The SDK was genuinely working (digesting a large prompt before first auth).
2. **Orphan subprocess**: After the health check transitions `running → pending`, the original Claude SDK subprocess keeps running. Heartbeats continue firing for minutes ("SDK heartbeat: running 420s, 480s, ..."). This wastes API tokens, may emit results that never land anywhere, and double-counts against `MAX_CONCURRENT_SESSIONS` if a new pickup happens.
3. **Silent deletion**: By the next day, the entire session record is gone — `valor-session status --id 0_1776422754682` returns "Session not found". No terminal status (`failed`/`abandoned`/`killed`), no history, no log_path. Likely deleted by the `Meta.ttl = 2592000` (30-day) backstop after a longer interval, or by `cleanup_corrupted_agent_sessions` if the record hit a validation error. Either way: no audit trail.

**Desired outcome:**

- A session whose SDK heartbeat is alive is NOT killed by the no-progress guard, regardless of how long the first turn takes.
- When a session IS killed by the guard, the SDK subprocess is terminated within the next health-check tick (or at most 60s later) — no orphan heartbeats.
- Sessions that are recovered and genuinely never make progress transition to a terminal status (`failed`) with full history preserved. Nothing silently disappears from `valor-session` listings.

## Freshness Check

**Baseline commit:** `81db469f3861652933ed8b77e4475eec6d31e2bc`
**Issue filed at:** 2026-04-18T04:32:19Z (≈1 hour before plan time)
**Disposition:** Unchanged

**File:line references re-verified (all still hold at baseline SHA):**
- `agent/agent_session_queue.py:130-132` — `AGENT_SESSION_HEALTH_MIN_RUNNING = 300` — still exact.
- `agent/agent_session_queue.py:1532-1568` — `_has_progress()` body — still matches. Checks `turn_count`, `log_path`, `claude_session_uuid`, then `get_children()`.
- `agent/agent_session_queue.py:1615-1627` — terminal-status zombie guard (#1006) — still present.
- `agent/agent_session_queue.py:1664-1675` — guard fire site for worker-alive + no-progress — still exact.
- `agent/agent_session_queue.py:1702-1726` — `response_delivered_at` guard (#918) — still exact.
- `agent/agent_session_queue.py:1738-1765` — `finalize_session("abandoned", ...)` for local sessions + `transition_status("pending", ...)` for remote — still exact.
- `agent/messenger.py:194-220` — `_watchdog` that logs SDK heartbeat — still exact.
- `worker/__main__.py:179-182` — `MAX_CONCURRENT_SESSIONS` semaphore — still exact.

**Cited sibling issues re-checked:**
- #944 — closed 2026-04-14 as fixed (added `_has_progress()` to handle slugless dev sessions sharing a worker_key with their PM). Plan must preserve this behavior.
- #963 — closed 2026-04-14. Added child-progress check to `_has_progress()`. Plan must preserve the `get_children()` branch.
- #1006 — closed 2026-04-16. Added terminal-status zombie guard at the top of the health loop. Plan must preserve this.
- #918 — closed 2026-04-12. Added `response_delivered_at` guard in recovery path. Plan must preserve this.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since=$ISSUE_CREATED -- agent/agent_session_queue.py agent/messenger.py worker/__main__.py` returned empty.

**Active plans in `docs/plans/` overlapping this area:** none. Grep for `_has_progress`, `AGENT_SESSION_HEALTH_MIN_RUNNING`, `session-health` in `docs/plans/` returned no active matches.

**Notes:** All assumptions from the issue body hold. Safe to proceed with the original Solution Sketch.

## Prior Art

Closed issues touching the same code:

- **#944** — "bug: health check skips recovery for stuck dev sessions when a shared project-keyed worker is alive" — added `_has_progress()` because `worker_alive` alone doesn't prove a slugless dev session is being handled. Current work must NOT weaken this — slugless dev sessions sharing a worker_key with their PM must still be recoverable when the dev is stuck.
- **#963** — "Session routing integrity" — added the child-activity branch to `_has_progress()`. Current work preserves this branch verbatim.
- **#1006** — "Killed sessions resurrect in running index after worker restart or health check" — added terminal-status guard at top of health loop. Current work preserves this ordering.
- **#918** — "Bridge delivers same message multiple times to same session" — added `response_delivered_at` guard so recovery finalizes instead of re-queueing when a response was already sent. Current work preserves this guard.

No prior fix has addressed the messenger-heartbeat-vs-own-progress-field divergence or the orphan-subprocess reap. This is new territory.

## Research

External research is NOT required — this is purely internal concurrency and Redis-field work. All relevant signals (heartbeat interval, cancellation semantics, Popoto TTL) are visible in the codebase. Proceeding with codebase context and training data.

## Spike Results

Three spikes resolved assumptions that shaped the design:

### spike-1: Where is the SDK subprocess tracked so we can cancel it?
- **Assumption**: "`_active_workers[worker_key]` points to a task we can cancel per session."
- **Method**: code-read
- **Finding**: `_active_workers` maps `worker_key → asyncio.Task` for the **entire worker loop** (`_worker_loop()`). A worker loop can serially handle many sessions over its lifetime. Cancelling the worker task would tear down the loop and trigger startup recovery — heavy-handed and also racy with the health check's own re-queue. The closer handle is `BackgroundTask._task` inside `_execute_agent_session()` (agent/agent_session_queue.py:4119-4120, agent/messenger.py:98-145). But that `BackgroundTask` is local to `_execute_agent_session`; there is no registry mapping `agent_session_id → BackgroundTask`. **Design implication**: Fix 2 must introduce a registry `_active_sessions: dict[str, asyncio.Task]` keyed by `agent_session_id` that tracks the coroutine running `_execute_agent_session` for each live session. The health check cancels by looking up the id.
- **Confidence**: high
- **Impact on plan**: Technical Approach Fix 2 now specifies a new `_active_sessions` registry (set on entry to `_execute_agent_session`, popped in `finally`). Recovery calls `.cancel()` on the task, which propagates `CancelledError` into `BackgroundTask._task` and terminates the subprocess via the SDK client's own cancellation handling.

### spike-2: What deletes the session record silently?
- **Assumption**: "Some cleanup path is hard-deleting non-terminal sessions without a terminal status."
- **Method**: code-read
- **Finding**: Two paths can delete an AgentSession record:
  1. `AgentSession.Meta.ttl = 2592000` (30 days) — Redis key expiration. This is the most likely culprit for "gone by next day" only if the TTL was already short for some reason, OR if reaffirming comment: *the issue says "by the next day"* — that contradicts a 30-day TTL. Need a second look: check `tools/agent_session_scheduler.py::cmd_cleanup` (agent_session_scheduler.py:1019-1080). Cleanup filter: `status in ("killed", "abandoned", "failed")` AND `age > --age minutes`. `running → pending` does NOT produce a terminal status, so this path can't delete a session that got stuck in the recovery loop — **unless** the session was transitioned to `abandoned` for being a local session (agent/agent_session_queue.py:1740-1753) AND the scheduler ran with a low `--age`. Local sessions DO hit this path (`worker_key.startswith("local")`).
  2. `cleanup_corrupted_agent_sessions()` (agent/agent_session_queue.py:4588) — deletes records with ID length != 32 or that fail `.save()` validation. Reliable only for genuinely corrupted records.
- **Confidence**: medium
- **Impact on plan**: Fix 3 has two sub-prongs. (a) For the healthiest outcome, the recovery code path itself must leave a terminal status when the session has been recovered repeatedly and still shows no progress — not leave it in `pending` forever. Introduce a `recovery_attempts` counter that increments on each `running → pending` transition via the health check, and at `recovery_attempts >= 2` transition to `failed` with full history. (b) Audit the `cmd_cleanup` path to ensure it never deletes a session that hasn't been terminal for at least the `--age` threshold — the current code already does this (it filters by terminal status + age), so no change needed. The actual 1036 scenario is the 30-day TTL backstop firing after a long-abandoned `pending` session sits unobserved — the fix is to force it to terminal state inside the recovery loop so it either completes or fails, never stays `pending` for days.

### spike-3: Is `messenger.py::_watchdog` plumbed to the AgentSession record?
- **Assumption**: "The watchdog already has a handle to the AgentSession."
- **Method**: code-read
- **Finding**: The `BackgroundTask._watchdog()` method has access to `self.messenger.session_id` but NOT to the `AgentSession` ORM object. The `AgentSession` is scoped to `_execute_agent_session()` (as `agent_session` local) and is used for the `updated_at` heartbeat inside `_heartbeat_loop()` at the queue level (agent/agent_session_queue.py:4124-4142). The cleanest fix is to piggyback on the existing `_heartbeat_loop` in `_execute_agent_session` rather than modify `BackgroundTask._watchdog` — `_heartbeat_loop` already has the `agent_session` reference and already writes `updated_at` every 25 minutes. We can add a `last_heartbeat_at` field write that fires on a **shorter** interval (every 60-90s while `task._task` is not done), which will keep the field fresh enough for the 5-min health check to see it. Modifying `BackgroundTask._watchdog` would require plumbing the AgentSession record into `messenger.py`, increasing coupling without benefit.
- **Confidence**: high
- **Impact on plan**: Fix 1 will NOT modify `messenger.py`. Instead, a new short-interval heartbeat write inside `_execute_agent_session`'s existing `_heartbeat_loop` will update `last_heartbeat_at` every 60s. `_has_progress()` then adds a fourth signal: `last_heartbeat_at >= now - 90s` counts as progress.

## Data Flow

End-to-end timing of the fix, from prompt arrival to health check decision:

1. **Entry point**: A session is picked up from the pending queue by `_worker_loop`, enters `_execute_agent_session(session)` (agent/agent_session_queue.py:3491).
2. **Registration**: `_execute_agent_session` sets `_active_sessions[session.agent_session_id] = asyncio.current_task()` so the health check can cancel this specific session. Registered in a try/finally pair so it's always popped on exit.
3. **Heartbeat loop start**: `_execute_agent_session` starts `task = BackgroundTask(...); await task.run(do_work(), ...)` then launches `_heartbeat_loop` (agent/agent_session_queue.py:4124) which already runs while `task._task` is not done.
4. **Every 60s (new, fast interval)**: `_heartbeat_loop` writes `agent_session.last_heartbeat_at = datetime.now(tz=UTC); agent_session.save(update_fields=["last_heartbeat_at"])`. This happens regardless of whether the SDK has authenticated or emitted a turn.
5. **Every 25 minutes (existing, slow interval)**: The existing calendar heartbeat + `updated_at` write continues unchanged.
6. **Health check every 300s**: `_agent_session_health_check` reads `entry.last_heartbeat_at`. `_has_progress()` now returns True if `last_heartbeat_at` is within the last 90 seconds — meaning the subprocess is alive.
7. **If heartbeat is fresh**: The guard does NOT fire. Session continues. First turn eventually lands (minutes 5-7). `claude_session_uuid` then gets populated, at which point the heartbeat field becomes redundant — either signal is sufficient.
8. **If heartbeat is stale (subprocess actually dead)**: Guard fires as before. Recovery path now (a) looks up `_active_sessions[agent_session_id]` and cancels it if present — this triggers `CancelledError` in the worker loop, which the worker handles by leaving the session in `running` for startup recovery, BUT the health check then transitions it to `pending` or `failed` (see next step), so the worker's `CancelledError` handler must coordinate with the health check's explicit transition. Finer design: health check cancels first, awaits the cancellation briefly (up to 1s), then applies the transition.
9. **Recovery counter**: Each `running → pending` transition increments `agent_session.recovery_attempts`. On reaching 2, the health check transitions to `failed` instead of `pending`, preserving the record in a terminal status.
10. **Output**: Sessions that never make progress end up `failed` with `log_lifecycle_transition` entries describing each recovery attempt — fully auditable.

## Why Previous Fixes Failed

Prior fixes didn't fail — they solved different problems. But they left one gap visible in hindsight:

| Prior Fix | What It Did | Why It Didn't Cover This Bug |
|-----------|-------------|------------------------------|
| #944 | Added `_has_progress()` to avoid false-kill of slugless dev sessions sharing worker_key with PM | Scoped to dev-session specificity. Only looked at three own-progress fields — didn't consider SDK liveness as a signal. |
| #963 | Added `get_children()` child-activity branch to `_has_progress()` | Solved the PM-with-active-children case. Sessions with no children (solo PM with a big initial prompt — this issue's scenario) still have only own-progress to rely on. |
| #1006 | Terminal-status guard at top of health loop | Protects against resurrection. Doesn't prevent false kills in the first place, and doesn't preserve never-progressed sessions in terminal state. |
| #918 | `response_delivered_at` guard to avoid duplicate delivery during recovery | Only fires when a response was already delivered. For the 1036 scenario, no response was ever delivered — guard doesn't apply. |

**Root cause pattern:** Each prior fix added a heuristic for a specific false-kill pattern without ever treating **SDK liveness** (the most direct signal) as a progress input. The health check's own-progress model was incomplete.

## Architectural Impact

- **New dependencies**: None. `last_heartbeat_at` is a new `DatetimeField` on `AgentSession`. `recovery_attempts` is a new `IntField`. No new services or libraries.
- **Interface changes**:
  - `AgentSession` gains two fields: `last_heartbeat_at: DatetimeField(null=True)` and `recovery_attempts: IntField(default=0)`.
  - `_has_progress()` gains a fourth signal (heartbeat freshness).
  - `_execute_agent_session` gains registration in a new `_active_sessions` registry and one additional `save(update_fields=["last_heartbeat_at"])` per 60s in the heartbeat loop.
- **Coupling**: Slight increase — the session recovery path now knows about `_active_sessions`. The heartbeat loop gains a field write. `messenger.py` is NOT touched (the heartbeat log line there is informational; the progress signal is written at the queue layer, where the AgentSession reference is already in scope). This keeps coupling contained to `agent_session_queue.py`.
- **Data ownership**: `last_heartbeat_at` is owned by the `_execute_agent_session` heartbeat loop (single writer). `recovery_attempts` is owned by the health check (single writer). No multi-writer concerns.
- **Reversibility**: Fully reversible. Both new fields are optional (null=True / default=0). Reverting requires removing the four touch points (field defs, heartbeat write, `_has_progress` branch, recovery counter logic).

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (acceptance criteria validation after regression test scenarios run)
- Review rounds: 1 (standard PR review; concurrency-sensitive, so extra scrutiny on the cancellation path)

Medium because the change touches three independent subsystems (health check, session execution, lifecycle) and must preserve four prior fix invariants (#944/#963/#1006/#918). Not Large because each fix is a localized edit to a single function; the complexity is in the invariants, not the code volume.

## Prerequisites

No prerequisites — all required fields, models, and code are present. The fix adds fields to an existing model and edits existing functions. No new services, keys, or config.

## Solution

### Key Elements

- **`last_heartbeat_at` field on AgentSession**: Records when the `_heartbeat_loop` in `_execute_agent_session` last confirmed the SDK subprocess was running. Written every 60s while `BackgroundTask._task` is active.
- **`_has_progress()` heartbeat branch**: New first check — if `last_heartbeat_at` is within the last 90 seconds, return True. Ordering: heartbeat first (cheapest, newest signal), then the three existing own-progress fields, then the child probe.
- **`_active_sessions` registry**: `dict[str, asyncio.Task]` mapping `agent_session_id → session-execution task`. Populated at the top of `_execute_agent_session`, removed in its `finally`. Allows the health check to cancel a specific session's subprocess on recovery.
- **Recovery task cancellation**: In the `running → pending` recovery branch, look up `_active_sessions[agent_session_id]`; if present and not done, `.cancel()` it. Await briefly (up to 1s) for cancellation to propagate before issuing the lifecycle transition.
- **`recovery_attempts` counter**: New `IntField(default=0)` on `AgentSession`. Incremented by the health check on each `running → pending` recovery. At `>= 2`, transition to `failed` with full history instead of `pending`.

### Flow

SDK subprocess start → `_heartbeat_loop` writes `last_heartbeat_at` every 60s → Health check every 300s reads `last_heartbeat_at` → If fresh (< 90s), session is NOT recovered → If stale, health check cancels `_active_sessions[session_id]`, awaits up to 1s, then transitions → First recovery: `running → pending` and `recovery_attempts = 1` → Re-picked up by worker → If it stalls again and heartbeat goes stale again, second recovery: `recovery_attempts = 2` triggers `running → failed` (terminal, preserved).

### Technical Approach

Four localized edits plus one new file section for tests:

1. **`models/agent_session.py`** (add two fields):
   - `last_heartbeat_at = DatetimeField(null=True)` — add to `_DATETIME_FIELDS` set so the existing type-coercion logic handles it.
   - `recovery_attempts = IntField(default=0)`.

2. **`agent/agent_session_queue.py::_execute_agent_session`** (heartbeat loop change + registry):
   - At function entry (before `task.run(...)`), register current task: `_active_sessions[session.agent_session_id] = asyncio.current_task()`.
   - Wrap the existing body so `_active_sessions.pop(session.agent_session_id, None)` is called in a `finally` — guaranteed cleanup on normal exit, exception, or cancellation.
   - Inside `_heartbeat_loop()`, replace the existing 25-minute cadence with a two-tier cadence:
     - Every `HEARTBEAT_WRITE_INTERVAL = 60` seconds: write `last_heartbeat_at`. Use `save(update_fields=["last_heartbeat_at"])` to avoid clobbering other fields.
     - Every `CALENDAR_HEARTBEAT_INTERVAL = 1500` (25 min): continue to fire the calendar write + `updated_at` save (existing behavior).
   - Do this with `asyncio.sleep(HEARTBEAT_WRITE_INTERVAL)` as the inner loop; use `elapsed % CALENDAR_HEARTBEAT_INTERVAL < HEARTBEAT_WRITE_INTERVAL` to gate the 25-min work. Best-effort: if `save` fails, log at WARNING and continue (mirrors the current pattern at line 4138).

3. **`agent/agent_session_queue.py::_has_progress`** (add heartbeat branch):
   - First check (before `turn_count`):
     ```python
     hb = getattr(entry, "last_heartbeat_at", None)
     if hb is not None:
         age_s = (datetime.now(tz=UTC) - hb).total_seconds()
         if age_s < HEARTBEAT_FRESHNESS_WINDOW:  # 90
             return True
     ```
   - `HEARTBEAT_FRESHNESS_WINDOW = 90` at module level. Chosen to be 1.5× the write interval, providing one missed write of slack.
   - Keep every other existing check in `_has_progress` unchanged — preserves #944/#963 invariants.

4. **`agent/agent_session_queue.py::_agent_session_health_check`** (recovery path changes):
   - In the recovery branch (after the `response_delivered_at` guard at line 1704-1726 and before the existing `transition_status` call at line 1761), add:
     ```python
     # Reap orphan SDK subprocess
     active_task = _active_sessions.get(entry.agent_session_id)
     if active_task is not None and not active_task.done():
         active_task.cancel()
         try:
             await asyncio.wait_for(active_task, timeout=1.0)
         except (asyncio.CancelledError, asyncio.TimeoutError):
             pass
         logger.info(
             "[session-health] Cancelled orphan task for session %s",
             entry.agent_session_id,
         )
     ```
   - Replace the unconditional `transition_status(entry, "pending", ...)` with logic that checks `recovery_attempts`:
     ```python
     entry.recovery_attempts = (entry.recovery_attempts or 0) + 1
     if entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:  # 2
         finalize_session(
             entry, "failed",
             reason=f"health check: {entry.recovery_attempts} recovery attempts, never progressed",
         )
     else:
         entry.priority = "high"
         entry.started_at = None
         transition_status(
             entry, "pending",
             reason=f"health check: recovered stuck session (attempt {entry.recovery_attempts})",
         )
         _ensure_worker(worker_key, is_project_keyed=entry.is_project_keyed)
         event = _active_events.get(worker_key)
         if event is not None:
             event.set()
     ```
   - `MAX_RECOVERY_ATTEMPTS = 2` at module level.
   - The existing local-session path (`is_local` branch at line 1740-1753) continues to go to `abandoned` on first recovery — unchanged. Local sessions can't be re-picked up, so a single recovery is already terminal.

5. **`_active_sessions` registry**: Add as module-level `_active_sessions: dict[str, asyncio.Task] = {}` near `_active_workers` at line 2124. Document it inline (single-writer: `_execute_agent_session`; single-reader: health check).

**Preserving prior fixes (explicit checks):**
- #918: The `response_delivered_at` guard at line 1704 runs BEFORE the new task-cancel + transition logic, so it's unaffected. Sessions with a delivered response still get finalized, never cancelled mid-flight.
- #944: `_has_progress()` still returns True for slugless dev sessions with own-progress signals. The heartbeat branch is additive, not substitutive.
- #963: `get_children()` branch preserved verbatim.
- #1006: Terminal-status zombie guard at line 1620-1627 still runs first. Sessions already terminal skip the whole recovery path.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `save(update_fields=["last_heartbeat_at"])` call inside `_heartbeat_loop` must be wrapped in try/except that logs at WARNING on failure and continues — silent failure here would mean progress signals stop flowing, which would cause false kills again. Test: inject a `save` failure, assert WARNING is logged, assert loop continues.
- [ ] The `active_task.cancel()` + `asyncio.wait_for` path must handle the case where the task has already completed between `.get()` and `.cancel()` (returns `None` for `not done()` case, or raises on cancel after completion). Test: race — schedule task completion before cancel call, assert no exception propagates.
- [ ] The `finalize_session(entry, "failed", ...)` call at `recovery_attempts >= 2` must handle the case where `finalize_session` itself raises (e.g. stale Redis reference). Test: inject ORM save failure, assert the recovery loop does not crash; the session's failure is logged and re-attempted next tick.

### Empty/Invalid Input Handling
- [ ] `_has_progress` must handle `entry.last_heartbeat_at = None` (new session, never written) — should fall through to existing checks without raising. Test: session with all own-progress fields empty AND `last_heartbeat_at=None` → `_has_progress` returns False (unchanged from current).
- [ ] `_has_progress` must handle `last_heartbeat_at` as a non-datetime (backward-compat with old records loaded from Redis). The existing `_DATETIME_FIELDS` coercion handles this — confirm `last_heartbeat_at` is in that set.
- [ ] `recovery_attempts` must handle `None` (old records without the field). Use `(entry.recovery_attempts or 0)` in arithmetic.

### Error State Rendering
- [ ] Sessions that reach `failed` via `recovery_attempts >= 2` must appear in `valor-session status --id <id>` with a clear reason. Test: simulate 2 recoveries, confirm `valor-session status` returns the session and shows status=failed with the reason in its lifecycle history.

## Test Impact

Existing tests that assert `_has_progress` behavior:

- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestHasProgressChildActivity` (lines 188-249) — UPDATE: add a new test class `TestHasProgressHeartbeat` that exercises the new heartbeat branch. Existing child-activity tests stay intact (heartbeat is the first check, but returning True via heartbeat doesn't regress the child-activity path because the tests construct entries with `last_heartbeat_at=None`).
- [ ] `tests/unit/test_stall_detection.py` — UPDATE: audit for any mocked AgentSession that hardcodes absence of `last_heartbeat_at` expecting progress-false. With default `last_heartbeat_at=None`, existing assertions should still hold, but the test setup may need one-line updates to be explicit.
- [ ] `tests/unit/test_agent_session_hierarchy.py` — REVIEW: ensure no existing hierarchy tests depend on the exact `running → pending` transition without the cancellation step. If any assert `_active_workers` state but not `_active_sessions`, add `_active_sessions` cleanup to their teardown.

New tests required (added as part of this work, not pre-existing):

- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestHasProgressHeartbeat` (NEW):
  - `test_heartbeat_within_window_returns_true` — `last_heartbeat_at = now - 30s`, other fields empty → True.
  - `test_heartbeat_at_boundary_returns_true` — `last_heartbeat_at = now - 89s` → True.
  - `test_heartbeat_stale_returns_false_when_other_fields_empty` — `last_heartbeat_at = now - 200s`, other fields empty → False.
  - `test_heartbeat_none_falls_through_to_other_checks` — `last_heartbeat_at = None`, `turn_count=5` → True (unchanged behavior).
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestRecoveryCancellation` (NEW):
  - `test_recovery_cancels_active_session_task` — register a task in `_active_sessions`, trigger recovery, assert task is cancelled.
  - `test_recovery_handles_completed_task_gracefully` — register a completed task, assert no exception.
  - `test_recovery_handles_missing_registry_entry` — session not in `_active_sessions`, assert recovery still transitions status (no crash).
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestRecoveryAttempts` (NEW):
  - `test_first_recovery_transitions_to_pending` — `recovery_attempts=0` → becomes 1, status=pending.
  - `test_second_recovery_finalizes_as_failed` — `recovery_attempts=1` → becomes 2, status=failed.
  - `test_local_session_terminal_on_first_recovery` — local worker_key → abandoned (unchanged from current behavior).
  - `test_finalized_failure_preserves_history` — after failure, `valor_session.status(session_id)` returns session with full lifecycle entries.
- [ ] `tests/integration/test_session_heartbeat_progress.py` (NEW, integration): start a real session that intentionally delays its first turn by 360s using a stub SDK; assert the session is NOT recovered by the health check while the heartbeat is alive. Also simulate heartbeat stoppage and assert cancellation + transition within one health-check cycle.

## Rabbit Holes

- **Don't rewrite `_has_progress` as a full-blown observability system.** The bug is specifically the missing heartbeat signal. Adding weighted multi-signal scoring, configurable thresholds, or a pluggable progress-signal registry is out of scope. Four-signal checks (heartbeat, uuid, log_path, turn_count) + child probe is sufficient.
- **Don't modify `agent/messenger.py`.** The watchdog there is informational (logs to stdout). Piping the AgentSession record into messenger adds coupling with no benefit — the queue-level heartbeat loop already has the reference.
- **Don't try to cancel the SDK subprocess via os.kill or signal handling.** `asyncio.Task.cancel()` propagates to `BackgroundTask._task`, which is already awaiting the SDK coroutine. The SDK client's own cleanup (via async context managers and `CancelledError` propagation) handles subprocess termination. Introducing signal handling would bypass this clean path and risk zombies.
- **Don't adjust `AGENT_SESSION_HEALTH_MIN_RUNNING`.** Raising it hides the problem briefly for slightly longer; the fix is the heartbeat signal. Keep at 300.
- **Don't scale the guard timeout by prompt size.** That was Open Question 3 in the issue — after spike-3, the heartbeat-based signal makes prompt-size scaling unnecessary. Large prompts simply keep the heartbeat fresh.
- **Don't audit the `Meta.ttl` deletion path.** After Fix 3, sessions always reach a terminal status within 2 recovery cycles (~10 minutes). The 30-day TTL backstop is irrelevant if sessions are already terminal before day 30. No change to `Meta.ttl`.

## Risks

### Risk 1: Heartbeat-write load on Redis
**Impact:** A 60-second write per live session creates Redis load. With 8 concurrent sessions (`MAX_CONCURRENT_SESSIONS = 8`), that's 8 writes per minute. Each write is a single-field partial update (~50 bytes), so load is negligible — but worth watching at higher concurrency caps.
**Mitigation:** Use `save(update_fields=["last_heartbeat_at"])` to minimize payload. Log ERROR if any save fails for >2 consecutive ticks — would be early-warning for Redis throughput issues.

### Risk 2: Task cancellation race with worker loop
**Impact:** `active_task.cancel()` raises `CancelledError` inside the worker loop's `await _execute_agent_session(session)`. The worker's existing `except asyncio.CancelledError` handler (agent/agent_session_queue.py:2617-2635) leaves the session in `running` and re-raises to exit the worker loop. If the health check's recovery transition runs right after, the `running` state is overwritten to `pending` or `failed` — correct outcome. But if the health check's transition runs BEFORE the `CancelledError` propagates, the worker's handler might try to save `running` over the already-transitioned state.
**Mitigation:** The health check cancels first, `awaits wait_for(active_task, timeout=1.0)` to allow propagation, then applies the transition. The `transition_status` function has CAS (check-and-set) semantics that detect stale state and raise — so even if the worker's handler runs late, the transition is atomic. Additionally, test `test_recovery_cancels_active_session_task` will confirm the serialization.

### Risk 3: `recovery_attempts` counter drift across restarts
**Impact:** If the worker restarts between two recovery events, `recovery_attempts` is persisted in Redis and survives the restart — good. But startup recovery (`_recover_interrupted_agent_sessions_startup`) also transitions `running → pending`. If those transitions also increment the counter, we double-count and prematurely fail sessions.
**Mitigation:** Only increment `recovery_attempts` in the `_agent_session_health_check` path, NOT in startup recovery. Startup recovery is a separate code path with its own semantics (worker crash, not session stall). Test `test_startup_recovery_does_not_increment_attempts` will enforce this.

### Risk 4: Session heartbeat gets stuck (paused task, not exited)
**Impact:** If `task._task` is alive but in a state where `_heartbeat_loop` is itself blocked (event loop starved, huge blocking call), the heartbeat field stops updating despite the session being "alive". The health check would then correctly kill it — but the user might perceive this as a false kill.
**Mitigation:** This is a correct outcome — a task that can't even write a 50-byte field every 60s is genuinely wedged, not "alive but slow". The heartbeat signal directly measures liveness of the session's own async machinery, which is exactly what the guard should measure. Document this in the feature doc.

## Race Conditions

### Race 1: Cancel-then-transition ordering
**Location:** `agent/agent_session_queue.py::_agent_session_health_check`, new recovery block (to be added near line 1738).
**Trigger:** Health check cancels `_active_sessions[id]`. Worker's `except CancelledError` handler runs first, calls `log_lifecycle_transition` on `running` state. Health check's `transition_status` runs next, transitioning `running → pending`.
**Data prerequisite:** `_active_sessions[id]` must still point to the live task when `.cancel()` is called; the task must be gone when `_execute_agent_session` pops it in its finally.
**State prerequisite:** `session.status` is `running` at the moment of cancel.
**Mitigation:** `asyncio.wait_for(active_task, timeout=1.0)` after cancel ensures the worker's `CancelledError` handler has a chance to run before `transition_status` is called. `transition_status` itself uses CAS and is atomic — it will either apply cleanly or raise `StaleSessionError`, both handled by the existing retry/logging pattern.

### Race 2: Heartbeat save vs. other field writes
**Location:** `_heartbeat_loop` writes `last_heartbeat_at`; other code paths in `_execute_agent_session` write `claude_session_uuid`, `log_path`, `turn_count`.
**Trigger:** Two concurrent writes in the same 60s window.
**Data prerequisite:** Each writer uses `save(update_fields=[...])` to limit to its own field — no cross-field clobbering.
**State prerequisite:** Popoto's partial-save must correctly handle single-field updates against the Redis hash.
**Mitigation:** `update_fields=["last_heartbeat_at"]` — Popoto only writes that field to the hash, preserving other fields. Confirmed pattern (same approach used at line 4136 for `updated_at`). No additional mitigation needed.

### Race 3: Registry cleanup vs. recovery lookup
**Location:** `_active_sessions.pop()` in `_execute_agent_session` finally block vs. `_active_sessions.get()` in health check.
**Trigger:** Session finishes naturally at the same moment health check fires.
**Data prerequisite:** Both operations are non-blocking dict operations in the same event loop.
**State prerequisite:** N/A — single-threaded cooperatively scheduled; dict ops are atomic.
**Mitigation:** The health check's `active_task.get()` returns `None` if the pop already happened. `None` check guards the `.cancel()` call. If `.get()` returns a task that completes between lookup and cancel, `.cancel()` on a done task is a no-op. Race is benign.

## No-Gos (Out of Scope)

- Scaling guard timeout by prompt size (Open Question 3 in the issue). Not needed with heartbeat signal.
- Modifying `agent/messenger.py`. Coupling increase without benefit.
- Observability dashboard for heartbeat lag. Future work if needed.
- Changing `AgentSession.Meta.ttl = 2592000`. Orthogonal; the fix makes non-terminal long-lived sessions impossible, so TTL is effectively irrelevant.
- Auditing `cleanup_corrupted_agent_sessions` for false positives. Reviewed in spike-2; it only deletes genuinely corrupted records.
- Per-prompt-size timeout tuning.
- Exposing `_active_sessions` as a debug endpoint. Internal only for this fix.

## Update System

No update system changes required — this fix is purely internal to `agent/agent_session_queue.py` and `models/agent_session.py`. The two new fields (`last_heartbeat_at`, `recovery_attempts`) default to null/0, so existing AgentSession records loaded from Redis continue to work. No migration, no config, no new dependency. The fix is deployed by the normal update flow: `git pull && scripts/valor-service.sh restart`.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. No new MCP tools, no `.mcp.json` changes, no new functions exposed to the agent. The fix changes how the health check decides to recover sessions; the agent itself is unaffected.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` with a new section "No-progress guard with heartbeat signal" describing the four-signal progress check and the orphan-reap flow.
- [ ] Update `docs/features/bridge-worker-architecture.md` to note the new `_active_sessions` registry and its role.
- [ ] Add entry to `docs/features/README.md` if no existing doc covers session health — otherwise update the existing entry.

### External Documentation Site
- None (repo does not publish external docs).

### Inline Documentation
- [ ] Update `_has_progress` docstring to document the heartbeat branch, its 90s window, and the rationale (SDK liveness > own-progress fields for long warmup prompts).
- [ ] Add docstring to `_active_sessions` explaining its single-writer/single-reader pattern and cleanup contract.
- [ ] Update `_agent_session_health_check` docstring to describe the recovery cancellation flow and `recovery_attempts` counter.
- [ ] Note in `AgentSession.last_heartbeat_at` field comment: "Written every 60s by `_execute_agent_session`'s heartbeat loop. Read by `_has_progress()` as the primary liveness signal."
- [ ] Note in `AgentSession.recovery_attempts` field comment: "Incremented by `_agent_session_health_check` on each `running → pending` recovery. At `>= 2`, session transitions to `failed`."

## Success Criteria

- [ ] A PM session with a 4000-character initial prompt that takes 5-7 minutes to first turn is NOT killed by the no-progress guard — `logs/worker.log` shows the SDK heartbeat continuing past 300s and no `[session-health] Recovering stuck session` line for that session until either first turn lands or the SDK actually hangs.
- [ ] When a session IS recovered by the no-progress guard, the SDK subprocess for that session is terminated within 60s — `logs/worker.log` shows one final `SDK heartbeat` line shortly after the recovery line, then none after.
- [ ] A session that is recovered twice and still makes no progress transitions to `failed` with full history — `valor-session status --id <id>` continues to work and shows `status: failed` with lifecycle entries for both recovery attempts.
- [ ] Regression test `test_recovery_cancels_active_session_task` passes.
- [ ] Regression test `test_second_recovery_finalizes_as_failed` passes.
- [ ] Integration test `tests/integration/test_session_heartbeat_progress.py` passes.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No existing tests regress — specifically, `test_health_check_recovery_finalization.py::TestHasProgressChildActivity` still passes verbatim.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (queue)**
  - Name: queue-builder
  - Role: Add fields to AgentSession, edit `_has_progress`, edit `_execute_agent_session` heartbeat loop, edit `_agent_session_health_check` recovery block.
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: tests-builder
  - Role: Add new test classes for heartbeat, recovery cancellation, and recovery attempts. Extend `test_health_check_recovery_finalization.py`. Add integration test.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (concurrency)**
  - Name: concurrency-validator
  - Role: Verify race conditions are mitigated; confirm `_active_sessions` cleanup contract; verify `transition_status` CAS behavior is correctly leveraged.
  - Agent Type: async-specialist
  - Resume: true

- **Documentarian**
  - Name: session-docs
  - Role: Update `docs/features/bridge-self-healing.md` and `docs/features/bridge-worker-architecture.md`; add docstrings to new fields and edited functions.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

- `builder` for Python changes in `agent/` and `models/`.
- `test-engineer` for new tests.
- `async-specialist` for concurrency review.
- `documentarian` for docs + docstrings.

## Step by Step Tasks

### 1. Add AgentSession fields
- **Task ID**: build-model-fields
- **Depends On**: none
- **Validates**: `python -c "from models.agent_session import AgentSession; s = AgentSession(chat_id='x'); assert hasattr(s, 'last_heartbeat_at') and hasattr(s, 'recovery_attempts')"`
- **Informed By**: spike-3 (confirmed: heartbeat written at queue layer, not messenger)
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `last_heartbeat_at = DatetimeField(null=True)` to `AgentSession`.
- Add `recovery_attempts = IntField(default=0)` to `AgentSession`.
- Add `"last_heartbeat_at"` to `_DATETIME_FIELDS` set.
- Add docstring notes per the Documentation section.

### 2. Add _active_sessions registry and heartbeat loop
- **Task ID**: build-heartbeat-registry
- **Depends On**: build-model-fields
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py -x`
- **Informed By**: spike-1 (confirmed: registry keyed by agent_session_id, not worker_key)
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_active_sessions: dict[str, asyncio.Task] = {}` near `_active_workers` at line 2124.
- Modify `_execute_agent_session` to register `asyncio.current_task()` at the top; pop in a `finally` block.
- Modify the existing `_heartbeat_loop` to write `last_heartbeat_at` every `HEARTBEAT_WRITE_INTERVAL = 60s`, gate the existing 25-min calendar work behind `elapsed % CALENDAR_HEARTBEAT_INTERVAL` check.
- Add `HEARTBEAT_WRITE_INTERVAL = 60` at module level.
- Wrap the save in try/except WARNING.

### 3. Extend _has_progress with heartbeat branch
- **Task ID**: build-has-progress
- **Depends On**: build-model-fields
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestHasProgressHeartbeat -x`
- **Informed By**: Research notes
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `HEARTBEAT_FRESHNESS_WINDOW = 90` at module level.
- At the top of `_has_progress`, add the heartbeat check. Keep all existing checks after it, in order.
- Update the docstring to document the new branch.

### 4. Extend health check with task cancellation and recovery counter
- **Task ID**: build-health-recovery
- **Depends On**: build-heartbeat-registry, build-has-progress
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestRecoveryCancellation tests/unit/test_health_check_recovery_finalization.py::TestRecoveryAttempts -x`
- **Informed By**: spike-1, spike-2
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `MAX_RECOVERY_ATTEMPTS = 2` at module level.
- In the recovery branch of `_agent_session_health_check`, before the existing `transition_status` call:
  - Look up `_active_sessions[entry.agent_session_id]`. If present and not done, cancel and `asyncio.wait_for(task, timeout=1.0)` with `except (asyncio.CancelledError, asyncio.TimeoutError): pass`.
  - Log at INFO if cancelled.
- Replace the unconditional `running → pending` transition with:
  - Increment `recovery_attempts`.
  - If `>= MAX_RECOVERY_ATTEMPTS`, `finalize_session(entry, "failed", ...)`.
  - Else, transition to `pending` (existing path).
- Do NOT modify the local-session `abandoned` branch or the `response_delivered_at` branch.
- Do NOT increment `recovery_attempts` in startup recovery (`_recover_interrupted_agent_sessions_startup`).

### 5. Add unit tests for heartbeat branch
- **Task ID**: build-tests-heartbeat
- **Depends On**: build-has-progress
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestHasProgressHeartbeat -x`
- **Informed By**: Failure Path Test Strategy
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestHasProgressHeartbeat` class with 4 tests per the Test Impact section.
- Ensure tests use `datetime.now(tz=UTC)` consistently.

### 6. Add unit tests for recovery cancellation
- **Task ID**: build-tests-cancellation
- **Depends On**: build-health-recovery
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestRecoveryCancellation -x`
- **Informed By**: Race 1, Race 3
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestRecoveryCancellation` class with 3 tests per the Test Impact section.
- Mock `_active_sessions` directly (module-level dict) and simulate task state.

### 7. Add unit tests for recovery attempts counter
- **Task ID**: build-tests-attempts
- **Depends On**: build-health-recovery
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestRecoveryAttempts -x`
- **Informed By**: Risk 3
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestRecoveryAttempts` class with 4 tests per the Test Impact section.
- Include `test_startup_recovery_does_not_increment_attempts` to enforce the boundary from Risk 3.

### 8. Add integration test
- **Task ID**: build-integration-test
- **Depends On**: build-health-recovery, build-heartbeat-registry
- **Validates**: `pytest tests/integration/test_session_heartbeat_progress.py -x`
- **Informed By**: Issue #1036 acceptance criteria
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_session_heartbeat_progress.py`.
- Test 1: session with stub SDK that delays first turn 6 minutes, heartbeat alive → NOT recovered.
- Test 2: session with heartbeat stoppage → recovered + task cancelled within one health cycle.
- Use in-process redis fakes (Popoto test harness) and asyncio time mocking where feasible.

### 9. Concurrency validation
- **Task ID**: validate-concurrency
- **Depends On**: build-tests-cancellation, build-tests-attempts
- **Assigned To**: concurrency-validator
- **Agent Type**: async-specialist
- **Parallel**: false
- Verify `_active_sessions` single-writer (`_execute_agent_session`) / single-reader (health check) is enforced — grep for any other writers.
- Verify `transition_status` CAS semantics handle the cancel-then-transition race (Race 1).
- Verify all `_has_progress` branches are short-circuited correctly (heartbeat-true skips subsequent checks, preserving #944/#963).
- Confirm `save(update_fields=["last_heartbeat_at"])` does not clobber other fields — verify against Popoto test harness.

### 10. Documentation updates
- **Task ID**: document-feature
- **Depends On**: validate-concurrency
- **Assigned To**: session-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with "No-progress guard with heartbeat signal" section.
- Update `docs/features/bridge-worker-architecture.md` with `_active_sessions` registry note.
- Update docstrings per Documentation section.
- Ensure `docs/features/README.md` index reflects any new or updated docs.

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature, build-integration-test
- **Assigned To**: concurrency-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_health_check_recovery_finalization.py tests/unit/test_stall_detection.py tests/unit/test_agent_session_hierarchy.py tests/integration/test_session_heartbeat_progress.py`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Confirm all Success Criteria checkboxes.
- Confirm all Failure Path Test Strategy items are covered.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_health_check_recovery_finalization.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_session_heartbeat_progress.py -x -q` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |
| `last_heartbeat_at` field present | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession(chat_id='x'), 'last_heartbeat_at')"` | exit code 0 |
| `recovery_attempts` field present | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession(chat_id='x'), 'recovery_attempts')"` | exit code 0 |
| `_active_sessions` registry exists | `python -c "from agent.agent_session_queue import _active_sessions; assert isinstance(_active_sessions, dict)"` | exit code 0 |
| `_has_progress` heartbeat branch | `grep -n 'last_heartbeat_at' agent/agent_session_queue.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

All three issue open questions were resolved during spikes:

1. **Why doesn't messenger.py currently write progress fields?** — spike-3: because `BackgroundTask._watchdog` doesn't have the `AgentSession` ORM reference. The queue-level `_heartbeat_loop` in `_execute_agent_session` does, so we write there instead. No change to messenger.py needed.

2. **What code path actually deletes session records that never reached terminal state?** — spike-2: the `Meta.ttl = 2592000` (30-day) Redis backstop. `cleanup_corrupted_agent_sessions` only deletes genuinely-corrupted records. `cmd_cleanup` only deletes terminal statuses. The 1036 symptom "gone by the next day" was likely an earlier cleanup tick combined with one of the cleanup paths — but the correct fix is to ensure sessions always reach a terminal status within ~10 minutes, so the TTL becomes irrelevant.

3. **Should the guard timeout scale with prompt size?** — spike-3 (and the heartbeat solution in general): No. The heartbeat signal directly measures liveness, making prompt-size heuristics unnecessary. Large prompts keep the heartbeat fresh just like small ones do.

No remaining open questions for the supervisor.
