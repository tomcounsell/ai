---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1826
last_comment_id: IC_kwDOEYGa088AAAABIrlQHA
revision_applied: true
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

1. **The drain-loop hot path** — `agent/agent_session_queue.py:1649-1658`:
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
  (`agent_session_queue.py:1649-1658`) and the startup scans
  (`worker/__main__.py`) to it — no sync path left behind.
- Expose a redis-call-latency / loop-stall operator metric (dashboard + threshold
  WARNING log) so a regression is visible.
- Verify no loop-wide freeze under an artificially slowed Redis, and that the
  #1815 tick keeps advancing throughout.

**OUT OF SCOPE (see No-Gos):** rewriting Popoto to an async client; Fix #2 (SQLite
secondary store) and Fix #5 (Redis replication + Sentinel) from #1814; the liveness
fixes #1820/#1821 (now merged separately — this plan composes with them, never touches them).

## Freshness Check

**Original baseline commit:** `b99e295821573d011c2981c401c8977ee87fe045` (main, plan time 2026-07-01)
**Re-verified against:** `06fca8078a47b704ee9b4e8defc054be3e4004f4` (main HEAD, 2026-07-03)
**Issue filed at:** 2026-06-30T05:37:07Z
**Disposition:** **Minor drift** — line numbers moved substantially (nine resilience-cluster
commits landed between the two baselines, several touching the target files), but **every
premise still holds and the build-critical cut-over target survives verbatim.** Inline
`file:line` references throughout the rest of this plan reflect the original `b99e2958`
baseline; the corrected line map below is authoritative, and the Verification section's checks
are regex/`grep`-based (line-number-independent), so the build does not depend on the stale
inline numbers. The only build-critical reference (`agent_session_queue.py`, the hot-path
site) has been updated in place to its current location.

**Corrected line map (re-verified against `06fca807`):**
- **Hot-path cut-over target (build-critical):** `agent/agent_session_queue.py:1649-1658` —
  `_has_pending = bool(AgentSession.query.filter(..., status="pending"))` — **still
  synchronous, still on-loop, verbatim.** Was `:1367-1376` at plan time; drifted ~+282 lines.
  The lost-wakeup guard comment (`:1645-1648`), the `if _has_pending: continue` (`:1659`),
  and `event.clear()` (`:1664`) → `await event.wait()` (`:1668`) ordering are all intact on
  HEAD. The plan's inline `1649-1658` references now point at the real site. **Note:** the
  build deliberately *inverts* this HEAD ordering to **clear-then-check** for the async form
  (see Technical Approach §2 and Risk 1) — the sync check-before-clear is unsafe once an
  `await` sits at the check.
- **#1820 composition — verified safe.** #1820 (PR/commit `72ba5d50`) rewrote 513 lines of
  `agent_session_queue.py`, migrating the drain loop from `asyncio.Semaphore` to a
  `SlotLeaseRegistry`. Crucially, the slot is released via `registry.release_unbound()` at
  `:1641-1643` **before** the `_has_pending` idle-check, so the offloaded `await offload_redis(...)`
  runs while holding **no** lease — adding the `await` there introduces no lease-ownership
  hazard. #1820's progress-deadline cancel scope wraps *session execution*, not the
  between-sessions idle-check, so Risk 3 (cancellation leaking a Redis call) is unchanged.
- `agent/agent_session_queue.py:1122` — `DRAIN_TIMEOUT = 1.5` (was `:970`) — **still holds**.
- `agent/agent_session_queue.py:326,368,380,409,781,1085` — the enqueue/listener path
  `asyncio.to_thread` offloads (was `:321,363,375,404,776,939`) — **still holds**: direct
  in-repo precedent that the redis-py client is relied upon as thread-safe.
- `worker/__main__.py` startup scans (all **descoped — not touched by this plan**):
  `register_worker_pid()` `:781` (was `:691`); Redis-verify pending scan `:789` (was `:699`);
  `run_cleanup()` `:911`, `cleanup_corrupted_agent_sessions()` `:925` (were `:772/786`);
  dead-worker sweep 3a `:984`, recover 3b `:992` (were `:841/849`) — **ordering comment
  intact**, 3a MUST precede 3b (`:975-981`); pending-kick scan `:1093` (was `:931`) — **all
  still hold**, structurally unchanged.
