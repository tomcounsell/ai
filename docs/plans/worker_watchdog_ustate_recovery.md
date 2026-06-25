---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-25
tracking: https://github.com/tomcounsell/ai/issues/1767
last_comment_id: 
---

# Worker Watchdog: Deterministic Recovery of a U-State Hung Worker

## Problem

On 2026-06-23, a `/update` restarted the worker mid-flight. The fresh worker (PID 60968)
wedged on a granite PTY read and entered OS **uninterruptible-sleep (`U`) state** — blocked
in a kernel syscall that cannot receive signals. The worker stopped writing heartbeats, every
in-flight session's heartbeat froze, the granite containers looped on `transcript read:
no-new-entry`, and the pending queue stopped draining. Three sessions stalled until a human
manually killed the wedged sessions, force-killed the worker once the syscall released, and
let `valor-catchup` re-enqueue the dropped messages.

The worker watchdog (`monitoring/worker_watchdog.py`) — a separate launchd service that exists
*precisely* to kill a wedged worker that launchd's own `KeepAlive` cannot detect — failed to
recover the incident for four compounding reasons.

**Current behavior:**

1. **launchd is blind to `U`-state.** `com.valor.worker.plist` uses `KeepAlive=true`, which
   only restarts a process that *exits*. A `U`-state process never exits.
2. **The watchdog's kill is unverified and cannot beat `U`-state.** `recover()`
   (`monitoring/worker_watchdog.py:184`) does `SIGTERM → sleep(3) → SIGKILL` then logs
   `"Worker killed — launchd will restart"` *without confirming the process died*. `SIGKILL`
   against a `U`-state process is queued, not delivered, until the blocking syscall returns —
   so the log line was emitted while PID 60968 was still alive.
3. **The detection threshold is lax.** `HEARTBEAT_THRESHOLD = 600` (10 min) with
   `StartInterval: 120s` yields up to a ~10-minute dead window before recovery is even attempted.
4. **The heartbeat is not isolated from the hang.** The worker heartbeat is written at the top
   of each health-loop tick (`agent/session_health.py:2647`), but the loop only reaches the next
   write *after* `await _agent_session_health_check()` returns — and that coroutine offloads work
   onto the **default thread-pool executor** (`run_in_executor(None, …)`), the same pool the
   granite `container.run` (`agent/granite_container/bridge_adapter.py:544`, via
   `asyncio.to_thread`) and the reflection scheduler (`agent/reflection_scheduler.py:413`) use.
   Three sessions each blocking a `container.run` saturate the pool; the health-check coroutine
   queues behind the hung reads and never returns, so the loop never writes the next heartbeat.
   This is why the heartbeat went stale at 564s — "stale heartbeat" propagated *from* the hang
   rather than being an independent oracle.
5. **Killed-session work is silently dropped.** Worker startup recovery
   (`_recover_interrupted_agent_sessions_startup`, `agent/session_health.py:531`) only re-queues
   `status="running"` sessions; the worker startup loop (`worker/__main__.py:407-424`) only
   re-kicks `status="pending"` sessions. `killed` sessions are never resumed, and nothing in the
   recovery path triggers `valor-catchup`.

**Desired outcome:**

A worker hung in `U`-state is detected within ~2-3 minutes and **deterministically** recovered:
the kill is verified-dead (or escalated through a ladder that closes the worker's PTY master fds
to force the blocking `os.read()` to return EOF — the only thing that frees a `U`-state read),
the heartbeat survives thread-pool/PTY saturation so "stale heartbeat" reliably means "wedged,"
and the dead worker's in-flight sessions are swept to `killed` and their unanswered human
messages re-enqueued — all with no human intervention.

## Freshness Check

**Baseline commit:** `3251104a0316689d96acd5741b2fa36de199f0cb`
**Issue filed at:** 2026-06-23T06:01:17Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `monitoring/worker_watchdog.py:184` `recover()` — still holds. `SIGTERM → sleep(3) → SIGKILL`,
  logs success without a verify poll. Confirmed verbatim.
- `monitoring/worker_watchdog.py:64` `HEARTBEAT_THRESHOLD = 600` — still holds.
- `agent/reflection_scheduler.py:411` shared default executor — drifted to **`:413`**
  (`return await loop.run_in_executor(None, call)`); claim holds.
- `worker/__main__.py:206,412` only re-kicks pending — still holds; startup recovery sequence
  runs at `worker/__main__.py:339-345` (calls `_recover_interrupted_agent_sessions_startup`),
  pending re-kick loop at `:407-424`.
- `agent/granite_container/` PTY read — confirmed at `pty_driver.py:545`
  (`self._child.read_nonblocking(size=8192, timeout=0.5)` inside a deadline-gated loop,
  `pty_driver.py:536`). `container.run` offloaded via `asyncio.to_thread` at
  `bridge_adapter.py:544`.
- #1789 fields — confirmed present: `last_pty_read_loop_at` (`models/agent_session.py:388`),
  `last_pty_activity_at` (`:392`), `mid_run_quiescent_since` (`:397`), written by the
  `_on_pty_read` callback (`bridge_adapter.py:753-780`). The default-tier liveness gate
  `_pty_quiescent_long_enough` lives at `agent/session_health.py:388-462`.

**Cited sibling issues/PRs re-checked:**
- #1784 / PR #1789 — MERGED, present in this worktree (commit `2408c9d2`). The PTY-liveness
  gate is the canonical definition of granite session liveness; this plan **reconciles with**
  rather than re-derives it.
- #1768 (companion early-detection layer) — **CLOSED**; PR #1773 (`stall-advisory actor +
  granite_wedged signal`) merged 2026-06-23. The session-level prevention layer already shipped.
  This issue (#1767) is the independent worker-level backstop, exactly as the companion note
  promised. No scope overlap — #1773 strengthens *session* recovery while the worker event loop
  is alive; it cannot help when the worker itself is wedged.

**Commits on main since issue was filed (touching referenced files):**
- `2408c9d2` feat(session-health): gate default-tier tool_timeout kill on PTY liveness (#1789)
  — adds the liveness fields this plan reuses. Relevant: *reduces* false-positive session kills
  but does NOT touch the worker watchdog or the heartbeat path. Root cause unchanged.
- `3251104a` fix(granite): trivial messages get a one-line ack — irrelevant to this path.

**Active plans in `docs/plans/` overlapping this area:** none. (`worker_lifecycle_cleanup.md`,
`worker-kickstart-race.md` touch adjacent watchdog code but address different failure modes;
neither covers verified-kill, heartbeat isolation, or U-state escalation.)

**Notes:** The companion #1768 closing since filing is the only material landscape shift, and it
confirms the split-of-concerns this plan assumes. `reflection_scheduler` line corrected 411→413.

## Prior Art

- **#1055 / closed**: *Sync Anthropic calls in memory_extraction freeze the worker event loop
  and block session finalization* — same failure *class* (a synchronous call on a shared
  executor stalls the worker loop). Resolution moved the offending call off the hot path. Direct
  precedent for the "heartbeat must not share fate with blocking work" element of this plan.
- **#1311 / PR #1315**: *Worker watchdog active recovery via launchctl kickstart* — added the
  L1→L4 escalation ladder for the **missing-worker** path (`_handle_missing_worker`). This plan
  extends the *stale-heartbeat* path (`recover()`) with an analogous verified ladder; reuses the
  existing `_verify_worker_alive` poll-primitive pattern.
- **#1407 / closed**: *L2/L3 broken: launchctl load vs bootstrap mismatch* — established the
  `launchctl bootout`/`bootstrap` semantics this plan's `bootout` escalation step relies on.
- **#1331 / closed**: *Watchdog kills healthy worker: pgrep case-sensitivity* — cautionary prior
  art: tightening detection (this plan lowers the threshold to ~180s) risks false positives.
  Reinforces the requirement for a heartbeat that is a *trustworthy* hang oracle before tightening.
- **#1614 / closed**: *Ungated sticky own-progress fields let a stale session evade recovery* —
  precedent that "alive-looking" signals must be gated on freshness; informs why we gate the
  session sweep on `claude_pid` liveness, not on status alone.
- **#1537 (in code)**: `_confirm_subprocess_dead` / `SubprocessKillResult`
  (`agent/session_health.py:1205`) already implements verified SIGTERM→SIGKILL with a poll loop
  for *session* subprocesses. This plan reuses that exact pattern for the *worker* kill.

## Research

No relevant external findings needed — `U`-state semantics (`man ps`, `os.read` blocking on a
PTY master that cannot be SIGKILL-interrupted until the fd is closed) are standard Unix behavior
and well-covered by codebase context and the issue body. Proceeding with codebase context.

## Data Flow

The recovery path crosses three processes (watchdog, dying worker, fresh worker) and Redis/Popoto:

1. **Entry point**: launchd fires `worker_watchdog.py` every 120s (`StartInterval`).
2. **Detect**: `check()` stat()s `data/last_worker_connected`. If the heartbeat age exceeds the
   (tightened) threshold AND a worker PID is found → `status="stale"`.
3. **Verified kill (new ladder)**: `recover()` sends SIGTERM→SIGKILL, then **polls
   `os.kill(pid, 0)`** for a grace window. If the PID survives (true `U`-state), escalate:
   `launchctl bootout` the job → if still alive, **close the worker's PTY master fds** (the only
   thing that frees a blocked `os.read`) → re-verify → if still alive, write a CRITICAL Redis key
   and alert.
4. **launchd respawn**: once the worker actually exits, `KeepAlive=true` respawns it.
5. **Post-restart sweep (new)**: the fresh worker's startup recovery enumerates the dead worker's
   `running` sessions, checks each `claude_pid` for liveness (`os.kill(pid, 0)`), flips
   dead-PID sessions `running → killed`, then triggers `valor-catchup` so genuinely-unanswered
   human messages re-enqueue as fresh `pending` sessions.
6. **Output**: pending queue drains; the human's original message is answered by a fresh session.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1315 (#1311) | Built the L1→L4 ladder for the **missing-worker** path | Only the `status="down"` branch got the verified ladder. The `status="stale"` branch (`recover()`) was left with the unverified `SIGTERM→sleep→SIGKILL` — exactly the path a U-state hang takes. |
| Original `recover()` | `SIGTERM → sleep(3) → SIGKILL`, log success | Assumes SIGKILL is *delivered*. Against a U-state process SIGKILL is *queued*; the log lies and the watchdog never escalates because there is no re-verify poll. |
| #1226/#1356/#1614 session-health work | Tightened *session*-level liveness detection | All run *inside the worker event loop*. A wedged worker stops ticking them — they cannot self-heal a hung worker (the issue's "why this can't be a reflection" point). |

**Root cause pattern:** every prior fix improved detection/recovery *inside* the worker or only
for the *missing-worker* path. The U-state hung-but-alive worker falls in the gap: it is neither
missing (so launchd and the down-path ladder don't fire) nor self-healable (the in-process loops
are frozen), and the one external actor that should catch it — the stale-heartbeat `recover()` —
trusts an unverified kill against a process that ignores it.

## Architectural Impact

- **New dependencies**: none. Reuses `os.kill`, `launchctl`, `psutil` (already a dep), Redis via
  Popoto.
- **Interface changes**: `recover()` gains a verified escalation ladder (internal refactor; same
  call site). A new pure helper `_worker_pty_master_fds(pid)` discovers the wedged worker's PTY
  master fds (via `psutil.Process(pid).open_files()` / `/dev/fd`). A new
  `_sweep_dead_worker_sessions()` runs in worker startup recovery.
- **Coupling**: the watchdog becomes aware of *which* fds to close — a deliberate, narrow coupling
  to the granite PTY mechanism, justified because closing the blocking fd is the only OS-level
  remedy for a U-state read. Documented as such.
- **Data ownership**: the post-restart sweep is the new owner of the `running → killed` transition
  for dead-worker sessions; it must use the same `update_session`/`finalize_session` Popoto paths
  (never raw Redis) as existing recovery.
- **Reversibility**: high. Threshold is an env-tunable constant; the escalation ladder and sweep
  are additive and gated; a kill-switch env var (`WORKER_WATCHDOG_PTY_CLOSE_DISABLED`) disables
  the most aggressive step.

## Appetite

**Size:** Medium

**Team:** Solo dev (builder), async-specialist (escalation-ladder review), validator, documentarian

**Interactions:**
- PM check-ins: 1-2 (confirm threshold value and which escalation steps are auto vs alert-only)
- Review rounds: 1 (async/process-safety review of the kill ladder + sweep idempotency)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (Popoto) | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as R; R.ping()"` | Watchdog critical-key + sweep transitions |
| psutil available | `python -c "import psutil"` | PTY-fd discovery and process status checks |

