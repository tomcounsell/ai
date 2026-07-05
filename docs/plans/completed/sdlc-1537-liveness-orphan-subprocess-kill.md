---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-03
tracking: https://github.com/tomcounsell/ai/issues/1537
last_comment_id:
revision_applied: false
---

# Liveness recovery must confirm subprocess death, not requeue an orphan (sdlc-1537)

## Problem

When the session-liveness check recovers a no-progress `running` session, the recovery
transition (`agent/session_health.py::_apply_recovery_transition`, the `else` branch at
~`agent/session_health.py:1153`) sets `started_at = None`, bumps priority, and requeues the
DB record to `pending` — after calling `handle.task.cancel()` with a `TASK_CANCEL_TIMEOUT`
of 0.25s (`agent/session_health.py:1091-1105`). It **never confirms the underlying
`claude -p` subprocess actually exited**. If the subprocess ignores cancellation (a true
hang), it becomes an orphan tracked by no detector and wedges the worker's execution slot.

The blind spot (confirmed by code-read spike during #1536 planning):

1. **Forward health check** queries only `status="running"` (`_filter_hydrated_sessions`,
   `agent/session_health.py:75`, used by the forward scan). After recovery the session is
   `pending`, so the forward scan never re-examines it.
2. **Recovery transition** nulls `started_at` and requeues to `pending`, then `task.cancel()`
   with a 0.25s timeout (`agent/session_health.py:1091-1105`). On timeout it logs and moves
   on — **no SIGTERM-to-PID fallback, no verification the process is gone**.
3. **In-process orphan reaper** only acts on sessions whose DB status is in
   `_TERMINAL_STATUSES` (`agent/session_health.py:1556`). A `pending` session is
   non-terminal → skipped.
4. **Cross-process reaper (#1271)** (`_reap_orphan_session_processes`,
   `agent/session_health.py:2420`) only reaps `claude` processes with `PPID==1` (worker
   died → launchd reparents). A live-parent orphan (worker still alive, PPID≠1) is out of
   scope.

Net: a hung subprocess at `pending` status with a live parent is invisible to every
detector until a human runs `./scripts/valor-service.sh worker-restart`. This was the root
cause of the 2026-05-31 incident (25.5h hang).

## Desired Outcome

Recovery verifies subprocess termination. After `task.cancel()` times out, escalate to
SIGTERM (then SIGKILL) against the recorded `claude_pid`; confirm exit before transitioning.
If the subprocess **cannot be confirmed dead**, the session escalates to `failed` (terminal)
so the orphan reaper owns cleanup — never silently to `pending`. A confirmed-dead subprocess
requeues to `pending` as today.

## Freshness Check

**Baseline commit:** head of `main` at build time (verify with `git rev-parse HEAD`).
**Disposition:** Unchanged — re-verify the file:line anchors below against current `main`
before editing; line numbers may have drifted under recent `agent/session_health.py` churn.

**File:line anchors (verify, do not trust blindly):**
- `agent/session_health.py:946` — `async def _apply_recovery_transition(...)`.
- `agent/session_health.py:1091-1105` — `handle.task.cancel()` + `wait_for(TASK_CANCEL_TIMEOUT)`; the escalation hook goes here.
- `agent/session_health.py:1153` — the `else` branch that requeues to `pending` (sets `started_at = None`, `priority = "high"`).
- `agent/session_health.py:219` — `TASK_CANCEL_TIMEOUT = 0.25`.
- `agent/session_health.py:2402` — `_increment_orphan_process_counter` (observability pattern to mirror).
- `agent/session_health.py:2420` — `_reap_orphan_session_processes` (PPID==1 cross-process reaper).
- `AgentSession.find_by_claude_pid(pid)` — used at `agent/session_health.py:2544`; the lookup to map the recorded PID back to a session if needed. `entry.claude_pid` holds the recorded subprocess PID.

## Prior Art

- **#1271** — cross-process orphan reaper (PPID==1). This plan complements it for the
  live-parent case, reusing its kill helpers if present.
- **#1226, #1356, #1172** — prior liveness/reaper work this gap slips between.
- **#1536** — telemetry epic; this bug motivates the recorder's `status_transition` event.
  Out of scope here except to emit minimal observability counters.

## Solution

A focused change to `_apply_recovery_transition`, confined to `agent/session_health.py`:

1. **Add a subprocess-kill escalation helper** (module-level), e.g.
   `_confirm_subprocess_dead(pid: int | None, *, timeout: float) -> bool`:
   - If `pid` is `None` or `pid <= 0`, return `True` (nothing to kill).
   - `os.kill(pid, 0)` to check liveness; if already gone, return `True`.
   - `os.kill(pid, SIGTERM)`, poll for exit up to a short grace (e.g. 2s); if still alive,
     `os.kill(pid, SIGKILL)`, poll again briefly.
   - Return `True` only if the PID is confirmed gone (`os.kill(pid, 0)` raises
     `ProcessLookupError`); else `False`. Guard every `os.kill` in `try/except`
     (`ProcessLookupError` → dead/good; `PermissionError`/other → treat as not-confirmed).
   - Reuse any existing kill helper in `agent/session_health.py` (e.g. logic already used by
     the PPID==1 reaper) rather than duplicating signal handling.

2. **Wire it into the cancel path** (after `agent/session_health.py:1091-1105`): once
   `task.cancel()` has been awaited (or timed out), call `_confirm_subprocess_dead(
   entry.claude_pid, ...)`. Capture the boolean.

3. **Branch on the result before the requeue (`else` at ~1153):**
   - If confirmed dead → proceed with the existing requeue-to-`pending` behavior.
   - If **not** confirmed dead → `finalize_session(entry, "failed", reason="health check:
     subprocess <pid> survived cancel+SIGTERM+SIGKILL; escalating to failed so the orphan
     reaper owns cleanup")`. Do **not** null `started_at` into `pending`.

4. **Observability:** increment a counter for each path
   (`session-health:subprocess_kill_escalated`, `:subprocess_kill_failed`) mirroring
   `_increment_orphan_process_counter` (`agent/session_health.py:2402`). Best-effort,
   wrapped in `try/except`.

No `AgentSession` schema change. No bridge/worker/nudge change. The `failed` terminal status
is already owned by the in-process reaper (`_TERMINAL_STATUSES`).

## Data Flow

```
Liveness check finds no-progress running session
  -> _apply_recovery_transition (agent/session_health.py:946)
      -> handle.task.cancel(); wait_for(TASK_CANCEL_TIMEOUT)        [existing]
      -> [NEW] dead = _confirm_subprocess_dead(entry.claude_pid)
            SIGTERM -> poll -> SIGKILL -> poll
      -> branch:
           local           -> abandoned        [existing]
           attempts >= MAX -> failed            [existing]
           else:
             [NEW] if not dead -> finalize_session(failed)  (reaper owns it)
                   if dead     -> requeue pending (started_at=None, priority=high) [existing]
```

## Race Condition Analysis

- `os.kill(pid, 0)` between SIGTERM and the exit-poll can race with natural exit — handled by
  treating `ProcessLookupError` as "confirmed dead" at every check.
- PID reuse: a recorded `claude_pid` could in principle be reused by an unrelated process
  before recovery runs. Mitigation: only escalate kills when the session record still
  references that PID and the session is the no-progress one being recovered; the window is
  the few hundred ms of the recovery path. Accept the residual risk (matches the existing
  PPID==1 reaper's assumptions) and note it in a comment.
- Concurrent recovery of the same session is already serialized by the liveness loop; no new
  shared mutable state is introduced beyond best-effort Redis counters (independent keys).

## Step by Step Tasks

- [ ] Re-verify all Freshness Check anchors against current `main`; correct any drifted line numbers in this plan's references as you go.
- [ ] Add `_confirm_subprocess_dead(pid, *, timeout)` helper near the other process helpers in `agent/session_health.py`, reusing existing SIGTERM/SIGKILL logic if present.
- [ ] Call it after the `task.cancel()` await block (~`agent/session_health.py:1105`); store the boolean.
- [ ] In the `else` requeue branch (~`agent/session_health.py:1153`), finalize to `failed` when the subprocess is not confirmed dead; otherwise keep the existing requeue-to-`pending`.
- [ ] Add best-effort Redis counters for escalated / failed-to-kill paths, mirroring `_increment_orphan_process_counter`.
- [ ] Add unit tests in `tests/unit/test_session_health_subprocess_kill.py` (see Failure Path Test Strategy).
- [ ] `python -m ruff format . && python -m ruff check agent/session_health.py`.

## Success Criteria

- A no-progress `running` session whose `claude -p` subprocess ignores cancellation is
  escalated to `failed` (terminal), not requeued to `pending` — verified by unit test with a
  mocked `os.kill` that keeps the PID alive through SIGTERM and SIGKILL.
- A session whose subprocess is confirmed dead after cancel/SIGTERM still requeues to
  `pending` as before — no regression to the healthy-recovery path.
- SIGKILL is only sent when SIGTERM fails to terminate the PID within the grace window.
- Observability counters increment on the escalated and failed-to-kill paths; a counter
  backend failure never propagates out of recovery.
- `ruff check agent/session_health.py` clean; new + existing `test_session_health*` unit
  tests pass.
- No worker slot remains wedged by a `pending` orphan after recovery (the original #1537
  incident class cannot recur via this path).

## Failure Path Test Strategy

- **Subprocess survives cancel → escalates to failed:** monkeypatch `os.kill` so the PID
  stays "alive" through SIGTERM and SIGKILL; assert `_apply_recovery_transition` calls
  `finalize_session(..., "failed", ...)` and does **not** requeue to `pending`
  (`started_at` is not nulled into a pending record).
- **Subprocess confirmed dead → normal requeue:** monkeypatch `os.kill` so the PID is gone
  after SIGTERM (`ProcessLookupError`); assert the existing requeue-to-`pending` path runs.
- **No PID recorded:** `entry.claude_pid is None` → `_confirm_subprocess_dead` returns `True`
  immediately; existing behavior unchanged.
- **SIGTERM suffices (no SIGKILL needed):** PID dies after SIGTERM poll; assert SIGKILL is
  not sent (call-count on the mocked `os.kill`).
- **PermissionError on kill:** `os.kill` raises `PermissionError`; treated as not-confirmed →
  escalate to `failed`.
- **Counters:** assert the escalated/failed counters increment on their respective paths and
  that a counter failure (Redis raising) never propagates.

## Test Impact

- [ ] `tests/unit/test_session_health*.py` — UPDATE if any existing test asserts the recovery
      `else` branch always requeues to `pending`; such a test must now account for the
      not-confirmed-dead → `failed` branch. Audit with
      `grep -rn "_apply_recovery_transition\|recovery_attempts\|requeue" tests/`.
- [ ] `tests/unit/test_session_health_subprocess_kill.py` — NEW: all Failure Path scenarios.
- [ ] If no existing test exercises `_apply_recovery_transition`'s requeue branch, state that
      and rely on the new test file for coverage of both branches.

## Documentation

- [ ] Update `docs/features/session-lifecycle.md` (and/or `docs/features/session-health-check.md`
      if present) with a subsection: "Recovery confirms subprocess death — after task
      cancellation, the recorded `claude_pid` is SIGTERM/SIGKILL-escalated; a subprocess that
      cannot be confirmed dead escalates the session to `failed` (terminal) so the orphan
      reaper owns cleanup, rather than requeuing an invisible orphan to `pending`."
- [ ] No new docs file required — this is an addition to existing liveness/recovery machinery.

## Update System

No update system changes required. The change is internal to `agent/session_health.py`; no
new dependencies, config, or migrations. The next worker restart on each machine picks up the
new recovery behavior automatically.

## Agent Integration

No agent integration changes required. Recovery runs inside the worker's liveness loop, not
via any Telegram or CLI surface. No new entry point; no bridge import changes.

## No-Gos

- Do NOT add a new field to `AgentSession`. Use the existing `failed` terminal status and
  `claude_pid`.
- Do NOT modify the bridge, worker main loop, or nudge loop.
- Do NOT widen the PPID==1 cross-process reaper's scope here — the fix is at the recovery
  site, where the live PID is known. (A broader live-parent reaper is a separate concern.)
- Do NOT block the recovery path on a long kill timeout — keep the SIGTERM/SIGKILL grace
  short (single-digit seconds total) so the liveness loop is not stalled.
- Do NOT silently requeue to `pending` when the subprocess is not confirmed dead. That is the
  exact defect being fixed.

## Rabbit Holes

- **A general live-parent orphan reaper.** Tempting to extend the cross-process reaper to
  detached live-parent subprocesses. Out of scope — the recovery site already has the PID;
  fix it there.
- **Full `status_transition` telemetry recorder (#1536).** Emit only minimal counters here;
  the recorder is its own epic.
- **PID-reuse hardening beyond a comment.** The window is sub-second and matches existing
  reaper assumptions. Don't build PID-generation tracking.
- **Tuning `TASK_CANCEL_TIMEOUT`.** Leave the 0.25s cancel timeout as-is; the fix is the
  post-cancel kill escalation, not the cancel timeout.
