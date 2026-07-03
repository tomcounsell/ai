"""Off-loop execution seam for synchronous Popoto/redis-py calls.

Popoto (the redis-py-based ORM used throughout this repo) is entirely
synchronous. Calling it directly from an `async def` on the event loop blocks
the whole loop for the call's duration. Every session, every monitor, and the
#1815 dead-man's-switch liveness tick all freeze in lockstep. This module
gives the ONE hot-path call site (the drain-loop idle-check in
`agent/agent_session_queue.py`) a way to run that blocking call on a bounded,
isolated worker-thread pool instead, so a slow or restarting Redis degrades
that call's latency without wedging the loop.

See `docs/plans/hot-path-redis-off-loop.md` for the full design (issue #1826).
"""

import asyncio
import functools
import logging
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

logger = logging.getLogger(__name__)

# --- Configuration (env-tunable; all defaults are provisional, tune after
# observing real drain-loop idle-check latency in production) ---

# Number of worker threads in the off-loop Redis I/O bulkhead. A serialized
# drain-loop awaiter issues ONE offload at a time, so a single drain loop
# needs about 1 concurrent offload; 2 covers the realistic overlap of two
# drain loops idle-checking concurrently without over-provisioning. Clamped
# to max(1, ...) so a 0 or misconfigured value never creates a zero-worker
# pool that would deadlock every offloaded call. Deliberately not coupled to
# any session-concurrency setting: offloads happen per-idle-check, not
# per-session, so scaling this pool with session concurrency would over-size
# the bulkhead for no benefit.
REDIS_IO_POOL_MAX_WORKERS = max(1, int(os.environ.get("REDIS_IO_POOL_MAX_WORKERS", "2")))

# Log a WARNING when a single offloaded call takes longer than this, in
# seconds. An early signal that Redis is degrading before it's severe enough
# to threaten the #1815 liveness tick.
REDIS_OFFLOAD_SLOW_THRESHOLD = float(os.environ.get("REDIS_OFFLOAD_SLOW_THRESHOLD", "1.0"))

# Rolling window (seconds) over which the latency gauges (p95/max) are
# computed. A single slow blip ages out of the window instead of latching a
# lifetime high-water mark red on the dashboard forever.
REDIS_LATENCY_WINDOW_S = float(os.environ.get("REDIS_LATENCY_WINDOW_S", "300"))

# Kill switch: when false, `offload_redis` runs the wrapped callable inline
# on the event loop (the pre-cut-over behavior) instead of dispatching it to
# the thread pool. This is a complete rollback for the sole cut-over site
# this module serves; see `offload_redis`'s docstring.
REDIS_OFFLOAD_ENABLED = os.environ.get("REDIS_OFFLOAD_ENABLED", "true").strip().lower() not in (
    "",
    "0",
    "false",
)

# Bulkhead pool for off-loop Redis I/O. Isolated from the shared asyncio
# default executor so a slow/restarting Redis cannot starve unrelated
# offloads (granite probes, session_executor's `run_in_executor` calls, etc.)
# that also rely on that shared default pool.
#
# Invariant: this pool's worker count, plus `REFLECTION_POOL_WORKERS`
# (agent/reflection_scheduler.py's bulkhead, which this pool mirrors), plus
# the shared default pool's peak concurrent usage, must stay at or below the
# redis-py `ConnectionPool` capacity built by `configure_resilient_redis()`
# (config/redis_bootstrap.py). That pool is unbounded today (no
# `max_connections` set), so any small worker count here is safe by
# construction. If `max_connections` is ever introduced, it must be sized to
# cover all three, or offloaded calls will block on `BlockingConnectionPool`
# checkout, reintroducing the very stall this module exists to remove.
_redis_io_pool = ThreadPoolExecutor(
    max_workers=REDIS_IO_POOL_MAX_WORKERS, thread_name_prefix="redis-io-"
)

# Rolling time-windowed latency samples: (monotonic_ts, dt). Pruned to
# REDIS_LATENCY_WINDOW_S on every write so `get_redis_latency_p95()` and
# `get_redis_latency_max()` reflect only recent behavior, never a
# never-resetting lifetime high-water mark.
_samples: deque = deque()
_samples_lock = Lock()
_last_latency: float = 0.0


def _record(dt: float) -> None:
    """Append a latency sample and prune anything older than the window."""
    global _last_latency
    now = time.monotonic()
    with _samples_lock:
        _last_latency = dt
        _samples.append((now, dt))
        cutoff = now - REDIS_LATENCY_WINDOW_S
        while _samples and _samples[0][0] < cutoff:
            _samples.popleft()


def _windowed_sorted() -> list[float]:
    """Return sorted latency values for samples still inside the window."""
    cutoff = time.monotonic() - REDIS_LATENCY_WINDOW_S
    with _samples_lock:
        return sorted(dt for ts, dt in _samples if ts >= cutoff)


def get_redis_latency_max() -> float:
    """Max offload latency observed within the current rolling window."""
    vals = _windowed_sorted()
    return vals[-1] if vals else 0.0


def get_redis_latency_p95() -> float:
    """95th-percentile offload latency within the current rolling window."""
    vals = _windowed_sorted()
    if not vals:
        return 0.0
    return vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]


def get_last_redis_latency() -> float:
    """Latency of the most recently completed offloaded call."""
    return _last_latency


def reset_max_redis_latency() -> None:
    """Operator reset of the windowed latency gauges (clears all samples)."""
    with _samples_lock:
        _samples.clear()


async def offload_redis(fn, *args, **kwargs):
    """Run a synchronous Popoto/redis-py callable off the event loop.

    Thread-safety contract: the global `POPOTO_REDIS_DB` client rebuilt by
    `configure_resilient_redis()` (config/redis_bootstrap.py) uses redis-py's
    default `ConnectionPool`, which is thread-safe and, by default, unbounded.
    Each call dispatched here checks out its own connection from that pool;
    there is no shared mutable client state for concurrent offloaded calls to
    corrupt. This mirrors the pattern already used by this repo's enqueue-path
    `asyncio.to_thread` offloads (agent/agent_session_queue.py).

    Rollback: when `REDIS_OFFLOAD_ENABLED` is false, `fn` runs inline on the
    caller's event loop (the pre-cut-over, fully synchronous behavior) instead
    of being dispatched to the thread pool. That is a complete, instant
    revert for the sole read-hot-path site this module serves.

    Any exception raised by `fn` propagates unchanged to the caller. Latency
    is still recorded for the attempt (see the `finally` block below) so a
    failing call doesn't silently disappear from the operator-visible metric.
    """
    call = functools.partial(fn, *args, **kwargs)
    if not REDIS_OFFLOAD_ENABLED:
        return call()  # rollback: synchronous, on-loop pass-through

    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    try:
        return await loop.run_in_executor(_redis_io_pool, call)
    finally:
        dt = time.monotonic() - t0
        _record(dt)
        if dt > REDIS_OFFLOAD_SLOW_THRESHOLD:
            logger.warning(
                "[redis-offload] slow Redis call: %.2fs (threshold %.2fs)",
                dt,
                REDIS_OFFLOAD_SLOW_THRESHOLD,
            )
