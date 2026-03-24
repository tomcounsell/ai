"""Unit tests for bridge/resilience.py CircuitBreaker."""

import asyncio
import time

import pytest

from bridge.resilience import CircuitBreaker, CircuitState


@pytest.fixture
def cb():
    """Create a circuit breaker with low thresholds for testing."""
    return CircuitBreaker(
        name="test",
        failure_threshold=3,
        window_seconds=10.0,
        probe_interval_seconds=0.1,  # short for testing
    )


class TestCircuitBreakerStates:
    """Test state transitions."""

    def test_initial_state_is_closed(self, cb):
        assert cb.state == CircuitState.CLOSED
        assert cb.is_closed()
        assert not cb.is_open()

    @pytest.mark.asyncio
    async def test_stays_closed_below_threshold(self, cb):
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_closed()

    @pytest.mark.asyncio
    async def test_opens_at_threshold(self, cb):
        for _ in range(3):
            await cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open()

    @pytest.mark.asyncio
    async def test_half_open_after_probe_interval(self, cb):
        for _ in range(3):
            await cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # Wait for probe interval
        await asyncio.sleep(0.15)
        # is_closed() transitions to HALF_OPEN when probe interval elapses
        assert cb.is_closed()
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_closes_after_successful_probe(self, cb):
        for _ in range(3):
            await cb.record_failure()
        await asyncio.sleep(0.15)
        cb.is_closed()  # triggers half-open
        await cb.record_success()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_reopens_after_failed_probe(self, cb):
        for _ in range(3):
            await cb.record_failure()
        await asyncio.sleep(0.15)
        cb.is_closed()  # triggers half-open
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_in_closed_state_no_effect(self, cb):
        await cb.record_success()
        assert cb.state == CircuitState.CLOSED
        status = cb.status()
        assert status["total_successes"] == 1

    def test_reset(self, cb):
        cb._state = CircuitState.OPEN
        cb._failures = [time.monotonic()] * 5
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert len(cb._failures) == 0


class TestCircuitBreakerEdgeCases:
    """Test edge cases."""

    @pytest.mark.asyncio
    async def test_zero_threshold_always_open(self):
        cb = CircuitBreaker("zero", failure_threshold=0)
        # Zero threshold means first failure opens it
        # Actually, with 0 threshold the circuit should open on 0 failures
        # but we need at least one failure to trigger the check
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_high_threshold_never_opens(self):
        cb = CircuitBreaker("high", failure_threshold=999999)
        for _ in range(100):
            await cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failures_expire_from_window(self):
        cb = CircuitBreaker(
            "window-test",
            failure_threshold=3,
            window_seconds=0.1,
        )
        await cb.record_failure()
        await cb.record_failure()
        await asyncio.sleep(0.15)
        # Old failures should have expired
        await cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_immediate_success_no_sleep(self):
        """Verify that a CB doesn't introduce unnecessary delay."""
        cb = CircuitBreaker("fast", failure_threshold=5)
        start = time.monotonic()
        assert cb.is_closed()
        elapsed = time.monotonic() - start
        assert elapsed < 0.01


class TestCircuitBreakerCallbacks:
    """Test on_open and on_close callbacks."""

    @pytest.mark.asyncio
    async def test_on_open_called(self):
        called = []
        cb = CircuitBreaker(
            "cb-open",
            failure_threshold=2,
            on_open=lambda: called.append("opened"),
        )
        await cb.record_failure()
        await cb.record_failure()
        assert called == ["opened"]

    @pytest.mark.asyncio
    async def test_on_close_called(self):
        called = []
        cb = CircuitBreaker(
            "cb-close",
            failure_threshold=2,
            probe_interval_seconds=0.05,
            on_close=lambda: called.append("closed"),
        )
        await cb.record_failure()
        await cb.record_failure()
        await asyncio.sleep(0.1)
        cb.is_closed()  # half-open
        await cb.record_success()
        assert called == ["closed"]

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash(self):
        def bad_callback():
            raise RuntimeError("callback error")

        cb = CircuitBreaker(
            "cb-error",
            failure_threshold=1,
            on_open=bad_callback,
        )
        # Should not raise
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerStatus:
    """Test status reporting."""

    def test_status_empty(self, cb):
        status = cb.status()
        assert status["name"] == "test"
        assert status["state"] == "closed"
        assert status["failures_in_window"] == 0
        assert status["total_failures"] == 0
        assert status["total_successes"] == 0
        assert status["last_failure_ago"] is None

    @pytest.mark.asyncio
    async def test_status_after_failures(self, cb):
        await cb.record_failure()
        await cb.record_failure()
        status = cb.status()
        assert status["failures_in_window"] == 2
        assert status["total_failures"] == 2
        assert status["last_failure_ago"] is not None
        assert status["last_failure_ago"] < 1.0


class TestCircuitBreakerConcurrency:
    """Test concurrent access (asyncio)."""

    @pytest.mark.asyncio
    async def test_concurrent_failures(self):
        cb = CircuitBreaker("concurrent", failure_threshold=10, window_seconds=5)
        # Fire 20 concurrent failures
        tasks = [cb.record_failure() for _ in range(20)]
        await asyncio.gather(*tasks)
        assert cb._total_failures == 20
        assert cb.state == CircuitState.OPEN