Run all checks: `python scripts/check_prerequisites.py docs/plans/worker_watchdog_ustate_recovery.md`

## Solution

### Key Elements

- **Heartbeat isolation (oracle fix)**: Move the worker heartbeat write off the shared default
  executor's fate. Run `_write_worker_heartbeat()` on a **dedicated daemon OS thread** with its
  own short loop (independent of the asyncio health loop), so saturation of the default thread
  pool by `container.run` can never delay it. After this, a stale heartbeat is an *unambiguous*
  "worker loop is wedged" signal — the precondition for tightening the threshold.
- **Tightened detection threshold**: Lower `HEARTBEAT_THRESHOLD` from 600s toward ~180s. Safe
  *only because* of the heartbeat-isolation element: the dedicated thread writes every ~30s, so a
  180s age means ~6 missed writes — far beyond any legitimate stall of a thread that does nothing
  but a file write. With `StartInterval=120s`, worst-case detection is ~180s + one tick ≈ 5 min;
  add the verified-kill ladder and recovery completes within the issue's ~2-3 min *detection*
  target plus a bounded kill grace.
- **Verified-kill escalation ladder** in `recover()`:
  - **W1**: SIGTERM → poll `os.kill(pid,0)` for a grace window.
  - **W2**: SIGKILL → poll again. (Matches `_confirm_subprocess_dead` semantics from #1537.)
  - **W3** (still alive = true U-state): `launchctl bootout gui/<uid>/com.valor.worker` to detach
    the job, then re-verify.
  - **W4** (still alive): **close the worker's PTY master fds** to force the blocked `os.read()`
    to return EOF — the only OS remedy for a U-state read. Gated by
    `WORKER_WATCHDOG_PTY_CLOSE_DISABLED` kill-switch.
  - **W5** (still alive after grace): write `worker:watchdog:critical:{host}` Redis key + log
    CRITICAL (alert-only; matches the existing missing-worker L4).
- **Post-restart session sweep** in worker startup recovery: for each `running` session belonging
  to the dead worker, if `claude_pid` is not alive, transition `running → killed` (via Popoto
  `finalize_session`), then trigger `valor-catchup` so unanswered human messages re-enqueue. Idempotent
  and gated on `claude_pid` liveness so a session a *live* new worker already picked up is never swept.

### Flow

launchd tick → `check()` reads isolated heartbeat → stale → `recover()` W1 SIGTERM → poll →
W2 SIGKILL → poll → (U-state) W3 bootout → poll → W4 close-PTY-master-fds → poll → worker exits →
launchd respawns worker → startup recovery sweeps dead-worker `running` sessions → flips dead-PID
sessions to `killed` → triggers `valor-catchup` → unanswered messages re-enqueue → queue drains.

### Technical Approach

- **Heartbeat thread**: add a `threading.Thread(daemon=True)` started in `worker/__main__.py`
  startup that loops `_write_worker_heartbeat()` every `WORKER_HEARTBEAT_INTERVAL` (~30s) on
  `time.sleep`. The function is already a pure local-file write + Redis `set`; it does NOT touch
  the event loop. Remove the synchronous `_write_worker_heartbeat()` call from the asyncio health
  loop's top (`agent/session_health.py:2647`) to avoid double-writing and to sever the loop's role
  as the heartbeat author. **The thread must use its own Redis connection** (Popoto's
  `POPOTO_REDIS_DB` is process-global; confirm thread-safety of the `set` call in build — if the
  connection is not thread-safe, create a dedicated client in the thread).
- **PTY-fd discovery**: `_worker_pty_master_fds(pid)` uses `psutil.Process(pid).open_files()` to
  enumerate fds, filters to PTY masters (path under `/dev/ptmx` or matching pty patterns), and
  returns the integer fds. The watchdog runs as the same uid and can `os.close` them in the target
  *only* via the `/dev/fd` path of the target process — **research note for build**: a process
  cannot directly close another process's fd; the realistic W4 mechanism is to send the worker a
  custom signal handler that closes its own PTY fds, OR to `bootout`+`SIGKILL` and rely on kernel
  fd teardown when the process is finally reaped. **Spike-1 resolves which W4 mechanism is real
  before build commits to it.**
- **Reconcile with #1789**: the sweep's "is this session genuinely dead" decision uses
  `claude_pid` liveness (process-level), NOT the `mid_run_quiescent_since` PTY-liveness gate (that
  gate decides whether to *kill a running tool inside a live worker* — a different question). A
  session whose worker is dead is dead regardless of PTY paint state. Do not invoke
  `_pty_quiescent_long_enough` in the sweep.
- **Threshold + interval as env-tunable constants**: `HEARTBEAT_THRESHOLD` (default lowered),
  `WORKER_HEARTBEAT_INTERVAL`, `WORKER_WATCHDOG_PTY_CLOSE_DISABLED`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `recover()` swallows kill exceptions (`except Exception` at `worker_watchdog.py:205`) — each
      escalation step's failure must log AND advance to the next ladder rung; add a test asserting
      a failed W3 `bootout` still attempts W4 and that the CRITICAL key is written if all fail.
- [ ] Heartbeat thread loop must `except Exception: log + continue` (never die silently) — test
      that one failing write does not stop the thread.
- [ ] Sweep's `finalize_session` failure path: assert a Popoto error on one session does not abort
      the sweep of the others (per-session try/except with WARNING).

### Empty/Invalid Input Handling
- [ ] `_worker_pty_master_fds(pid)` with a dead/None PID returns `[]` (no crash).
- [ ] Sweep with zero dead-worker `running` sessions is a no-op (and does NOT spuriously trigger
      `valor-catchup`).
- [ ] `recover()` when the PID is already gone at W1 returns success without escalating.

### Error State Rendering
- [ ] CRITICAL Redis key + CRITICAL log fire when the full ladder fails — assert both surfaces
      (matches existing missing-worker L4 test pattern in `test_worker_watchdog.py`).
- [ ] Sweep logs a structured summary line (`N swept → killed, valor-catchup triggered`) so an
      operator can see recovery happened.

## Test Impact

- [ ] `tests/unit/test_worker_watchdog.py::TestLoggerConfiguration` — UPDATE: unaffected by logic
      change but re-run after refactor to confirm single-handler invariant holds.
- [ ] `tests/unit/test_worker_watchdog.py` (the `recover()` / stale-path coverage) — REPLACE: the
      current stale path has no verify assertions; rewrite to assert the W1→W5 ladder, the
      verify-poll after each kill, and that the CRITICAL key is written only after the full ladder
      fails. Add a regression test reproducing the unverified-kill bug (kill "queued not
      delivered" → watchdog must re-verify and escalate, NOT log success).
- [ ] `tests/integration/test_watchdog_recovery.py` — UPDATE: add a U-state escalation scenario
      (PID survives SIGKILL via a mocked `os.kill(pid,0)` that keeps reporting alive) and assert
      the ladder advances to bootout/PTY-close and finally the CRITICAL alert.
- [ ] `agent/session_health.py` heartbeat write — REPLACE the assumption in any test asserting the
      health loop writes the heartbeat: search `tests/` for `_write_worker_heartbeat` /
      `last_worker_connected` and repoint to the new dedicated-thread writer. (`grep -rn
      'last_worker_connected\|_write_worker_heartbeat' tests/` during build to enumerate.)
- [ ] Startup recovery tests touching `_recover_interrupted_agent_sessions_startup` — UPDATE: add
      coverage for the new dead-PID `running → killed` sweep and `valor-catchup` trigger; assert
      a session whose `claude_pid` IS alive is NOT swept (idempotency / live-worker guard).

## Rabbit Holes

- **Rewriting the granite PTY read to be non-blocking / fully cancellable.** Tempting but huge —
  the `pexpect` read loop is the heart of the container. The watchdog backstop + heartbeat
  isolation solves the production incident without touching `pty_driver.py`'s read mechanism.
  Per-read cancellability is a separate concern; keep the W4 fd-close as the targeted remedy.
- **Building a generic cross-process fd-closing facility.** The `U`-state remedy is narrow;
  resist generalizing into a "kill anything stuck on any fd" tool. Spike-1 picks the minimal
  real mechanism.
- **Replacing launchd `KeepAlive` with a custom supervisor.** Out of scope; the watchdog
  augments launchd, it does not replace it.
- **Re-deriving granite session liveness.** #1789 already defines it; reuse, don't reinvent.

## Risks

### Risk 1: Tightening the threshold causes false-positive worker kills
**Impact:** A legitimately long syscall (or a heartbeat-thread starvation) trips a kill of a
healthy worker, dropping in-flight work.
**Mitigation:** The threshold tightening is *contingent on* the heartbeat-isolation element — the
dedicated thread does nothing but a file write, so it cannot be starved by `container.run`
saturation. Keep `HEARTBEAT_THRESHOLD` env-tunable and conservatively above the heartbeat interval
(≥6× the ~30s write cadence). Prior art #1331 is the cautionary precedent.

### Risk 2: W4 (closing PTY master fds) is not actually possible from the watchdog process
**Impact:** The most-aggressive step is a no-op and a true U-state never frees.
**Mitigation:** Spike-1 resolves the real W4 mechanism *before* build. If cross-process fd close is
infeasible, W4 falls back to repeated `bootout` + `SIGKILL` and the CRITICAL alert fires sooner —
the verified-kill + sweep + tightened-threshold elements still deliver the bulk of the fix.
Kill-switch `WORKER_WATCHDOG_PTY_CLOSE_DISABLED` lets ops disable W4 independently.

### Risk 3: Post-restart sweep races a live new worker that already picked up a session
**Impact:** The sweep kills a session a healthy worker is actively running → double-drop.
**Mitigation:** Gate every transition on `os.kill(claude_pid, 0)` liveness AND the existing
`AGENT_SESSION_HEALTH_MIN_RUNNING` (300s) recency guard, and use CAS via
`finalize_session(expected_status="running")` so a concurrent pickup loses the race safely (same
pattern as `_recover_interrupted_agent_sessions_startup`).

### Risk 4: Heartbeat thread and asyncio loop both writing Redis registered-PID key
**Impact:** Redis connection contention or duplicate writes.
**Mitigation:** The registered-PID refresh moves to the dedicated thread with the heartbeat; the
asyncio loop stops calling it. Confirm Popoto `POPOTO_REDIS_DB` thread-safety in build; create a
dedicated client in the thread if needed.

## Race Conditions

### Race 1: Sweep vs. concurrent worker pickup of the same session
**Location:** worker startup recovery (`agent/session_health.py`, new `_sweep_dead_worker_sessions`)
**Trigger:** Fresh worker A starts the sweep while worker B (or A's own pending loop) picks up a
session and transitions it `pending → running` with a fresh `claude_pid`.
**Data prerequisite:** `claude_pid` must reflect the *current* live subprocess before the sweep
reads it.
**State prerequisite:** Only sessions whose `claude_pid` is dead AND whose `started_at` predates
the recency guard may be swept.
**Mitigation:** Liveness check (`os.kill(pid,0)`) + recency guard + CAS
`finalize_session(expected_status="running")`. A session re-picked-up between read and transition
fails CAS and is skipped.

### Race 2: Watchdog kill vs. worker exiting on its own during the grace poll
**Location:** `recover()` verify-poll loop
**Trigger:** The worker's blocking syscall returns and the worker exits naturally mid-ladder.
**Data prerequisite:** none.
**State prerequisite:** PID-reuse window — macOS recycles PIDs (~5 min); the ladder must not
escalate against a recycled PID.
**Mitigation:** Each rung re-checks `os.kill(pid,0)` immediately before acting; once the PID is
gone the ladder returns success. Bound the total ladder wall-clock (single-digit seconds per rung)
so the watchdog tick never stalls; accept the documented PID-reuse residual risk (matches #1537).

### Race 3: Heartbeat thread writes during worker shutdown
**Location:** dedicated heartbeat thread
**Trigger:** Worker is being killed; thread writes a fresh heartbeat moments before exit, briefly
masking staleness.
**Data prerequisite:** none.
**State prerequisite:** none — a daemon thread dies with the process.
**Mitigation:** Acceptable: the window is one write interval, and the watchdog re-evaluates each
tick. The thread is a daemon so it cannot outlive the process or block shutdown.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1768] Session-level early detection of heartbeating-but-wedged granite sessions
  (stall-advisory actor + `granite_wedged` signal). Already shipped via PR #1773; this plan is the
  complementary worker-level backstop and does not touch the session-level layer.
- [EXTERNAL] Reinstalling the updated watchdog plist on the production bridge machine — requires a
  `/update` run on that machine (the agent cannot reach it from this skills-only machine).
- Rewriting `agent/granite_container/pty_driver.py`'s blocking read into a cancellable async read —
  not deferred-as-laziness; it is a genuinely separate, large refactor whose absence does not block
  this fix (the watchdog + heartbeat isolation solve the incident). [SEPARATE-SLUG #1767] tracks
  only the backstop; if per-read cancellability is later wanted it is a fresh issue.

## Update System

- **Watchdog plist unchanged structurally** but `StartInterval` stays 120s; the threshold change
  is in `monitoring/worker_watchdog.py` (Python constant), so no plist edit is strictly required.
  If we add new env vars (`WORKER_HEARTBEAT_INTERVAL`, `WORKER_WATCHDOG_PTY_CLOSE_DISABLED`,
  tunable `HEARTBEAT_THRESHOLD`) they have safe defaults — no `.env` change needed for correctness.
- **`scripts/install_worker.sh`** renders both the worker and watchdog plists. No template change
  required unless a new env var must be injected into the plist; document defaults so a stale plist
  still behaves correctly.
- **Migration for existing installations**: `/update` already reinstalls the watchdog plist
  (idempotent bootout+bootstrap) and restarts the worker, which starts the new heartbeat thread —
  no manual migration step. Document in the update skill notes that the watchdog logic changed so
  operators know to expect verified-kill log lines.
- **Optional `/update` quiesce** (issue open question): a complementary mitigation would have
  `scripts/update/run.py` await in-flight granite PTY sessions before `launchctl bootout` of the
  worker (`scripts/update/run.py:1278-1310` currently bootout→bootstrap with no drain). Deferred to
  an open question — the watchdog backstop makes it optional, not required.

## Agent Integration

No new agent-facing capability. This is bridge/worker-internal self-healing infrastructure. The
existing `valor-catchup` CLI (`valor-catchup = "bridge.agent_catchup:main"`, `bridge/agent_catchup.py`)
is invoked by the new sweep via subprocess (same invocation the `/update` catchup step uses) — no
new entry point, no `.mcp.json` change, no bridge import change. Integration coverage is the
sweep→catchup test asserting the subprocess is invoked when (and only when) dead-worker sessions
are swept.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` — add the worker-watchdog verified-kill
      escalation ladder (W1→W5), the tightened threshold + heartbeat-isolation rationale, and the
      post-restart session sweep. This is named explicitly in the issue's acceptance criteria.
- [ ] Update `docs/features/granite-pty-production.md` — note the U-state read failure mode and the
      watchdog's PTY-fd-close remedy (W4) as the external backstop.
- [ ] Add/confirm entry in `docs/features/README.md` index for the updated self-healing behavior.

### Inline Documentation
- [ ] Docstring on the new escalation ladder in `recover()` enumerating each rung and its
      auto-vs-alert disposition.
- [ ] Docstring on the dedicated heartbeat thread explaining *why* it is off the event loop
      (cite this incident + #1055).
- [ ] Docstring on `_sweep_dead_worker_sessions` explaining the `claude_pid`-liveness gate and the
      `valor-catchup` trigger.

## Success Criteria

- [ ] A worker in `U`-state with a stale heartbeat is recovered without human intervention, with
      the kill **verified dead** (or escalated through the ladder) — proven by an integration test
      simulating a PID that survives SIGKILL until the fd-close/bootout rung.
- [ ] Detection-to-recovery window reduced from ~10 min toward ~2-3 min detection, with the
      documented false-positive guard (heartbeat isolation + ≥6× interval threshold).
- [ ] Worker heartbeat writes survive default-thread-pool / PTY saturation — proven by a test that
      saturates the default executor and asserts the heartbeat file mtime still advances.
- [ ] After a hung-worker kill, dead-worker `running` sessions are swept to `killed` and
      `valor-catchup` is triggered (no silently dropped human messages) — proven by a sweep test.
- [ ] A live-worker session (alive `claude_pid`) is NOT swept (idempotency / no double-drop).
- [ ] Tests cover: unverified-kill regression, U-state escalation path, post-restart session sweep.
- [ ] `docs/features/bridge-self-healing.md` updated with the new ladder and thresholds.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms the sweep references `valor-catchup` / `bridge.agent_catchup`.

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (watchdog-ladder)**
  - Name: watchdog-builder
  - Role: Implement the verified-kill W1→W5 escalation ladder in `recover()` and the PTY-fd
    discovery helper.
  - Agent Type: builder
  - Resume: true

- **Builder (heartbeat-isolation)**
  - Name: heartbeat-builder
  - Role: Move the heartbeat write to a dedicated daemon thread; sever the asyncio-loop write;
    tighten the threshold constant.
  - Agent Type: builder
  - Resume: true

- **Builder (session-sweep)**
  - Name: sweep-builder
  - Role: Implement `_sweep_dead_worker_sessions` in startup recovery + `valor-catchup` trigger.
  - Agent Type: builder
  - Resume: true

- **Reviewer (process-safety)**
  - Name: async-reviewer
  - Role: Review the kill ladder for PID-reuse safety, the heartbeat thread for Redis
    thread-safety, and the sweep for CAS/idempotency races.
  - Agent Type: async-specialist
  - Resume: true

- **Validator (recovery)**
  - Name: recovery-validator
  - Role: Verify all success criteria; run the unit + integration watchdog suites.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: self-healing-doc
  - Role: Update self-healing + granite-pty docs.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### spike-1: Determine the real W4 mechanism for freeing a U-state read
- **Task ID**: spike-pty-fd-close
- **Assumption**: "The watchdog can force a blocked `os.read()` on the worker's PTY master to
  return EOF — either by closing the fd cross-process, or by signalling the worker to close its
  own fds, or by a bootout+reap path."
- **Method**: code-read + prototype (worktree-isolated)
- **Agent Type**: Explore (code-read) / builder in worktree (prototype)
- **Time cap**: 10 min
- **Result**: [filled after spike]
- **Confidence**: [filled]
- **Impact if false**: W4 falls back to repeated bootout+SIGKILL and earlier CRITICAL alert;
  documented in the ladder.

### 1. Heartbeat isolation
- **Task ID**: build-heartbeat-isolation
- **Depends On**: none
- **Validates**: `tests/unit/test_worker_watchdog.py` (heartbeat-oracle), new saturation test
- **Assigned To**: heartbeat-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a daemon heartbeat thread in `worker/__main__.py` writing `_write_worker_heartbeat()` every
  `WORKER_HEARTBEAT_INTERVAL` (~30s); confirm/ensure thread-safe Redis use.
- Remove the synchronous `_write_worker_heartbeat()` call from the asyncio health loop
  (`agent/session_health.py:2647`).
- Lower `HEARTBEAT_THRESHOLD` (env-tunable) to ~180s with the ≥6×-interval guard documented.

### 2. Verified-kill escalation ladder
- **Task ID**: build-watchdog-ladder
- **Depends On**: spike-pty-fd-close
- **Validates**: `tests/unit/test_worker_watchdog.py` (ladder), `tests/integration/test_watchdog_recovery.py`
- **Informed By**: spike-pty-fd-close (W4 mechanism), #1537 `_confirm_subprocess_dead` pattern
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `recover()`'s unverified kill with W1→W5: SIGTERM→poll, SIGKILL→poll, bootout→poll,
  PTY-fd-close (gated by `WORKER_WATCHDOG_PTY_CLOSE_DISABLED`)→poll, CRITICAL key+log.
- Add `_worker_pty_master_fds(pid)` per spike-1's chosen mechanism.

### 3. Post-restart session sweep
- **Task ID**: build-session-sweep
- **Depends On**: none
- **Validates**: startup-recovery tests (new sweep + catchup trigger + live-worker guard)
- **Assigned To**: sweep-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_sweep_dead_worker_sessions`: enumerate `running` sessions, `claude_pid`-liveness gate +
  recency guard + CAS, transition dead-PID sessions `running → killed`, trigger `valor-catchup`.
- Wire it into the worker startup recovery sequence (`worker/__main__.py:339-345` region).

### 4. Process-safety review
- **Task ID**: review-process-safety
- **Depends On**: build-watchdog-ladder, build-heartbeat-isolation, build-session-sweep
- **Assigned To**: async-reviewer
- **Agent Type**: async-specialist
- **Parallel**: false
- Review PID-reuse handling, Redis thread-safety, sweep CAS/idempotency, kill-grace wall-clock bound.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: review-process-safety
- **Assigned To**: self-healing-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` and `docs/features/granite-pty-production.md`;
  confirm `docs/features/README.md` index.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run unit + integration watchdog suites; verify every success criterion; generate report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_worker_watchdog.py tests/integration/test_watchdog_recovery.py -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/ agent/ worker/` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/ agent/ worker/` | exit code 0 |
| Recover re-verifies kill | `grep -n "os.kill(.*, 0)" monitoring/worker_watchdog.py` | output > 1 |
| Ladder escalation present | `grep -n "bootout" monitoring/worker_watchdog.py` | output contains bootout |
| Heartbeat off event loop | `grep -n "Thread" worker/__main__.py` | output contains Thread |
| Sweep triggers catchup | `grep -rn "agent_catchup\|valor-catchup" agent/session_health.py worker/__main__.py` | output contains catchup |
| Threshold tightened | `grep -n "HEARTBEAT_THRESHOLD" monitoring/worker_watchdog.py` | output does not contain 600 |
| No raw Redis on sessions | `grep -n "r.delete\|r.srem\|hgetall" agent/session_health.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Threshold value.** Is ~180s the right `HEARTBEAT_THRESHOLD`, or do you want a more
   conservative ~240-300s for the first production rollout given the false-positive history
   (#1331)? The heartbeat-isolation element makes 180s *safe*, but the choice is a risk-appetite call.
2. **W4 disposition (auto vs alert-only).** Once spike-1 determines the real PTY-fd-close
   mechanism: should W4 (the aggressive fd-close) run **automatically**, or should the ladder stop
   at W3 (bootout) + CRITICAL alert and leave the fd-close as an operator-confirmed step? The
   issue lists this as deliberately open.
3. **`/update` quiesce.** Should `scripts/update/run.py` await in-flight granite PTY sessions
   before restarting the worker (the incident trigger), as a complementary mitigation — or is the
   watchdog backstop + sweep sufficient and quiesce a separate follow-up? Currently scoped as a
   No-Go / open question, not built.
