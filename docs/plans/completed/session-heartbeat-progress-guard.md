---
status: docs_complete
type: bug
appetite: Medium
owner: Valor
created: 2026-04-18
tracking: https://github.com/valorengels/ai/issues/1036
last_comment_id:
revision_applied: true
revision_applied_at: 2026-04-18
revision_summary: Adopted two-tier false-negative-minimizing health detector (dual heartbeat + activity-positive reprieve gates) per /do-plan-critique concerns.
allow_unchecked: true
allow_unchecked_reason: Remaining unchecked items are sub-bullets under Test Impact, Success Criteria, and Inline Documentation sections whose parent stages (BUILD, TEST, DOCS) are all complete. All 15 Step-by-Step tasks are done, 56+ new tests pass, docs cascaded in commit 13b85f4a, and the kill-path correctness fix is in 10daf7b0. Final approval in PR #1039 review #issuecomment-4273058533.
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

**Design priority (from critique revision 2026-04-18):** Minimize **false-negatives** (killing a working session) above all else. A single queue-layer heartbeat does not cover every liveness failure mode — in particular, the "SDK alive but queue heartbeat loop wedged" case where the event loop is starved but tool execution is still progressing. The solution must therefore combine a **dual heartbeat** (two independent liveness writers must BOTH go stale before flagging stuck) with **activity-positive reprieve gates** (even after flagging, a positive signal from any of process-alive / has-children / recent-stdout reprieves the kill). A false-positive (leaving a genuinely dead session to be reaped on the next tick) is strictly preferable to a false-negative.

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

**Revision research (2026-04-18):** Revisited `psutil` availability and process-introspection semantics for Tier 2 gates. `psutil` is already present in the repo's `uv.lock` (used by `tools/doctor.py` and `monitoring/`); no new dependency. `psutil.Process(pid).status()` returns one of `{running, sleeping, disk-sleep, stopped, tracing-stop, zombie, dead, ...}` — `zombie`/`dead`/`stopped` are the "not actually alive" states. `psutil.Process(pid).children()` returns immediate children; for our purpose (proving tool execution is happening), immediate children are sufficient — SDK-spawned subprocess → tool subprocesses are direct children. `os.kill(pid, 0)` is a cheaper fallback that only tells us whether the pid exists (doesn't distinguish zombie). Decision: use `psutil.Process(pid)` with a `try/except psutil.NoSuchProcess` wrapper; fall back to `os.kill(pid, 0)` only if `psutil.Process()` itself raises.

## Spike Results

Three spikes resolved assumptions that shaped the design:

### spike-1: Where is the SDK subprocess tracked so we can cancel it?
- **Assumption**: "`_active_workers[worker_key]` points to a task we can cancel per session."
- **Method**: code-read
- **Finding**: `_active_workers` maps `worker_key → asyncio.Task` for the **entire worker loop** (`_worker_loop()`). A worker loop can serially handle many sessions over its lifetime. Cancelling the worker task would tear down the loop and trigger startup recovery — heavy-handed and also racy with the health check's own re-queue. The closer handle is `BackgroundTask._task` inside `_execute_agent_session()` (agent/agent_session_queue.py:4119-4120, agent/messenger.py:98-145). But that `BackgroundTask` is local to `_execute_agent_session`; there is no registry mapping `agent_session_id → BackgroundTask`. **Design implication**: introduce a registry `_active_sessions: dict[str, SessionHandle]` keyed by `agent_session_id`. `SessionHandle` is a small dataclass holding both the session-execution `asyncio.Task` AND the SDK subprocess `pid` (obtained from `BackgroundTask._proc.pid` once the SDK subprocess is spawned). The health check cancels by looking up the id; the pid is used by Tier 2 reprieve gates for process-alive / has-children checks.
- **Confidence**: high
- **Impact on plan**: Technical Approach now specifies `_active_sessions: dict[str, SessionHandle]` (set on entry to `_execute_agent_session`, updated with pid once SDK starts, popped in `finally`). Recovery path: if Tier 1 flags stuck, query Tier 2 gates using `handle.pid`; if all reprieves fail, call `handle.task.cancel()`, which propagates `CancelledError` into `BackgroundTask._task` and terminates the subprocess via the SDK client's own cancellation handling. The `asyncio.wait_for` timeout for task cancellation is **0.25s** (not 1.0s) — SDK client's `__aexit__` completes near-instantly once `CancelledError` propagates, and a shorter wait tightens the health-check tick budget.

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
- **Finding**: The `BackgroundTask._watchdog()` method has access to `self.messenger.session_id` but NOT to the `AgentSession` ORM object. The `AgentSession` is scoped to `_execute_agent_session()` (as `agent_session` local) and is used for the `updated_at` heartbeat inside `_heartbeat_loop()` at the queue level (agent/agent_session_queue.py:4124-4142). `_heartbeat_loop` already has the `agent_session` reference and already writes `updated_at` every 25 minutes; we can add a `last_heartbeat_at` field write that fires on a shorter 60s interval.
- **Confidence**: high
- **Impact on plan (revised 2026-04-18)**: We now adopt a **dual-heartbeat** design. The queue-layer heartbeat in `_heartbeat_loop` writes `last_heartbeat_at` every 60s (as originally planned). In addition, `BackgroundTask._watchdog` — which already ticks every 60s and logs the "SDK heartbeat: running Ns, communicated=..." line — is plumbed with a **callback**, not the full AgentSession object. The callback signature is `(agent_session_id: str) -> None` and is threaded into the `BackgroundTask` / `Messenger` constructor at session start. On each watchdog tick, the callback bumps `last_sdk_heartbeat_at` (a separate field). This keeps `messenger.py` decoupled from the ORM — the messenger module imports nothing from `models/` — while still surfacing its existing liveness signal into the guard. Kill-trigger requires BOTH `last_heartbeat_at` AND `last_sdk_heartbeat_at` to be stale beyond 90s, so a single-writer failure (e.g. event loop starved such that the queue heartbeat loop skips a beat) cannot falsely flag a session.

### spike-4 (new, revision 2026-04-18): Is `psutil` available and does `BackgroundTask` expose the SDK pid?
- **Assumption**: "We can use `psutil` for process-alive / has-children checks without a new dependency, and `BackgroundTask` exposes the SDK subprocess pid."
- **Method**: code-read
- **Finding**: `psutil` is already a transitive dependency via `tools/doctor.py` and `monitoring/bridge_watchdog.py`; no new dep required. For the pid: `BackgroundTask` wraps an `asyncio.create_subprocess_exec` call in `agent/messenger.py`; the `Process` object has a `.pid` attribute that's populated as soon as the subprocess is spawned. Exposing this requires a small addition to `BackgroundTask` — a public `pid` property (or setter) that the queue can read once `start()` returns. Alternatively, the messenger can pass the pid to the same callback used for Tier 2 heartbeat plumbing — `(agent_session_id, pid) -> None` called once at SDK subprocess-start, then `(agent_session_id) -> None` on each heartbeat tick. Choosing the latter for cleanliness.
- **Confidence**: high
- **Impact on plan**: Tier 2 reprieve gates are implementable without a new dependency. `SessionHandle.pid` is populated via a one-shot "SDK-started" callback from the messenger, then Tier 2 uses `psutil.Process(pid)` with a `try/except (psutil.NoSuchProcess, psutil.AccessDenied)` wrapper.

## Data Flow

End-to-end timing of the fix, from prompt arrival to health check decision (revised for two-tier detector):

1. **Entry point**: A session is picked up from the pending queue by `_worker_loop`, enters `_execute_agent_session(session)` (agent/agent_session_queue.py:3491).
2. **Registry registration (T+0, before any raise sites)**: `_execute_agent_session` creates `handle = SessionHandle(task=asyncio.current_task(), pid=None)` and sets `_active_sessions[session.agent_session_id] = handle`. Registered in a try/finally pair so the entry is always popped on exit. **Registration must precede every raise site** — any exception raised before registration means the health check has no handle to cancel.
3. **T+0 heartbeat write**: Immediately after registration, write `agent_session.last_heartbeat_at = datetime.now(tz=UTC)` and save with `update_fields=["last_heartbeat_at"]`. This ensures the very first health-check tick after session creation sees a fresh heartbeat, even before the 60s heartbeat loop has had a chance to run.
4. **BackgroundTask start with callbacks**: `_execute_agent_session` builds the `BackgroundTask`/`Messenger` with two callbacks:
   - `on_sdk_started(pid)` — one-shot, called by the messenger once the SDK subprocess is spawned. Stores pid into `_active_sessions[id].pid` and updates `agent_session.last_sdk_heartbeat_at` to now.
   - `on_heartbeat_tick()` — called on each 60s `_watchdog` tick. Updates `agent_session.last_sdk_heartbeat_at = now()` via `save(update_fields=["last_sdk_heartbeat_at"])`.
   - `on_stdout_event()` — called by the messenger whenever a stdout event flows from the SDK (existing plumbing just gains a callback invocation). Updates `agent_session.last_stdout_at = now()` via `save(update_fields=["last_stdout_at"])`.
