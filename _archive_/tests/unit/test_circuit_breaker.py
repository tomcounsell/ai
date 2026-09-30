"""Tests for bridge.resilience.CircuitBreaker."""

import time

import pytest

from bridge.resilience import CircuitBreaker, CircuitState


@pytest.fixture
def cb():
    """Standard circuit breaker with low thresholds for testing."""
    return CircuitBreaker(
        name="test",
        failure_threshold=3,
        failure_window=10.0,
        half_open_interval=1.0,
    )


@pytest.mark.asyncio
async def test_initial_state_closed(cb):
    assert cb.state == CircuitState.CLOSED
    assert cb.is_closed
    assert not cb.is_open
    assert cb.allows_request()


@pytest.mark.asyncio
async def test_success_keeps_closed(cb):
    await cb.record_success()
    assert cb.is_closed
    assert cb.stats["total_successes"] == 1


@pytest.mark.asyncio
async def test_failures_below_threshold_stay_closed(cb):
    await cb.record_failure()
    await cb.record_failure()
    assert cb.is_closed
    assert cb.allows_request()


@pytest.mark.asyncio
async def test_failures_at_threshold_opens(cb):
    for _ in range(3):
        await cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.is_open
    assert not cb.allows_request()


@pytest.mark.asyncio
async def test_open_transitions_to_half_open_after_interval(cb):
    for _ in range(3):
        await cb.record_failure()
    assert cb.is_open

    # Manually set opened_at to the past
    cb._opened_at = time.time() - 2.0
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allows_request()


@pytest.mark.asyncio
async def test_half_open_success_closes(cb):
    for _ in range(3):
        await cb.record_failure()
    cb._opened_at = time.time() - 2.0
    assert cb.state == CircuitState.HALF_OPEN

    await cb.record_success()
    assert cb.is_closed


@pytest.mark.asyncio
async def test_half_open_failure_reopens(cb):
    for _ in range(3):
        await cb.record_failure()
    cb._opened_at = time.time() - 2.0
    assert cb.state == CircuitState.HALF_OPEN

    await cb.record_failure()
    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_manual_reset(cb):
    for _ in range(3):
        await cb.record_failure()
    assert cb.is_open

    await cb.reset()
    assert cb.is_closed
    assert cb.stats["recent_failures"] == 0


@pytest.mark.asyncio
async def test_failures_outside_window_pruned(cb):
    """Failures older than failure_window don't count toward threshold."""
    await cb.record_failure()
    await cb.record_failure()
    # Manually age out the failures
    old_time = time.time() - 20.0
    cb._failures.clear()
    cb._failures.append(old_time)
    cb._failures.append(old_time)

    await cb.record_failure()
    # Old failures pruned, only 1 recent failure — should stay closed
    assert cb.is_closed


@pytest.mark.asyncio
async def test_callbacks_called():
    opened = []
    closed = []
    cb = CircuitBreaker(
        name="cb-test",
        failure_threshold=2,
        failure_window=10.0,
        half_open_interval=0.5,
        on_open=lambda: opened.append(True),
        on_close=lambda: closed.append(True),
    )

    await cb.record_failure()
    await cb.record_failure()
    assert len(opened) == 1

    cb._opened_at = time.time() - 1.0
    await cb.record_success()
    assert len(closed) == 1


@pytest.mark.asyncio
async def test_zero_threshold_always_open():
    """Edge case: zero threshold should open on first failure."""
    cb = CircuitBreaker(name="zero", failure_threshold=0, failure_window=10.0)
    # With threshold=0, len(failures) >= 0 is always true on any failure
    await cb.record_failure()
    assert cb.is_open


@pytest.mark.asyncio
async def test_stats_reflect_state(cb):
    stats = cb.stats
    assert stats["name"] == "test"
    assert stats["state"] == "closed"
    assert stats["total_failures"] == 0
    assert stats["total_successes"] == 0

    await cb.record_failure()
    stats = cb.stats
    assert stats["total_failures"] == 1
    assert stats["recent_failures"] == 1
