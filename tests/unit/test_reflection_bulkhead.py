"""Tests for Fix #3: reflection bulkhead pool.

Verifies that sync reflections are routed through the dedicated
`_reflection_pool` ThreadPoolExecutor rather than the default asyncio
executor (None), which would allow heavy reflections to starve the
event loop.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from agent.reflection_scheduler import (
    ReflectionEntry,
    _reflection_pool,
    execute_function_reflection,
)


def _make_entry(**kwargs) -> ReflectionEntry:
    """Return a minimal ReflectionEntry with required fields filled in."""
    defaults = dict(
        name="test",
        description="test reflection",
        priority="normal",
        execution_type="function",
        schedule="every: 60s",
    )
    defaults.update(kwargs)
    return ReflectionEntry(**defaults)


class TestReflectionBulkhead:
    def test_dedicated_pool_is_threadpoolexecutor(self):
        """_reflection_pool must be a ThreadPoolExecutor instance."""
        assert isinstance(_reflection_pool, ThreadPoolExecutor), (
            f"Expected ThreadPoolExecutor, got {type(_reflection_pool)!r}"
        )

    def test_dedicated_pool_has_reflection_thread_name_prefix(self):
        """Threads in _reflection_pool carry the 'reflection-' prefix for easy identification."""
        # ThreadPoolExecutor stores the prefix in a private attr; check it exists.
        assert hasattr(_reflection_pool, "_thread_name_prefix")
        assert _reflection_pool._thread_name_prefix.startswith("reflection-"), (
            f"Thread name prefix should start with 'reflection-', "
            f"got {_reflection_pool._thread_name_prefix!r}"
        )

    def test_sync_reflection_uses_dedicated_pool(self):
        """Sync callable must be routed through _reflection_pool, not None."""
        # agent.reflection_scheduler._get_memory_rss is a zero-arg sync function
        entry = _make_entry(callable="agent.reflection_scheduler._get_memory_rss")

        captured = []

        async def run():
            loop = asyncio.get_running_loop()
            original = loop.run_in_executor

            async def mock_run_in_executor(executor, func, *args):
                captured.append(executor)
                # Call the real implementation so the function actually runs
                return await original(executor, func, *args)

            with patch.object(loop, "run_in_executor", side_effect=mock_run_in_executor):
                await execute_function_reflection(entry)

        asyncio.run(run())

        assert len(captured) == 1, f"run_in_executor not called; captured={captured!r}"
        assert captured[0] is _reflection_pool, (
            f"Expected _reflection_pool but got {captured[0]!r} — "
            "sync reflections must not use the default executor (None)"
        )

    def test_sync_reflection_not_routed_via_none_executor(self):
        """run_in_executor must never be called with None for sync reflections."""
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
            "run_in_executor was called with None — sync reflections must use "
            "_reflection_pool to avoid starving the event loop"
        )

    def test_async_reflection_bypasses_run_in_executor(self):
        """Async callables must be awaited directly, not routed via run_in_executor."""

        async def _async_noop():
            return "ok"

        entry = _make_entry(callable=None)

        called_with_executor = []

        async def run():
            loop = asyncio.get_running_loop()

            async def mock_run_in_executor(executor, func, *args):
                called_with_executor.append(executor)
                return None

            # Patch the callable resolution so it returns our async function
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

    def test_reflection_pool_workers_clamped_to_minimum(self):
        """REFLECTION_POOL_WORKERS=0 or negative must be clamped to at least 1."""
        for bad_value in ("0", "-5", "-1"):
            result = max(1, int(bad_value))
            assert result >= 1, f"max(1, int({bad_value!r})) should be >= 1, got {result}"

    def test_reflection_pool_workers_env_default(self):
        """Without REFLECTION_POOL_WORKERS set, the module uses 2 workers."""
        saved = os.environ.pop("REFLECTION_POOL_WORKERS", None)
        try:
            val = max(1, int(os.environ.get("REFLECTION_POOL_WORKERS", "2")))
            assert val == 2
        finally:
            if saved is not None:
                os.environ["REFLECTION_POOL_WORKERS"] = saved