5. **Every 60s (queue-layer heartbeat)**: `_heartbeat_loop` writes `agent_session.last_heartbeat_at` with `save(update_fields=["last_heartbeat_at"])`. Wrapped in try/except that logs WARNING on failure and continues.
6. **Every 60s (messenger heartbeat)**: `BackgroundTask._watchdog` fires `on_heartbeat_tick()`, writing `agent_session.last_sdk_heartbeat_at`.
7. **Every 25 minutes (existing, slow interval)**: The existing calendar heartbeat + `updated_at` write continues unchanged.
8. **Health check every 300s — Tier 1 evaluation**: `_agent_session_health_check` reads both `entry.last_heartbeat_at` AND `entry.last_sdk_heartbeat_at`. `_has_progress()` returns True if **either** heartbeat is within the last 90s. The kill-trigger (Tier 1 "flagged stuck") requires **BOTH** heartbeats to be stale beyond 90s.
9. **Tier 1 fresh on either signal**: The guard does NOT fire. Session continues. First turn eventually lands (minutes 5-7). `claude_session_uuid` then gets populated, at which point the heartbeat fields become redundant — any signal is sufficient.
10. **Tier 1 flags stuck (both heartbeats stale) — Tier 2 reprieve evaluation**: Before any kill, consult activity-positive gates using `handle.pid` from `_active_sessions`:
    - (c) `psutil.Process(pid).status() not in {zombie, dead, stopped}` — proves process exists and is not a zombie.
    - (d) `psutil.Process(pid).children()` is non-empty — proves tool-subprocess execution is happening right now.
    - (e) `entry.last_stdout_at >= now() - 90s` — proves the SDK recently emitted stdout.

    Any ONE of (c-alive) OR (d) OR (e) being true → **reprieve**: increment `reprieve_count`, log the reprieve (which signal), do NOT kill, do NOT increment `recovery_attempts`. The session gets another full tick to prove liveness.
11. **All Tier 2 reprieves fail**: Session is genuinely wedged. `DISABLE_PROGRESS_KILL=1` env var, if set, skips the kill transition entirely (but still logs the flag). Otherwise, the recovery path:
    - Looks up `handle.task` and cancels it if not done.
    - `asyncio.wait_for(handle.task, timeout=0.25s)` with `except (asyncio.CancelledError, asyncio.TimeoutError): pass`.
    - Catches `StatusConflictError` around the transition-status call so a race with the worker's own CancelledError handler doesn't crash the health check.
12. **Recovery counter**: A `running → pending` transition (kill path only, never on reprieve, never on worker restart) increments `agent_session.recovery_attempts`. On reaching `MAX_RECOVERY_ATTEMPTS = 2`, the health check transitions to `failed` instead of `pending`, preserving the record in a terminal status.
13. **Output**: Sessions that never make progress end up `failed` with `log_lifecycle_transition` entries describing each recovery attempt — fully auditable. Counters (`tier1_flagged_total`, `tier2_reprieve_total`, `kill_total`) provide operational visibility.

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

- **New dependencies**: None. `psutil` is already a transitive dependency (via `tools/doctor.py` and `monitoring/bridge_watchdog.py`). Four new fields are added to `AgentSession`; all default null/0.
- **Interface changes**:
  - `AgentSession` gains **four** fields:
    - `last_heartbeat_at: DatetimeField(null=True)` (queue-layer heartbeat)
    - `last_sdk_heartbeat_at: DatetimeField(null=True)` (messenger-sourced heartbeat)
    - `last_stdout_at: DatetimeField(null=True)` (messenger-sourced, updated on stdout events)
    - `recovery_attempts: IntField(default=0)` (health-check kill counter)
    - Optional observability: `reprieve_count: IntField(default=0)` (Tier 2 saves — useful for counting how often the two-tier detector is earning its keep)
  - All five fields MUST be added to `_AGENT_SESSION_FIELDS` so they round-trip through save/load (B2 from prior critique).
  - `_has_progress()` considers heartbeat freshness on **either** heartbeat field (logical OR).
  - `_execute_agent_session` gains registration in a new `_active_sessions: dict[str, SessionHandle]` registry, a T+0 heartbeat write, and one additional `save(update_fields=["last_heartbeat_at"])` per 60s in the heartbeat loop.
  - `agent/messenger.py::BackgroundTask`/`Messenger` gains three **optional callbacks** passed via constructor kwargs: `on_sdk_started(pid)`, `on_heartbeat_tick()`, `on_stdout_event()`. All default to None — if not provided, messenger behavior is identical to today. The queue injects callbacks that bump per-session ORM fields; the messenger module itself imports nothing from `models/`.
- **Coupling**: Contained. `agent_session_queue.py` owns all ORM writes. `agent/messenger.py` gains three no-op-by-default callback hooks but retains zero ORM imports. The health check gains read access to `_active_sessions` for Tier 2 gates.
- **Data ownership**:
  - `last_heartbeat_at`: single writer, the queue-layer `_heartbeat_loop` inside `_execute_agent_session`.
  - `last_sdk_heartbeat_at`: single writer, the `on_heartbeat_tick()` callback invoked from messenger's `_watchdog`.
  - `last_stdout_at`: single writer, the `on_stdout_event()` callback invoked from messenger's stdout pump.
  - `recovery_attempts`, `reprieve_count`: single writer, the health check.
  - No multi-writer concerns. `update_fields=[...]` is used consistently so each write touches only its own field.
- **Reversibility**: Fully reversible. All new fields are optional (null=True / default=0). Callbacks default to None. Reverting requires removing the field defs, heartbeat writes, `_has_progress` branches, the two-tier recovery block, and the three messenger callback hooks. The `DISABLE_PROGRESS_KILL=1` env var provides a runtime kill-switch for operators to disable the kill action entirely while keeping the flagging + logging active — useful during rollout.

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

### Key Elements (Two-Tier Architecture)

**Tier 1 — Dual heartbeat (cheap, checked every tick):**
- **(a) Queue-layer heartbeat (`last_heartbeat_at`)**: Written every 60s by `_heartbeat_loop` inside `_execute_agent_session`.
- **(b) Messenger heartbeat (`last_sdk_heartbeat_at`)**: Written every 60s by an `on_heartbeat_tick()` callback plumbed into `BackgroundTask._watchdog`.
- **Freshness rule**: `_has_progress()` returns True if **either** heartbeat is within the last `HEARTBEAT_FRESHNESS_WINDOW = 90s`.
- **Kill-trigger**: BOTH heartbeats must be stale beyond 90s to flag a session as Tier-1-stuck.

**Tier 2 — Activity-positive reprieve gates (only evaluated when Tier 1 flags stuck):**
- **(c) Process alive**: `psutil.Process(pid).status()` not in `{zombie, dead, stopped}`.
- **(d) Has children**: `psutil.Process(pid).children()` is non-empty (proves tool execution).
- **(e) Recent stdout**: `entry.last_stdout_at >= now() - 90s`.
- **Reprieve rule**: ANY ONE of (c), (d), or (e) being true → do NOT kill. Increment `reprieve_count`, log the reprieve (tagged with which signal), leave session running.

**Supporting elements:**
- **`SessionHandle` dataclass + `_active_sessions` registry**: `dict[str, SessionHandle]` where `SessionHandle(task: asyncio.Task, pid: int | None)`. Populated at the top of `_execute_agent_session` (task immediately, pid once SDK subprocess spawns via `on_sdk_started` callback), removed in its `finally`.
- **Messenger callbacks**: Three optional kwargs on `BackgroundTask`/`Messenger` — `on_sdk_started(pid)`, `on_heartbeat_tick()`, `on_stdout_event()` — all default None. Messenger imports nothing from `models/`; the queue wires callbacks that bump ORM fields.
- **Recovery task cancellation**: In the kill path, look up `_active_sessions[agent_session_id].task`; if present and not done, `.cancel()` it. `asyncio.wait_for(task, timeout=0.25)` with `except (asyncio.CancelledError, asyncio.TimeoutError): pass`. Catch `StatusConflictError` around the subsequent `transition_status` call to handle races with the worker's own CancelledError handler.
- **`recovery_attempts` counter**: Incremented ONLY on actual kills (Tier 1 AND Tier 2 both say stuck), never on reprieves, never on worker restart / startup recovery. At `>= MAX_RECOVERY_ATTEMPTS = 2`, transition to `failed` instead of `pending`.
- **Kill-switch**: `DISABLE_PROGRESS_KILL=1` env var — when set, skips the kill transition entirely but keeps Tier 1 flagging + logging active. Operators can deploy with this flag during rollout to collect data before enabling kills.

### Flow

Session start (T+0) → register `SessionHandle(task, pid=None)` → write `last_heartbeat_at` → launch `BackgroundTask` with callbacks → SDK spawns → `on_sdk_started(pid)` fires → `_active_sessions[id].pid = pid` → messenger ticks 60s → `on_heartbeat_tick()` bumps `last_sdk_heartbeat_at` → queue ticks 60s → `_heartbeat_loop` bumps `last_heartbeat_at` → health check ticks 300s → `_has_progress()` returns True if either heartbeat < 90s old → session continues.

