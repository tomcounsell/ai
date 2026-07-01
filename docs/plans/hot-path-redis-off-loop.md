---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1826
last_comment_id:
---

# Move Hot-Path Redis Off the Event Loop

## Problem

Popoto is the **synchronous** redis-py client (`redis==7.4.0`). Every
`AgentSession.query.*` / `Memory.*` call blocks the calling thread until Redis
answers. When those calls run **on the asyncio event loop**, a slow or restarting
Redis wedges the *entire* loop — every session, every monitor, the dead-man's-switch
tick, all frozen in lockstep — for up to `socket_timeout` (5s) per call, now
compounded by the `Retry(ExponentialBackoff, retries=3)` layer added in #1814
(a single wedged op can block the loop for ~5s × up to 3 attempts + backoff).

Two concrete on-loop call sites are the hot spots:

1. **The drain-loop hot path** — `agent/agent_session_queue.py:1367-1376`:
   ```python
   _has_pending = bool(
       AgentSession.query.filter(
           **({"project_key": worker_key} if is_project_keyed else {"chat_id": worker_key}),
           status="pending",
       )
   )
   ```
   This unwrapped synchronous scan runs inside the per-worker drain loop on every
   idle iteration. Under a slow Redis it blocks the loop directly.

2. **Startup scans** — `worker/__main__.py`: the Redis-verify scan
   (`list(AgentSession.query.filter(status="pending"))`, `:699`), the pending-sessions
   scan that kicks worker loops (`:931`), and the heavy synchronous cleanup/recovery
   helpers (`run_cleanup` `:772`, `cleanup_corrupted_agent_sessions` `:786`,
   `clean_indexes` `:811`, `_heal_future_updated_at` `:827`, `_sweep_dead_worker_sessions`
   `:841`, `_recover_interrupted_agent_sessions_startup` `:849`) all SCAN Redis
   synchronously on the loop during startup.

**Why #1814 did not close this.** #1814 (PR #1824, merged) added retry/backoff/
health-check to the Popoto client via `config/redis_bootstrap.py` (Fix #3). That
*bounds and recovers* from slowness (a transient restart reconnects instead of
raising) — but a slow call still **blocks the loop for its whole duration**.
Retry/backoff makes the block *longer*, not shorter. #1814 explicitly deferred the
off-loop move as Fix #4 (this plan) — see `docs/plans/completed/redis-durability-hardening.md`
No-Gos, `[SEPARATE-SLUG #1814] Fix #4`.

**Composition hazard with #1815 (already merged).** #1815 installed an on-loop
liveness beacon (`_loop_tick_task`, `worker/__main__.py:975-996`, bumps
`last_loop_tick` every ~5s) read by an off-loop watchdog that `os.abort()`s the
worker if the beacon goes stale past `WORKER_DEADMAN_STALENESS_THRESHOLD` (~90s).
Today a slow/restarting Redis that stacks several ~15s on-loop hot-path blocks can
starve the tick task's `asyncio.sleep` resumption → the beacon goes stale → the
dead-man's-switch **false-SIGABRTs a perfectly healthy worker** whose only problem
is a slow Redis, needlessly re-queuing in-flight work. Moving the hot path off-loop
is therefore not just a latency fix — it is what keeps #1815's dead-man's-switch
from firing on a Redis blip.

**Current behavior:** A slow/restarting Redis freezes the whole worker loop up to
`socket_timeout × retries` per call; unrelated sessions and monitors stall; the
#1815 tick can go stale and trigger a false self-kill.

**Desired outcome:** Hot-path and startup Popoto scans execute off the event loop
on a bounded, thread-safe executor so a slow Redis degrades *individual call
latency* without freezing the loop; the #1815 tick keeps firing throughout;
unrelated sessions/monitors keep making progress; and redis-call latency is
operator-visible so a regression is caught early.

## Scope (Fix #4 from the #1814 ranked-fix table)

**IN SCOPE (Medium appetite):**
- Introduce a single off-loop redis-offload mechanism (`run_in_executor` on a
  dedicated bounded thread pool) and **cut over** the hot-path drain query
  (`agent_session_queue.py:1367-1376`) and the startup scans
  (`worker/__main__.py`) to it — no sync path left behind.
- Expose a redis-call-latency / loop-stall operator metric (dashboard + threshold
  WARNING log) so a regression is visible.
- Verify no loop-wide freeze under an artificially slowed Redis, and that the
  #1815 tick keeps advancing throughout.

**OUT OF SCOPE (see No-Gos):** rewriting Popoto to an async client; Fix #2 (SQLite
secondary store) and Fix #5 (Redis replication + Sentinel) from #1814; the deferred
liveness fixes #1820/#1821.

## Freshness Check

**Baseline commit:** `b99e295821573d011c2981c401c8977ee87fe045` (main, plan time)
**Issue filed at:** 2026-06-30T05:37:07Z
**Disposition:** Unchanged

**File:line references re-verified against `b99e2958`:**
- `agent/agent_session_queue.py:1367-1376` — the `_has_pending = bool(AgentSession.query.filter(...))`
  synchronous drain-loop scan — **still holds** (verified verbatim; inside the
  per-worker loop's "session is None" branch, guarding the `event.clear()` before
  `await event.wait()`).
- `agent/agent_session_queue.py:970` — `DRAIN_TIMEOUT = 1.5` — **still holds**
  (bridge-mode wait bound; standalone mode waits indefinitely, `:1384-1386`).
- `agent/agent_session_queue.py:404` — `await asyncio.to_thread(POPOTO_REDIS_DB.publish, ...)`
  — **still holds**: the enqueue path *already* offloads a Popoto/redis-py call via
  `asyncio.to_thread` (also `:321,363,375,776,939`). This is direct in-repo precedent
  that the redis-py client is already relied upon as thread-safe.
- `worker/__main__.py:691` — `register_worker_pid()` (sync Redis write) — **still holds**.
- `worker/__main__.py:699` — `list(AgentSession.query.filter(status="pending"))` Redis-verify
  scan, `sys.exit(1)` on failure — **still holds**.
