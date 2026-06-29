---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-29
tracking: https://github.com/tomcounsell/ai/issues/1815
last_comment_id:
---

# Worker Liveness Recovery — Dead-Man's-Switch + Bounded PTY Waits

## Problem

The worker is supervised by launchd, which restarts a service only when the
**process exits**. A wedged asyncio event loop never exits — so a frozen worker
reports green forever and advances zero work until a human notices and restarts
it (incidents #1808, #1537, #1767). The self-audit found this wedge class is
*instrumented but not fixed*.

Two reinforcing code facts make it unrecoverable today:

- **The heartbeat is a deliberate lie about loop health.** `worker/__main__.py:74-94`
  runs an off-loop daemon thread that writes `data/last_worker_connected` every 30s
  *unconditionally* (done on purpose in #1767 so PTY/thread-pool saturation can't
  starve the write). A synchronously-frozen event loop still reports alive.
- **The PTY pool has unbounded waits on events a dead slot never sets.**
  `agent/granite_container/pty_pool.py:285,358,362`. The POOL-1 hazard (docstring
  lines 40-50): a slot whose respawn task dies is left `respawning` forever; the
  next `acquire_pair` blocks on its `event.wait()` with no timeout. Each parked
  acquirer holds a semaphore permit → permits exhaust → the whole granite path
  deadlocks.

**Current behavior:** A synchronous freeze or a stuck-`respawning` PTY slot wedges
the worker indefinitely; the dashboard shows green; recovery requires a human
`worker-restart`.

**Desired outcome:** A synchronous loop freeze converts itself into a process
*crash* that launchd respawns within a bounded window, and a PTY-pool acquirer can
never block forever — a stuck `respawning` slot is force-recycled after a bounded
wait.

## Scope Decision (read this first)

Issue #1815 ranks **six** fixes and recommends landing the smallest first. This
plan deliberately scopes to the **first two landings only**, which together are
shippable and reviewable in one PR:

| In this plan | Fix | Type | Why first |
|---|---|---|---|
| ✅ | **#4 — Bound every `await event.wait()` in the PTY pool** | REAL FIX (narrow) | "Smallest blast radius — good first landing" (issue). One file. |
| ✅ | **#1 — Invert the off-loop heartbeat into a loop-driven dead-man's-switch** | REAL FIX | "The safety net under the rest" (issue). Converts wedge→crash→launchd respawn. |

**Split into separate filed issues** (including them would bloat this PR beyond
reviewable size; each builds on the primitives landed here):

- Fix #2 (lease semaphore) + Fix #3 (progress-deadline cancel scope) → **#1820**
- Fix #5 (out-of-domain recovery) + Fix #6 (per-tool budget backstop) → **#1821**

The two fixes here are complementary and address **different** wedge mechanisms:
fix #1 detects a *synchronous* event-loop freeze (the loop thread itself stops
spinning); fix #4 bounds *asyncio awaits* parked inside the PTY pool (the loop
keeps spinning but a coroutine is stuck). Neither subsumes the other. Per the
issue's own caveat, fix #1 makes wedges recoverable by *restart* (lossy, re-queues
work); the lossless self-healing of the slot itself is fix #2, deferred to #1820.

Two of the six acceptance criteria in #1815 (leaked-permit reclaim; recovery runs
from another process) belong to the deferred fixes #2/#5 and are explicitly **not**
claimed by this PR — see Success Criteria.

## Freshness Check

**Baseline commit:** `b7fd781b32da5f80596bc68f88ad516e9900893b`
**Issue filed at:** 2026-06-29T09:21:23Z
**Disposition:** Unchanged

The issue was filed ~2h before planning. No commits have landed on main touching
`worker/__main__.py`, `agent/granite_container/pty_pool.py`, `agent/session_state.py`,
or `agent/agent_session_queue.py` since the issue was filed (`git log --since` empty).