If both heartbeats stale: Tier 1 flags stuck → Tier 2 checks (c)(d)(e) using `handle.pid` → if any positive, reprieve and log → if all negative and `DISABLE_PROGRESS_KILL` is not set, cancel `handle.task`, wait 0.25s, increment `recovery_attempts`, transition `running → pending` (or `running → failed` if `recovery_attempts >= 2`) inside a `try/except StatusConflictError`.

### Technical Approach

Six localized edits plus tests:

1. **`models/agent_session.py`** (add four + optional one field):
   - `last_heartbeat_at = DatetimeField(null=True)`
   - `last_sdk_heartbeat_at = DatetimeField(null=True)`
   - `last_stdout_at = DatetimeField(null=True)`
   - `recovery_attempts = IntField(default=0)`
   - `reprieve_count = IntField(default=0)` (optional observability)
   - Add all three datetime fields to `_DATETIME_FIELDS` set so existing type-coercion handles them.
   - **Critical (B2):** Add all five fields to `_AGENT_SESSION_FIELDS` so they round-trip through save/load. Omitting this is a silent data-loss bug.

2. **`agent/messenger.py::BackgroundTask`/`Messenger`** (add optional callbacks — messenger stays ORM-free):
   - Add three optional kwargs: `on_sdk_started: Callable[[int], None] | None = None`, `on_heartbeat_tick: Callable[[], None] | None = None`, `on_stdout_event: Callable[[], None] | None = None`.
   - After the SDK subprocess is spawned and `self._proc.pid` is populated, if `on_sdk_started` is not None, call `on_sdk_started(self._proc.pid)` once. Any exception in the callback is caught and logged at WARNING so messenger resilience is not affected.
   - Inside `_watchdog`, after the existing heartbeat log line, if `on_heartbeat_tick` is not None, call it. Same try/except WARNING wrapping.
   - In the stdout event pump (wherever the messenger currently processes SDK events), if `on_stdout_event` is not None, call it on each event. Same try/except WARNING wrapping.
   - **No imports from `models/`.** The messenger remains decoupled from the ORM.

3. **`agent/agent_session_queue.py` — add `SessionHandle` + `_active_sessions` registry**:
   - Add `@dataclass class SessionHandle: task: asyncio.Task; pid: int | None = None` at module level.
   - Add `_active_sessions: dict[str, SessionHandle] = {}` near `_active_workers` at line 2124.
   - Inline docstring documenting single-writer (`_execute_agent_session`) / multi-reader (health check + Tier 2 gates) pattern and cleanup contract.

4. **`agent/agent_session_queue.py::_execute_agent_session`** (registration + T+0 write + callback wiring + heartbeat loop):
   - **Before any raise site**: register `_active_sessions[session.agent_session_id] = SessionHandle(task=asyncio.current_task())`. Wrap the entire body in try/finally; the `finally` pops the registry entry. This ordering is NON-NEGOTIABLE — any exception raised before registration leaves the health check with no handle.
   - **T+0 heartbeat write**: Immediately after registration, write `session.last_heartbeat_at = datetime.now(tz=UTC); session.save(update_fields=["last_heartbeat_at"])`. Wrapped in try/except WARNING.
   - **Build callbacks**:
     ```python
     def _on_sdk_started(pid: int) -> None:
         handle = _active_sessions.get(session.agent_session_id)
         if handle is not None:
             handle.pid = pid
         try:
             session.last_sdk_heartbeat_at = datetime.now(tz=UTC)
             session.save(update_fields=["last_sdk_heartbeat_at"])
         except Exception as e:
             logger.warning("on_sdk_started save failed: %s", e)

     def _on_heartbeat_tick() -> None:
         try:
             session.last_sdk_heartbeat_at = datetime.now(tz=UTC)
             session.save(update_fields=["last_sdk_heartbeat_at"])
         except Exception as e:
             logger.warning("on_heartbeat_tick save failed: %s", e)

     def _on_stdout_event() -> None:
         try:
             session.last_stdout_at = datetime.now(tz=UTC)
             session.save(update_fields=["last_stdout_at"])
         except Exception as e:
             logger.warning("on_stdout_event save failed: %s", e)
     ```
     Pass into `BackgroundTask(...)` / `Messenger(...)` constructor.
   - **Heartbeat loop (60s queue-layer writes)**: Modify `_heartbeat_loop` to write `last_heartbeat_at` every `HEARTBEAT_WRITE_INTERVAL = 60s`. Gate the existing 25-minute calendar work with `elapsed % CALENDAR_HEARTBEAT_INTERVAL < HEARTBEAT_WRITE_INTERVAL`. Use `save(update_fields=["last_heartbeat_at"])`. Wrap in try/except WARNING.

5. **`agent/agent_session_queue.py::_has_progress`** (dual-heartbeat OR check):
   - Prepend to the existing body (before `turn_count`):
     ```python
     now_utc = datetime.now(tz=UTC)
     for hb_attr in ("last_heartbeat_at", "last_sdk_heartbeat_at"):
         hb = getattr(entry, hb_attr, None)
         if hb is not None:
             age_s = (now_utc - hb).total_seconds()
             if age_s < HEARTBEAT_FRESHNESS_WINDOW:  # 90
                 return True
     ```
   - `HEARTBEAT_FRESHNESS_WINDOW = 90` at module level.
   - Keep every other existing check in `_has_progress` unchanged — preserves #944/#963 invariants.

6. **`agent/agent_session_queue.py::_agent_session_health_check`** (two-tier recovery path):
   - Add `MAX_RECOVERY_ATTEMPTS = 2`, `TASK_CANCEL_TIMEOUT = 0.25`, `STDOUT_FRESHNESS_WINDOW = 90` at module level.
   - Add helper `_tier2_reprieve_signal(handle: SessionHandle | None, entry: AgentSession) -> str | None` that returns the name of the first passing reprieve gate (`"alive"`, `"children"`, `"stdout"`) or None if none pass. Implementation:
     ```python
     def _tier2_reprieve_signal(handle, entry):
         pid = handle.pid if handle else None
         if pid is not None:
             try:
                 import psutil
                 proc = psutil.Process(pid)
                 status = proc.status()
                 if status not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD, psutil.STATUS_STOPPED):
                     # (c) alive and not zombie
                     # (d) has children
                     if proc.children():
                         return "children"
                     return "alive"
             except (psutil.NoSuchProcess, psutil.AccessDenied, ImportError):
                 pass
         # (e) recent stdout
         lso = getattr(entry, "last_stdout_at", None)
         if lso is not None:
             age = (datetime.now(tz=UTC) - lso).total_seconds()
             if age < STDOUT_FRESHNESS_WINDOW:
                 return "stdout"
         return None
     ```
     Note: check (c) returns `"alive"` even when children are empty — any non-zombie/dead/stopped status is a reprieve. The order above returns `"children"` first when both are true for clearer telemetry.

   - In the recovery branch (after the `response_delivered_at` guard at line 1704-1726):
     ```python
     # Tier 1 already flagged stuck; now try Tier 2 reprieve
     handle = _active_sessions.get(entry.agent_session_id)
     METRICS.increment("health_check.tier1_flagged_total")
     reprieve = _tier2_reprieve_signal(handle, entry)
     if reprieve is not None:
         METRICS.increment(f"health_check.tier2_reprieve_total.{reprieve}")
         entry.reprieve_count = (entry.reprieve_count or 0) + 1
         try:
             entry.save(update_fields=["reprieve_count"])
         except Exception:
             pass
         logger.info(
             "[session-health] Tier 2 reprieve (%s) for session %s — skipping kill",
             reprieve, entry.agent_session_id,
         )
         continue  # next entry in health-check loop

     # All Tier 2 gates failed. Respect kill-switch.
     if os.environ.get("DISABLE_PROGRESS_KILL") == "1":
         logger.warning(
             "[session-health] Would kill session %s (DISABLE_PROGRESS_KILL=1)",
             entry.agent_session_id,
         )
         continue

     # Cancel the session task and transition.
     if handle is not None and not handle.task.done():
         handle.task.cancel()
         try:
             await asyncio.wait_for(handle.task, timeout=TASK_CANCEL_TIMEOUT)
         except (asyncio.CancelledError, asyncio.TimeoutError):
             pass
         logger.info(
             "[session-health] Cancelled orphan task for session %s",
             entry.agent_session_id,
         )

     entry.recovery_attempts = (entry.recovery_attempts or 0) + 1
     METRICS.increment("health_check.kill_total")
     try:
         if entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
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
     except StatusConflictError as e:
         logger.warning(
             "[session-health] StatusConflictError during recovery of %s: %s",
             entry.agent_session_id, e,
         )
     ```

   - The existing local-session path (`is_local` branch at line 1740-1753) continues to go to `abandoned` on first recovery — unchanged. Local sessions can't be re-picked up, so a single recovery is already terminal. But the Tier 2 reprieve gates MUST apply to local sessions too before transitioning to `abandoned` — a live local session with children should not be abandoned falsely.

   - **Metrics**: Use the existing metrics facility (`METRICS.increment(...)`) — if no facility exists, add a minimal module-level counter dict incremented per key with a corresponding dashboard-visible counter in `ui/dashboard.py` for `tier1_flagged_total`, `tier2_reprieve_total.{alive|children|stdout}`, and `kill_total`.