- `worker/__main__.py:1137-1147` — `_loop_tick_task()` (the #1815 on-loop beacon) + its
  done-callback at `:1149-1158` (was `:975-996`) — **still holds**; MUST remain on-loop and
  unchanged.
- `agent/reflection_scheduler.py:60-63` — `_reflection_pool = ThreadPoolExecutor(...)` bulkhead
  (`REFLECTION_POOL_WORKERS`, default 2, clamped ≥1) (was `:57-64`) — **still holds**; the
  template this plan mirrors for the redis-offload bulkhead.
- `config/redis_bootstrap.py:114-129` — `Retry(ExponentialBackoff(cap=10, base=1), 3)`,
  `health_check_interval=30`; **no `max_connections` set** → redis-py `ConnectionPool`
  unbounded — **still holds**; load-bearing for the thread-safety plan.

**Cited sibling issues/PRs re-checked against `06fca807`:**
- #1814 — CLOSED (PR #1824 merged): added the retry/backoff client; explicitly deferred
  this off-loop move as Fix #4. Confirmed in `docs/plans/completed/redis-durability-hardening.md`.
- #1815 — the on-loop tick / dead-man's-switch this plan composes with (merged). Its tick task
  and `session_state.last_loop_tick` beacon are **read/preserved, not modified** by this plan.
- #1816 — worker fault containment (merged `bab446d8`): established the `_reflection_pool`
  bulkhead this plan mirrors. No file overlap with the hot-path query.
- #1818 — OPEN — resilience-cluster umbrella; #1826 is the #1814 Fix #4 child.
- **#1820 — now CLOSED/merged (`72ba5d50`)** and **#1821 — now CLOSED** (landed via #1872
  `b01d7fce`). The plan lists these as "deferred liveness fixes, out of scope." They have
  since landed; this does **not** change this plan's scope — those were separate concerns this
  plan composes with but never touches (see the #1820 composition note above). The No-Gos
  reference to them is retained as a scope boundary, not a claim that they are still pending.

**Commits on main since the issue was filed touching referenced files:** nine, all
resilience-cluster siblings (`46850300`, `6e846f0d`, `b01d7fce`, `72ba5d50`, `d1b73b04`,
`f8eac988`, `b624607b`, `a9616f27`, `bab446d8`). Each was reviewed: they moved line numbers in
`agent/agent_session_queue.py` and `worker/__main__.py` but left the hot-path idle-check
structure, the startup-scan ordering, and the #1815 beacon intact. Premises hold.

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
| enqueue-path `to_thread` wrapping | Offloaded the *write* path's Popoto calls | Never covered the *read* hot path (`:1649-1658`) — the one remaining on-loop, concurrently-running blocking site that starves the tick. |

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
  `REDIS_IO_POOL_MAX_WORKERS`, default **2**, clamped ≥1) — a bulkhead mirroring
  `_reflection_pool` so a slow Redis cannot exhaust the shared asyncio default pool that
  granite probes / `session_executor` also use. A serialized drain-loop awaiter issues one
  offload at a time, so 2 workers cover the realistic overlap of two drain loops idle-checking
  concurrently without over-provisioning (see resolved Open Question 1). The read hot path uses
  ONE instrumented seam (`offload_redis` on `_redis_io_pool`); the plan adds no NEW raw
  `asyncio.to_thread` on that path (which would route calls through the unmeasured shared
  default pool and defeat the metric). The six pre-existing enqueue-path `to_thread` offloads
  are grandfathered, not migrated (out of scope).
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
1. Per-worker drain loop pops a session; if `None`, releases the slot lease.
2. **`event.clear()` FIRST** (the async-safe inversion — see Risk 1). Clearing before the
   Redis query means any producer that enqueues + `event.set()`s *after* this clear is either
   observed by the query below (Redis is source of truth) or leaves the event set so the
   subsequent `event.wait()` returns immediately. There is no window where a set is swallowed.
