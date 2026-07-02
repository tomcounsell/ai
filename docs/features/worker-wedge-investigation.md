# Worker Wedge Investigation (Issue #1808)

**Status: Investigation complete — mechanism demonstrated, no concrete slot-leak found.**

**Implemented fix:** The two highest-priority findings from this investigation have been
addressed. See [Worker Liveness Recovery](worker-liveness-recovery.md) for the implemented
solution: a dead-man's-switch heartbeat that aborts a frozen event loop (fix #1), and
bounded PTY-pool waits with force-recycle that close the POOL-1 deadlock hazard (fix #4).

**The slot-leak class this document analyzes is now self-healing.** The PENDING-WEDGE
FINGERPRINT described here no longer only logs — see
[Slot-Lease Ownership](slot-lease-ownership.md) (issue #1820): the ownerless concurrency
semaphore is replaced by an owner-keyed `SlotLeaseRegistry`, and the fingerprint became a
reclaim call that frees a leaked permit without a process restart.

This document records the full findings of the `session/wedged-worker-pending-investigation`
branch investigating issue #1808 ("Wedged-but-alive worker leaves sessions pending
indefinitely despite 300s health backstop").

## Problem Statement

A worker process that is *alive* — heartbeat green, process running, `asyncio.Task`
not done — can become *wedged*: it cannot pick up `pending` work. The 300-second
health-check backstop (`agent/session_health.py`, `_agent_session_health_check`)
fails to recover the situation because its pending-session branch only checks
`worker.done()` and calls `event.set()`. A worker parked at
`await semaphore.acquire()` is not `.done()`, so the health check sees
`worker_alive = True` and takes no escalation action.

**Precondition fix**: #1804 (merged `71c1edc7`) fixed the notify-listener dead-
subscription issue. That fix ensures the event-based wake-up fires reliably; the
investigation here confirms/rules out the semaphore-exhaustion wedge that can still
occur even with a healthy subscription.

---

## Four Hypotheses

### Hypothesis 1 — Semaphore exhaustion

**Verdict: MECHANISM DEMONSTRATED. No concrete slot-leak path found in current code.**

The `_worker_loop` (line 1314 of `agent/agent_session_queue.py`) acquires the global
`_global_session_semaphore` at the very top of each iteration, before calling
`_pop_agent_session`. When the semaphore has 0 permits, the loop parks at
`await semaphore.acquire()` and never reaches the pop.

Acquire/release audit for `_worker_loop`:

| Exit path | acquire() line | release() line | Released on all exits? |
|-----------|----------------|----------------|------------------------|
| Normal pop → execute → finally | 1314 | 1641 (`finally`) | YES |
| `StatusConflictError` skip | 1314 | 1333 (explicit) | YES (`_semaphore_acquired = False`, `continue`) |
| `BaseException` from `_pop_agent_session` | 1314 | 1338 (except handler) | YES (then `raise`) |
| `session is None` drain path | 1314 | 1344 (explicit) | YES (`_semaphore_acquired = False`) |
| Standalone re-acquire after `event.wait` | 1376 | 1388 (`if None`) or 1382 (`BaseException`) or 1641 (`execute`) | YES |
| Bridge mode re-acquire after `event.wait` | 1397 | 1407 or 1404 or 1641 | YES |
| Bridge `TimeoutError` re-acquire | 1414 | 1428 or 1421 or 1641 | YES |
| Bridge exit-time final re-acquire | 1438 | 1458 or 1445 | YES |
| `CancelledError` from `_execute_agent_session` | 1314 | 1641 (`finally` runs before re-raise) | YES |

No slot-leak path exists in the current code. The `finally` block at line 1641 releases
the slot on every exit path, including `CancelledError`.

However, slots can be legitimately exhausted by Hypothesis 3 below.

### Hypothesis 2 — Event loop fully blocked

**Verdict: NOT CONFIRMED by log analysis.**

The `[session-health] Health check:` log lines in `logs/worker.log` show consistent
~4-5 minute cadence during all active worker periods. No gap > 600s was observed
within a running session. This rules out the event loop being frozen by a synchronous
blocking call during the investigation period.

**C1 limitation**: `asyncio.set_debug()` (shipped via `WORKER_ASYNCIO_DEBUG=1`, issue
#1808 deliverable B/C-rev4) would catch synchronous callbacks that block the loop. But
it is structurally blind to coroutines parked at `await semaphore.acquire()` — a parked
coroutine yields the event loop cleanly, never executing a "slow callback". For the
await-suspension wedge, the always-on forensic log in `session_health.py` is the correct
detection surface.

### Hypothesis 3 — PTY-pool acquire holds semaphore slot (H1 sub-case)

**Verdict: CONFIRMED as a valid production path for slot exhaustion.**

In `_worker_loop`:
1. Global semaphore slot is acquired at line 1314.
2. `_execute_agent_session(session)` is called at line 1478.
3. Inside `_execute_agent_session` (`agent/session_executor.py:1591`), the code acquires
   a PTY pair from the granite pool via `get_pty_pool()` → `BridgeAdapter` → `pool.acquire_pair()`.

A session therefore **holds a global semaphore slot while blocked waiting for a PTY pair**.

If `MAX_CONCURRENT_SESSIONS=8` sessions all hold semaphore slots while blocked waiting
for PTY pairs (e.g., `GRANITE__PTY_POOL_SIZE=3` with 3 pairs busy), the global semaphore
reaches 0. Any further pending session's worker loop parks at `await semaphore.acquire()`
and cannot make progress. The sessions executing (blocked on PTY) will eventually complete
or be cancelled by the health-check's no-progress detector, releasing slots. But during
the blockage window, new pending sessions see the wedge.

This is the most likely production mechanism that existed concurrently with the dead-
subscription issue fixed in #1804.

### Hypothesis 4 — set/clear race in event handling

**Verdict: RACE CLOSED by `_has_pending` guard.**

Lines 1347-1362 in `_worker_loop` check for pending work synchronously before calling
`event.clear()`. If a health-check `event.set()` fires while the loop is inside
`_pop_agent_session` (between the pop returning `None` and the `event.clear()`), the
`_has_pending` guard detects the new session and `continue`s (skipping the wait) without
ever losing the event signal.

---

## Reproduction Tests

Two tests live in `tests/integration/test_worker_wedge_pending.py`.

### A1 — `test_worker_loop_parks_on_zero_semaphore` (mechanism test, load-bearing)

Drives the **real** `_worker_loop` against a zero-permit semaphore.

- Proves the mechanism: a slot-starved loop parks at `await semaphore.acquire()` before
  calling `_pop_agent_session`. The pending session remains in the queue.
- Proves recovery: one `semaphore.release()` unblocks the loop; the sentinel mock is called.
- A **PASS** means the wedge mechanism is real and recovery works as expected.
- A **FAIL** would mean the loop's control flow changed such that it no longer parks
  at the semaphore before checking for work.

### A2 — `test_health_check_cannot_escalate_parked_worker` (backstop blindness test)

Registers a non-done asyncio.Future in `_active_workers` and runs the health check.

- Proves the consequence: `_agent_session_health_check` calls `worker.done()` → `False`
  → sets `worker_alive = True` → calls `event.set()` → `continue`. No escalation.
- The session remains `pending` after the health check runs.
- `WORKER_ASYNCIO_DEBUG` + `set_debug` cannot see this: the parked coroutine yields the
  loop cleanly (no slow callback). Only the slot-exhaustion forensic log catches it.

  ```python
  # NOTE: _agent_session_health_check() never reads _global_session_semaphore.
  # It only checks worker.done(). This test proves the 300s backstop is BLIND
  # to the parked-loop wedge. The actual loop-park proof lives in A1
  # (test_worker_loop_parks_on_zero_semaphore).
  ```

---

## Detection Surfaces Shipped

### `WORKER_ASYNCIO_DEBUG` (B2 / C-rev4)

`worker/__main__.py` ships `_asyncio_debug_enabled(env_value: str | None) -> bool` and
the startup wiring:

```python
if _asyncio_debug_enabled(os.environ.get("WORKER_ASYNCIO_DEBUG")):
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.1  # 100 ms
```

Enable with `WORKER_ASYNCIO_DEBUG=1`. Catches synchronous callbacks that block the event
loop. **C1 limitation**: does NOT detect the await-suspension wedge (Hypothesis 1).

### Always-on slot-exhaustion forensic line (Deliverable D)

`agent/session_health.py` (around line 2560) logs on every health-check tick that finds
a pending session with a live (not done) worker:

```
[session-health] PENDING-WEDGE FINGERPRINT: worker_key=... session=... —
semaphore permits_free=0 AND running_count=N < max_sessions=M.
Slot held by non-running session (#1537 class): worker loop is parked at
await semaphore.acquire(). See docs/features/worker-wedge-investigation.md.
```

This fires only when `_global_session_semaphore._value == 0` AND running sessions are
fewer than the configured maximum. It is **always-on** (no env flag) and **logging-only**
— the `event.set(); continue` recovery decision is unchanged.

#### How to read the forensic log

| Log level | Message pattern | Interpretation |
|-----------|-----------------|----------------|
| `WARNING` | `PENDING-WEDGE FINGERPRINT: … running_count=N < max_sessions=M` | Leaked-slot wedge: a non-running session holds a semaphore permit |
| `INFO` | `permits_free=0, running_count=N >= max_sessions=M (healthy backpressure)` | All slots held by legitimately running sessions; normal back-pressure |
| (silent) | No log | Semaphore has free permits; wedge is a different anomaly |

---

## Binary Decision

**MECHANISM DEMONSTRATED — NOT REPRODUCIBLE AS ISOLATED ROOT CAUSE AFTER #1804.**

The acquire/release audit confirms there is no concrete slot-leak path in the current
code. All exit paths from `_worker_loop` — including `CancelledError`, normal completion,
`StatusConflictError`, and `BaseException` — release the slot via the `finally` block at
line 1641 or explicit exception handlers.

The wedge mechanism is real (proven by A1) and can manifest in production via:

1. PTY-pool saturation (H3): all global semaphore slots held by sessions blocking inside
   `_execute_agent_session` waiting for a PTY pair. Slots are eventually released when
   the blocking sessions complete or are cancelled by the no-progress detector.

2. Dead notify-listener + full semaphore at wake-time: the dead-subscription issue fixed
   in #1804 prevented timely event-based wakeup. If the health-check's 300s nudge woke
   the loop while the semaphore was simultaneously at 0, the loop would re-park at
   `semaphore.acquire()` rather than at `event.wait()`, making the nudge ineffective.

Both root causes are addressed: #1804 ensures timely event delivery; the forensic log
and PTY no-progress detector provide detection and recovery for the PTY-saturation path.

A follow-up GitHub issue would be warranted if a concrete slot-leak path is found
in a future incident — watch the `PENDING-WEDGE FINGERPRINT` log line. For now,
issue #1808 can be closed.

---

## See Also

- `docs/features/bridge-worker-architecture.md` — overall bridge/worker design
- `docs/features/agent-session-health-monitor.md` — health monitor design
- `tests/integration/test_worker_wedge_pending.py` — reproduction harness
- `agent/agent_session_queue.py:1286` — `_worker_loop` definition
- `agent/session_health.py:2330` — `_agent_session_health_check` definition
- `worker/__main__.py:54` — `_asyncio_debug_enabled` helper