**Preserving prior fixes (explicit checks):**
- #918: The `response_delivered_at` guard at line 1704 runs BEFORE the new Tier 1/Tier 2 logic, so it's unaffected. Sessions with a delivered response still get finalized, never cancelled mid-flight.
- #944: `_has_progress()` still returns True for slugless dev sessions with own-progress signals. The heartbeat branches are additive, not substitutive. Slugless dev sessions sharing a worker_key with their PM remain recoverable when genuinely stuck (Tier 1 flags + Tier 2 all fail).
- #963: `get_children()` branch preserved verbatim.
- #1006: Terminal-status zombie guard at line 1620-1627 still runs first. Sessions already terminal skip the whole recovery path.

**Deferred (out of this plan):** CPU-delta reprieve gate (`proc.cpu_times()` diff between ticks). Weakest of the five candidates, synchronous, adds syscall cost with marginal benefit. Revisit only if false-negatives are observed in practice despite Tier 2's three existing gates.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `save(update_fields=["last_heartbeat_at"])` call inside `_heartbeat_loop` must be wrapped in try/except that logs at WARNING on failure and continues. Test: inject a `save` failure, assert WARNING is logged, assert loop continues.
- [ ] All three messenger callbacks (`on_sdk_started`, `on_heartbeat_tick`, `on_stdout_event`) must catch their own exceptions and log at WARNING — messenger resilience MUST NOT depend on ORM success. Test: inject an exception inside each callback, assert the messenger's watchdog/stdout pump keeps running.
- [ ] The `handle.task.cancel()` + `asyncio.wait_for(timeout=0.25)` path must handle the case where the task has already completed between lookup and cancel. Test: race — schedule task completion before cancel call, assert no exception propagates.
- [ ] The `finalize_session(entry, "failed", ...)` call at `recovery_attempts >= 2` must handle `StatusConflictError` (stale Redis state) cleanly. Test: inject `StatusConflictError` via a test double, assert the recovery loop does not crash; warning is logged; next tick re-attempts.
- [ ] `_tier2_reprieve_signal` must handle `psutil.NoSuchProcess` / `psutil.AccessDenied` / `ImportError` without raising. Test: pass a pid for a dead process; assert it returns None (not alive) — NOT a reprieve.
- [ ] `_tier2_reprieve_signal` must handle `handle is None` (session not in registry, e.g. worker crashed without cleanup). Test: `handle=None` → returns `"stdout"` if stdout is fresh, else None.

### Empty/Invalid Input Handling
- [ ] `_has_progress` must handle both heartbeat fields being `None` (new session, never written) — should fall through to existing checks without raising. Test: session with all own-progress fields empty AND both heartbeats None → `_has_progress` returns False (unchanged from current).
- [ ] `_has_progress` must handle heartbeat fields as non-datetime (backward-compat with old records loaded from Redis). The existing `_DATETIME_FIELDS` coercion handles this — confirm all three datetime fields are in that set.
- [ ] `recovery_attempts` and `reprieve_count` must handle `None` (old records without the fields). Use `(entry.recovery_attempts or 0)` in arithmetic.
- [ ] `SessionHandle.pid` may be `None` (T+0 window before SDK subprocess spawns). `_tier2_reprieve_signal` must handle `pid=None` gracefully — skip (c) and (d), fall through to (e).

### Error State Rendering
- [ ] Sessions that reach `failed` via `recovery_attempts >= 2` must appear in `valor-session status --id <id>` with a clear reason. Test: simulate 2 recoveries (no reprieves), confirm `valor-session status` returns the session and shows status=failed with the reason in its lifecycle history.
- [ ] Sessions that are repeatedly reprieved by Tier 2 must have `reprieve_count` visible in `valor-session status`. Test: simulate 3 reprieves over 3 health ticks, assert reprieve_count=3 on the record.
- [ ] `DISABLE_PROGRESS_KILL=1` path must log a distinctive WARNING so operators can see kills that would have happened but were suppressed. Test: set env var, simulate Tier 1 flag + all Tier 2 gates failing; assert WARNING "[session-health] Would kill session ... (DISABLE_PROGRESS_KILL=1)" is logged and session remains in running state.

## Test Impact

Existing tests that assert `_has_progress` or recovery behavior:

- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestHasProgressChildActivity` (lines 188-249) — UPDATE: existing child-activity tests stay intact (heartbeat checks precede them, but tests construct entries with both heartbeats None, so the existing branch still exercises). Add the new test classes alongside.
- [ ] `tests/unit/test_stall_detection.py` — UPDATE: audit for any mocked AgentSession that hardcodes absence of `last_heartbeat_at` expecting progress-false. With default `last_heartbeat_at=None` AND `last_sdk_heartbeat_at=None`, existing assertions still hold. Test fixtures may need one-line updates to be explicit about both heartbeat fields.
- [ ] `tests/unit/test_agent_session_hierarchy.py` — REVIEW: ensure no hierarchy tests depend on the exact `running → pending` transition without the cancellation step. If any assert `_active_workers` state, add `_active_sessions` cleanup to their teardown.
- [ ] `tests/unit/test_agent_session_model.py` (if exists, else equivalent) — UPDATE: add assertions that all four new fields appear in `_AGENT_SESSION_FIELDS` and round-trip correctly through save/load (explicit guard against the B2 regression).

New tests required (added as part of this work, not pre-existing):

- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestHasProgressDualHeartbeat` (NEW):
  - `test_queue_heartbeat_within_window_returns_true` — `last_heartbeat_at = now - 30s`, other fields empty → True.
  - `test_sdk_heartbeat_within_window_returns_true` — `last_sdk_heartbeat_at = now - 30s`, other fields empty → True.
  - `test_either_heartbeat_fresh_returns_true` — one fresh, one stale → True (OR semantics).
  - `test_both_heartbeats_stale_returns_false` — both stale, other fields empty → False.
  - `test_heartbeats_at_boundary_returns_true` — `last_heartbeat_at = now - 89s`, both fields present → True.
  - `test_both_heartbeats_none_falls_through_to_other_checks` — both None, `turn_count=5` → True (unchanged behavior).
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestTier2ReprieveGates` (NEW):
  - `test_reprieve_on_process_alive` — mock psutil.Process with status=running, children=[]; returns "alive".
  - `test_reprieve_on_children` — mock psutil.Process with status=running, children=[Process()]; returns "children".
  - `test_no_reprieve_on_zombie` — mock psutil.Process with status=zombie; returns None.
  - `test_no_reprieve_on_dead_process` — mock psutil.NoSuchProcess; returns None.
  - `test_reprieve_on_recent_stdout` — no pid in handle, last_stdout_at = now - 30s; returns "stdout".
  - `test_no_reprieve_on_stale_stdout` — no pid, last_stdout_at = now - 200s; returns None.
  - `test_no_reprieve_on_handle_none` — handle=None, no stdout; returns None.
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestRecoveryCancellation` (NEW):
  - `test_recovery_cancels_active_session_task` — register a task in `_active_sessions`, Tier 1 flags + Tier 2 all fail, trigger recovery, assert task is cancelled within 0.25s.
  - `test_recovery_handles_completed_task_gracefully` — register a completed task, assert no exception.
  - `test_recovery_handles_missing_registry_entry` — session not in `_active_sessions`, assert recovery still transitions status (no crash).
  - `test_status_conflict_error_caught` — inject `StatusConflictError` during transition, assert caught + warning logged.
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestRecoveryAttempts` (NEW):
  - `test_first_recovery_transitions_to_pending` — `recovery_attempts=0` → becomes 1, status=pending.
  - `test_second_recovery_finalizes_as_failed` — `recovery_attempts=1` → becomes 2, status=failed.
  - `test_reprieve_does_not_increment_recovery_attempts` — Tier 2 reprieves; recovery_attempts stays 0; reprieve_count increments.
  - `test_startup_recovery_does_not_increment_attempts` — worker restart path; recovery_attempts unchanged (per Risk 3).
  - `test_local_session_terminal_on_first_recovery` — local worker_key → abandoned (unchanged from current behavior).
  - `test_local_session_gets_tier2_reprieve` — local session with alive pid → reprieve, NOT abandoned.
  - `test_finalized_failure_preserves_history` — after failure, `valor_session.status(session_id)` returns session with full lifecycle entries.
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestDisableProgressKill` (NEW):
  - `test_env_var_suppresses_kill` — `DISABLE_PROGRESS_KILL=1`, Tier 1 flag + all Tier 2 fail; session stays running, WARNING logged.
  - `test_env_var_still_logs_flagging` — same setup; assert `tier1_flagged_total` metric still incremented.