3. **Idle-check (was on-loop, now off-loop):** `_has_pending = bool(await offload_redis(lambda: list(AgentSession.query.filter(..., status="pending"))))` — runs on `_redis_io_pool`; the loop stays free while Redis is queried.
4. If pending → `continue` (skip the wait). Else → bound the wait in **both** modes:
   `await asyncio.wait_for(event.wait(), DRAIN_TIMEOUT)` — bridge already did this; standalone
   is now bounded too (defensive belt-and-suspenders: on `TimeoutError` the loop re-checks
   Redis rather than parking forever on a bare `await event.wait()`).

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
5. `offload_redis` times the **drain-loop idle-check** call it wraps, feeds a rolling
   time-windowed latency buffer (recent samples, aged out by `REDIS_LATENCY_WINDOW_S`), and
   emits a threshold-gated WARNING; `ui/app.py::dashboard_json` surfaces windowed **p95** and
   windowed **max** (not a never-resetting lifetime high-water mark) under the label
   *drain-loop idle-check latency*. The six pre-existing enqueue-path `asyncio.to_thread`
   offloads and the `_reflection_pool` remain un-instrumented and grandfathered (see Success
   Criteria) — the metric measures the read hot path's bulkhead-isolated seam, not all Redis I/O.

## Architectural Impact

- **New dependencies:** none (stdlib `concurrent.futures.ThreadPoolExecutor`,
  `asyncio.get_running_loop().run_in_executor`).
- **Interface changes:** a new `agent/redis_offload.py` with `offload_redis(fn, *args)`,
  the `_redis_io_pool`, and windowed-latency accessors (`get_redis_latency_p95`,
  `get_redis_latency_max`, `get_last_redis_latency`, `reset_max_redis_latency`). No signature
  changes to existing functions; one call site (the drain-loop idle-check) changes from sync to
  a clear-then-check `await`, and `ui/app.py::dashboard_json` gains a read-only metric block.
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
| #1815 tick present | `grep -c _loop_tick_task worker/__main__.py` | Composition target exists (tick beacon defined + created) |

## Solution

### Key Elements

- **`agent/redis_offload.py`** — a new module holding:
  - `_redis_io_pool = ThreadPoolExecutor(max_workers=REDIS_IO_POOL_MAX_WORKERS,
    thread_name_prefix="redis-io-")` (default **2**, clamped ≥1) — the bulkhead.
  - `async def offload_redis(fn, *args, **kwargs)` — `await loop.run_in_executor(_redis_io_pool, functools.partial(fn, *args, **kwargs))`, timing the call, appending the sample to a rolling time-windowed buffer, and emitting a threshold-gated WARNING. A `REDIS_OFFLOAD_ENABLED` kill switch (default true) makes it a synchronous pass-through for rollback.
  - Windowed-latency accessors (`get_redis_latency_p95()`, `get_redis_latency_max()` over the recent window, `get_last_redis_latency()`, and `reset_max_redis_latency()`) — backed by a `deque` of `(timestamp, dt)` samples pruned to `REDIS_LATENCY_WINDOW_S` (default 300s). The window means a single slow blip ages out instead of latching the dashboard red forever.
- **Hot-path cut-over (the ONLY read-hot-path cut-over)** (`agent_session_queue.py:1649-1668`):
  invert the HEAD ordering to **clear-then-check** — `event.clear()` FIRST, then
  `_has_pending = bool(await offload_redis(lambda: list(AgentSession.query.filter(...))))`,
  then `if _has_pending: continue`, and only fall to a **bounded**
  `await asyncio.wait_for(event.wait(), DRAIN_TIMEOUT)` on an empty post-clear query (both
  standalone and bridge). This is the async-safe replacement for the old sync
  check-before-clear (see Risk 1). No sync fallback left at the site.
- **Startup scans left untouched** (`worker/__main__.py:691,699,772,786,811,827,841,849,931`):
  NOT offloaded. The beacon (`:985`) is created after all of them, so they protect no liveness,
  and their order is load-bearing — see No-Gos.
- **Operator metric**: `ui/app.py::dashboard_json` gains a `redis_offload` block labeled
  *drain-loop idle-check latency* (`p95_latency_s`, `max_latency_s` — both over the rolling
  window — plus `last_latency_s`); a WARNING logs when a single call exceeds
  `REDIS_OFFLOAD_SLOW_THRESHOLD` (default ~1s). The block measures the read hot path only, not
  the grandfathered enqueue-path `to_thread` offloads.
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
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

