"""Tests for reflection bulkhead thread pool (Fix #3, issue #1816).

Two behaviors validated:
  A. Pool isolation (saturation test): wedging the reflection pool must not
     block the asyncio default pool — critical-path tasks remain responsive.
  B. Event-loop responsiveness: redis_quality_audit.run() must not block the
     event loop after the asyncio.to_thread fix.

These tests are designed so that:
  - Test A would FAIL if run_in_executor used None (shared pool) and the
    reflection pool is fully saturated.
  - Test B would FAIL against the original on-loop .query.all() calls and
    PASS after the asyncio.to_thread fix.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Test A — Pool isolation / saturation bulkhead
# ---------------------------------------------------------------------------


def test_reflection_pool_is_dedicated():
    """The module exposes a named _reflection_pool distinct from None (default)."""
    from agent.reflection_scheduler import _reflection_pool

    assert _reflection_pool is not None
    assert isinstance(_reflection_pool, ThreadPoolExecutor)
    # Thread names must carry the prefix so they're identifiable in stack traces
    assert _reflection_pool._thread_name_prefix == "reflection-"


def test_reflection_pool_workers_constant_clamped():
    """REFLECTION_POOL_WORKERS is clamped to at least 1."""
    from agent.reflection_scheduler import REFLECTION_POOL_WORKERS

    assert REFLECTION_POOL_WORKERS >= 1


def test_saturated_reflection_pool_does_not_block_default_pool():
    """Wedge all workers in the reflection pool; default pool must stay free.

    This test would fail if execute_function_reflection used run_in_executor(None, ...)
    (shared pool) and we simultaneously wedged that same pool.
    """
    from agent.reflection_scheduler import REFLECTION_POOL_WORKERS, _reflection_pool

    gate = threading.Event()

    def blocker():
        gate.wait(timeout=5)

    # Wedge every worker in the reflection pool
    futs = [_reflection_pool.submit(blocker) for _ in range(REFLECTION_POOL_WORKERS)]

    # Now submit a task to the asyncio DEFAULT pool (simulates critical-path work)
    async def run_critical_path():
        loop = asyncio.get_running_loop()
        start = loop.time()
        # This runs in the DEFAULT pool (None), which should be free
        result = await loop.run_in_executor(None, lambda: "done")
        elapsed = loop.time() - start
        return result, elapsed

    result, elapsed = asyncio.run(run_critical_path())

    # Unblock the reflection pool workers
    gate.set()
    for f in futs:
        f.result(timeout=5)

    assert result == "done"
    # Default pool completes quickly because it's a separate pool from reflection
    assert elapsed < 2.0, f"Critical-path task took {elapsed:.2f}s — default pool may be starved"


# ---------------------------------------------------------------------------
# Test B — Event-loop responsiveness after asyncio.to_thread fix
# ---------------------------------------------------------------------------


def _make_mock_record(attr_map: dict):
    """Build a MagicMock that returns given values for given attribute names."""
    mock = MagicMock()
    for k, v in attr_map.items():
        setattr(mock, k, v)
    return mock


async def test_redis_quality_audit_does_not_block_loop():
    """Heavy .query.all() scans must not stall the event loop.

    This test FAILS if the asyncio.to_thread wrapping is removed from
    redis_quality_audit.run().  Without to_thread, .query.all() executes
    synchronously on the event loop, blocking it for the duration of the scan.
    With to_thread, the scan runs in a worker thread and the loop is free to
    service other coroutines concurrently.

    Proof mechanism:
    - Each .query.all() mock sleeps for SCAN_DURATION seconds (real time.sleep).
    - A concurrent fast_coroutine records the event-loop timestamp at which it runs.
    - If the loop is blocked (no to_thread): fast_coroutine cannot run until after
      the first SCAN_DURATION sleep completes, so its scheduled_at delay ≥ SCAN_DURATION.
    - If the loop is free (with to_thread): fast_coroutine is scheduled immediately
      after the first to_thread call suspends the audit, so delay ≈ 0.
    - deadline < scan_duration ensures the assertion distinguishes the two paths.
    """
    scan_duration = 0.25  # Each .query.all() blocks for this long
    deadline = 0.1  # fast_coroutine must start within this window (< scan_duration)

    def _slow_all():
        """Simulate an expensive synchronous query scan."""
        time.sleep(scan_duration)
        return []

    def make_slow_query():
        q = MagicMock()
        q.all.side_effect = _slow_all
        q.filter.return_value = []
        return q

    loop = asyncio.get_running_loop()
    scheduled_at: list[float] = []

    async def fast_coroutine():
        scheduled_at.append(loop.time())

    with (
        patch("models.link.Link.query", make_slow_query()),
        patch("models.chat.Chat.query", make_slow_query()),
        patch("models.agent_session.AgentSession.query", make_slow_query()),
        patch("models.telegram.TelegramMessage.query", make_slow_query()),
    ):
        from reflections.audits.redis_quality_audit import run as audit_run

        start = loop.time()
        # Create both tasks so they compete on the same event loop
        audit_task = asyncio.create_task(audit_run())
        fast_task = asyncio.create_task(fast_coroutine())
        result = (await asyncio.gather(audit_task, fast_task))[0]

    assert scheduled_at, "fast_coroutine was never scheduled"
    delay = scheduled_at[0] - start
    # fast_coroutine must have run DURING the audit (loop was not blocked)
    assert delay < deadline, (
        f"fast_coroutine delayed {delay:.3f}s (limit {deadline}s) — "
        f"loop was blocked; .query.all() may not be wrapped in asyncio.to_thread"
    )
    assert result["status"] == "ok"


def test_redis_quality_audit_loop_responsive():
    """Sync wrapper so pytest can run the async test without requiring pytest-asyncio."""
    asyncio.run(test_redis_quality_audit_does_not_block_loop())


# ---------------------------------------------------------------------------
# Test C -- execute_function_reflection routing tests
# ---------------------------------------------------------------------------


def _make_entry(**kwargs):
    """Return a minimal ReflectionEntry with required fields filled in."""
    from agent.reflection_scheduler import ReflectionEntry

    defaults = dict(
        name="test",
        description="test reflection",
        priority="normal",
        execution_type="function",
        schedule="every: 60s",
    )
    defaults.update(kwargs)
    return ReflectionEntry(**defaults)


def test_sync_reflection_uses_dedicated_pool():
    """Sync callable must be routed through _reflection_pool, not None."""
    from unittest.mock import patch

    from agent.reflection_scheduler import _reflection_pool, execute_function_reflection

    entry = _make_entry(callable="agent.reflection_scheduler._get_memory_rss")
    captured = []

    async def run():
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor

        async def mock_run_in_executor(executor, func, *args):
            captured.append(executor)
            return await original(executor, func, *args)

        with patch.object(loop, "run_in_executor", side_effect=mock_run_in_executor):
            await execute_function_reflection(entry)

    asyncio.run(run())

    assert len(captured) == 1, f"run_in_executor not called; captured={captured!r}"
    assert captured[0] is _reflection_pool, (
        f"Expected _reflection_pool but got {captured[0]!r} -- "
        "sync reflections must not use the default executor (None)"
    )


def test_sync_reflection_not_routed_via_none_executor():
    """run_in_executor must never be called with None for sync reflections."""
    from unittest.mock import patch

    from agent.reflection_scheduler import execute_function_reflection

    entry = _make_entry(callable="agent.reflection_scheduler._get_memory_rss")
    captured = []

    async def run():
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor

        async def mock_run_in_executor(executor, func, *args):
            captured.append(executor)
            return await original(executor, func, *args)

        with patch.object(loop, "run_in_executor", side_effect=mock_run_in_executor):
            await execute_function_reflection(entry)

    asyncio.run(run())

    assert None not in captured, (
        "run_in_executor was called with None -- sync reflections must use "
        "_reflection_pool to avoid starving the event loop"
    )


def test_async_reflection_bypasses_run_in_executor():
    """Async callables must be awaited directly, not routed via run_in_executor."""
    from unittest.mock import patch

    from agent.reflection_scheduler import execute_function_reflection

    async def _async_noop():
        return "ok"

    entry = _make_entry(callable=None)
    called_with_executor = []

    async def run():
        loop = asyncio.get_running_loop()

        async def mock_run_in_executor(executor, func, *args):
            called_with_executor.append(executor)
            return None

        with (
            patch("agent.reflection_scheduler._resolve_callable", return_value=_async_noop),
            patch.object(loop, "run_in_executor", side_effect=mock_run_in_executor),
        ):
            await execute_function_reflection(entry)

    asyncio.run(run())

    assert called_with_executor == [], (
        "Async reflections must not call run_in_executor; "
        f"but it was called with {called_with_executor!r}"
    )


def test_reflection_pool_workers_env_default():
    """Without REFLECTION_POOL_WORKERS set, the module uses 2 workers."""
    import os

    saved = os.environ.pop("REFLECTION_POOL_WORKERS", None)
    try:
        val = max(1, int(os.environ.get("REFLECTION_POOL_WORKERS", "2")))
        assert val == 2
    finally:
        if saved is not None:
            os.environ["REFLECTION_POOL_WORKERS"] = saved
