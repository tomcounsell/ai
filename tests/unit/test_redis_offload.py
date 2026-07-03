"""Tests for agent/redis_offload.py (issue #1826).

Covers the off-loop Redis execution seam: the REDIS_OFFLOAD_ENABLED
pass-through/rollback path, the REDIS_IO_POOL_MAX_WORKERS clamp, exception
propagation, latency recording (including on exceptions), the slow-call
WARNING, thread-safety (concurrent calls land on distinct threads), and the
rolling time-windowed p95/max decay (a blip must age out, not latch a
lifetime high-water mark).
"""

import asyncio
import importlib
import logging
import threading
import time

import pytest

import agent.redis_offload as redis_offload


@pytest.fixture(autouse=True)
def _reset_window():
    """Clear the rolling latency window before and after each test."""
    redis_offload.reset_max_redis_latency()
    yield
    redis_offload.reset_max_redis_latency()


class TestPassThroughRollback:
    """REDIS_OFFLOAD_ENABLED=false must run the callable inline (no thread)."""

    def test_pass_through_when_disabled(self, monkeypatch):
        monkeypatch.setattr(redis_offload, "REDIS_OFFLOAD_ENABLED", False)

        calling_thread = {}

        def fn(x, y):
            calling_thread["name"] = threading.current_thread().name
            return x + y

        result = asyncio.run(redis_offload.offload_redis(fn, 2, 3))

        assert result == 5
        # The pass-through path must run inline on the caller's thread, not
        # dispatch to the redis-io- thread pool.
        assert not calling_thread["name"].startswith("redis-io-")

    def test_dispatches_to_pool_when_enabled(self, monkeypatch):
        monkeypatch.setattr(redis_offload, "REDIS_OFFLOAD_ENABLED", True)

        calling_thread = {}

        def fn():
            calling_thread["name"] = threading.current_thread().name
            return "ok"

        result = asyncio.run(redis_offload.offload_redis(fn))

        assert result == "ok"
        assert calling_thread["name"].startswith("redis-io-")


class TestPoolMaxWorkersClamp:
    """REDIS_IO_POOL_MAX_WORKERS must clamp to >= 1 even when misconfigured."""

    def test_zero_clamps_to_one(self, monkeypatch):
        monkeypatch.setenv("REDIS_IO_POOL_MAX_WORKERS", "0")
        reloaded = importlib.reload(redis_offload)
        try:
            assert reloaded.REDIS_IO_POOL_MAX_WORKERS == 1
            assert reloaded._redis_io_pool._max_workers == 1
            # A single-worker pool must not deadlock: two sequential offloads
            # both complete.
            assert asyncio.run(reloaded.offload_redis(lambda: 1)) == 1
            assert asyncio.run(reloaded.offload_redis(lambda: 2)) == 2
        finally:
            reloaded._redis_io_pool.shutdown(wait=True)
            monkeypatch.delenv("REDIS_IO_POOL_MAX_WORKERS", raising=False)
            importlib.reload(redis_offload)

    def test_default_is_two(self, monkeypatch):
        monkeypatch.delenv("REDIS_IO_POOL_MAX_WORKERS", raising=False)
        reloaded = importlib.reload(redis_offload)
        try:
            assert reloaded.REDIS_IO_POOL_MAX_WORKERS == 2
        finally:
            reloaded._redis_io_pool.shutdown(wait=True)
            importlib.reload(redis_offload)


class TestExceptionPropagation:
    """offload_redis must propagate the wrapped callable's exception."""

    def test_exception_propagates(self):
        def raising_fn():
            raise ValueError("redis connection error")

        with pytest.raises(ValueError, match="redis connection error"):
            asyncio.run(redis_offload.offload_redis(raising_fn))

    def test_latency_recorded_in_finally_when_raises(self):
        def raising_fn():
            time.sleep(0.02)
            raise RuntimeError("boom")

        before = len(redis_offload._samples)
        with pytest.raises(RuntimeError):
            asyncio.run(redis_offload.offload_redis(raising_fn))
        after = len(redis_offload._samples)

        assert after == before + 1, "a raising call must still record a latency sample"
        assert redis_offload.get_last_redis_latency() >= 0.02


class TestSlowCallWarning:
    """A call exceeding REDIS_OFFLOAD_SLOW_THRESHOLD must log a WARNING."""

    def test_slow_call_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setattr(redis_offload, "REDIS_OFFLOAD_SLOW_THRESHOLD", 0.01)

        def slow_fn():
            time.sleep(0.05)
            return "done"

        with caplog.at_level(logging.WARNING, logger="agent.redis_offload"):
            result = asyncio.run(redis_offload.offload_redis(slow_fn))

        assert result == "done"
        assert any("slow Redis call" in r.getMessage() for r in caplog.records)

    def test_fast_call_does_not_log_warning(self, monkeypatch, caplog):
        monkeypatch.setattr(redis_offload, "REDIS_OFFLOAD_SLOW_THRESHOLD", 10.0)

        with caplog.at_level(logging.WARNING, logger="agent.redis_offload"):
            asyncio.run(redis_offload.offload_redis(lambda: "fast"))

        assert not any("slow Redis call" in r.getMessage() for r in caplog.records)


class TestThreadSafety:
    """Concurrent offloads must run on distinct pool threads."""

    def test_concurrent_calls_run_on_distinct_threads(self):
        seen_threads = []
        lock = threading.Lock()

        def fn():
            with lock:
                seen_threads.append(threading.current_thread().name)
            time.sleep(0.05)
            return threading.current_thread().name

        async def run():
            return await asyncio.gather(
                redis_offload.offload_redis(fn),
                redis_offload.offload_redis(fn),
            )

        results = asyncio.run(run())

        assert len(results) == 2
        assert len(set(results)) == 2, "the two offloads must run on distinct threads"
        assert all(name.startswith("redis-io-") for name in results)


class TestWindowedLatencyGauges:
    """p95/max must reflect only the rolling window, not a lifetime high-water mark."""

    def test_windowed_decay_drops_stale_samples(self, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr(redis_offload.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(redis_offload, "REDIS_LATENCY_WINDOW_S", 300.0)

        # Record a slow sample at t=1000.
        redis_offload._record(9.0)
        assert redis_offload.get_redis_latency_max() == 9.0

        # Advance the clock past the window and record a fast sample.
        clock["t"] = 1000.0 + 301.0
        redis_offload._record(0.1)

        # The slow sample must have aged out — max reflects only the fast
        # sample now, not a never-resetting lifetime high-water mark.
        assert redis_offload.get_redis_latency_max() == 0.1
        assert redis_offload.get_redis_latency_p95() == 0.1

    def test_reset_clears_window(self, monkeypatch):
        clock = {"t": 2000.0}
        monkeypatch.setattr(redis_offload.time, "monotonic", lambda: clock["t"])

        redis_offload._record(5.0)
        assert redis_offload.get_redis_latency_max() == 5.0

        redis_offload.reset_max_redis_latency()

        assert redis_offload.get_redis_latency_max() == 0.0
        assert redis_offload.get_redis_latency_p95() == 0.0

    def test_empty_window_returns_zero(self, monkeypatch):
        # get_last_redis_latency() tracks the most recent call independent of
        # the window, so pin it directly rather than relying on test order.
        monkeypatch.setattr(redis_offload, "_last_latency", 0.0)

        assert redis_offload.get_redis_latency_max() == 0.0
        assert redis_offload.get_redis_latency_p95() == 0.0
        assert redis_offload.get_last_redis_latency() == 0.0