logger = logging.getLogger(__name__)

# Bulkhead pool for off-loop Redis I/O. Isolated from the shared asyncio default
# pool so a slow Redis cannot starve granite probes / session_executor offloads.
# A serialized drain-loop awaiter issues ONE offload at a time; 2 covers realistic
# two-drain-loop overlap without over-provisioning. Do NOT couple to
# MAX_CONCURRENT_SESSIONS (offloads are per-idle-check, not per-session).
REDIS_IO_POOL_MAX_WORKERS = max(1, int(os.environ.get("REDIS_IO_POOL_MAX_WORKERS", "2")))
REDIS_OFFLOAD_SLOW_THRESHOLD = float(os.environ.get("REDIS_OFFLOAD_SLOW_THRESHOLD", "1.0"))
REDIS_LATENCY_WINDOW_S = float(os.environ.get("REDIS_LATENCY_WINDOW_S", "300"))
REDIS_OFFLOAD_ENABLED = os.environ.get("REDIS_OFFLOAD_ENABLED", "true").strip().lower() \
    not in ("", "0", "false")

_redis_io_pool = ThreadPoolExecutor(
    max_workers=REDIS_IO_POOL_MAX_WORKERS, thread_name_prefix="redis-io-"
)
# Rolling time-windowed latency samples: (monotonic_ts, dt). A slow blip ages out
# of the window instead of latching a lifetime high-water mark red forever.
_samples: deque = deque()
_samples_lock = Lock()
_last_latency: float = 0.0

def _record(dt: float) -> None:
    global _last_latency
    now = time.monotonic()
    with _samples_lock:
        _last_latency = dt
        _samples.append((now, dt))
        cutoff = now - REDIS_LATENCY_WINDOW_S
        while _samples and _samples[0][0] < cutoff:
            _samples.popleft()

def _windowed_sorted():
    cutoff = time.monotonic() - REDIS_LATENCY_WINDOW_S
    with _samples_lock:
        return sorted(dt for ts, dt in _samples if ts >= cutoff)

def get_redis_latency_max() -> float:
    vals = _windowed_sorted()
    return vals[-1] if vals else 0.0

def get_redis_latency_p95() -> float:
    vals = _windowed_sorted()
    if not vals:
        return 0.0
    return vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]

def get_last_redis_latency() -> float:
    return _last_latency

def reset_max_redis_latency() -> None:
    """Operator reset of the windowed max/p95 gauges."""
    with _samples_lock:
        _samples.clear()

async def offload_redis(fn, *args, **kwargs):
    """Run a synchronous Popoto/redis-py callable off the event loop.

    redis-py's ConnectionPool is thread-safe; each offloaded call checks out its
    own connection. When REDIS_OFFLOAD_ENABLED is false, runs inline (rollback).
    """
    call = functools.partial(fn, *args, **kwargs)
    if not REDIS_OFFLOAD_ENABLED:
        return call()  # rollback: on-loop behavior
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    try:
        return await loop.run_in_executor(_redis_io_pool, call)
    finally:
        dt = time.monotonic() - t0
        _record(dt)
        if dt > REDIS_OFFLOAD_SLOW_THRESHOLD:
            logger.warning("[redis-offload] slow Redis call: %.2fs (threshold %.2fs)",
                           dt, REDIS_OFFLOAD_SLOW_THRESHOLD)
```

**2. Hot-path cut-over — clear-then-check (`agent_session_queue.py:1649-1668`):**
Adding an `await` at the idle-check makes the old *check-before-clear* ordering unsafe: a
producer could `enqueue + event.set()` during the in-flight offload, and the later
`event.clear()` would swallow that wakeup, parking a standalone worker on a bare
`await event.wait()`. Invert to **clear-then-check** (Redis is source of truth):
```python
from agent.redis_offload import offload_redis  # module-level import

# ... inside the drain loop, `session is None` branch, after releasing the slot lease:
event.clear()  # CLEAR FIRST — async-safe ordering (see Risk 1)

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
if _has_pending:
    continue  # work is pending — skip the wait

# Empty post-clear query: wait for a signal, BOUNDED in both modes so a
# truly-lost wakeup self-heals on the next iteration instead of parking forever.
try:
    await asyncio.wait_for(event.wait(), timeout=DRAIN_TIMEOUT)
