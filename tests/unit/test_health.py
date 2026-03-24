"""Unit tests for bridge/health.py DependencyHealth."""

import pytest

from bridge.health import DependencyHealth
from bridge.resilience import CircuitBreaker


@pytest.fixture
def health():
    return DependencyHealth()


class TestDependencyHealth:
    def test_empty_summary(self, health):
        summary = health.summary()
        assert summary["overall"] == "healthy"
        assert summary["circuits"] == {}

    def test_register_and_summary(self, health):
        cb = CircuitBreaker("test-dep", failure_threshold=3)
        health.register(cb)
        summary = health.summary()
        assert "test-dep" in summary["circuits"]
        assert summary["overall"] == "healthy"

    def test_unregister(self, health):
        cb = CircuitBreaker("removable", failure_threshold=3)
        health.register(cb)
        health.unregister("removable")
        summary = health.summary()
        assert "removable" not in summary["circuits"]

    def test_get_circuit(self, health):
        cb = CircuitBreaker("lookup", failure_threshold=3)
        health.register(cb)
        assert health.get_circuit("lookup") is cb
        assert health.get_circuit("nonexistent") is None

    @pytest.mark.asyncio
    async def test_degraded_when_one_open(self, health):
        cb1 = CircuitBreaker("dep-a", failure_threshold=1)
        cb2 = CircuitBreaker("dep-b", failure_threshold=99)
        health.register(cb1)
        health.register(cb2)
        await cb1.record_failure()
        summary = health.summary()
        assert summary["overall"] == "degraded"

    @pytest.mark.asyncio
    async def test_down_when_all_open(self, health):
        cb1 = CircuitBreaker("dep-a", failure_threshold=1)
        cb2 = CircuitBreaker("dep-b", failure_threshold=1)
        health.register(cb1)
        health.register(cb2)
        await cb1.record_failure()
        await cb2.record_failure()
        summary = health.summary()
        assert summary["overall"] == "down"

    def test_formatted_status_no_circuits(self, health):
        text = health.formatted_status()
        assert "healthy" in text
        assert "no circuits registered" in text

    @pytest.mark.asyncio
    async def test_formatted_status_with_circuits(self, health):
        cb = CircuitBreaker("anthropic", failure_threshold=2)
        health.register(cb)
        await cb.record_failure()
        await cb.record_failure()
        text = health.formatted_status()
        assert "degraded" in text or "down" in text
        assert "anthropic:open" in text