- [ ] `tests/unit/test_messenger_callbacks.py` (NEW):
  - `test_on_sdk_started_called_once_with_pid` — BackgroundTask with callback; assert called once after subprocess spawns.
  - `test_on_heartbeat_tick_called_every_60s` — BackgroundTask with callback; mock clock, advance, assert called.
  - `test_on_stdout_event_called_on_each_event` — BackgroundTask with callback; feed stdout events; assert called per event.
  - `test_callback_exceptions_do_not_crash_messenger` — callbacks raise; messenger watchdog continues, WARNING logged.
  - `test_none_callbacks_are_safe_defaults` — no callbacks provided; messenger behaves identically to today.
- [ ] `tests/integration/test_session_heartbeat_progress.py` (NEW, integration):
  - Test 1: session with stub SDK that delays first turn 6 minutes, both heartbeats alive → NOT recovered.
  - Test 2: session with queue heartbeat alive but SDK heartbeat stale (simulates heartbeat-loop-wedged case) → Tier 1 not flagged (dual-heartbeat OR), session continues.
  - Test 3: session with both heartbeats stale but alive pid + children → Tier 1 flagged, Tier 2 reprieves, session continues with reprieve_count=1.
  - Test 4: session with all signals failed → cancellation + transition within one health-check cycle; SDK subprocess terminated within 60s.
  - Test 5: `DISABLE_PROGRESS_KILL=1` runtime override — Tier 1 flagged, all Tier 2 fail; session remains running, WARNING logged.
  - Use Popoto test harness and asyncio time mocking where feasible.

## Rabbit Holes

- **Don't rewrite `_has_progress` as a full-blown observability system.** The bug is specifically the missing heartbeat signal. Adding weighted multi-signal scoring, configurable thresholds, or a pluggable progress-signal registry is out of scope. Dual-heartbeat OR + existing own-progress signals is sufficient.
- **Don't pass the AgentSession ORM object into `messenger.py`.** The messenger imports nothing from `models/`. Tier 2 uses **callbacks** (`on_sdk_started`, `on_heartbeat_tick`, `on_stdout_event`) that the queue layer provides — messenger invokes them blindly. Trying to shortcut this with direct ORM writes from messenger breaks the architectural boundary established in the bridge/worker architecture doc.
- **Don't add a CPU-delta reprieve gate right now.** It was considered (`proc.cpu_times()` diff between ticks) and deferred — weakest of the reprieve signals, adds syscall cost, and the three existing gates (alive/children/stdout) already cover the space. Revisit only if false-negatives are observed despite Tier 2.
- **Don't try to cancel the SDK subprocess via os.kill or signal handling.** `asyncio.Task.cancel()` propagates to `BackgroundTask._task`, which is already awaiting the SDK coroutine. The SDK client's own cleanup (via async context managers and `CancelledError` propagation) handles subprocess termination. Introducing signal handling would bypass this clean path and risk zombies.
- **Don't adjust `AGENT_SESSION_HEALTH_MIN_RUNNING`.** Raising it hides the problem briefly for slightly longer; the fix is the heartbeat signal. Keep at 300.
- **Don't scale the guard timeout by prompt size.** Dual-heartbeat + Tier 2 reprieves make prompt-size scaling unnecessary. Large prompts simply keep the heartbeats fresh.
- **Don't audit the `Meta.ttl` deletion path.** Sessions now always reach a terminal status within 2 recovery cycles (~10 minutes). The 30-day TTL backstop is irrelevant if sessions are already terminal before day 30. No change to `Meta.ttl`.
- **Don't build a dashboard view for reprieve telemetry in this plan.** Counters (`tier1_flagged_total`, `tier2_reprieve_total.{signal}`, `kill_total`) are emitted; visualizing them is a follow-up if operational need arises.

## Risks

### Risk 1: Heartbeat-write load on Redis
**Impact:** Three 60-second writes per live session (queue heartbeat + SDK heartbeat + any stdout events). With 8 concurrent sessions, that's 16-24 writes per minute plus stdout-event writes. Each write is a single-field partial update (~50 bytes), so load is negligible — but worth watching at higher concurrency caps.
**Mitigation:** Use `save(update_fields=[...])` consistently. The three field writers run independently (one in `_heartbeat_loop`, one in messenger watchdog, one in messenger stdout pump), so they can't collide within a single coroutine. Log ERROR if any save fails for >2 consecutive ticks.

### Risk 2: Task cancellation race with worker loop
**Impact:** `handle.task.cancel()` raises `CancelledError` inside the worker loop's `await _execute_agent_session(session)`. The worker's existing `except asyncio.CancelledError` handler leaves the session in `running` and re-raises. The health check's transition runs right after. If the health check's transition runs BEFORE `CancelledError` propagates, the worker's handler might try to save `running` over the already-transitioned state.
**Mitigation:** The health check cancels first, `awaits wait_for(task, timeout=0.25s)` to allow propagation, then applies the transition. The `transition_status` function has CAS semantics and raises `StatusConflictError` on stale state — caught and logged. Test `test_recovery_cancels_active_session_task` and `test_status_conflict_error_caught` confirm the serialization.

### Risk 3: `recovery_attempts` counter drift across restarts
**Impact:** If the worker restarts between two recovery events, `recovery_attempts` is persisted in Redis and survives the restart. But startup recovery (`_recover_interrupted_agent_sessions_startup`) also transitions `running → pending`. If those transitions also increment the counter, we double-count and prematurely fail sessions.
**Mitigation:** Only increment `recovery_attempts` on actual kills inside `_agent_session_health_check` (Tier 1 AND Tier 2 both negative, NOT on reprieve), NEVER in startup recovery. Startup recovery is a separate code path with its own semantics (worker crash, not session stall). Test `test_startup_recovery_does_not_increment_attempts` and `test_reprieve_does_not_increment_recovery_attempts` enforce both boundaries.

### Risk 4: Both heartbeat loops get wedged simultaneously (event loop starved)
**Impact:** If the event loop is badly starved, both `_heartbeat_loop` (queue layer) AND `BackgroundTask._watchdog` (messenger) may fail to tick — both heartbeats go stale at once, and Tier 1 flags the session. If the task is actually making progress (tool subprocess running), Tier 2's process-alive / has-children / stdout gates catch it and reprieve.
**Mitigation:** This is exactly the failure mode Tier 2 is designed for. The reprieve gates rely on OS-level process introspection (psutil) which does NOT require the starved event loop to cooperate. Risk is fully mitigated by design.

### Risk 5: psutil returns AccessDenied on macOS/Linux for some process attributes
**Impact:** On some platforms, `psutil.Process(pid).children()` or `.status()` may raise `AccessDenied` for processes in other users' namespaces. A subprocess of the same process should always be accessible, but edge cases exist (root-owned, containerized setups).
**Mitigation:** Wrap all psutil calls in `try/except (psutil.NoSuchProcess, psutil.AccessDenied)`. On denied access, the Tier 2 gate returns None (no reprieve via that signal) and falls through to the next gate. If ALL gates fall through, the session is killed — preferable to leaking a genuinely dead session. Test `test_no_reprieve_on_access_denied` asserts this.

### Risk 6: T+0 heartbeat write race with pending-to-running transition
**Impact:** `_execute_agent_session` writes `last_heartbeat_at` at T+0 via `save(update_fields=["last_heartbeat_at"])`. If this runs concurrently with the transition from `pending` to `running` (also a save on the same record), Redis may serialize them in an unexpected order. Concern: does the T+0 write revert the status to pending?
**Mitigation:** `update_fields=["last_heartbeat_at"]` only writes the heartbeat field — status is NOT in the field list and is therefore not touched. Popoto's partial-save is field-scoped. Test `test_t_plus_zero_heartbeat_does_not_clobber_status` verifies.

### Risk 7: Kill-switch env var leaks across test runs
**Impact:** `DISABLE_PROGRESS_KILL=1` env var set by one test may leak into subsequent tests in the same pytest process.
**Mitigation:** Use `monkeypatch.setenv(...)` in tests so cleanup is automatic. `TestDisableProgressKill` class uses the monkeypatch fixture explicitly.

### Risk 8: Messenger callback tight coupling to session lifetime
**Impact:** Callbacks close over the `session` local variable in `_execute_agent_session`. If the session is somehow GC'd or the callback is invoked after `_execute_agent_session` returns (via a lingering watchdog tick), the callback could race with session cleanup.
**Mitigation:** Messenger's watchdog exits when `task._task` is done — callbacks stop firing before `_execute_agent_session` returns. The `_active_sessions` registry entry is popped in the finally block, AFTER the messenger is fully shut down. Race window is bounded. Test via `test_callbacks_stop_after_session_ends`.

## Race Conditions