**File:line references re-verified against `b7fd781b`:**
- `worker/__main__.py:74-94` — off-loop `_heartbeat_thread_main` writing unconditionally — **still holds** (verified verbatim; thread started ~`:525-535`).
- `worker/__main__.py:48` — `WORKER_HEARTBEAT_INTERVAL = int(os.environ.get("WORKER_HEARTBEAT_INTERVAL", "30"))` — **still holds**.
- `agent/granite_container/pty_pool.py:285` — `await self._sem.acquire()` in `acquire_pair()` — **still holds**.
- `agent/granite_container/pty_pool.py:358` — `await self._slot_available.wait()` in `_wait_for_idle_slot()` — **still holds**.
- `agent/granite_container/pty_pool.py:362` — `await slot.event.wait()` (the POOL-1 hazard) — **still holds**; the divergent-primitive wake (a healthy slot finishing respawn notifies the condition var, not the parked event) is at `:540-568`.
- `agent/granite_container/pty_pool.py:40-50` — POOL-1 hazard docstring — **still holds**.
- `agent/session_state.py:75` — `_global_session_semaphore: asyncio.Semaphore | None = None` — **still holds**; natural home for `last_loop_tick`.
- `agent/session_health.py:3001-3017` — `_write_worker_heartbeat()` (writes `data/last_worker_connected`, refreshes Redis PID #1271) — **still holds**.

**Cited sibling issues/PRs re-checked:**
- #1808 — OPEN — "wedged-but-alive worker leaves sessions pending"; the investigation this fix operationalizes.
- #1818 — OPEN — tracking umbrella for the 4-issue resilience cluster (#1814/#1815/#1816/#1817).
- #1767 — the heartbeat-isolation change this plan *inverts*; its rationale (off-loop write survives saturation) is preserved by keeping the watchdog off-loop.
- #1712 — the bridge's "update-loop wedged" detector (Telethon handler silently stops) — precedent for loop-liveness detection from outside a frozen loop; the conceptual mirror of the deferred fix #5.

**Active plans in `docs/plans/` overlapping this area:**
- `worker_watchdog_ustate_recovery.md`, doc `worker-wedge-investigation.md` — adjacent but non-conflicting: those concern unhealthy-state recovery and the investigation write-up; this plan adds the *self-kill* + *bounded-wait* primitives they reference.
- **Overlap risk with sibling cluster #1816** (six concerns share one event loop / respawn) which also touches `worker/__main__.py` startup wiring and the worker loop. Coordinate so the two PRs don't both rewrite the startup task structure — see Rabbit Holes + Open Questions.

## Prior Art

- **#1808 (OPEN)** — Investigation: "wedged-but-alive worker leaves sessions pending indefinitely despite 300s health backstop." Root-causes the wedge to semaphore exhaustion + PTY-pool hold. This plan is the *fix* for the mechanism #1808 documents.
- **#1767 (merged)** — Moved the heartbeat to a dedicated off-loop daemon thread + added `monitoring/worker_watchdog.py`'s stale-heartbeat W1-W5 kill ladder. **Correct for its goal (process-liveness signal), but it is exactly what makes a frozen loop report green.** This plan inverts the *meaning* of the write (green only if the loop is ticking) while preserving the off-loop *mechanism*, and in doing so finally *arms* the `worker_watchdog.py` ladder for loop wedges (a stale beacon now actually goes stale).
- **#1172 (merged)** — Retired the wall-clock execution timeout around `agent_session_queue.py:1494`. Confirms a progress-based deadline (deferred fix #3, #1820) is the right replacement, not a wall-clock one — out of scope here.
- **#1271 (merged)** — `_write_worker_heartbeat()` also refreshes the Redis worker-PID key; the inverted watchdog must keep doing this on the green path.
- `docs/features/worker-wedge-investigation.md` — existing write-up of the wedge mechanism (Hypothesis 3: semaphore exhaustion + PTY-pool hold).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1767 | Moved heartbeat off the event loop onto a daemon thread | Made the heartbeat *more* reliable as a process-liveness signal — but decoupled it from loop health, so a frozen loop reports green forever. Fixed detection of process death; introduced blindness to loop freeze. |
| #1172 | Removed the wall-clock execution timeout | Wall-clock timeout killed legitimately-long sessions; removing it was right, but left *no* backstop for parked execution. Progress-deadline replacement deferred to #1820. |
| `session_health.py:2561-2613` (leaked-slot detection) | Reads `_sem._value` to print a fingerprint | **Logging-only** by design comment ("recovery decision is unchanged") — detection without recovery. The classic industry trap (jcode's watchdog also only logs). |

**Root cause pattern:** Each prior change improved *detection* or removed a *blunt* mechanism without converting the wedge into a *recoverable event*. The fix is to make the wedge crash (fix #1) or time out (fix #4) so existing recovery infrastructure (launchd, the acquire retry loop) takes over.

## Research

External patterns were mined in the issue itself (systemd, k8s, Erlang/OTP, Go, omnigent, jcode). One confirmatory search grounds the dead-man's-switch design.

**Queries used:**
- `systemd WatchdogSec sd_notify keep-alive emit interval vs timeout`

**Key findings:**
- systemd's watchdog is exactly the dead-man's-switch shape fix #1 needs: live code must emit a keep-alive (`sd_notify("WATCHDOG=1")`) within `WatchdogSec`; a deadlocked loop fails to emit and the supervisor restarts it. Recommended emit interval is **half** the timeout. Informs the design: the tick must come from *live on-loop code* (not a thread/timer that keeps running through a freeze), and the staleness threshold should be a comfortable multiple of the tick interval.
- launchd has **no native `WatchdogSec`** equivalent — the pattern must be emulated: the off-loop thread plays the supervisor-timer role (self-`SIGABRT` when the on-loop tick is stale), and `com.valor.worker.plist`'s existing `KeepAlive=true` + `ThrottleInterval=10` provides the respawn (no plist change needed; `ThrottleInterval` also rate-limits a respawn storm).

## Data Flow

**Fix #1 — liveness signal:**
1. **Entry point:** worker process starts (`worker/__main__.py` `_run_worker`).
2. **On-loop tick task** (new): an asyncio task bumps `session_state.last_loop_tick = time.monotonic()` every `WORKER_DEADMAN_TICK_INTERVAL` (~5s). Because it runs *on* the event loop, a synchronous freeze stops the bumps.
3. **Off-loop watchdog thread** (inverted `_heartbeat_thread_main`): each wake reads `get_loop_tick()`. If fresh (`now - tick <= WORKER_DEADMAN_STALENESS_THRESHOLD`) → write `data/last_worker_connected` (green) + refresh Redis PID, as today. If stale → log CRITICAL + `os.abort()` (SIGABRT).
4. **Output:** launchd observes process exit (SIGABRT) → respawns the worker; recovery (`worker/__main__.py` startup Step 3a/3b) re-queues the interrupted session. The slower `worker_watchdog.py` heartbeat ladder is now also armed as an out-of-process backstop.

**Fix #4 — bounded acquire:**
1. **Entry point:** a session needs a PTY pair → `acquire_pair()` (`pty_pool.py:263`).
2. `await self._sem.acquire()` (`:285`) → now `asyncio.wait_for(..., PTY_POOL_ACQUIRE_TIMEOUT)`. On timeout → raise `PTYPoolError` (acquirer fails loudly; session re-queued instead of wedging the whole path).
3. `_wait_for_idle_slot()` (`:333`): `await self._slot_available.wait()` (`:358`) and `await slot.event.wait()` (`:362`) → each bounded by `PTY_POOL_WAIT_TIMEOUT`. On `_slot_available` timeout → `continue` (re-scan; defeats a missed `notify`). On `slot.event` timeout → the slot is stuck in `respawning` past deadline → `_force_recycle_slot(slot)` (new) re-schedules a respawn + `continue`.
4. **Output:** either a live pair is handed out, or the acquirer errors within a bounded window — never an unbounded park.

## Architectural Impact

- **New dependencies:** none (stdlib `os.abort`, `time.monotonic`, `asyncio.wait_for`).
- **Interface changes:** `agent/session_state.py` gains module globals `last_loop_tick` + accessors `bump_loop_tick()` / `get_loop_tick()`. `pty_pool.py` gains a private `_force_recycle_slot()`. No public API or signature changes.
- **Coupling:** the off-loop watchdog now *reads* an on-loop beacon — a deliberate, minimal cross-thread read (single float, GIL-atomic in CPython). This is the same beacon the deferred fix #5 (#1821) will later read from the bridge process.
- **Data ownership:** unchanged. `data/last_worker_connected` still owned by the watchdog thread; its *write condition* changes.
- **Reversibility:** high. `WORKER_DEADMAN_ENABLED=false` reverts to unconditional green writes (old #1767 behavior); the PTY timeouts revert by setting them very high.

## Appetite

**Size:** Medium

**Team:** Solo dev, async-specialist (cross-thread liveness + `asyncio.wait_for` semantics), PM check-in, 1 review round.

**Interactions:**
- PM check-ins: 1-2 (confirm scope = fixes #1 + #4 only; confirm self-kill default-on)
- Review rounds: 1 (async correctness of the dead-man's-switch and the force-recycle path)

## Prerequisites

No prerequisites — this work has no external dependencies. Python 3.14.3 (verified)
makes `asyncio.wait_for(Semaphore.acquire())` cancellation leak-safe (the historical
pre-3.10 bug is fixed), so bounding the semaphore acquire is safe.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Python ≥ 3.11 | `python -c "import sys; assert sys.version_info >= (3, 11)"` | Leak-safe `wait_for` + `Semaphore.acquire` |
| Worker plist has KeepAlive | `python -c "import pathlib,sys; t=pathlib.Path('com.valor.worker.plist').read_text(); sys.exit(0 if 'KeepAlive' in t else 1)"` | Confirms launchd respawns on the SIGABRT exit |

## Solution

### Key Elements

- **`last_loop_tick` beacon** (`agent/session_state.py`): a module-global `float | None`
  holding `time.monotonic()` of the last on-loop heartbeat, with `bump_loop_tick()`
  and `get_loop_tick()` accessors. Lives next to `_global_session_semaphore` (line 75).
- **On-loop tick task** (`worker/__main__.py`): an asyncio task that bumps the beacon
  every `WORKER_DEADMAN_TICK_INTERVAL` (~5s). Started alongside `health_task`.
- **Inverted watchdog** (`worker/__main__.py` `_heartbeat_thread_main`): writes green
  *only if* the beacon is fresh; otherwise `os.abort()` (SIGABRT) so launchd respawns.
  Arms only after observing the first real tick (startup grace) and is gated by
  `WORKER_DEADMAN_ENABLED`.
- **Bounded PTY waits** (`pty_pool.py`): all three unbounded awaits wrapped in
  `asyncio.wait_for`; a stuck-`respawning` slot is force-recycled.
- **`_force_recycle_slot()`** (`pty_pool.py`): takes a slot stuck in `respawning` past
  the wait deadline, logs loudly, and re-schedules a fresh respawn task so the slot
  cannot stay unavailable forever (closes the POOL-1 hazard).

### Flow

Worker starts → on-loop tick task bumps `last_loop_tick` every 5s → off-loop watchdog
checks freshness every cycle → **fresh** → write green + refresh PID (unchanged) /
**stale** → log CRITICAL + SIGABRT → launchd respawns → recovery re-queues session.

PTY acquire → `wait_for(sem.acquire, T)` → idle-slot scan → `wait_for` on slot waits →
**live pair** → yield / **timeout on respawning slot** → force-recycle + retry /
**timeout on sem** → raise `PTYPoolError` (session re-queued).

### Technical Approach

**Fix #1 — dead-man's-switch (the safety net):**

- Add to `agent/session_state.py` near line 75:
  ```python
  # Loop-liveness beacon: bumped by an on-loop task; read by the off-loop
  # watchdog to distinguish "loop ticking" from "loop frozen". monotonic()
  # so wall-clock jumps (NTP, sleep/wake) can't forge freshness.
  last_loop_tick: float | None = None
  ```
  plus `bump_loop_tick()` / `get_loop_tick()` accessors. (No Popoto model touched —
  in-memory only; see Update System.)
- New constants in `worker/__main__.py` (all env-overridable; **provisional — tune
  after observing real freeze/false-positive rates**):
  - `WORKER_DEADMAN_TICK_INTERVAL` (default ~5s) — on-loop bump cadence.
  - `WORKER_DEADMAN_STALENESS_THRESHOLD` (default ~90s) — several multiples of the
    tick + the 30s watchdog cycle, generous enough to absorb a GC pause or a brief
    legitimate sync block without a false abort. (The systemd "half-interval" guidance
    inverts to: threshold ≫ tick; 90s ≫ 5s leaves wide margin.)
  - `WORKER_DEADMAN_ENABLED` (default `true`) — conservative-rollback kill switch;
    `false` restores #1767's unconditional green write.
- New on-loop task `_loop_tick_task` in `_run_worker()` startup: `while True: bump_loop_tick(); await asyncio.sleep(WORKER_DEADMAN_TICK_INTERVAL)`. Start it *before* the heartbeat thread arms, and initialize `last_loop_tick` once at task start so the watchdog has a baseline.
- Invert `_heartbeat_thread_main()`:
  - Maintain an `armed` latch: stay unarmed (write green based on process liveness,
    i.e. current behavior) until the first beacon newer than the thread's start time
    is observed. This prevents a false SIGABRT during slow startup (index rebuild,
    recovery sweep) when the loop legitimately isn't ticking yet.
  - Once armed: if `now - get_loop_tick() <= WORKER_DEADMAN_STALENESS_THRESHOLD` →
    `_write_worker_heartbeat()` (green, unchanged). Else → `logger.critical(...)` with
    the staleness age + `os.abort()` (only when `WORKER_DEADMAN_ENABLED`). Use a
    `_self_kill()` seam so tests can assert the call without aborting the test process.
  - Check `_heartbeat_stop_event` first each cycle so graceful shutdown never aborts.
- Cross-thread safety: the beacon is a single `float` read/written without a lock —
  CPython GIL makes the read/write atomic; staleness math tolerates a one-cycle skew.
  No lock needed (documented inline). `None` is treated as unarmed, never as stale.

**Fix #4 — bound the three PTY awaits (the narrow first landing):**

- New constants near the top of `pty_pool.py` (env-overridable; **provisional**):
  - `PTY_POOL_ACQUIRE_TIMEOUT` (default ~120s) — bound on `self._sem.acquire()`.
  - `PTY_POOL_WAIT_TIMEOUT` (default ~60s) — bound on the two slot waits.
- `:285` → `await asyncio.wait_for(self._sem.acquire(), PTY_POOL_ACQUIRE_TIMEOUT)`;
  on `TimeoutError` raise `PTYPoolError("acquire timed out; pool may be wedged")`.
  Because the timeout fires *before* the slot is acquired, the `finally` block's
  `self._sem.release()` must not run for the never-acquired permit — guard release on
  a `sem_acquired` flag.
- `:358` → wrap `self._slot_available.wait()` in `wait_for(..., PTY_POOL_WAIT_TIMEOUT)`;
  on `TimeoutError` → `continue` the outer `while` (re-scan). Defeats a missed `notify_all`.
- `:362` → wrap `slot.event.wait()` in `wait_for(..., PTY_POOL_WAIT_TIMEOUT)`; on
  `TimeoutError` → the slot is stuck in `respawning` (POOL-1 hazard) → call
  `_force_recycle_slot(slot)` then `continue`.
- `_force_recycle_slot(slot)`: log `error`; under `slot.lock`, only act if the slot is
  still `respawning` with its event unset **and** its respawn task is done/dead (not
  merely slow); then schedule a fresh `_respawn_slot(slot)` (reusing `_schedule_respawn`
  / the `_respawn_tasks` list at `:486-488`). The recycle must wake **both** primitives
  the acquire path waits on — `slot.event` and the `_slot_available` condition var
  (`:535`) — to avoid re-triggering the divergent-primitive miss at `:540-568`.
  Idempotent: if the original respawn later completes, the event-set path wins.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The inverted watchdog's `_write_worker_heartbeat()` call already swallows `OSError` (it must keep doing so — a transient FS error must NOT trigger SIGABRT). Test: write failure does not abort.
- [ ] `_force_recycle_slot()` must not raise into the acquire loop — wrap respawn scheduling so a recycle failure logs and the loop continues (re-scans). Test asserts `logger.error` on recycle-schedule failure.
- [ ] The `os.abort()`/`_self_kill()` path is gated by `WORKER_DEADMAN_ENABLED`; test asserts that with the flag false, a stale beacon does NOT abort (only logs).

### Empty/Invalid Input Handling
- [ ] `get_loop_tick()` returns `None` before the first tick — the watchdog must treat `None` as "unarmed", never as "stale" (no abort). Test the `None` beacon path explicitly.
- [ ] `wait_for` with a 0 or negative timeout (misconfig) must not busy-loop — clamp to a minimum or document that the env value must be positive; test a tiny timeout still recycles cleanly.

### Error State Rendering
- [ ] The SIGABRT path emits a CRITICAL log with the staleness age before aborting (so the crash is explained in `logs/worker.log`). Test captures the log record.
- [ ] `PTYPoolError` from a timed-out acquire propagates to the caller (session fails + re-queues) rather than being swallowed. Test asserts the error surfaces.

## Test Impact

- [ ] `tests/unit/test_worker_watchdog.py::TestHeartbeatIsolation` — UPDATE: this asserts the off-loop thread writes independent of the loop (the #1767 behavior). Under the inversion the thread writes green *only when the beacon is fresh*. Update to assert: fresh beacon → green write; the isolation property (thread survives loop freeze) still holds and is now what enables the SIGABRT.
- [ ] `tests/unit/test_worker_health_check.py::TestCheckWorkerHealth` — UPDATE (verify, likely no change): it checks `data/last_worker_connected` staleness against the 600s threshold. The file's *meaning* changes (loop-liveness, not process-liveness) but its format (ISO timestamp) and the consumer contract are unchanged. Confirm no assertion depends on "always written every 30s".
- [ ] `tests/unit/granite_container/test_pty_pool.py::TestAcquireRelease` — UPDATE: add bounded-wait assertions; ensure the existing "blocks when all slots locked" test still passes within the new timeout (use a short `PTY_POOL_WAIT_TIMEOUT` via env in the test).
- [ ] `tests/unit/granite_container/test_pty_pool.py::TestRespawnFailure` — UPDATE/EXTEND: today it asserts the slot lands in `respawning` after a spawn failure. Extend to assert the next acquirer force-recycles it within `PTY_POOL_WAIT_TIMEOUT` instead of blocking forever (this is the POOL-1 regression guard / acceptance criterion).
- [ ] `tests/unit/granite_container/test_pty_pool_hardening.py` — UPDATE: add a case asserting a stuck-`respawning` slot is recycled by a bounded acquirer (new `_force_recycle_slot` coverage), and that both primitives are notified.
- [ ] No DELETE/REPLACE: all changes are additive guards on existing paths; no test describes behavior that is being removed (the unconditional-green write is replaced, covered by the `TestHeartbeatIsolation` UPDATE above).

New tests to add (greenfield, no prior coverage):
- `tests/unit/test_worker_deadman.py` — beacon freshness → green; stale beacon + enabled → `_self_kill` called; stale + disabled → no abort; `None` beacon → unarmed → no abort; write-failure → no abort; startup grace (unarmed until first tick).
- `tests/unit/granite_container/test_pty_pool_bounded_waits.py` — each of the three awaits times out and recovers (sem.acquire → `PTYPoolError`; `_slot_available` → re-scan; `slot.event` → force-recycle with both primitives notified).

## Rabbit Holes

- **Do NOT implement the lease semaphore (#2) or progress-deadline cancel scope (#3) here.** They are the larger REAL FIXes and are filed as #1820. The bounded-wait + dead-man's-switch primitives in this plan are what they build on.
- **Do NOT try to detect `await semaphore.acquire()` parks with the dead-man's-switch.** Fix #1 detects *synchronous* loop freezes only — the loop keeps spinning while a coroutine is parked, so the tick keeps bumping. That park is fix #4's job (for the PTY pool) and #2/#3's job (for the global slot). Conflating them produces false aborts.
- **Do NOT close the wedged worker's PTY fds from the watchdog.** Already proven infeasible (#1767 spike: macOS `os.close` only owns own fds, `/proc` is Linux-only). Respawn the process instead.
- **Do NOT rewrite the PTY pool state machine.** Fix #4 is a narrow `wait_for` + force-recycle, not a redesign of the respawn contract. Patch the divergent-primitive miss surgically (notify both primitives), don't re-architect.
- **Do NOT rewrite the worker startup task wiring beyond adding the tick task.** Sibling issue #1816 (event-loop sharing / respawn) also touches `worker/__main__.py` startup — keep this PR's footprint to the heartbeat thread + one new task to avoid a merge collision. Coordinate via #1818.
- **Do NOT lower `WORKER_HEARTBEAT_INTERVAL` to chase faster detection.** The bounded window is `STALENESS_THRESHOLD + one watchdog cycle`; tune the threshold, not the cycle.
- **Do NOT add a launchd `WatchdogSec` change.** launchd has no such knob; the SIGABRT→exit path uses the existing `KeepAlive`+`ThrottleInterval` restart, which needs no plist change.

## Risks

### Risk 1: False-positive SIGABRT during legitimate long synchronous work
**Impact:** A real (non-wedged) worker self-kills mid-session, re-queuing work unnecessarily (lossy restart).
**Mitigation:** Generous `WORKER_DEADMAN_STALENESS_THRESHOLD` (~90s, provisional) — far longer than any legitimate on-loop sync block should be (real async code never blocks the loop that long). Startup-grace `armed` latch prevents aborts during index rebuild/recovery. `WORKER_DEADMAN_ENABLED=false` is an instant kill switch. Ship with the threshold deliberately conservative and tighten only after observing real tick-cadence histograms in `logs/worker.log`.

### Risk 2: `asyncio.wait_for` cancelling `Semaphore.acquire()` corrupts the semaphore
**Impact:** A bounded acquire that times out could (on old Python) leave a phantom permit, shrinking effective pool capacity.
**Mitigation:** Python 3.14.3 (verified) fixed this pre-3.10 bug — a cancelled `acquire()` does not consume a permit. Guard the `finally` release behind a `sem_acquired` flag so a never-acquired permit is never released. Prerequisite check pins Python ≥ 3.11.

### Risk 3: `_force_recycle_slot` races the original respawn task completing
**Impact:** Double-spawn of a slot, or a recycle that clobbers a slot that just became idle.
**Mitigation:** Recycle is idempotent — under `slot.lock`, only re-schedule a respawn if the slot is still `respawning`, its event unset, **and** its respawn task is done/dead (not merely slow). If the original respawn completed (event set, state `idle`) the re-scan picks it up and recycle is a no-op.

### Risk 4: SIGABRT respawn storm under a persistent wedge cause
**Impact:** If the wedge cause is deterministic (poisoned session), the worker self-kills, respawns, re-picks the same work, and wedges again — a crash loop.
**Mitigation:** `ThrottleInterval=10` on `com.valor.worker.plist` rate-limits launchd respawns. Recovery on restart already quarantines the session running at crash time via the existing crash-signature / auto-resume-policy machinery rather than immediately re-picking it. (No new code in this PR — relies on the existing recovery path; called out so the validator confirms re-pick is not infinite.)

## Race Conditions

### Race 1: Watchdog reads `last_loop_tick` while the on-loop task writes it
**Location:** `agent/session_state.py` (beacon) read in `worker/__main__.py` watchdog thread.
**Trigger:** Watchdog wakes exactly as the tick task assigns a new `monotonic()`.
**Data prerequisite:** `last_loop_tick` initialized (non-`None`) before the watchdog arms.
**State prerequisite:** A single `float` global; CPython guarantees the read/write is atomic (no torn value).
**Mitigation:** No lock — a one-cycle stale read is harmless given the 90s threshold ≫ 5s tick. `None` is treated as unarmed, never stale. Documented inline.

### Race 2: `slot.event` set by the original respawn during a force-recycle
**Location:** `pty_pool.py` `_wait_for_idle_slot` timeout path → `_force_recycle_slot`.
**Trigger:** The original respawn task finishes (sets event, state→idle) just as the acquirer times out and decides to recycle.
**Data prerequisite:** Slot state + event consistency under `slot.lock`.
**State prerequisite:** Recycle only acts on a slot still `respawning`, event unset, respawn task done/dead.
**Mitigation:** Re-check state under `slot.lock` inside `_force_recycle_slot`; if already idle or the task is still running, no-op and let the outer `continue` re-scan pick it up. Idempotent.

### Race 3: SIGABRT fires during graceful shutdown
**Location:** watchdog thread vs. `_heartbeat_stop_event` set on shutdown.
**Trigger:** Shutdown stops the tick task (beacon goes stale) before the watchdog thread exits → spurious abort during clean shutdown.
**Data prerequisite:** `_heartbeat_stop_event` checked before the staleness check each cycle.
**State prerequisite:** Shutdown sets the stop event; the watchdog's `while not _heartbeat_stop_event.wait(...)` already exits on it.
**Mitigation:** On each cycle, check the stop event first (existing loop guard) and skip the staleness/abort path when shutdown is requested. Test the shutdown path asserts no abort.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1820] Fix #2 (lease-based slot ownership, the only fix that removes the wedge *class*) and Fix #3 (progress-deadline cancel scope around `agent_session_queue.py:1494`). These are larger REAL FIXes that build on the `last_loop_tick` beacon and bounded-wait primitives landed in this plan.
- [SEPARATE-SLUG #1821] Fix #5 (move recovery into a separate failure domain — extend `monitoring/session_watchdog.py` to read the beacon from the bridge process) and Fix #6 (synchronous per-tool-call budget backstop). Fix #5 depends on the lease registry from #1820.
- [ORDERED] launchd `WatchdogSec`-style timing tuning — the issue's Downstream note defers it; the SIGABRT path uses launchd's existing `KeepAlive` restart, which needs no plist change to function. Any final threshold tightening waits on observed production wedge incidents on the live bridge machine.

## Update System

No update-script or migration changes required. The `last_loop_tick` beacon is an
in-memory module global, **not** a Popoto model field — no `scripts/update/migrations.py`
entry is needed. `com.valor.worker.plist` already has `KeepAlive=true` + `ThrottleInterval=10`,
so no plist change. The new env vars (`WORKER_DEADMAN_*`, `PTY_POOL_*_TIMEOUT`) are all
optional with safe defaults, so no `.env` propagation is required; add them to `.env.example`
with a comment line above each (completeness-check requirement) only if we want them
operator-discoverable. The worker is restarted by the standard
`./scripts/valor-service.sh worker-restart` after merge — no new deploy step in
`scripts/update/run.py`.

## Agent Integration

No agent integration required — this is a worker-internal resilience change. No new
CLI entry point in `pyproject.toml [project.scripts]`, no MCP surface, and the bridge
does not import the new code. The dashboard already consumes `data/last_worker_connected`
via the existing health check; its contract (ISO-timestamp freshness) is unchanged, so
no dashboard wiring changes. (The deferred fix #5 in #1821 is where a *bridge-side*
consumer of the beacon will be added — explicitly out of scope here.)

## Documentation

### Feature Documentation
- [ ] Create `docs/features/worker-liveness-recovery.md` describing the heartbeat-inversion (dead-man's-switch) model, the on-loop beacon vs. off-loop watchdog split, the bounded PTY-pool waits + force-recycle, the env constants with their provisional defaults, and the systemd-watchdog/launchd-KeepAlive analogy. State up front that this is the first of multiple landings (#1820/#1821 follow). (Acceptance criterion of #1815.)
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Forward-link from `docs/features/worker-wedge-investigation.md` (the #1808 instrument-only write-up) to this doc (the fix), per the no-historical-artifacts rule — describe the new status quo. Note in `docs/features/worker-service.md` that the heartbeat is now loop-liveness-gated.

### Inline Documentation
- [ ] Comment the cross-thread atomicity assumption on `last_loop_tick` (GIL-atomic single-float read; `None` = unarmed).
- [ ] Comment each PTY/deadman timeout constant with the grain-of-salt "provisional, tune after observing real rates" note.
- [ ] Docstring the `armed`/startup-grace semantics in the inverted `_heartbeat_thread_main`, and update the `pty_pool.py:40-50` POOL-1 docstring (it currently says "recovers via the operator's intervention" — fix #4 replaces that with automatic recycle).

## Success Criteria

In-scope acceptance criteria (the two this PR claims):
- [ ] A wedged event loop self-kills (SIGABRT) and is respawned by launchd within `WORKER_DEADMAN_STALENESS_THRESHOLD + one watchdog cycle` (test: inject a synchronous freeze on the loop; observe the `_self_kill` seam fires — patched in unit test).
- [ ] A parked PTY-pool acquirer cannot block forever: a forced respawn failure leaves a slot in `respawning`, and the next acquirer force-recycles it within `PTY_POOL_WAIT_TIMEOUT` instead of blocking forever (test: `TestRespawnFailure` extension).

Supporting criteria:
- [ ] A missed `_slot_available` notify no longer parks an acquirer (test: timeout → re-scan finds the idle slot).
- [ ] With `WORKER_DEADMAN_ENABLED=false`, a stale beacon logs but does NOT abort (rollback path).
- [ ] The green-write path still refreshes the Redis worker PID (#1271 preserved) and still swallows FS write errors without aborting.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`): `docs/features/worker-liveness-recovery.md` exists.

Explicitly **NOT** claimed by this PR (belong to deferred fixes): leaked-permit reclaim
without restart (#1820 fix #2), no-progress cancel (#1820 fix #3), recovery from a
separate process (#1821 fix #5). Tracked under #1815's umbrella.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (dead-man's-switch)**
  - Name: deadman-builder
  - Role: Fix #1 — `session_state.py` beacon + `worker/__main__.py` on-loop tick task + inverted watchdog
  - Agent Type: async-specialist
  - Resume: true

- **Builder (pty-bounded-waits)**
  - Name: pty-builder
  - Role: Fix #4 — `pty_pool.py` three bounded awaits + `_force_recycle_slot`
  - Agent Type: async-specialist
  - Resume: true

- **Validator (resilience)**
  - Name: resilience-validator
  - Role: Verify both in-scope fixes against success criteria + failure-path tests
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: liveness-doc
  - Role: `docs/features/worker-liveness-recovery.md` + index + forward-link the #1808 doc
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Bound the PTY-pool waits (fix #4 — smallest first landing)
- **Task ID**: build-pty-bounded-waits
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_pty_pool.py, tests/unit/granite_container/test_pty_pool_hardening.py, tests/unit/granite_container/test_pty_pool_bounded_waits.py (create)
- **Assigned To**: pty-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `PTY_POOL_ACQUIRE_TIMEOUT` / `PTY_POOL_WAIT_TIMEOUT` env constants (provisional, commented).
- Wrap `:285`, `:358`, `:362` awaits in `asyncio.wait_for`; guard the `finally` `sem.release()` behind a `sem_acquired` flag.
- Implement `_force_recycle_slot(slot)` (idempotent, under `slot.lock`, task-done check, notify both `slot.event` and `_slot_available`, re-schedule respawn via `_schedule_respawn`).
- Wire the timeout branches: sem → `PTYPoolError`; `_slot_available` → `continue`; `slot.event` → recycle + `continue`.

### 2. Invert the heartbeat into a dead-man's-switch (fix #1 — safety net)
- **Task ID**: build-deadman
- **Depends On**: none
- **Validates**: tests/unit/test_worker_watchdog.py, tests/unit/test_worker_health_check.py, tests/unit/test_worker_deadman.py (create)
- **Assigned To**: deadman-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `last_loop_tick` + `bump_loop_tick()` / `get_loop_tick()` to `agent/session_state.py` (near line 75).
- Add `WORKER_DEADMAN_TICK_INTERVAL` / `_STALENESS_THRESHOLD` / `_ENABLED` constants.
- Add the on-loop `_loop_tick_task` in `_run_worker()` startup; initialize the beacon at task start.
- Invert `_heartbeat_thread_main`: `armed` latch + startup grace; fresh → green (+ PID refresh + swallow FS errors); stale & enabled → CRITICAL log + `_self_kill()` (SIGABRT); honor `_heartbeat_stop_event` first each cycle.

### 3. Validate both fixes
- **Task ID**: validate-resilience
- **Depends On**: build-pty-bounded-waits, build-deadman
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the new + updated tests; verify each in-scope Success Criterion and Failure Path item.
- Confirm no regression in `TestHeartbeatIsolation` (updated) and the dashboard health-check contract.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-resilience
- **Assigned To**: liveness-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/worker-liveness-recovery.md`; add README index entry; forward-link investigation + worker-service docs.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table; confirm docs deliverable exists; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_worker_deadman.py tests/unit/granite_container/test_pty_pool.py tests/unit/granite_container/test_pty_pool_hardening.py tests/unit/test_worker_watchdog.py -q` | exit code 0 |
| Lint clean | `python -m ruff check worker/ agent/granite_container/pty_pool.py agent/session_state.py` | exit code 0 |
| Format clean | `python -m ruff format --check worker/ agent/granite_container/pty_pool.py agent/session_state.py` | exit code 0 |
| Beacon accessors exist | `grep -c "def bump_loop_tick\|def get_loop_tick" agent/session_state.py` | output > 0 |
| Watchdog can self-kill | `grep -c "os.abort\|SIGABRT" worker/__main__.py` | output > 0 |
| Self-kill is gated | `grep -c "WORKER_DEADMAN_ENABLED" worker/__main__.py` | output > 0 |
| PTY waits bounded | `grep -c "wait_for" agent/granite_container/pty_pool.py` | output > 0 |
| Force-recycle exists | `grep -c "_force_recycle_slot" agent/granite_container/pty_pool.py` | output > 0 |
| Lease fix NOT smuggled in (anti-criterion #1820) | `grep -rc "owner_session_id" agent/session_state.py` | match count == 0 |
| Feature doc exists | `test -f docs/features/worker-liveness-recovery.md && echo found` | output contains found |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Self-kill default:** Ship `WORKER_DEADMAN_ENABLED=true` (default-on, the issue calls fix #1 "the safety net under the rest") or stage it `false` for one release to observe tick-cadence histograms first? Plan assumes default-on with a conservative 90s threshold.
2. **Staleness threshold:** Is ~90s the right provisional `WORKER_DEADMAN_STALENESS_THRESHOLD`, or larger (e.g. 120s) given occasional heavy synchronous startup work outside the armed window?
3. **Coordination with #1816:** Sibling issue #1816 also touches `worker/__main__.py` startup wiring. Should this land first (it's the smaller, foundational pair) so #1816 rebases onto the new task structure?