- `worker/__main__.py:772/786/811/827/841/849` — the sequential startup cleanup/recovery
  scans (index rebuild → corrupted cleanup → class-set clean → heal-future → dead-worker
  sweep 3a → recover 3b) — **still holds**; ordering comments intact (`:832-839` explicitly
  requires 3a BEFORE 3b).
- `worker/__main__.py:891` — `granite_ok, granite_detail = await asyncio.to_thread(ensure_granite_model)`
  — **still holds**: existing off-loop offload precedent adjacent to the startup scans.
- `worker/__main__.py:931` — `pending_sessions = list(AgentSession.query.filter(status="pending"))`
  feeding the `_ensure_worker` startup kick — **still holds**.
- `worker/__main__.py:975-996` — `_loop_tick_task()` (the #1815 on-loop beacon) + its
  done-callback — **still holds**; MUST remain on-loop and unchanged.
- `agent/reflection_scheduler.py:57-64` — `_reflection_pool = ThreadPoolExecutor(...)`
  bulkhead (`REFLECTION_POOL_WORKERS`, default 2, clamped ≥1) — **still holds**; the
  template this plan mirrors for the redis-offload bulkhead.
- `config/redis_bootstrap.py:122-129` — `set_REDIS_DB_settings(..., retry=Retry(ExponentialBackoff, 3), health_check_interval=30)`; **no `max_connections` set** → the redis-py `ConnectionPool` is unbounded (default) — **still holds**; load-bearing for the thread-safety plan (executor workers ≤ pool capacity trivially satisfied).

**Cited sibling issues/PRs re-checked:**
- #1814 — CLOSED (PR #1824 merged): added the retry/backoff client; explicitly deferred
  this off-loop move as Fix #4. `docs/plans/completed/redis-durability-hardening.md`
  confirms the deferral and names `agent/agent_session_queue.py:1367-1376` as the hot path.
- #1815 — the on-loop tick / dead-man's-switch this plan must compose with (merged;
  `docs/plans/completed/liveness-wedge-recovery.md`). This plan touches
  `agent/agent_session_queue.py` (hot path) and `worker/__main__.py` (startup scans);
  #1815's tick task and `session_state.last_loop_tick` beacon are **read/preserved, not
  modified**.
- #1818 — OPEN — resilience-cluster umbrella; #1826 is the #1814 Fix #4 child.
- #1816 — worker fault containment (merged): established the bulkhead pattern
  (`_reflection_pool`) this plan reuses. No file overlap with the hot-path query.

**Commits on main since the issue was filed touching referenced files:** none.
`git log --since=2026-06-30T05:37:07Z` over `agent/agent_session_queue.py`,
`worker/__main__.py`, `config/redis_bootstrap.py` returned zero. Premises intact.

**Active plans in `docs/plans/` overlapping this area:** none live. `liveness-wedge-recovery.md`
and `redis-durability-hardening.md` are both **completed**; this plan builds on both.

## Prior Art

- **#1814 / `config/redis_bootstrap.py`** — the resilient client (retry/backoff/health-check).
  This plan is the deferred Fix #4 that the durability plan named but did not implement.
  The bootstrap's unbounded `ConnectionPool` is what makes off-thread calls safe.
- **`agent/agent_session_queue.py:321,363,375,404,776,939`** — the enqueue path already
  wraps Popoto/redis-py calls (`transition_status`, `_init_stage_states`,
  `POPOTO_REDIS_DB.publish`, session reads, the pubsub listen thread) in
  `asyncio.to_thread`. This is the proven in-repo pattern; the hot-path read is the
  conspicuous *un-offloaded* survivor.