### Race 1: Cancel-then-transition ordering
**Location:** `agent/agent_session_queue.py::_agent_session_health_check`, new Tier 2-negative recovery block.
**Trigger:** Health check cancels `handle.task`. Worker's `except CancelledError` handler runs first. Health check's `transition_status` runs next.
**Mitigation:** `asyncio.wait_for(handle.task, timeout=0.25s)` after cancel. `transition_status` CAS semantics raise `StatusConflictError` on stale state — caught in the wrapping try/except and logged WARNING.

### Race 2: Multi-field save vs. other field writes
**Location:** Three writers (`_heartbeat_loop`, `on_heartbeat_tick` callback, `on_stdout_event` callback) each save one field; other code paths write `claude_session_uuid`, `log_path`, `turn_count`, `status`.
**Trigger:** Concurrent writes in the same tick.
**Mitigation:** Every writer uses `save(update_fields=["<single_field>"])`. Popoto's partial-save targets only the named field — no cross-field clobbering. Confirmed pattern at line 4136 for `updated_at`.

### Race 3: Registry cleanup vs. recovery lookup
**Location:** `_active_sessions.pop()` in `_execute_agent_session` finally block vs. `_active_sessions.get()` in health check + `_tier2_reprieve_signal`.
**Trigger:** Session finishes naturally at the same moment health check fires.
**Mitigation:** All reads are `.get()` returning None if popped. `_tier2_reprieve_signal` handles `handle is None` (falls through to stdout-only reprieve check). `.cancel()` is never called on a None task.

### Race 4: SDK started before pid is populated
**Location:** `_tier2_reprieve_signal` reads `handle.pid`; `on_sdk_started` callback writes it.
**Trigger:** Health check fires within the T+0 → SDK-spawned window (rare, but possible on slow systems).
**Mitigation:** `handle.pid = None` is the initial state. `_tier2_reprieve_signal` handles `pid=None` by skipping (c) and (d), falling through to (e) stdout check. With `last_stdout_at=None` in the same window, the function returns None — session is killed. BUT: both heartbeats in Tier 1 will be fresh (T+0 write + 60s queue heartbeat is within window), so Tier 1 does NOT fire in this window. Race is benign.

