# Worker Liveness Recovery: Dead-Man's-Switch + Bounded PTY Waits

This document describes the first landing of the liveness-vs-progress wedge recovery work
(issue #1815). Two independent fixes shipped together: a dead-man's-switch heartbeat that
aborts a frozen event loop, and bounded timeouts on every PTY-pool await that previously
had no deadline. Deferred follow-ups: fix #2 (lease semaphore) + fix #3 (progress-deadline
cancel scope) are tracked in issue #1820; fix #5 (out-of-domain recovery) + fix #6
(per-tool budget backstop) are tracked in issue #1821.

Fixes #2 and #3 have since shipped — see [Slot-Lease Ownership](slot-lease-ownership.md)
(issue #1820): the ownerless concurrency semaphore is now an owner-keyed
`SlotLeaseRegistry` whose leaked permits self-heal via a top-of-tick reap, and a
progress-deadline cancel scope owns the no-progress kill for worker-alive sessions.

## Background

Issue #1808 (documented in [Worker Wedge Investigation](worker-wedge-investigation.md))
established that a worker process can be alive at the OS level while its asyncio event loop
is wedged. The prior heartbeat write in `data/last_worker_connected` was unconditional on
process liveness, so a frozen loop still produced a fresh green heartbeat from the off-loop
watchdog thread. The watchdog never declared the process sick.

Separately, the PTY pool's three internal awaits had no timeout. If a PTY slot's respawn
task died on the error path without setting its completion event, the next caller would
block on that event forever, holding a global semaphore slot. With enough concurrent
sessions all wedged this way, the whole granite path deadlocked.

## Fix 1: Dead-Man's-Switch Heartbeat (Heartbeat Inversion)

### The Core Idea

The old design wrote "green" unconditionally from the off-loop watchdog thread every
heartbeat cycle. The new design inverts this: the on-loop code must bump a beacon on
every event-loop tick, and the off-loop watchdog writes "green" only when it sees a fresh
beacon. If the beacon goes stale, the watchdog dumps an all-thread Python stack trace via
`faulthandler` and recycles the process with `SIGKILL`, and launchd's existing
`KeepAlive=true` + `ThrottleInterval=10` respawns the worker. Worker startup recovery then
re-queues the interrupted session.

This is the same pattern as systemd's `WatchdogSec` + `sd_notify("WATCHDOG=1")`: live
code must emit a keep-alive within the configured interval or the supervisor restarts.
launchd has no native `WatchdogSec`, so the off-loop thread plays the supervisor-timer role
(self-SIGKILL when the on-loop tick is stale) and launchd's `KeepAlive` provides the
respawn.

### On-Loop Beacon (`agent/session_state.py`)

Two new module-level globals and two accessors were added:

- `last_loop_tick: float | None` initialized to `None` (unarmed state)
- `bump_loop_tick()` sets it to `time.monotonic()`. Called by the on-loop asyncio task.
- `get_loop_tick()` returns the current value. Read by the off-loop watchdog thread.

`None` means unarmed: the process started but the event loop has not yet run the tick
task. `time.monotonic()` is used (not wall clock) for freeze detection.

### On-Loop Tick Task (`worker/__main__.py`)

`_loop_tick_task()` is an asyncio task scheduled at worker startup. It runs an infinite
loop that calls `bump_loop_tick()` then sleeps for `WORKER_DEADMAN_TICK_INTERVAL` seconds.
Because it runs on the event loop, a synchronous freeze that blocks the loop also stops
the bumps. This is the property that makes the mechanism work: the beacon going stale
proves the loop is not making progress.

### Off-Loop Watchdog Thread (`worker/__main__.py`)

`_heartbeat_thread_main()` runs on a daemon thread outside the event loop. Each cycle:

1. Read `get_loop_tick()`.
2. If `None` (unarmed): check the startup-freeze guard (see below), then write green
   (the old #1767 unconditional behavior). This keeps the heartbeat green during normal
   slow startup (index rebuild, recovery sweep).
3. If armed and fresh (age below `WORKER_DEADMAN_STALENESS_THRESHOLD`): write
   `data/last_worker_connected` green and refresh the Redis PID record.
4. If armed and stale (age at or above threshold): log a CRITICAL message and call
   `_self_kill()`, which emits an all-thread Python stack dump via
   `faulthandler.dump_traceback(all_threads=True)` to stderr and then delivers `SIGKILL`
   to the process.

An `armed` latch prevents false aborts during startup: the watchdog stays in the
unarmed (green-write) path until `bump_loop_tick()` has fired at least once.

### Startup-Freeze Guard

If the beacon is still `None` after `WORKER_DEADMAN_STARTUP_GRACE_MAX` seconds (measured
by how long the process has been running), the event loop froze before ever ticking. The
watchdog aborts anyway (when `WORKER_DEADMAN_ENABLED=true`). This closes the gap where
a freeze during index rebuild or recovery sweep would leave the process alive but silent
forever, never arming the normal dead-man's-switch path.

### Beacon-Age Auditing

The green-write path logs `[deadman] beacon age=...` at roughly once per minute so
operators can observe the margin between the actual on-loop sync work duration and the
abort threshold. If real sync work routinely approaches the threshold, the threshold
should be raised before it produces false aborts.

### Composition With Off-Loop Redis Access (issue #1826)

The beacon only stays fresh if the on-loop tick task actually gets to run. Before
[Off-Loop Redis Access](redis-durability.md#off-loop-redis-access-fix-4), the
worker drain loop's hot-path idle-check ran its Redis query synchronously, directly
on the event loop. A slow or restarting Redis could block the loop long enough to
starve the tick task's `asyncio.sleep` resumption, letting the beacon go stale and
triggering a false self-kill on a worker whose only problem was a slow Redis, not a
real freeze.

That hot-path query now runs off the loop via `offload_redis()` on a dedicated
thread pool, so a slow Redis lengthens only that one call's latency instead of
occupying the loop. The tick task keeps bumping the beacon throughout, so the
dead-man's-switch no longer false-triggers on Redis slowness. It still fires
correctly on a genuine synchronous freeze elsewhere in the loop, since that
composition path was never touched. See
[Redis Durability: Off-Loop Redis Access](redis-durability.md#off-loop-redis-access-fix-4)
for the bulkhead, the cut-over ordering, and the latency metric.

### Environment Constants (Provisional)

All constants are env-overridable and marked provisional/tunable. They live in
`worker/__main__.py`.

| Constant | Default | Description |
|----------|---------|-------------|
| `WORKER_DEADMAN_ENABLED` | `true` | Set to `false` to restore #1767 unconditional-green write (instant rollback, no code change needed) |
| `WORKER_DEADMAN_TICK_INTERVAL` | `5` s | How often the on-loop task bumps the beacon |
| `WORKER_DEADMAN_STALENESS_THRESHOLD` | `90` s | Age at which the watchdog declares the loop frozen and aborts |
| `WORKER_DEADMAN_STARTUP_GRACE_MAX` | `300` s | How long the process may stay unarmed before the startup-freeze guard aborts it |

The margin between `WORKER_DEADMAN_TICK_INTERVAL` and `WORKER_DEADMAN_STALENESS_THRESHOLD`
is intentionally large (5s vs 90s). Real on-loop sync work (awaiting Claude tool calls,
DB writes) should complete well within 90 seconds. The threshold is a freeze detector,
not a latency budget.

### Recovery After Recycle

`SIGKILL` exits the process immediately with a non-zero status and produces no macOS
crash dialog or `.ips` report (unlike the earlier abort-based design). launchd sees the
non-zero exit, applies `ThrottleInterval=10`, and respawns the process after 10 seconds.
The worker's startup sequence (index rebuild, corrupted+orphan cleanup, dead-worker sweep,
recovery) re-queues any session that was running at recycle time. No manual intervention
is required.

**Forensics moved from `.ips` to a Python stack dump (#1808):** the original design relied
on a self-abort call specifically because macOS would capture a crash report
(`Python-*.ips`) with a C-level frame dump of the process at abort time. In practice that
report is not very
diagnostic for the wedges this switch exists to catch — issue #1808 established that the
freezes are event-loop-level (an `await` that never resumes, a synchronous call blocking
the loop), which shows up far more clearly in a Python-level stack trace than in a C-frame
snapshot. `_self_kill()` now calls `faulthandler.dump_traceback(all_threads=True)` before
delivering SIGKILL, writing an all-thread Python traceback straight to stderr. The launchd
plist (`com.valor.worker.plist`, `StandardErrorPath`) captures that stream into
`logs/worker_error.log`, so the exact wedged frame across every thread is available for
post-mortem without a macOS crash dialog or `.ips` file ever being produced.

## Fix 4: Bounded PTY-Pool Waits

### The POOL-1 Hazard

The PTY pool (`agent/granite_container/pty_pool.py`) maintains a fixed set of slots, each
cycling through spawning, ready, and respawning states. The pre-fix code had three
unbounded `await` calls:

1. `await self._sem.acquire()` (semaphore for pool-size limit)
2. `await self._slot_available.wait()` (condition signaling a ready slot exists)
3. `await slot.event.wait()` (per-slot event signaling respawn complete)

The POOL-1 hazard targeted await #3. A slot whose `_spawn_slot` task dies on the failure
path (the task re-raises before calling `slot.event.set()`) is left stuck in `respawning`
forever with `slot.event` never set. The next caller that wins the semaphore and the
condition wait then parks on `slot.event.wait()` indefinitely. It holds a semaphore permit.
With enough callers blocked this way, all permits exhaust and the entire granite path
deadlocks: new sessions can never acquire, existing waiting calls never finish.

### Bounded Awaits

All three awaits are now wrapped in `asyncio.wait_for` with the following timeouts and
recovery behaviors:

**Semaphore acquire (`await self._sem.acquire()`):**
- Timeout: `PTY_POOL_ACQUIRE_TIMEOUT` (120s by default).
- On `asyncio.TimeoutError`: raise `PTYPoolError`. The session fails and is re-queued for
  retry instead of wedging the worker permanently.
- A `sem_acquired` flag guards the `finally` release: if the acquire timed out, the permit
  was never held, so it must not be released.

**Condition wait (`await self._slot_available.wait()`):**
- Timeout: `PTY_POOL_WAIT_TIMEOUT` (60s by default).
- On `asyncio.TimeoutError`: re-scan by breaking out of the inner wait loop. This defeats
  a missed `notify_all` where all waiters were asleep when the notification fired.

**Slot event wait (`await slot.event.wait()`):**
- Timeout: `PTY_POOL_WAIT_TIMEOUT` (60s by default).
- On `asyncio.TimeoutError`: the slot is still stuck in `respawning` past the deadline.
  `_force_recycle_slot(slot)` is called.

### Force Recycle (`_force_recycle_slot`)

`_force_recycle_slot(slot)` is called when a slot's event wait times out. Under
`slot.lock`, it checks that the slot is still in `respawning` with `event` unset (the
stuck condition). If so, it schedules a fresh `_respawn_slot` task. The rescheduled task's
success path sets both `slot.event` and notifies `_slot_available`. `_force_recycle_slot`
does not set these directly: it hands the work to the new spawn task, which performs the
proper state transitions under the slot lock.

The re-check under `slot.lock` is important: between the timeout and the lock acquisition,
another caller might have already recycled the slot. The guard prevents a double-recycle.

### Environment Constants (Provisional)

Both constants are env-overridable and live in `agent/granite_container/pty_pool.py`.

| Constant | Default | Description |
|----------|---------|-------------|
| `PTY_POOL_ACQUIRE_TIMEOUT` | `120` s | Max wait for the pool semaphore before raising `PTYPoolError` |
| `PTY_POOL_WAIT_TIMEOUT` | `60` s | Max wait for both the ready-slot condition and the per-slot respawn event before re-scan or force-recycle |

## How the Two Fixes Interact

Fix 1 (dead-man's-switch) catches event-loop freezes, including cases where the granite
executor itself is stuck somewhere that never reaches the PTY pool. Fix 4 (bounded PTY
waits) catches PTY-pool-level deadlocks: callers return an error or trigger a force-recycle
instead of parking forever. Together they close the two most common wedge shapes observed
in production.

## Disabling and Rollback

**Dead-man's-switch:** Set `WORKER_DEADMAN_ENABLED=false` in the environment before
starting the worker. This restores #1767's unconditional-green heartbeat write with no
code change.

**PTY-pool timeouts:** Set `PTY_POOL_ACQUIRE_TIMEOUT` and `PTY_POOL_WAIT_TIMEOUT` to very
large values (e.g., `86400`) to effectively disable the timeouts without removing the
`asyncio.wait_for` wrappers.

## Observability

- Dead-man's-switch status: `[deadman] beacon age=...` INFO lines in `logs/worker.log`
  (emitted roughly once per minute from the green-write path).
- Recycle events: CRITICAL log line from `_heartbeat_thread_main` immediately before
  `_self_kill()` fires, followed by the `faulthandler` all-thread stack dump in
  `logs/worker_error.log`.
- PTY pool timeouts: `PTYPoolError` is logged at ERROR level with the session ID; the
  session transitions to failed/re-queued.
- Force-recycle events: logged at WARNING level from `_force_recycle_slot`.

## Status Quo

This landing covered fix #1 (dead-man's-switch) and fix #4 (bounded PTY waits).
Both deferred follow-ups have since shipped:

- Issue #1820 — fix #2 (lease semaphore to decouple global slot holds from PTY
  wait duration) + fix #3 (progress-deadline cancel scope inside the
  executor). See [Slot-Lease Ownership](slot-lease-ownership.md).
- Issue #1821 — fix #5 (out-of-domain session recovery) + fix #6 (per-tool
  budget backstop). The beacon this doc publishes is now also read
  cross-process by a bridge-domain watchdog, which drives restart-free slot
  reclamation without ever touching the worker's in-memory state or sending
  it a kill signal. See [Out-of-Domain Recovery + Per-Tool Budget Backstop](out-of-domain-recovery.md).

## See Also

- [Worker Wedge Investigation](worker-wedge-investigation.md) — root-cause analysis that
  motivated these fixes (issue #1808)
- [Worker Service](worker-service.md) — worker architecture, launchd setup, env vars
- [Bridge Self-Healing](bridge-self-healing.md) — worker watchdog and escalation ladder
- [Headless Session Runner](headless-session-runner.md) — the current session-execution
  substrate (no PTY pool; each turn is a short-lived `claude -p` subprocess)
- [Slot-Lease Ownership](slot-lease-ownership.md) — the lease registry and reap
  pass built on top of this dead-man's-switch (#1820)
- [Out-of-Domain Recovery + Per-Tool Budget Backstop](out-of-domain-recovery.md) —
  the bridge-domain reclaim trigger built on top of this beacon, plus the
  synchronous per-tool budget (#1821)
- [Bridge Resilience: Worker-liveness Ingestion Signal](bridge-resilience.md#worker-liveness-ingestion-signal) —
  the ingestion-time companion to this recovery machinery: the bridge reads the
  same loop beacon via `worker_loop_beacon_fresh` and applies a ⚠ reaction when
  the worker is not alive, so a paused pipeline is visible to the user (#1312)
- [Redis Durability: Off-Loop Redis Access](redis-durability.md#off-loop-redis-access-fix-4):
  moves the drain loop's hot-path Redis query off the event loop so a slow Redis
  cannot starve this beacon's tick task (#1826)
- `agent/session_state.py` — beacon globals and accessors
- `worker/__main__.py` — tick task, watchdog thread, and dead-man's-switch constants
- `agent/granite_container/pty_pool.py` — bounded awaits and force-recycle