- **`agent/reflection_scheduler.py:57-64`** — the `_reflection_pool` bulkhead (a dedicated
  `ThreadPoolExecutor` so heavy scans don't starve critical-path work). This plan mirrors
  it exactly for the redis-offload pool.
- **`worker/__main__.py:891`, `agent/session_health.py:2047`, `agent/session_executor.py:354,486`**
  — existing `asyncio.to_thread` / `run_in_executor` offloads of sync work in the
  health/execution path — establishes that off-loop offload of blocking work is idiomatic here.
  (Precedent only; this plan adds one new instrumented seam, `offload_redis`, not raw `to_thread`.)
- **#1815 `_loop_tick_task`** — the on-loop beacon whose liveness this plan protects.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Left This Gap |
|-----------|-------------|----------------------|
| #1814 (Fix #3) resilient client | Added retry/backoff/health-check so a transient Redis restart reconnects | Bounds *recovery*, not *loop occupancy*: a slow call still blocks the loop for its full duration, and retries make that block **longer**. Fix #4 (off-loop) was explicitly deferred. |
| #1815 on-loop tick / dead-man's-switch | Detects a synchronously-frozen loop and self-kills | Cannot distinguish "worker wedged" from "Redis slow, loop blocked in a sync redis call" — so a slow Redis can *false-trigger* the self-kill. This plan removes the on-loop block that confuses it. |
| enqueue-path `to_thread` wrapping | Offloaded the *write* path's Popoto calls | Never covered the *read* hot path (`:1367-1376`) — the one remaining on-loop, concurrently-running blocking site that starves the tick. |

**Root-cause pattern:** the async migration of Popoto calls was done piecemeal on the
write/enqueue path but the read hot path was left synchronous on the loop, where it runs
concurrently with the tick and every session. This plan finishes the cut-over for that one
site (the startup scans are deliberately left alone — see the Problem-section rationale).

## Research

No external research needed — the mechanism (redis-py `ConnectionPool` thread-safety +
`loop.run_in_executor` on a bounded pool) is well-established and already used in this
repo (`_reflection_pool`, the enqueue `to_thread` calls). One decision was validated
against source (below): a real async client is infeasible because Popoto's entire ORM
(query builder, index sets, class sets, `save`/`delete`) is synchronous and third-party.

**Executor vs. async-client decision (the #1 risk per the #1820 critique):**

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **`run_in_executor` / thread pool** | ✅ **CHOSEN** | Popoto is sync and *pervasive* (every model, query, index op). redis-py's `redis.Redis` + `ConnectionPool` are **thread-safe** — the pool hands out a distinct connection per concurrent operation. The repo **already** offloads Popoto via `asyncio.to_thread` on the enqueue path (`:321,363,375,404,776,939`), proving thread-safety is already relied upon. Zero ORM changes; surgical cut-over of one call site. |
| **Real async client (`redis.asyncio`)** | ❌ Rejected | Would require reimplementing Popoto's ORM (models, query builder, index/class sets, save/delete) against an async client — a multi-week rewrite of a third-party package, far beyond a Medium appetite, and would create a **parallel** async ORM alongside the sync one used everywhere else. |

**Thread-safety plan (spelled out, per critique lesson #2):**
- The global `POPOTO_REDIS_DB` client rebuilt by `configure_resilient_redis()` uses redis-py's
  default `ConnectionPool`, which is **thread-safe** and **unbounded** (`max_connections`
  unset — verified at `config/redis_bootstrap.py:122-129`). Concurrent offloaded calls each
  check out their own connection; there is no shared mutable client state to corrupt.
- Offload runs on a **dedicated bounded** `ThreadPoolExecutor` (`_redis_io_pool`,
  `REDIS_IO_POOL_MAX_WORKERS`, default 4, clamped ≥1) — a bulkhead mirroring
  `_reflection_pool` so a slow Redis cannot exhaust the shared asyncio default pool that
  granite probes / `session_executor` also use. There is exactly ONE offload seam
  (`offload_redis` on `_redis_io_pool`); the plan does NOT mix in raw `asyncio.to_thread`
  (which would route calls through the unmeasured shared default pool and defeat the metric).
- **Invariant (documented + guarded by a No-Go):** executor `max_workers` must stay ≤ the
  redis-py pool capacity. Because the pool is unbounded today, any small worker count is
  safe; if anyone later sets `max_connections`, it must be ≥ `REDIS_IO_POOL_MAX_WORKERS +
  REFLECTION_POOL_WORKERS + default-pool peak`, else offloaded calls would block on
  `BlockingConnectionPool` checkout — re-introducing a stall. Called out in No-Gos.
- Popoto's module-global client symbol is read (not reassigned) by the offloaded callables;
  the only reassignment is the one-shot `configure_resilient_redis()` at startup, which runs
  **before** any worker loop or offloaded call. No cross-thread reassignment race.

## Data Flow

**Hot path (drain loop, `agent_session_queue.py`):**
1. Per-worker drain loop pops a session; if `None`, releases the semaphore.
2. **Idle-check (was on-loop, now off-loop):** `_has_pending = await offload_redis(lambda: bool(AgentSession.query.filter(..., status="pending")))` — runs on `_redis_io_pool`; the loop stays free while Redis is queried.
3. If pending → `continue` (skip the wait). Else → `event.clear()` → `await event.wait()`
   (standalone) / `asyncio.wait_for(event.wait(), DRAIN_TIMEOUT)` (bridge). **Ordering
   preserved:** the pending-check still happens BEFORE `event.clear()` (the existing
   lost-wakeup mitigation, `:1363-1366`).

**Startup (`worker/__main__.py`, sequential) — UNCHANGED by this plan:**
1. `register_worker_pid()` → Redis-verify scan (`:699`) → cleanup/recovery scans
   (`:772,786,811,827,841,849`) → pending-sessions scan (`:931`) → `_ensure_worker` kick →
   `_loop_tick_task` created (`:985`). These run synchronously on the loop exactly as today.
2. They are **not** offloaded: they precede beacon arming (`:985`), run sequentially with
   nothing else on the loop, and their order is load-bearing (3a before 3b, `:832-839`).
   Offloading them would protect no liveness and risk a startup re-order — see No-Gos.

**Liveness composition (#1815):**
3. Once the drain loops are running, the on-loop `_loop_tick_task` keeps bumping
   `last_loop_tick` every ~5s while the offloaded hot-path idle-check runs in a thread →
   beacon stays fresh under a slow Redis → the off-loop watchdog does **not** false-SIGABRT.
   The tick task itself is untouched and stays on-loop.

**Metric surface:**
4. `offload_redis` times **every** offloaded call (there is only the one hot-path site),
   updates module-global `last`/`max` latency gauges, and emits a threshold-gated WARNING;
   `ui/app.py::dashboard_json` surfaces the gauges — so 100% of offloaded Redis I/O is measured.

## Architectural Impact

- **New dependencies:** none (stdlib `concurrent.futures.ThreadPoolExecutor`,
  `asyncio.get_running_loop().run_in_executor`).
- **Interface changes:** a new `agent/redis_offload.py` with `offload_redis(fn, *args)`,
  the `_redis_io_pool`, and latency-gauge accessors. No signature changes to existing
  functions; one call site (the drain-loop idle-check) changes from sync to `await`, and
  `ui/app.py::dashboard_json` gains a read-only metric block.
- **Coupling:** slightly *reduces* on-loop coupling to Redis; adds a bounded, isolated
  bulkhead. The hot-path call site becomes `async`-aware (it is already inside an async
  function).
- **Data ownership:** unchanged. Redis remains the single store; query semantics identical.
- **Reversibility:** high and **complete**. The only offloaded site is the one that routes
  through `offload_redis`, so `REDIS_OFFLOAD_ENABLED=false` makes that seam a synchronous
  pass-through and restores prior on-loop behavior at **every** cut-over site (there is no raw
  `to_thread` startup path to leave un-reverted). The pool size is env-tunable.

## Appetite

**Size:** Medium

**Team:** Solo dev, async-specialist (executor / thread-safety / `run_in_executor`
cancellation semantics), 1 review round.

**Interactions:**
- PM check-ins: 1 (confirm executor-vs-async decision + the metric surface shape).
- Review rounds: 1 (async correctness of the cut-over + confirming the #1815 tick is preserved).

This is one small new module + one surgical call-site cut-over + a dashboard field. The
care goes into (a) not defeating the #1815 tick and (b) proving no loop freeze under a
slow Redis. Startup is left entirely untouched.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Python ≥ 3.11 | `python -c "import sys; assert sys.version_info >= (3, 11)"` | Modern `asyncio`/`run_in_executor` semantics (note: an in-flight executor thread still runs to completion on cancel — see Risk 3; this is not force-cancellation) |
| redis-py thread-safe pool | `python -c "import redis; print(redis.__version__)"` | `ConnectionPool` thread-safety (7.4.0 ✓) |
| Resilient client present (#1814) | `test -f config/redis_bootstrap.py && echo ok` | Off-thread calls use the retry/backoff client |
| #1815 tick present | `grep -c "_loop_tick_task\|bump_loop_tick" worker/__main__.py` | Composition target exists |

## Solution

### Key Elements

- **`agent/redis_offload.py`** — a new module holding:
  - `_redis_io_pool = ThreadPoolExecutor(max_workers=REDIS_IO_POOL_MAX_WORKERS,
    thread_name_prefix="redis-io-")` (default 4, clamped ≥1) — the bulkhead.
  - `async def offload_redis(fn, *args, **kwargs)` — `await loop.run_in_executor(_redis_io_pool, functools.partial(fn, *args, **kwargs))`, timing the call, updating latency gauges, and emitting a threshold-gated WARNING. A `REDIS_OFFLOAD_ENABLED` kill switch (default true) makes it a synchronous pass-through for rollback.
  - Latency gauges (`get_last_redis_latency()`, `get_max_redis_latency()`, `reset_max`) — module-global floats, GIL-atomic.
- **Hot-path cut-over (the ONLY code cut-over)** (`agent_session_queue.py:1367-1376`):
  replace the synchronous `_has_pending = bool(AgentSession.query.filter(...))` with the
  awaited `offload_redis(...)` form, **preserving the check-before-`event.clear()` ordering**.
  No sync fallback left. Every offloaded Redis call in the codebase after this change goes
  through the single instrumented `offload_redis` seam.
- **Startup scans left untouched** (`worker/__main__.py:691,699,772,786,811,827,841,849,931`):
  NOT offloaded. The beacon (`:985`) is created after all of them, so they protect no liveness,
  and their order is load-bearing — see No-Gos.
- **Operator metric**: `ui/app.py::dashboard_json` gains a `redis_offload` block
  (`last_latency_s`, `max_latency_s`); a WARNING logs when a single call exceeds
  `REDIS_OFFLOAD_SLOW_THRESHOLD` (default ~1s).
- **#1815 tick untouched**: `_loop_tick_task` and `last_loop_tick` are neither moved nor
  reworked; the plan only *relieves* the loop of the blocking calls that starved them.

### Flow

Worker starts → `configure_resilient_redis()` (unchanged, #1814) → startup scans run
synchronously **in order** (unchanged; beacon not yet armed) → `_loop_tick_task` starts
(on-loop, unchanged, `:985`) → drain loops run; the idle-check `offload_redis(...)` executes
on `_redis_io_pool` so the loop stays free → a slow Redis lengthens *individual* call latency
(surfaced on the dashboard + WARNING) but the tick keeps firing and unrelated sessions keep
progressing → no loop-wide freeze, no false dead-man's-switch abort.

### Technical Approach

**1. `agent/redis_offload.py` (new, mirrors `reflection_scheduler` bulkhead):**
```python
import asyncio, functools, logging, os, time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Bulkhead pool for off-loop Redis I/O. Isolated from the shared asyncio default
# pool so a slow Redis cannot starve granite probes / session_executor offloads.
# Grain of salt: default is PROVISIONAL — tune after observing real latency in logs.
REDIS_IO_POOL_MAX_WORKERS = max(1, int(os.environ.get("REDIS_IO_POOL_MAX_WORKERS", "4")))
REDIS_OFFLOAD_SLOW_THRESHOLD = float(os.environ.get("REDIS_OFFLOAD_SLOW_THRESHOLD", "1.0"))
REDIS_OFFLOAD_ENABLED = os.environ.get("REDIS_OFFLOAD_ENABLED", "true").strip().lower() \
    not in ("", "0", "false")

_redis_io_pool = ThreadPoolExecutor(
    max_workers=REDIS_IO_POOL_MAX_WORKERS, thread_name_prefix="redis-io-"
)
_last_latency: float = 0.0
_max_latency: float = 0.0

async def offload_redis(fn, *args, **kwargs):
    """Run a synchronous Popoto/redis-py callable off the event loop.

    redis-py's ConnectionPool is thread-safe; each offloaded call checks out its
    own connection. When REDIS_OFFLOAD_ENABLED is false, runs inline (rollback).
    """
    global _last_latency, _max_latency
    call = functools.partial(fn, *args, **kwargs)
    if not REDIS_OFFLOAD_ENABLED:
        return call()  # rollback: on-loop behavior
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    try:
        return await loop.run_in_executor(_redis_io_pool, call)
    finally:
        dt = time.monotonic() - t0
        _last_latency = dt
        if dt > _max_latency:
            _max_latency = dt
        if dt > REDIS_OFFLOAD_SLOW_THRESHOLD:
            logger.warning("[redis-offload] slow Redis call: %.2fs (threshold %.2fs)",
                           dt, REDIS_OFFLOAD_SLOW_THRESHOLD)
```
Plus `get_last_redis_latency()` / `get_max_redis_latency()` accessors.

**2. Hot-path cut-over (`agent_session_queue.py:1367-1376`):**
```python
from agent.redis_offload import offload_redis  # module-level import

# ... inside the drain loop, session is None branch, BEFORE event.clear():
_has_pending = bool(
    await offload_redis(
        lambda: list(
            AgentSession.query.filter(
                **({"project_key": worker_key} if is_project_keyed else {"chat_id": worker_key}),
                status="pending",
            )
        )
    )
)
```
The `lambda` materializes the query result **inside the thread** (Popoto's
`query.filter` returns a lazy result; wrap in `list(...)` so all Redis I/O happens
off-loop, then `bool(...)` on the loop). The check stays BEFORE `event.clear()`.

**3. Startup scans — NO code change (`worker/__main__.py`):** leave `:691` `register_worker_pid()`,
the `:699` verify scan (incl. its `except → sys.exit(1)`), the `:772/786/811/827/841/849`
cleanup/recovery helpers, and the `:931` pending scan exactly as they are — synchronous and
in-order. They run before the beacon is armed (`:985`), so offloading them protects no liveness,
and their order is load-bearing (3a before 3b, `:832-839`). Descoped — see No-Gos.

**4. Metric surface (`ui/app.py::dashboard_json`):** add
`"redis_offload": {"last_latency_s": get_last_redis_latency(), "max_latency_s": get_max_redis_latency()}`
under `health`. Import the accessors lazily inside `dashboard_json` (matching the existing
lazy-import style there).

**5. #1815 composition (do NOT touch):** leave `_loop_tick_task`, `bump_loop_tick`,
`get_loop_tick`, and the watchdog thread exactly as-is. The only interaction is that the
loop is now free during Redis calls, so the tick keeps firing — verified by the acceptance
test.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `offload_redis` must propagate the wrapped callable's exception (e.g. a Redis
      `ConnectionError`) to the awaiting caller — NOT swallow it — so the drain loop's
      existing error handling still applies. Test asserts the exception surfaces through
      `run_in_executor`.
- [ ] The `:699` verify scan still `sys.exit(1)` when the (unchanged, on-loop) scan raises
      (Redis down at boot). Verify-only: this path is NOT touched by this plan, so the test
      confirms the descope left boot-time exit semantics intact.
- [ ] `offload_redis`'s latency-gauge update runs in `finally` even when the call raises —
      test that a raising call still records latency and does not leak the gauge.

### Empty/Invalid Input Handling
- [ ] `REDIS_IO_POOL_MAX_WORKERS=0` (misconfig) must clamp to 1, not create a zero-worker
      pool that deadlocks. Test the clamp.
- [ ] `REDIS_OFFLOAD_ENABLED=false` runs the callable inline (rollback path) and still
      returns the correct result. Test the pass-through branch.
- [ ] A `query.filter` returning an empty result → `_has_pending` is `False` and the loop
      proceeds to `event.clear()` exactly as before. Test parity with the pre-cut-over path.

### Error State Rendering
- [ ] A slow call (> `REDIS_OFFLOAD_SLOW_THRESHOLD`) emits the WARNING with the measured
      duration. Test captures the log record.
- [ ] `dashboard_json` renders the `redis_offload` block with numeric latencies (never
      `None`/`KeyError`) even before any call has run (gauges init to 0.0). Test the shape.

## Test Impact

- [ ] `tests/unit/test_agent_session_queue_async.py` — UPDATE: the drain-loop idle-check is
      now `await offload_redis(...)`. Any test that patches `AgentSession.query.filter` must
      still observe the call (it now runs in a thread); assert the check-before-`event.clear()`
      ordering and that a pending result still `continue`s. If a test asserts the call happens
      synchronously on the loop, REPLACE that assertion with the offloaded form.
- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE (verify-only): confirm drain
      semantics (pop → idle-check → clear → wait) are unchanged by the offload.
- [ ] `tests/unit/test_worker_startup.py` / `tests/unit/test_worker_startup_validation.py` /
      `tests/unit/test_worker_entry.py` — UPDATE (verify-only): the startup scans are NOT
      changed by this plan (descoped). Confirm these tests still pass unchanged — the step ORDER
      (index rebuild → corrupted → class-set → heal → 3a sweep → 3b recover → pending kick) and
      the verify-scan `sys.exit(1)` path are untouched. No code change means no test change; this
      row is a regression guard proving the descope introduced no startup drift.
- [ ] `tests/unit/test_worker_deadman.py` — UPDATE (verify-only): confirm the tick task and
      watchdog are unchanged; add/point to the composition test (below) proving the tick keeps
      advancing while a Redis call is slow.
- [ ] `tests/unit/test_worker_session_sweep.py` — UPDATE (verify-only): `_sweep_dead_worker_sessions`
      is NOT offloaded (startup is descoped); confirm the sweep result/ordering is unchanged.
- [ ] New: `tests/unit/test_redis_offload.py` — CREATE: pass-through when disabled; clamp
      `max_workers` ≥ 1; exception propagation; latency gauge update in `finally`; slow-call
      WARNING; concurrent calls run on distinct threads (thread-safety smoke).
- [ ] New: `tests/integration/test_slow_redis_no_loop_freeze.py` — CREATE (the acceptance
      criterion): with an artificially slowed Redis (patch the offloaded callable / global
      client to `time.sleep(N)` inside the thread), assert (a) `get_loop_tick()` keeps
      advancing while the slow call is in flight, (b) an unrelated coroutine makes progress,
      (c) no `_self_kill`/SIGABRT is triggered.

## Rabbit Holes

- **Rewriting Popoto to `redis.asyncio`.** Do NOT. Popoto is pip-installed and its whole ORM
  is synchronous; an async rewrite is a multi-week third-party reimplementation and would
  create a parallel async ORM. The executor is the sanctioned, already-used seam.
- **Offloading the startup scans (`worker/__main__.py`).** Do NOT — this was in an earlier
  draft and is deliberately descoped. The `_loop_tick_task` beacon is not created until
  `:985`, *after* every startup scan (`:691,699,772,786,811,827,841,849,931`), so the beacon
  is unarmed during startup and offloading those scans protects **no** liveness. They are also
  strictly sequential with nothing else on the loop and their order is load-bearing (3a before
  3b, `:832-839`). Offloading them buys zero liveness while adding a startup re-ordering hazard.
  Leave them synchronous and in-order.
- **Offloading *every* Popoto call in the codebase.** Scope is the ONE named site (the hot-path
  drain query). A blanket sweep of every `.query.` in the repo is a separate, much larger effort
  and risks breaking synchronous call sites that are not on the loop.
- **Moving the #1815 tick task off-loop.** NEVER. The tick is the liveness signal — it MUST
  stay on-loop or it can no longer detect a real synchronous freeze. This plan protects the
  tick; it does not touch it.
- **Tightening the redis-py `ConnectionPool` `max_connections`.** Do NOT set it below the
  combined executor workers (`REDIS_IO_POOL_MAX_WORKERS` + `REFLECTION_POOL_WORKERS` +
  default-pool peak). A too-tight pool turns off-loop calls into `BlockingConnectionPool`
  waits — re-introducing the very stall this plan removes. Leave the pool unbounded (current).
- **Chasing a lost-wakeup fix in the drain loop.** The check-before-`event.clear()` race is
  pre-existing (`:1363-1366`); the definitive fix is the notify-listener design, out of scope.
  Preserve the existing ordering; do not attempt to redesign the wait/notify here.

## Risks

### Risk 1: The added `await` in the drain loop widens the pre-existing lost-wakeup window
**Impact:** Between the offloaded idle-check returning `False` and `event.clear()`, an
`enqueue_agent_session()` `event.set()` could be lost, parking the worker (indefinitely in
standalone mode) until the next notify.
**Mitigation:** The window already exists with the sync check; the offload only lengthens it
by the executor round-trip. The check stays BEFORE `event.clear()` (the existing mitigation),
and bridge mode bounds the wait at `DRAIN_TIMEOUT=1.5s`. The pubsub notify listener
(`agent_session_queue.py:939`) re-signals on new work. Document the ordering invariant inline;
do not redesign the wait/notify (Rabbit Hole). Test asserts a pending result observed at the
check still `continue`s without clearing.

### Risk 2: A slow Redis saturates the bounded `_redis_io_pool`
**Impact:** If more than `REDIS_IO_POOL_MAX_WORKERS` offloaded calls block on a slow Redis,
further offloads queue behind them — a *thread-pool* stall (bounded), not a loop freeze.
**Mitigation:** This is the intended bulkhead behavior: the *loop stays free* and the tick
keeps firing even if the pool is full. The dashboard `max_latency_s` + WARNING make pool
pressure visible. `REDIS_IO_POOL_MAX_WORKERS` is env-tunable to widen the bulkhead. The pool
is isolated from granite/`session_executor` offloads so their path is unaffected.

### Risk 3: `run_in_executor` cancellation leaks a running Redis call
**Impact:** If the drain loop task is cancelled while an offloaded call is in flight, the
thread keeps running to completion (executor futures aren't force-cancelled).
**Mitigation:** The offloaded callables are short reads/scans that complete on their own; a
leaked thread finishes and returns its connection to the pool. `daemon`-style
`ThreadPoolExecutor` threads cannot outlive the process. No shared mutable state is left
half-written (reads only). Acceptable for a Medium-appetite fix.

### Risk 4: Startup timing/ordering regression — ELIMINATED by descope
**Impact (in an earlier draft):** offloading startup scans risked a reorder running recovery
(3b) before the dead-worker sweep (3a), violating `:832-839`.
**Resolution:** The startup scans are **no longer offloaded** (descoped — see No-Gos). They
remain synchronous and in-order exactly as on HEAD, so this risk does not exist for the
current scope. The startup tests are retained as verify-only regression guards proving the
descope introduced no drift. No `gather`, no `await` reordering, no code change at those sites.

## Race Conditions

### Race 1: Idle-check offload result vs. a concurrent enqueue `event.set()`
**Location:** `agent_session_queue.py` drain loop, `:1363-1382`.
**Trigger:** `enqueue_agent_session()` fires `event.set()` while the offloaded idle-check is
in flight or between its return and `event.clear()`.
**Data prerequisite:** The idle-check reads `status="pending"` for `worker_key`.
**State prerequisite:** The check must run BEFORE `event.clear()` (lost-wakeup mitigation).
**Mitigation:** Preserve the existing check-before-clear ordering; the offload does not
reorder it. If work was pending at check time, `continue` skips the wait. The bounded
`DRAIN_TIMEOUT` (bridge) and the pubsub notify listener re-signal, so a lost notify is
recovered. Same guarantee as pre-fix, wider window by one executor round-trip (Risk 1).

### Race 2: Concurrent offloaded calls checking out Redis connections
**Location:** `_redis_io_pool` threads → shared `POPOTO_REDIS_DB` `ConnectionPool`.
**Trigger:** Multiple worker drain loops offload their idle-check Redis calls simultaneously.
**Data prerequisite:** Each call needs a live connection.
**State prerequisite:** The pool must be thread-safe and have capacity.
**Mitigation:** redis-py's `ConnectionPool` is thread-safe and unbounded (default); each
call checks out and returns its own connection. Executor `max_workers` ≪ any realistic pool
cap. No shared client mutation — the client is reassigned only once at startup (before any
drain loop and thus before any offload). Documented invariant + No-Go against tightening
`max_connections`.

### Race 3: First offload runs before `configure_resilient_redis()` finishes — cannot occur
**Location:** startup ordering vs. the drain loop.
**Trigger:** The (only) offloaded call is the drain-loop idle-check.
**Data prerequisite:** The resilient client must be installed before the first offload.
**State prerequisite:** `configure_resilient_redis()` is synchronous and runs at the top of
startup; the drain loops (the sole offload site) start only after all startup completes.
**Mitigation:** Ordering is guaranteed by construction — `configure_resilient_redis()` (sync,
#1814) completes at the top of startup, long before any drain loop issues an `offload_redis`.
No new ordering risk introduced.

## No-Gos (Out of Scope)

- **Rewriting Popoto / introducing `redis.asyncio` for the ORM.** Rejected in Research; would
  create a parallel async ORM. Out of scope.
- **Offloading the startup scans** (`worker/__main__.py:691,699,772,786,811,827,841,849,931`).
  DESCOPED. The `_loop_tick_task` beacon is not created until `:985`, *after* every startup scan,
  so the beacon is unarmed during startup and offloading those scans protects **no liveness**.
  They are strictly sequential with nothing else on the loop, and their order is load-bearing
  (3a before 3b, `:832-839`). Offloading them would add a startup re-ordering hazard for zero
  liveness gain. Leave them synchronous, on-loop, and in-order — unchanged from HEAD.
- **Offloading Popoto calls beyond the ONE named site** (the hot-path drain query). A repo-wide
  sweep is a separate effort.
- **Moving or reworking the #1815 `_loop_tick_task` / `last_loop_tick` beacon.** It stays
  on-loop and unchanged — this plan only relieves the loop of the blocking calls that starved it.
- **Mixing in raw `asyncio.to_thread` as a second offload mechanism.** There is exactly ONE
  offload seam (`offload_redis` on the instrumented `_redis_io_pool`). Routing any offloaded call
  through the shared asyncio default pool via bare `to_thread` would leave it unmeasured and
  could starve the default pool the bulkhead exists to protect. Do not add a parallel mechanism.
- **Tightening the redis-py `ConnectionPool` `max_connections`.** Would re-introduce a stall
  via blocking checkout; the pool stays unbounded.
- **[SEPARATE #1814] Fix #2 (SQLite secondary store) and Fix #5 (Redis replication + Sentinel).**
  Deferred by #1814; not part of this plan.
- **[SEPARATE #1820/#1821] The deferred liveness fixes** (lease semaphore, progress-deadline
  cancel scope, out-of-domain recovery). This plan composes with #1815 but does not extend it.

## Update System

No update-script or migration changes required. The new module and the single call-site
cut-over are pure in-repo Python — no new dependency, no Popoto model/field (so **no
`scripts/update/migrations.py` entry**), no plist change, no service-restart-sequence
change. The worker is restarted by the standard `./scripts/valor-service.sh worker-restart`
after merge.

The new env vars (`REDIS_IO_POOL_MAX_WORKERS`, `REDIS_OFFLOAD_SLOW_THRESHOLD`,
`REDIS_OFFLOAD_ENABLED`) are all optional with safe defaults, so no `.env` propagation is
required. Add them to `.env.example` (each with the required comment line above the `KEY=`)
only to make them operator-discoverable — recommended but not load-bearing.

## Agent Integration

No agent integration required — this is a worker-internal performance/resilience change.

- **No new CLI entry point** in `pyproject.toml [project.scripts]`.
- **No new MCP surface**; the bridge does not import `agent/redis_offload.py` (it is called
  only from the worker drain loop's idle-check). The bridge already gets the resilient
  client via `configure_resilient_redis()` (#1814); its own Redis calls are unchanged by
  this plan.
- **Operator-facing surface:** the `redis_offload` block added to `/dashboard.json`
  (`localhost:8500`) is the internal dashboard, consistent with the existing worker-health
  surface. No new external product surface.
- **Integration test:** `tests/integration/test_slow_redis_no_loop_freeze.py` exercises the
  worker loop + tick composition end-to-end under a slow Redis.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/redis-durability.md` (created by #1814) — replace the "off-loop
      hot path" line in its deferred-roadmap section with a concrete "Off-Loop Redis Access"
      section describing the `_redis_io_pool` bulkhead, the single `offload_redis` seam, the one
      cut-over site (the drain-loop idle-check), why the startup scans are deliberately left
      on-loop (beacon unarmed at boot), and the executor-vs-async decision. (Per the
      no-historical-artifacts rule: describe the new status quo, not "was deferred, now done".)
- [ ] Forward-link from `docs/features/worker-liveness-recovery.md` (#1815) noting that the
      off-loop hot path is what keeps the dead-man's-switch tick from false-firing under a
      slow Redis (the composition guarantee).
- [ ] Add/confirm an entry in `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstring on `agent/redis_offload.py::offload_redis` explaining the thread-safety
      contract (redis-py `ConnectionPool` thread-safe; each call checks out its own
      connection) and the `REDIS_OFFLOAD_ENABLED` rollback.
- [ ] Comment the `_redis_io_pool` bulkhead rationale + the "executor workers ≤ pool capacity"
      invariant, and mark the env defaults "provisional, tune after observing real latency".
- [ ] Comment at the hot-path cut-over that the pending-check MUST stay before `event.clear()`.

## Success Criteria

- [ ] The hot-path drain query (`agent_session_queue.py:1367-1376`) executes off the event
      loop via `offload_redis`; no synchronous `AgentSession.query.filter(...)` remains on the
      loop at that site (no parallel sync path). This is the ONLY offloaded site.
- [ ] The startup scans (`worker/__main__.py:691,699,772,786,811,827,841,849,931`) are
      **unchanged** — still synchronous, on-loop, and in-order (incl. 3a-before-3b and the
      verify-scan `sys.exit(1)`). Descoped: the beacon is unarmed at boot, so there is no
      liveness to protect. Startup tests pass unchanged as a regression guard.
- [ ] Every offloaded Redis call routes through the single instrumented `offload_redis` seam
      on `_redis_io_pool` — no raw `asyncio.to_thread` offload is introduced — so 100% of
      offloaded Redis I/O is latency-measured (the "operator-visible latency" criterion holds).
- [ ] Under an artificially slowed Redis: `get_loop_tick()` keeps advancing, an unrelated
      coroutine makes progress, and no `_self_kill`/SIGABRT fires (acceptance test passes).
- [ ] The #1815 `_loop_tick_task` and `last_loop_tick` beacon are unchanged (on-loop).
- [ ] Redis-call latency is operator-visible: `/dashboard.json` exposes a `redis_offload`
      block and a WARNING logs on calls exceeding `REDIS_OFFLOAD_SLOW_THRESHOLD`.
- [ ] `REDIS_OFFLOAD_ENABLED=false` restores prior on-loop behavior at **every** cut-over site
      — because the sole cut-over site routes through `offload_redis`, the kill switch is a
      complete rollback (no un-reverted `to_thread` path exists).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`): `docs/features/redis-durability.md` gains the
      off-loop section; `worker-liveness-recovery.md` forward-links the composition.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead
NEVER builds directly.

### Team Members

- **Builder (redis-offload)**
  - Name: offload-builder
  - Role: `agent/redis_offload.py` + hot-path cut-over (`agent_session_queue.py:1367-1376`) + metric gauges
  - Agent Type: async-specialist
  - Resume: true

- **Builder (dashboard-metric)**
  - Name: metric-builder
  - Role: `ui/app.py::dashboard_json` `redis_offload` metric block (lazy-import the gauge accessors). Does NOT touch `worker/__main__.py` startup scans (descoped).
  - Agent Type: async-specialist
  - Resume: true

- **Validator (loop-freeze)**
  - Name: freeze-validator
  - Role: verify off-loop cut-over, single-mechanism (`offload_redis`-only) invariant, slow-Redis acceptance test, #1815 tick composition, and that startup scans are unchanged
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: offload-doc
  - Role: `docs/features/redis-durability.md` off-loop section + `worker-liveness-recovery.md` forward-link + README index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Redis-offload module + hot-path cut-over
- **Task ID**: build-offload
- **Depends On**: none
- **Validates**: tests/unit/test_redis_offload.py (create), tests/unit/test_agent_session_queue_async.py
- **Assigned To**: offload-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Create `agent/redis_offload.py`: `_redis_io_pool` (clamped ≥1), `offload_redis` (pass-through
  when disabled; latency gauges in `finally`; slow-call WARNING), gauge accessors.
- Cut over `agent_session_queue.py:1367-1376` to `await offload_redis(lambda: list(...))`,
  preserving the check-before-`event.clear()` ordering. Leave NO sync fallback at the site.

### 2. Dashboard metric block
- **Task ID**: build-metric
- **Depends On**: build-offload
- **Validates**: dashboard `redis_offload` shape assertion (in test_redis_offload or a ui test)
- **Assigned To**: metric-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Add the `redis_offload` block (`last_latency_s`, `max_latency_s`) to `ui/app.py::dashboard_json`
  (lazy-import the gauge accessors, matching the existing lazy-import style there).
- Do NOT touch `worker/__main__.py` startup scans — they are descoped and stay unchanged.

### 3. Validate off-loop behavior + composition
- **Task ID**: validate-freeze
- **Depends On**: build-offload, build-metric
- **Validates**: tests/integration/test_slow_redis_no_loop_freeze.py (create)
- **Assigned To**: freeze-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the slow-Redis acceptance test: tick keeps advancing, unrelated coroutine progresses,
  no SIGABRT. Confirm the ONLY offloaded site is the hot-path drain query via `offload_redis`
  (no raw `to_thread` offload added). Confirm the startup scans are unchanged from HEAD (still
  synchronous, in-order, `sys.exit(1)` intact). Confirm #1815 tick untouched.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-freeze
- **Assigned To**: offload-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Add the off-loop section to `docs/features/redis-durability.md`; forward-link
  `docs/features/worker-liveness-recovery.md`; update the README index.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: freeze-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table; confirm every Success Criterion; generate the final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_redis_offload.py tests/unit/test_agent_session_queue_async.py tests/integration/test_slow_redis_no_loop_freeze.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/redis_offload.py agent/agent_session_queue.py worker/__main__.py ui/app.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/redis_offload.py agent/agent_session_queue.py worker/__main__.py` | exit code 0 |
| Offload module exists | `test -f agent/redis_offload.py && echo ok` | output contains `ok` |
| Hot path imports the seam | `grep -c "from agent.redis_offload import offload_redis" agent/agent_session_queue.py` | output ≥ 1 |
| Hot-path idle-check IS offloaded (guaranteed-green, portable) | `python3 -c "import re; print(1 if re.search(r'_has_pending\s*=\s*bool\(\s*await\s+offload_redis\(', open('agent/agent_session_queue.py').read()) else 0)"` | output `1` — matches the exact two-line form the builder writes |
| Old on-loop `_has_pending` scan is gone | `python3 -c "import re; print(1 if re.search(r'_has_pending\s*=\s*bool\(\s*AgentSession\.query\.filter', open('agent/agent_session_queue.py').read()) else 0)"` | output `0` — the sync-on-loop form no longer exists |
| Startup scans NOT offloaded (descoped) | `grep -c "offload_redis" worker/__main__.py` | match count == 0 |
| Startup file has no added offload lines | `git diff --unified=0 -- worker/__main__.py \| grep -cE "^\+.*(offload_redis|asyncio\.to_thread|run_in_executor)"` | match count == 0 (startup untouched) |
| #1815 tick preserved | `grep -c "_loop_tick_task\|bump_loop_tick" worker/__main__.py` | output > 0 |
| Tick NOT offloaded | `grep -c "offload_redis(.*bump_loop_tick\|to_thread(.*bump_loop_tick" worker/__main__.py` | match count == 0 |
| Bulkhead pool present | `grep -c "ThreadPoolExecutor" agent/redis_offload.py` | output > 0 |
| Single instrumented seam | `grep -c "run_in_executor(_redis_io_pool" agent/redis_offload.py` | output == 1 |
| No async-redis ORM rewrite | `grep -rc "redis.asyncio" agent/redis_offload.py agent/agent_session_queue.py` | match count == 0 |
| Metric on dashboard | `grep -c "redis_offload" ui/app.py` | output > 0 |
| Rollback switch present | `grep -c "REDIS_OFFLOAD_ENABLED" agent/redis_offload.py` | output > 0 |
| Startup ordering unchanged | `grep -n "Step 3a\|Step 3b" worker/__main__.py` | 3a line precedes 3b line (unchanged from HEAD) |

## Open Questions

1. **Executor pool sizing.** Is `REDIS_IO_POOL_MAX_WORKERS=4` the right provisional default,
   or should it track `MAX_CONCURRENT_SESSIONS` (default 8) so every drain loop can offload
   without queueing behind the bulkhead? Plan ships 4 (conservative); tune after observing
   `max_latency_s` under load.
2. **Slow-call threshold.** `REDIS_OFFLOAD_SLOW_THRESHOLD=1.0s` for the WARNING — right level,
   or align to `socket_timeout` (5s) so it only fires near the retry ceiling? Plan ships 1s to
   surface early degradation.
3. **`register_worker_pid` offload — RESOLVED (dropped).** It is a single startup write, not a
   scan, and runs before the beacon is armed. Offloading it was scope creep with no liveness
   benefit; it stays on-loop and unchanged, consistent with the startup-scan descope.
4. **Offload mechanism — RESOLVED (one mechanism).** There is exactly ONE offload seam:
   `offload_redis` on the instrumented, dedicated `_redis_io_pool`. The earlier draft's split
   (some sites via `offload_redis`, some via raw `asyncio.to_thread` on the shared default pool)
   is removed — it left most sites unmeasured and routed heavy scans through the very default
   pool the bulkhead protects. With the startup scans descoped, the sole offloaded site (the
   hot-path drain query) routes through `offload_redis`, so 100% of offloaded Redis I/O is
   measured. No open question remains; this is a coherent single mechanism.