except TimeoutError:
    pass  # fall through, re-pop / re-check at top of loop
```
Why this is safe: because `event.clear()` runs *before* the query, any enqueue that fires
after the clear is either (a) visible to the query → `continue`, or (b) fires after the query
returns empty, leaving the event **set** so `event.wait()` returns immediately. No set is lost.
The `lambda` materializes the lazy `query.filter` result with `list(...)` **inside the thread**
so all Redis I/O happens off-loop; `bool(...)` runs on the loop. Bounding the standalone wait
with `DRAIN_TIMEOUT` (was a bare `await event.wait()`) is a defensive belt-and-suspenders net —
the clear-then-check ordering already prevents the lost wakeup; the timeout guarantees recovery
even against an unforeseen edge. Preserve the existing slot-lease re-acquire before the retry pop.

**3. Startup scans — NO code change (`worker/__main__.py`):** leave `:691` `register_worker_pid()`,
the `:699` verify scan (incl. its `except → sys.exit(1)`), the `:772/786/811/827/841/849`
cleanup/recovery helpers, and the `:931` pending scan exactly as they are — synchronous and
in-order. They run before the beacon is armed (`:985`), so offloading them protects no liveness,
and their order is load-bearing (3a before 3b, `:832-839`). Descoped — see No-Gos.

**4. Metric surface (`ui/app.py::dashboard_json`):** add
`"redis_offload": {"label": "drain-loop idle-check latency", "p95_latency_s": get_redis_latency_p95(), "max_latency_s": get_redis_latency_max(), "last_latency_s": get_last_redis_latency()}`
under `health`. Both `p95`/`max` are over the rolling `REDIS_LATENCY_WINDOW_S` window (a blip
ages out), NOT a lifetime high-water mark. Import the accessors lazily inside `dashboard_json`
(matching the existing lazy-import style there).

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
      (having already cleared) proceeds to the bounded `event.wait()`. Test parity of the
      clear-then-check path with the pre-cut-over drain semantics.

### Error State Rendering
- [ ] A slow call (> `REDIS_OFFLOAD_SLOW_THRESHOLD`) emits the WARNING with the measured
      duration. Test captures the log record.
- [ ] `dashboard_json` renders the `redis_offload` block with numeric windowed latencies
      (`p95_latency_s`, `max_latency_s`, `last_latency_s` — never `None`/`KeyError`) even before
      any call has run (accessors return 0.0 on an empty window). Test the shape + the label.
- [ ] **Windowed decay (finding 2):** record a slow sample, advance the clock past
      `REDIS_LATENCY_WINDOW_S`, record a fast sample, and assert `get_redis_latency_max()` drops
      to the fast value — the max does NOT latch the old high-water mark. Also assert
      `reset_max_redis_latency()` clears the window.

### Lost-Wakeup / Clear-Then-Check Coverage (finding 1)
- [ ] **Enqueue-during-offload does not park (acceptance):** in the drain loop with a slowed
      offload (the offloaded idle-check sleeps), fire `enqueue_agent_session()` + `event.set()`
      *while the offload is in flight*. Assert the loop does NOT park on `event.wait()`: because
      `event.clear()` ran before the query, the enqueue is either seen by the (now-slow) query →
      `continue`, or leaves the event set → `event.wait()` returns at once. The worker must pick
      up the enqueued session, not stall.
- [ ] **Bounded standalone wait:** with no enqueue, assert a standalone drain loop's
      `event.wait()` is wrapped in `asyncio.wait_for(..., DRAIN_TIMEOUT)` and re-checks Redis on
      `TimeoutError` rather than blocking on a bare unbounded `await event.wait()`.

## Test Impact

- [ ] `tests/unit/test_agent_session_queue_async.py` — UPDATE: the drain-loop idle-check is
      now `event.clear()` **then** `await offload_redis(...)` (clear-then-check inversion). Any
      test that patches `AgentSession.query.filter` must still observe the call (it now runs in a
      thread); assert the NEW clear-then-check ordering (clear precedes the query), that a
      pending result still `continue`s, and that the standalone wait is bounded by
      `asyncio.wait_for(..., DRAIN_TIMEOUT)`. If a test asserts the old sync check-before-clear
      ordering, REPLACE it with the clear-then-check offloaded form.
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
      `max_workers` ≥ 1; exception propagation; latency `_record` update in `finally`; slow-call
      WARNING; concurrent calls run on distinct threads (thread-safety smoke); windowed p95/max
      decay past `REDIS_LATENCY_WINDOW_S`; `reset_max_redis_latency()` clears the window.
- [ ] New: `tests/integration/test_slow_redis_no_loop_freeze.py` — CREATE (the acceptance
      criterion): with an artificially slowed Redis (patch the offloaded callable / global
      client to `time.sleep(N)` inside the thread), assert (a) `get_loop_tick()` keeps
      advancing while the slow call is in flight, (b) an unrelated coroutine makes progress,
      (c) no `_self_kill`/SIGABRT is triggered, and (d) an `enqueue_agent_session()` fired
      *during* the in-flight offload does NOT park the drain loop — the clear-then-check ordering
      picks the work up (finding 1).

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
- **Redesigning the wait/notify machinery.** The clear-then-check inversion (Risk 1) is the
  minimal async-safe fix for THIS cut-over — do NOT go further and rebuild the notify-listener
  or event/lease protocol. Apply clear-then-check + the bounded `wait_for` exactly as specified;
  a broader wait/notify redesign is out of scope.

## Risks

### Risk 1: An `await` at the idle-check makes the HEAD *check-before-clear* ordering lose wakeups
**Impact:** With the old sync check-before-`event.clear()`, adding `await offload_redis(...)`
at the check opens a real yield point: a producer can `enqueue + event.set()` during the
in-flight offload, then the subsequent `event.clear()` swallows the wakeup and a standalone
worker parks forever on a bare `await event.wait()`. This is a NEW lost-wakeup hole, not merely
a widened pre-existing one — the async form demands a different ordering.
**Mitigation (clear-then-check):** Invert to `event.clear()` FIRST, then the offloaded query,
then `if _has_pending: continue`. Because the clear precedes the query and **Redis is the
source of truth**, any enqueue after the clear is either seen by the query (`continue`) or
leaves the event set so `event.wait()` returns immediately — no set is lost. This also still
covers the original "notify fired during `_pop_agent_session`" case (the query re-observes the
pending row). Defensively, bound the standalone wait with `asyncio.wait_for(event.wait(),
DRAIN_TIMEOUT)` so even an unforeseen edge self-heals on the next iteration instead of parking.
The pubsub notify listener (`agent_session_queue.py:1085`) re-signals on new work. A slow-Redis
test fires an enqueue during the in-flight offload and asserts the loop does not park.

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
**Location:** `agent_session_queue.py` drain loop, `:1637-1691`.
**Trigger:** `enqueue_agent_session()` fires `event.set()` while the offloaded idle-check is
in flight (during the `await`) or just after it returns empty.
**Data prerequisite:** The idle-check reads `status="pending"` for `worker_key`.
**State prerequisite:** `event.clear()` must run BEFORE the offloaded query (clear-then-check).
**Mitigation:** With clear-then-check, the clear happens first, so a concurrent `event.set()`
is never swallowed: the query (Redis = source of truth) either observes the newly-pending row
→ `continue`, or the set survives to make the bounded `event.wait()` return immediately. The
`DRAIN_TIMEOUT`-bounded wait (now both modes) and the pubsub notify listener are additional
recovery nets. The offload's `await` no longer sits inside a check-before-clear window (Risk 1).

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
- **[SEPARATE #1820/#1821] The liveness fixes** (lease semaphore, progress-deadline
  cancel scope, out-of-domain recovery) — now merged separately. This plan composes with #1815
  and with #1820's lease registry (slot released before the idle-check, so the added `await`
  holds no lease) but does not extend or modify either.

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
      section describing the `_redis_io_pool` bulkhead, the `offload_redis` seam for the read
      hot path (the one drain-loop idle-check cut-over site, with clear-then-check ordering), the
      windowed p95/max *drain-loop idle-check latency* metric, why the six enqueue-path
      `to_thread` offloads are grandfathered and the startup scans deliberately left on-loop
      (beacon unarmed at boot), and the executor-vs-async decision. (Per the no-historical-artifacts
      rule: describe the new status quo, not "was deferred, now done".)
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
- [ ] Comment at the hot-path cut-over that `event.clear()` MUST run BEFORE the offloaded query
      (clear-then-check) — the async `await` makes the reverse ordering lose wakeups (Risk 1).

## Success Criteria

- [ ] The hot-path drain query (`agent_session_queue.py:1649-1668`) executes off the event
      loop via `offload_redis` using **clear-then-check** ordering (`event.clear()` before the
      offloaded query); no synchronous `AgentSession.query.filter(...)` remains on the loop at
      that site. This is the ONLY read-hot-path offload site.
- [ ] The standalone drain wait is bounded by `asyncio.wait_for(event.wait(), DRAIN_TIMEOUT)`
      (no bare unbounded `await event.wait()`); an enqueue fired during the in-flight offload
      does not park the loop (finding-1 acceptance test passes).
- [ ] The startup scans (`worker/__main__.py:691,699,772,786,811,827,841,849,931`) are
      **unchanged** — still synchronous, on-loop, and in-order (incl. 3a-before-3b and the
      verify-scan `sys.exit(1)`). Descoped: the beacon is unarmed at boot, so there is no
      liveness to protect. Startup tests pass unchanged as a regression guard.
- [ ] No NEW raw `asyncio.to_thread` is introduced on the read hot path — the drain-loop
      idle-check routes through the instrumented `offload_redis` seam on the isolated
      `_redis_io_pool` bulkhead. (The six pre-existing enqueue-path `to_thread` offloads at
      `agent_session_queue.py:326,368,380,409,781,1085` and the `_reflection_pool` are
      **grandfathered** — out of scope, deliberately un-instrumented. The operator-visible
      latency criterion is satisfied for the read hot path, which is the site this plan moves
      off-loop; the metric is bulkhead-isolated latency for that seam, not all Redis I/O.)
- [ ] Under an artificially slowed Redis: `get_loop_tick()` keeps advancing, an unrelated
      coroutine makes progress, and no `_self_kill`/SIGABRT fires (acceptance test passes).
- [ ] The #1815 `_loop_tick_task` and `last_loop_tick` beacon are unchanged (on-loop).
- [ ] Drain-loop idle-check latency is operator-visible: `/dashboard.json` exposes a
      `redis_offload` block labeled *drain-loop idle-check latency* with **windowed** `p95` and
      `max` (a blip ages out of `REDIS_LATENCY_WINDOW_S` rather than latching red forever;
      `reset_max_redis_latency()` clears it), and a WARNING logs on calls exceeding
      `REDIS_OFFLOAD_SLOW_THRESHOLD`.
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
  - Role: `agent/redis_offload.py` + hot-path cut-over (`agent_session_queue.py:1649-1658`) + metric gauges
  - Agent Type: async-specialist
  - Resume: true

- **Builder (dashboard-metric)**
  - Name: metric-builder
  - Role: `ui/app.py::dashboard_json` `redis_offload` metric block (lazy-import the gauge accessors). Does NOT touch `worker/__main__.py` startup scans (descoped).
  - Agent Type: async-specialist
  - Resume: true

- **Validator (loop-freeze)**
  - Name: freeze-validator
  - Role: verify the clear-then-check off-loop cut-over + bounded wait (finding 1), no-NEW-raw-`to_thread`-on-read-hot-path invariant, windowed p95/max metric (finding 2), slow-Redis acceptance test, #1815 tick composition, and that startup scans are unchanged
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
- Create `agent/redis_offload.py`: `_redis_io_pool` (`max_workers=2`, clamped ≥1),
  `offload_redis` (pass-through when disabled; rolling-window latency `_record` in `finally`;
  slow-call WARNING), windowed accessors (`get_redis_latency_p95`, `get_redis_latency_max`,
  `get_last_redis_latency`, `reset_max_redis_latency`).
- Cut over `agent_session_queue.py:1649-1668` to **clear-then-check**: `event.clear()` FIRST,
  then `_has_pending = bool(await offload_redis(lambda: list(...)))`, then `if _has_pending: continue`,
  then a bounded `await asyncio.wait_for(event.wait(), DRAIN_TIMEOUT)` in BOTH modes (no bare
  `await event.wait()`). Leave NO sync fallback at the site.

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
  no SIGABRT, and an enqueue fired during the in-flight offload does not park the loop
  (clear-then-check, finding 1). Confirm the read hot path routes through `offload_redis` with
  clear-then-check ordering and a bounded standalone wait, and that no NEW raw `to_thread` was
  added on that path (the six grandfathered enqueue-path offloads are unchanged). Confirm the
  windowed p95/max metric (finding 2) and its *drain-loop idle-check* label (finding 3). Confirm
  the startup scans are unchanged from HEAD (still synchronous, in-order, `sys.exit(1)` intact).
  Confirm #1815 tick untouched.

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
| Pool default is 2 (finding 4) | `python3 -c "import re; print(1 if re.search(r'REDIS_IO_POOL_MAX_WORKERS\", \"2\"', open('agent/redis_offload.py').read()) else 0)"` | output `1` |
| Not coupled to session concurrency (finding 4) | `grep -c "MAX_CONCURRENT_SESSIONS" agent/redis_offload.py` | match count == 0 |
| Windowed p95 accessor present (finding 2) | `grep -c "def get_redis_latency_p95" agent/redis_offload.py` | output == 1 |
| Windowed max + reset present (finding 2) | `grep -c "def reset_max_redis_latency\|def get_redis_latency_max" agent/redis_offload.py` | output == 2 |
| No lifetime high-water gauge (finding 2) | `grep -c "get_max_redis_latency" agent/redis_offload.py ui/app.py` | match count == 0 |
| Dashboard exposes windowed p95 (finding 2) | `grep -c "p95_latency_s" ui/app.py` | output > 0 |
| Dashboard metric labeled idle-check (finding 3) | `grep -c "drain-loop idle-check" ui/app.py` | output > 0 |
| Standalone wait is bounded (finding 1) | `grep -c "wait_for(event.wait()" agent/agent_session_queue.py` | output ≥ 1 |
| No bare unbounded standalone wait (finding 1) | `python3 -c "import re; s=open('agent/agent_session_queue.py').read(); print(s.count('await event.wait()'))"` | output `0` (standalone wait now bounded by wait_for) |
| Startup ordering unchanged | `grep -n "Step 3a\|Step 3b" worker/__main__.py` | 3a line precedes 3b line (unchanged from HEAD) |

## Open Questions

1. **Executor pool sizing — RESOLVED (`max_workers=2`, decoupled).** A serialized drain-loop
   awaiter issues exactly one offload at a time, so a single drain loop needs ≈1 concurrent
   offload; 2 covers the realistic overlap of two drain loops idle-checking at once without
   over-provisioning. Ships **2** (was 4). Explicitly **not** coupled to
   `MAX_CONCURRENT_SESSIONS` — offloads are per-idle-check, not per-session, so scaling with
   session concurrency would over-size the bulkhead. Env-tunable via `REDIS_IO_POOL_MAX_WORKERS`
   if the windowed p95 shows queueing.
2. **Slow-call threshold.** `REDIS_OFFLOAD_SLOW_THRESHOLD=1.0s` for the WARNING — right level,
   or align to `socket_timeout` (5s) so it only fires near the retry ceiling? Plan ships 1s to
   surface early degradation.
3. **`register_worker_pid` offload — RESOLVED (dropped).** It is a single startup write, not a
   scan, and runs before the beacon is armed. Offloading it was scope creep with no liveness
   benefit; it stays on-loop and unchanged, consistent with the startup-scan descope.
4. **Offload mechanism — RESOLVED (one NEW seam on the read hot path).** The plan adds exactly
   ONE new offload seam — `offload_redis` on the instrumented, dedicated `_redis_io_pool` — for
   the read hot path (the drain-loop idle-check). The earlier draft's split (some sites via
   `offload_redis`, some via raw `asyncio.to_thread`) is removed. The six pre-existing
   enqueue-path `to_thread` offloads (`:326,368,380,409,781,1085`) and `_reflection_pool` are
   grandfathered and left as-is (out of scope), so the metric measures the read-hot-path seam's
   bulkhead-isolated latency, not literally all Redis I/O in the process. No open question remains.
