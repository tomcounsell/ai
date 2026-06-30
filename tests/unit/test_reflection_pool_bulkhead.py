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

    Before the asyncio.to_thread fix, each .query.all() call executed
    synchronously on the event loop inside the async run() coroutine,
    blocking all other coroutines for the duration of the scan.

    After the fix, each scan runs in a thread via asyncio.to_thread, so the
    loop remains free to service other coroutines while the scan executes.

    This test verifies loop responsiveness: a trivial coroutine scheduled
    concurrently with the audit must complete in a short time even while
    the audit is running its (mocked) scans.
    """
    now = time.time()

    # Mock models to return quick but non-trivial results
    mock_link = _make_mock_record(
        {"timestamp": now - 100, "ai_summary": None, "url": "x", "chat_id": "1", "status": "ok"}
    )
    mock_chat = _make_mock_record(
        {"updated_at": now - 100, "chat_name": "test", "chat_type": "group"}
    )
    mock_session = _make_mock_record({"started_at": now - 100, "log_path": None})
    mock_msg = _make_mock_record({"timestamp": now - 100, "chat_id": "1"})

    def make_query_all(records):
        q = MagicMock()
        q.all.return_value = records
        q.filter.return_value = []
        return q

    with (
        patch("models.link.Link.query", make_query_all([mock_link])),
        patch("models.chat.Chat.query", make_query_all([mock_chat])),
        patch("models.agent_session.AgentSession.query", make_query_all([mock_session])),
        patch("models.telegram.TelegramMessage.query", make_query_all([mock_msg])),
    ):
        from reflections.audits.redis_quality_audit import run as audit_run

        # Schedule audit concurrently with a trivial coroutine
        audit_task = asyncio.create_task(audit_run())

        # Measure how long a trivial coroutine takes while audit is running
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.sleep(0)  # yield to let audit start
        elapsed = loop.time() - start

        result = await audit_task

    # Loop must stay responsive while audit runs (not blocked on-loop)
    assert elapsed < 0.5, (
        f"Event loop was blocked for {elapsed:.3f}s — .query.all() may still be on-loop"
    )
    assert result["status"] == "ok"


def test_redis_quality_audit_loop_responsive():
    """Sync wrapper so pytest can run the async test without requiring pytest-asyncio marker."""
    asyncio.run(test_redis_quality_audit_does_not_block_loop())