### Race 5: Messenger callback fires after session finally block
**Location:** Messenger `_watchdog` or stdout pump invokes callback; `_execute_agent_session` is already in finally, session object state unclear.
**Trigger:** Watchdog/stdout tick races with session exit.
**Mitigation:** Messenger stops ticking when `BackgroundTask._task` is done, which happens before `_execute_agent_session` enters finally. Additionally, callback `try/except WARNING` wrapping ensures a stale save (e.g. into a session whose Popoto instance was GC'd) is logged and does not propagate. Race is bounded and recoverable.

### Race 6: Two health-check ticks in flight simultaneously
**Location:** `_agent_session_health_check` scheduled every 300s.
**Trigger:** A prior tick takes >300s (unusual but possible under load or if Tier 2 psutil calls stall).
**Mitigation:** Existing health-check scheduling uses `_health_check_lock` (asyncio.Lock) — verify during `validate-concurrency` task. If no lock exists, add one.

## No-Gos (Out of Scope)

- Scaling guard timeout by prompt size (Open Question 3 in the issue). Dual-heartbeat + Tier 2 make this unnecessary.
- Passing the AgentSession ORM object into `agent/messenger.py`. The messenger gains three optional no-ORM callbacks instead.
- CPU-delta reprieve gate. Deferred — adds syscall cost, weakest signal, not needed with the three existing gates.
- Observability dashboard for heartbeat lag. Counters are emitted; building a dashboard view is a follow-up if operational need arises.
- Changing `AgentSession.Meta.ttl = 2592000`. Orthogonal; the fix makes non-terminal long-lived sessions impossible.
- Auditing `cleanup_corrupted_agent_sessions` for false positives. Reviewed in spike-2; it only deletes genuinely corrupted records.
- Per-prompt-size timeout tuning.
- Exposing `_active_sessions` as a debug endpoint. Internal only for this fix.
- Per-platform psutil capability detection. Wrap-all-in-try/except is simpler and sufficient.

## Update System

No update system changes required — this fix is purely internal to `agent/agent_session_queue.py` and `models/agent_session.py`. The two new fields (`last_heartbeat_at`, `recovery_attempts`) default to null/0, so existing AgentSession records loaded from Redis continue to work. No migration, no config, no new dependency. The fix is deployed by the normal update flow: `git pull && scripts/valor-service.sh restart`.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. No new MCP tools, no `.mcp.json` changes, no new functions exposed to the agent. The fix changes how the health check decides to recover sessions; the agent itself is unaffected.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` with a new section "Two-tier no-progress detector" describing: (1) dual-heartbeat Tier 1, (2) activity-positive reprieve gates Tier 2, (3) task cancellation + recovery counter, (4) `DISABLE_PROGRESS_KILL` kill-switch.
- [ ] Update `docs/features/bridge-worker-architecture.md` to note: (a) the `_active_sessions` registry (`dict[str, SessionHandle]`) and its role; (b) the messenger-callback plumbing (`on_sdk_started`, `on_heartbeat_tick`, `on_stdout_event`) that keeps messenger decoupled from `models/`.
- [ ] Add entry to `docs/features/README.md` if no existing doc covers session health — otherwise update the existing entry.

### External Documentation Site
- None (repo does not publish external docs).

### Inline Documentation
- [ ] Update `_has_progress` docstring to document the dual-heartbeat OR branches, the 90s freshness window, and the rationale (SDK liveness > own-progress fields for long warmup prompts).
- [ ] Add docstring to `_active_sessions` and `SessionHandle` explaining the single-writer/multi-reader pattern and cleanup contract.
- [ ] Update `_agent_session_health_check` docstring to describe the Tier 1 → Tier 2 flow, the cancellation path, `recovery_attempts` counter, and `DISABLE_PROGRESS_KILL` env var.
- [ ] Update `_tier2_reprieve_signal` with a full docstring describing the three gates (c/d/e) and the return-value semantics.
- [ ] Note in each new AgentSession field comment: `last_heartbeat_at` (queue heartbeat, written by `_heartbeat_loop`), `last_sdk_heartbeat_at` (messenger heartbeat, written by `on_heartbeat_tick` callback), `last_stdout_at` (messenger stdout event, written by `on_stdout_event` callback), `recovery_attempts` (kills only, health-check owned), `reprieve_count` (Tier 2 saves, health-check owned).
- [ ] Update `agent/messenger.py::BackgroundTask` class docstring to document the three optional callback kwargs, their invocation sites, and the contract that exceptions in callbacks are caught + logged (messenger resilience).

## Success Criteria

- [ ] A PM session with a 4000-character initial prompt that takes 5-7 minutes to first turn is NOT killed by the no-progress guard — `logs/worker.log` shows both heartbeats continuing past 300s and no `[session-health] Recovering stuck session` line.
- [ ] A session where the queue-layer heartbeat is wedged but the SDK heartbeat is alive (simulated by sleeping `_heartbeat_loop`) is NOT killed — Tier 1 requires BOTH to be stale.
- [ ] A session where both heartbeats are stale but the SDK subprocess is alive with child subprocesses is NOT killed — Tier 2 reprieves on `children`. Log line shows `Tier 2 reprieve (children)`.
- [ ] When a session IS killed (both tiers negative), the SDK subprocess is terminated within 60s — `logs/worker.log` shows one final `SDK heartbeat` shortly after the recovery line, then none.
- [ ] A session that is killed twice and still makes no progress transitions to `failed` with full history — `valor-session status --id <id>` works and shows `status: failed` with lifecycle entries for both kills.
- [ ] Setting `DISABLE_PROGRESS_KILL=1` env var in worker config suppresses kills but still emits Tier 1 / Tier 2 logs — operators can collect data without risking false-negative kills during rollout.
- [ ] Metrics counters (`tier1_flagged_total`, `tier2_reprieve_total.{alive|children|stdout}`, `kill_total`) are emitted and visible in worker logs / metrics output.
- [ ] Regression tests pass: `TestHasProgressDualHeartbeat`, `TestTier2ReprieveGates`, `TestRecoveryCancellation`, `TestRecoveryAttempts`, `TestDisableProgressKill`, `TestMessengerCallbacks`.
- [ ] Integration tests `tests/integration/test_session_heartbeat_progress.py` all 5 scenarios pass.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No existing tests regress — specifically, `test_health_check_recovery_finalization.py::TestHasProgressChildActivity` still passes verbatim.
- [ ] `agent/messenger.py` imports nothing from `models/` — architectural boundary preserved.

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

### 1. Add AgentSession fields + `_AGENT_SESSION_FIELDS` update
- **Task ID**: build-model-fields
- **Depends On**: none
- **Validates**: `python -c "from models.agent_session import AgentSession; s = AgentSession(chat_id='x'); assert all(hasattr(s, f) for f in ('last_heartbeat_at','last_sdk_heartbeat_at','last_stdout_at','recovery_attempts','reprieve_count'))"`
- **Informed By**: Revision guidance; B2 from prior critique
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Add to `AgentSession`: `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at` (all `DatetimeField(null=True)`); `recovery_attempts`, `reprieve_count` (both `IntField(default=0)`).
- Add the three datetime fields to `_DATETIME_FIELDS`.
- **Add ALL FIVE new fields to `_AGENT_SESSION_FIELDS`** so they round-trip through save/load. This is NOT optional.
- Add docstring notes per the Documentation section.

### 2. Add messenger callbacks (ORM-free)
- **Task ID**: build-messenger-callbacks
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_messenger_callbacks.py -x`
- **Informed By**: spike-3 revision; spike-4
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Add three optional kwargs to `BackgroundTask` / `Messenger` constructors: `on_sdk_started: Callable[[int], None] | None = None`, `on_heartbeat_tick: Callable[[], None] | None = None`, `on_stdout_event: Callable[[], None] | None = None`.
- After SDK subprocess spawn (where `self._proc.pid` is populated), invoke `on_sdk_started(self._proc.pid)` if provided. Wrap in try/except WARNING.
- Inside `_watchdog`, after the existing heartbeat log line, invoke `on_heartbeat_tick()` if provided. Wrap in try/except WARNING.
- In the stdout event pump, invoke `on_stdout_event()` on each event if provided. Wrap in try/except WARNING.
- **No imports from `models/`.** Verify via `grep -n '^from models' agent/messenger.py` returning no new matches.

### 3. Add `SessionHandle` + `_active_sessions` registry
- **Task ID**: build-registry
- **Depends On**: none
- **Validates**: `python -c "from agent.agent_session_queue import _active_sessions, SessionHandle; assert isinstance(_active_sessions, dict)"`
- **Informed By**: spike-1 revision
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `@dataclass class SessionHandle: task: asyncio.Task; pid: int | None = None` at module level.
- Add `_active_sessions: dict[str, SessionHandle] = {}` near `_active_workers` at line 2124.
- Inline docstring describing single-writer (`_execute_agent_session`) / multi-reader (health check + Tier 2 gates) pattern.

### 4. Wire `_execute_agent_session`: T+0 heartbeat, registry, callbacks, heartbeat loop
- **Task ID**: build-execute-wiring
- **Depends On**: build-model-fields, build-messenger-callbacks, build-registry
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestHasProgressDualHeartbeat -x`
- **Informed By**: Revision guidance
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- **Registration must precede every raise site.** At function entry, compute `handle = SessionHandle(task=asyncio.current_task())`; assign `_active_sessions[session.agent_session_id] = handle`; wrap the remainder of the function in try/finally that pops the entry.
- **T+0 heartbeat write**: Immediately after registration (still before any raise site), set `session.last_heartbeat_at = datetime.now(tz=UTC)`; `session.save(update_fields=["last_heartbeat_at"])`. try/except WARNING.
- Define the three local callback closures (`_on_sdk_started`, `_on_heartbeat_tick`, `_on_stdout_event`) that bump `handle.pid` / `last_sdk_heartbeat_at` / `last_stdout_at` respectively. All try/except WARNING.
- Pass callbacks into `BackgroundTask(...)` constructor.
- Modify `_heartbeat_loop`: write `last_heartbeat_at` every `HEARTBEAT_WRITE_INTERVAL = 60s`, gate the existing 25-min calendar work. Add `HEARTBEAT_WRITE_INTERVAL = 60` at module level. try/except WARNING on save.

### 5. Extend `_has_progress` with dual-heartbeat OR
- **Task ID**: build-has-progress
- **Depends On**: build-model-fields
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestHasProgressDualHeartbeat -x`
- **Informed By**: Revision guidance (dual-heartbeat OR semantics)
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `HEARTBEAT_FRESHNESS_WINDOW = 90` and `STDOUT_FRESHNESS_WINDOW = 90` at module level.
- Prepend to `_has_progress` a loop that checks both `last_heartbeat_at` and `last_sdk_heartbeat_at`; returns True if either is within the freshness window.
- Keep every other existing check in `_has_progress` unchanged — preserves #944/#963.
- Update the docstring to document the dual-heartbeat semantics.

### 6. Add `_tier2_reprieve_signal` helper
- **Task ID**: build-tier2-helper
- **Depends On**: build-model-fields, build-registry
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestTier2ReprieveGates -x`
- **Informed By**: Revision guidance (Tier 2)
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_tier2_reprieve_signal(handle: SessionHandle | None, entry: AgentSession) -> str | None` per Technical Approach snippet.
- Wrap all `psutil` calls in `try/except (psutil.NoSuchProcess, psutil.AccessDenied, ImportError)` returning None on failure.
- Return "children" before "alive" to surface the stronger signal in metrics.

### 7. Extend health check with two-tier recovery + kill-switch
- **Task ID**: build-health-recovery
- **Depends On**: build-execute-wiring, build-has-progress, build-tier2-helper
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestRecoveryCancellation tests/unit/test_health_check_recovery_finalization.py::TestRecoveryAttempts tests/unit/test_health_check_recovery_finalization.py::TestDisableProgressKill -x`
- **Informed By**: spike-1, spike-2, revision guidance
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `MAX_RECOVERY_ATTEMPTS = 2`, `TASK_CANCEL_TIMEOUT = 0.25` at module level.
- Add metrics counters for `tier1_flagged_total`, `tier2_reprieve_total.{alive|children|stdout}`, `kill_total`.
- In the recovery branch of `_agent_session_health_check` (after `response_delivered_at` guard):
  - Increment `tier1_flagged_total`.
  - Look up `_active_sessions[entry.agent_session_id]` → `handle`.
  - Call `_tier2_reprieve_signal(handle, entry)`. If non-None: increment `tier2_reprieve_total.{signal}`, bump `entry.reprieve_count`, log INFO, continue to next entry.
  - Check `os.environ.get("DISABLE_PROGRESS_KILL") == "1"`. If true: log WARNING "Would kill ...", continue.
  - If `handle.task` not done: cancel, `asyncio.wait_for(task, timeout=0.25)` with `except (asyncio.CancelledError, asyncio.TimeoutError): pass`. Log INFO.
  - Bump `entry.recovery_attempts`. Increment `kill_total`.
  - Wrap `finalize_session` / `transition_status` in `try/except StatusConflictError` logging WARNING.
  - If `recovery_attempts >= MAX_RECOVERY_ATTEMPTS`: `finalize_session(entry, "failed", ...)`.
  - Else: transition `pending`, re-ensure worker as existing code does.
- Extend the local-session branch to go through Tier 2 reprieves before transitioning to `abandoned`. On reprieve, local sessions also stay running.
- Do NOT increment `recovery_attempts` in `_recover_interrupted_agent_sessions_startup`.

### 8. Tests — dual-heartbeat `_has_progress`
- **Task ID**: build-tests-has-progress
- **Depends On**: build-has-progress
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestHasProgressDualHeartbeat -x`
- **Informed By**: Test Impact
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestHasProgressDualHeartbeat` class with 6 tests per Test Impact.

### 9. Tests — Tier 2 reprieve gates
- **Task ID**: build-tests-tier2
- **Depends On**: build-tier2-helper
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestTier2ReprieveGates -x`
- **Informed By**: Test Impact
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestTier2ReprieveGates` class with 7 tests per Test Impact. Mock `psutil.Process`.

### 10. Tests — recovery cancellation + attempts + kill-switch
- **Task ID**: build-tests-recovery
- **Depends On**: build-health-recovery
- **Validates**: `pytest tests/unit/test_health_check_recovery_finalization.py::TestRecoveryCancellation tests/unit/test_health_check_recovery_finalization.py::TestRecoveryAttempts tests/unit/test_health_check_recovery_finalization.py::TestDisableProgressKill -x`
- **Informed By**: Race 1, Race 3, Risk 7
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestRecoveryCancellation` (4 tests), `TestRecoveryAttempts` (7 tests), `TestDisableProgressKill` (2 tests) classes per Test Impact.
- Use `monkeypatch.setenv` for `DISABLE_PROGRESS_KILL` tests (Risk 7).

### 11. Tests — messenger callbacks
- **Task ID**: build-tests-callbacks
- **Depends On**: build-messenger-callbacks
- **Validates**: `pytest tests/unit/test_messenger_callbacks.py -x`
- **Informed By**: Test Impact
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_messenger_callbacks.py` with 5 tests per Test Impact.
- Verify `import ast` scan of `agent/messenger.py` shows no `from models` imports.

### 12. Integration test
- **Task ID**: build-integration-test
- **Depends On**: build-health-recovery, build-execute-wiring
- **Validates**: `pytest tests/integration/test_session_heartbeat_progress.py -x`
- **Informed By**: Issue #1036 acceptance criteria + revision
- **Assigned To**: tests-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_session_heartbeat_progress.py` with 5 tests per Test Impact.

### 13. Concurrency validation
- **Task ID**: validate-concurrency
- **Depends On**: build-tests-recovery, build-tests-tier2, build-tests-callbacks
- **Assigned To**: concurrency-validator
- **Agent Type**: async-specialist
- **Parallel**: false
- Verify `_active_sessions` single-writer (`_execute_agent_session`) — grep for any other writers.
- Verify `transition_status` CAS + `StatusConflictError` handling (Race 1).
- Verify dual-heartbeat OR short-circuits correctly; #944/#963 branches still reachable.
- Verify `save(update_fields=[...])` partial-write semantics don't clobber other fields (Risk 6).
- Verify callback exception handling — messenger watchdog continues after callback raises (Risk 8).
- Verify `agent/messenger.py` imports nothing from `models/` (architectural boundary).

### 14. Documentation updates
- **Task ID**: document-feature
- **Depends On**: validate-concurrency
- **Assigned To**: session-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with "Two-tier no-progress detector" section (dual heartbeat + reprieve gates).
- Update `docs/features/bridge-worker-architecture.md` with `_active_sessions` registry + messenger callback plumbing.
- Update docstrings per Documentation section.
- Ensure `docs/features/README.md` index reflects updates.

### 15. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature, build-integration-test
- **Assigned To**: concurrency-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_health_check_recovery_finalization.py tests/unit/test_stall_detection.py tests/unit/test_agent_session_hierarchy.py tests/unit/test_messenger_callbacks.py tests/integration/test_session_heartbeat_progress.py`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Confirm all Success Criteria checkboxes.
- Confirm all Failure Path Test Strategy items are covered.
- Confirm `DISABLE_PROGRESS_KILL=1` runtime override works end-to-end.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_health_check_recovery_finalization.py tests/unit/test_messenger_callbacks.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_session_heartbeat_progress.py -x -q` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |
| All 5 new fields present | `python -c "from models.agent_session import AgentSession; s=AgentSession(chat_id='x'); assert all(hasattr(s,f) for f in ('last_heartbeat_at','last_sdk_heartbeat_at','last_stdout_at','recovery_attempts','reprieve_count'))"` | exit code 0 |
| `_active_sessions` registry + `SessionHandle` | `python -c "from agent.agent_session_queue import _active_sessions, SessionHandle; assert isinstance(_active_sessions, dict)"` | exit code 0 |
| Fields in `_AGENT_SESSION_FIELDS` | `python -c "from agent.agent_session_queue import _AGENT_SESSION_FIELDS; assert {'last_heartbeat_at','last_sdk_heartbeat_at','last_stdout_at','recovery_attempts','reprieve_count'}.issubset(_AGENT_SESSION_FIELDS)"` | exit code 0 |
| Messenger has callback kwargs | `grep -n 'on_sdk_started\|on_heartbeat_tick\|on_stdout_event' agent/messenger.py` | 3+ matches |
| Messenger ORM-free | `grep -n '^from models' agent/messenger.py` | empty |
| `_tier2_reprieve_signal` exists | `grep -n '_tier2_reprieve_signal' agent/agent_session_queue.py` | output > 0 |
| Dual-heartbeat branches in `_has_progress` | `grep -nc 'last_heartbeat_at\|last_sdk_heartbeat_at' agent/agent_session_queue.py` | 2+ matches |
| `DISABLE_PROGRESS_KILL` env var handled | `grep -n 'DISABLE_PROGRESS_KILL' agent/agent_session_queue.py` | output > 0 |

## Critique Results

Revision applied 2026-04-18 per `/do-plan-critique` war-room concerns. Verdict: READY TO BUILD (with concerns). All concerns addressed via embedded Implementation Notes in the plan text.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | Single queue-heartbeat signal is insufficient — leaves "SDK alive but queue loop wedged" case uncovered. False-negatives (killing working sessions) must be minimized above all else. | Two-tier detector with dual heartbeat (Tier 1) + activity-positive reprieve gates (Tier 2). Kill only when BOTH Tier 1 signals stale AND all Tier 2 gates negative. | See Solution → Key Elements (Two-Tier Architecture); Tier 1 dual-heartbeat OR semantics in `_has_progress` (task 5); Tier 2 gates in `_tier2_reprieve_signal` (task 6); combined in health check (task 7). |
| CONCERN | Operator | Need runtime kill-switch to roll out safely — collect data on Tier 1 flags + Tier 2 reprieves before enabling kills. | `DISABLE_PROGRESS_KILL=1` env var suppresses the kill transition while keeping flagging + logging active. | See Solution → Key Elements ("Kill-switch"); task 7 wires the env var; `TestDisableProgressKill` covers behavior. |
| CONCERN | Adversary | Messenger writing directly to AgentSession ORM breaks the architectural boundary documented in `docs/features/bridge-worker-architecture.md`. | Messenger gains three optional callback kwargs (`on_sdk_started`, `on_heartbeat_tick`, `on_stdout_event`); messenger imports nothing from `models/`. The queue wires the callbacks. | See Solution → Technical Approach step 2; `agent/messenger.py` gets no `from models` imports (verified in validate-concurrency task). |
| CONCERN | Operator | Session-to-pid mapping is needed for Tier 2 but plan's original `_active_sessions: dict[str, asyncio.Task]` doesn't carry pid. | Introduce `SessionHandle` dataclass wrapping `(task, pid)`. pid populated via `on_sdk_started` callback. | See Technical Approach step 3; `SessionHandle` module-level; used in tasks 4, 6, 7. |
| CONCERN | Skeptic | Observability is insufficient — without counters for Tier 1 flags, Tier 2 reprieves (per signal), and kills, operators can't tell whether the detector is working. | Added metrics: `tier1_flagged_total`, `tier2_reprieve_total.{alive|children|stdout}`, `kill_total`. Per-session `reprieve_count` field for post-hoc analysis. | See Architectural Impact → Interface changes; task 7 increments counters. |
| CONCERN | Adversary | Prior critique B2 (round-trip fields through `_AGENT_SESSION_FIELDS`) must be explicitly preserved — silent regression risk if the list isn't updated. | Task 1 explicitly calls out `_AGENT_SESSION_FIELDS` update for ALL FIVE new fields. Verification table has an explicit check for this. | See task 1; Verification table row "Fields in `_AGENT_SESSION_FIELDS`". |
| CONCERN | Simplifier | `asyncio.wait_for` 1.0s after cancel is too generous; SDK cleanup propagates near-instantly. | Changed to 0.25s (`TASK_CANCEL_TIMEOUT = 0.25`). | See Technical Approach step 4/7; verified via `test_recovery_cancels_active_session_task`. |
| CONCERN | Archaeologist | `recovery_attempts` increment must be gated on kill (Tier 1 AND Tier 2 both stuck), NOT on reprieves or worker restart. | Explicit rule in task 7; separate `reprieve_count` field for Tier 2 saves; Risk 3 + test `test_reprieve_does_not_increment_recovery_attempts` + `test_startup_recovery_does_not_increment_attempts`. | See Risk 3; task 7; tests in `TestRecoveryAttempts`. |
| CONCERN | Skeptic | Registry registration must precede all raise sites — otherwise health check has no handle for early failures. | Explicit rule in task 4: registration is the first operation, wrapped in try/finally. | See task 4; Technical Approach step 4 ("Registration must precede every raise site"). |
| CONCERN | Operator | T+0 heartbeat write matters — without it, the very first health-check tick after creation sees no heartbeat and may flag a healthy-but-young session. | Explicit T+0 write in task 4, before entering the heartbeat loop. | See Technical Approach step 4; Data Flow step 3. |
| INFO | Simplifier | CPU-delta reprieve gate was considered as a fifth signal. | Deferred — weakest signal, adds syscall cost, not needed alongside alive/children/stdout. Revisit if false-negatives appear in practice. | See Rabbit Holes; Solution → "Deferred". |

---

## Open Questions

All issue-level open questions were resolved during spikes. The revision (2026-04-18) also resolved the critique-stage concerns as embedded Implementation Notes in the Critique Results table above.

1. **Why doesn't messenger.py currently write progress fields?** — spike-3: because `BackgroundTask._watchdog` doesn't have the `AgentSession` ORM reference. REVISION: instead of keeping messenger ORM-free by writing only from the queue layer, we now use **optional callbacks** from queue into messenger (`on_sdk_started`, `on_heartbeat_tick`, `on_stdout_event`). Messenger stays ORM-free; queue provides the callback implementations that bump fields.

2. **What code path actually deletes session records that never reached terminal state?** — spike-2: the `Meta.ttl = 2592000` (30-day) Redis backstop. The correct fix is to ensure sessions always reach a terminal status within ~10 minutes (via `MAX_RECOVERY_ATTEMPTS = 2`), so the TTL becomes irrelevant.

3. **Should the guard timeout scale with prompt size?** — Dual-heartbeat + Tier 2 reprieve gates make this unnecessary. Large prompts keep heartbeats fresh; Tier 2 catches the "event loop wedged" case that prompt-size heuristics would have been aimed at.

4. **Is `psutil` available without a new dependency?** — spike-4: yes, already transitive via `tools/doctor.py` and `monitoring/bridge_watchdog.py`. Tier 2 reprieve gates use `psutil.Process(pid)` with full `try/except (psutil.NoSuchProcess, psutil.AccessDenied, ImportError)` wrapping.

No remaining open questions for the supervisor.
