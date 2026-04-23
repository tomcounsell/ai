"""Unit tests for worker/idle_sweeper.py (issue #1128).

The sweeper targets persistent Claude SDK clients on dormant sessions and
tears them down before the ~48h silent-death window (#1104). Tests cover
the status filter, dormancy-age filter, registry teardown, the
`sdk_connection_torn_down_at` marker, and the fail-quiet contract.

These tests use a `FakeClient` with an awaitable `close()` method and
mutate `agent.sdk_client._active_clients` directly — that dict is the
real process-local registry, which is correct for this test because the
sweeper reads it by import.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agent import sdk_client
from models.agent_session import AgentSession
from worker import idle_sweeper
from worker.idle_sweeper import IDLE_TEARDOWN_THRESHOLD, TEARDOWN_STATUSES, _sweep_once


class FakeClient:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clear_active_clients():
    """Reset the active-clients registry between tests."""
    sdk_client._active_clients.clear()
    yield
    sdk_client._active_clients.clear()


def _make_session(session_id: str, status: str, updated_at: datetime):
    """Build and persist an AgentSession with a specific `updated_at`.

    `updated_at` has `auto_now=True` on the model, so a plain save() would
    overwrite our explicit value with the current timestamp. We pass
    `skip_auto_now=True` to Popoto so the dormancy-age test fixtures hold.
    """
    s = AgentSession(
        session_id=session_id,
        project_key="tst-sweep",
        agent_session_id=f"as-{session_id}",
        status=status,
        updated_at=updated_at,
    )
    s.save(skip_auto_now=True)
    return s


class TestStatusFilter:
    @pytest.mark.asyncio
    async def test_dormant_session_older_than_threshold_is_torn_down(self):
        sid = "sweep-dormant"
        client = FakeClient()
        sdk_client._active_clients[sid] = client
        updated = datetime.now(UTC) - timedelta(seconds=IDLE_TEARDOWN_THRESHOLD + 3600)
        _make_session(sid, "dormant", updated)

        torn_down = await _sweep_once()

        assert torn_down == 1
        assert client.closed
        assert sid not in sdk_client._active_clients
        reloaded = list(AgentSession.query.filter(session_id=sid))[0]
        assert reloaded.sdk_connection_torn_down_at is not None

    @pytest.mark.asyncio
    async def test_running_session_is_skipped(self):
        sid = "sweep-running"
        client = FakeClient()
        sdk_client._active_clients[sid] = client
        updated = datetime.now(UTC) - timedelta(seconds=IDLE_TEARDOWN_THRESHOLD + 3600)
        _make_session(sid, "running", updated)

        torn_down = await _sweep_once()

        assert torn_down == 0
        assert not client.closed
        assert sid in sdk_client._active_clients

    @pytest.mark.asyncio
    async def test_paused_session_is_torn_down_when_idle(self):
        sid = "sweep-paused"
        client = FakeClient()
        sdk_client._active_clients[sid] = client
        updated = datetime.now(UTC) - timedelta(seconds=IDLE_TEARDOWN_THRESHOLD + 3600)
        _make_session(sid, "paused", updated)

        torn_down = await _sweep_once()
        assert torn_down == 1
        assert client.closed

    @pytest.mark.asyncio
    async def test_paused_circuit_session_is_torn_down_when_idle(self):
        sid = "sweep-paused-circuit"
        client = FakeClient()
        sdk_client._active_clients[sid] = client
        updated = datetime.now(UTC) - timedelta(seconds=IDLE_TEARDOWN_THRESHOLD + 3600)
        _make_session(sid, "paused_circuit", updated)

        torn_down = await _sweep_once()
        assert torn_down == 1
        assert client.closed


class TestDormancyThreshold:
    @pytest.mark.asyncio
    async def test_dormant_below_threshold_is_skipped(self):
        sid = "sweep-fresh-dormant"
        client = FakeClient()
        sdk_client._active_clients[sid] = client
        # Only 1h dormant — well below 24h threshold
        updated = datetime.now(UTC) - timedelta(seconds=3600)
        _make_session(sid, "dormant", updated)

        torn_down = await _sweep_once()
        assert torn_down == 0
        assert not client.closed
        assert sid in sdk_client._active_clients


class TestFailQuiet:
    @pytest.mark.asyncio
    async def test_close_raising_is_logged_but_registry_still_popped(self):
        sid = "sweep-close-fails"

        class BrokenClient:
            async def close(self):
                raise RuntimeError("already closed")

        client = BrokenClient()
        sdk_client._active_clients[sid] = client
        updated = datetime.now(UTC) - timedelta(seconds=IDLE_TEARDOWN_THRESHOLD + 1)
        _make_session(sid, "dormant", updated)

        torn_down = await _sweep_once()
        assert torn_down == 1
        # Registry entry must still be popped
        assert sid not in sdk_client._active_clients

    @pytest.mark.asyncio
    async def test_missing_session_is_quiet_skip(self):
        """A registered client with no AgentSession record does not raise."""
        sid = "sweep-orphan"
        sdk_client._active_clients[sid] = FakeClient()
        torn_down = await _sweep_once()
        # No session record → sweep does NOT tear down (safe default).
        assert torn_down == 0
        assert sid in sdk_client._active_clients

    @pytest.mark.asyncio
    async def test_empty_registry_returns_zero(self):
        # No active clients at all
        torn_down = await _sweep_once()
        assert torn_down == 0


class TestFeatureGate:
    @pytest.mark.asyncio
    async def test_env_disable_is_noop(self, monkeypatch):
        sid = "sweep-disabled"
        client = FakeClient()
        sdk_client._active_clients[sid] = client
        updated = datetime.now(UTC) - timedelta(seconds=IDLE_TEARDOWN_THRESHOLD + 1)
        _make_session(sid, "dormant", updated)

        monkeypatch.setenv("WATCHDOG_IDLE_TEARDOWN_ENABLED", "false")
        torn_down = await _sweep_once()
        assert torn_down == 0
        assert not client.closed


class TestConcurrentModification:
    @pytest.mark.asyncio
    async def test_snapshot_iteration_safe(self):
        """Adding to `_active_clients` during sweep must not raise."""
        sid = "sweep-snap-1"
        client = FakeClient()
        sdk_client._active_clients[sid] = client
        updated = datetime.now(UTC) - timedelta(seconds=IDLE_TEARDOWN_THRESHOLD + 1)
        _make_session(sid, "dormant", updated)

        # The sweeper takes a snapshot before iterating, so adding another
        # entry mid-flight is safe.
        async def add_during_sweep():
            await asyncio.sleep(0)  # yield
            sdk_client._active_clients["sweep-snap-2"] = FakeClient()

        results = await asyncio.gather(_sweep_once(), add_during_sweep())
        torn_down = results[0]
        assert torn_down == 1
        # The newly added entry should still be around (it was not in the snapshot).
        assert "sweep-snap-2" in sdk_client._active_clients


class TestConstants:
    def test_teardown_statuses_are_the_three_documented(self):
        assert TEARDOWN_STATUSES == frozenset({"dormant", "paused", "paused_circuit"})

    def test_threshold_is_configurable(self, monkeypatch):
        # Reimporting the module with the env override flips the constant.
        monkeypatch.setenv("WATCHDOG_IDLE_TEARDOWN_THRESHOLD_SECONDS", "12345")
        import importlib

        reloaded = importlib.reload(idle_sweeper)
        assert reloaded.IDLE_TEARDOWN_THRESHOLD == 12345
        # Restore module to canonical state for downstream tests.
        monkeypatch.delenv("WATCHDOG_IDLE_TEARDOWN_THRESHOLD_SECONDS", raising=False)
        importlib.reload(idle_sweeper)
