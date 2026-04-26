"""Tests for session health monitor: detect and recover stuck running sessions.

Tests cover:
- started_at field on AgentSession (set when session transitions to running)
- _agent_session_health_check() detecting and recovering dead workers
- CLI functions: format_duration, show_status, flush_stuck, flush_session

The previous wall-clock per-session timeout (``_get_agent_session_timeout`` +
``AGENT_SESSION_TIMEOUT_DEFAULT``/``BUILD``) was retired by issue #1172 — the
detector no longer kills on inferred staleness. Cost monitoring is the
long-run backstop for genuinely runaway sessions.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from agent.agent_session_queue import (
    _active_workers,
    _pop_agent_session_with_fallback,
)
from models.agent_session import AgentSession


def _create_test_session(**overrides) -> AgentSession:
    """Create an AgentSession with sensible defaults for testing."""
    defaults = {
        "project_key": "test",
        "status": "pending",
        "priority": "high",
        "created_at": time.time(),
        "session_id": "test_session",
        "working_dir": "/tmp/test",
        "message_text": "test message",
        "sender_name": "Test",
        "chat_id": "123",
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


class TestStartedAtField:
    """Tests for the started_at field on AgentSession."""

    def test_started_at_field_exists(self):
        """AgentSession should have a started_at field."""
        assert hasattr(AgentSession, "started_at"), "AgentSession missing started_at field"

    def test_started_at_defaults_to_none(self):
        """A newly created AgentSession should have started_at=None."""
        session = _create_test_session()
        assert session.started_at is None

    @pytest.mark.asyncio
    async def test_pop_agent_session_sets_started_at(self):
        """When _pop_agent_session transitions a session to running, started_at should be set."""
        _create_test_session()
        before = datetime.now(tz=UTC)
        session = await _pop_agent_session_with_fallback("123")
        after = datetime.now(tz=UTC)

        assert session is not None
        # Verify the AgentSession in Redis has started_at set
        running_jobs = AgentSession.query.filter(project_key="test", status="running")
        assert len(running_jobs) == 1
        assert running_jobs[0].started_at is not None
        started = running_jobs[0].started_at
        if isinstance(started, (int, float)):
            started = datetime.fromtimestamp(started, tz=UTC)
        elif isinstance(started, datetime) and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        assert before <= started <= after

    def test_started_at_in_extract_fields(self):
        """started_at should be included in _extract_agent_session_fields."""
        from agent.agent_session_queue import _extract_agent_session_fields

        session = _create_test_session(started_at=datetime.now(tz=UTC))
        fields = _extract_agent_session_fields(session)
        assert "started_at" in fields
        assert fields["started_at"] is not None


class TestJobHealthCheck:
    """Tests for _agent_session_health_check().

    Note: _agent_session_health_check uses `session.chat_id or project_key` as the worker key.
    Default chat_id in _create_test_session is "123", so workers must be keyed by "123".
    Recovery calls _ensure_worker which spawns real asyncio tasks, so cleanup must
    cancel all workers after each test.
    """

    # The default chat_id used by _create_test_session
    WORKER_KEY = "123"

    @pytest.fixture(autouse=True)
    def _cleanup_workers(self):
        """Clean up _active_workers after each test to prevent cross-test pollution."""
        yield
        # Cancel any worker tasks spawned by _ensure_worker during recovery.
        # The event loop may already be closed by the time teardown runs,
        # so silently ignore RuntimeError from cancel().
        for key in list(_active_workers.keys()):
            task = _active_workers.pop(key, None)
            if task and not task.done():
                try:
                    task.cancel()
                except RuntimeError:
                    pass

    @pytest.mark.asyncio
    async def test_recovers_job_with_dead_worker(self):
        """A running session whose worker task is done should be recovered."""
        from agent.agent_session_queue import _agent_session_health_check

        # Create a running session that has been running long enough
        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC) - timedelta(seconds=600),  # 10 minutes ago
            session_id="dead_worker_session",
        )

        # Set up a dead worker (asyncio Task that's already done)
        # Workers are keyed by chat_id (default "123"), not project_key
        done_task = asyncio.Future()
        done_task.set_result(None)
        _active_workers[self.WORKER_KEY] = done_task

        await _agent_session_health_check()

        # The running session should be gone, replaced by a pending one
        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 0

        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 1
        assert pending[0].session_id == "dead_worker_session"

    @pytest.mark.asyncio
    async def test_recovers_job_with_no_worker(self):
        """A running session with no entry in _active_workers should be recovered."""
        from agent.agent_session_queue import _agent_session_health_check

        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC) - timedelta(seconds=600),  # 10 minutes ago
            session_id="orphan_session",
        )

        # Ensure no worker exists for this chat_id
        _active_workers.pop(self.WORKER_KEY, None)

        await _agent_session_health_check()

        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 0

        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 1
        assert pending[0].session_id == "orphan_session"

    @pytest.mark.asyncio
    async def test_skips_job_with_alive_worker_under_timeout(self):
        """A running session with an alive worker under timeout should NOT be recovered."""
        from agent.agent_session_queue import _agent_session_health_check

        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC)
            - timedelta(seconds=60),  # 1 minute ago (under 5min guard)
            session_id="alive_session",
        )

        # Set up a live worker keyed by chat_id
        live_task = asyncio.Future()
        _active_workers[self.WORKER_KEY] = live_task

        await _agent_session_health_check()

        # The session should still be running
        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 1
        assert running[0].session_id == "alive_session"

        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_long_running_session_with_fresh_heartbeat_survives(self):
        """Issue #1172: a session with a fresh heartbeat is NOT killed regardless
        of wall-clock duration. The previous wall-clock cap is gone."""
        from agent.agent_session_queue import _agent_session_health_check

        # Simulate a 4-hour-old session — far beyond any prior wall-clock cap.
        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC) - timedelta(hours=4),
            last_heartbeat_at=datetime.now(tz=UTC) - timedelta(seconds=30),
            turn_count=12,
            session_id="long_running_session",
        )

        # Worker is alive — fresh heartbeat is the dispositive evidence.
        live_task = asyncio.Future()
        _active_workers[self.WORKER_KEY] = live_task

        await _agent_session_health_check()

        # Must remain running — no timeout-based recovery any more.
        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 1
        assert running[0].session_id == "long_running_session"

    @pytest.mark.asyncio
    async def test_skips_recently_started_job_with_dead_worker(self):
        """Sessions running < AGENT_SESSION_HEALTH_MIN_RUNNING not recovered (race guard)."""
        from agent.agent_session_queue import _agent_session_health_check

        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC)
            - timedelta(seconds=60),  # Only 1 minute ago (under 5min guard)
            session_id="recent_session",
        )

        # Worker is dead, keyed by chat_id
        done_task = asyncio.Future()
        done_task.set_result(None)
        _active_workers[self.WORKER_KEY] = done_task

        await _agent_session_health_check()

        # Should NOT be recovered due to race condition guard
        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 1
        assert running[0].session_id == "recent_session"

    @pytest.mark.asyncio
    async def test_handles_job_without_started_at(self):
        """Jobs without started_at (legacy) should still be checked for dead workers."""
        from agent.agent_session_queue import _agent_session_health_check

        # A running session with no started_at (legacy session that predates this field)
        _create_test_session(
            status="running",
            session_id="legacy_session",
        )
        # started_at defaults to None

        # Worker is dead — ensure no worker at chat_id key
        _active_workers.pop(self.WORKER_KEY, None)

        await _agent_session_health_check()

        # Without started_at, we can't determine how long it's been running
        # but if no worker exists, it should still be recovered if started_at is None
        # The health check should handle this gracefully
        running = AgentSession.query.filter(project_key="test", status="running")
        # Legacy jobs without started_at and no worker should still be recovered
        # since we can't determine their age, recovering is safer than leaving them stuck
        assert len(running) == 0

    @pytest.mark.asyncio
    async def test_no_running_jobs_is_noop(self):
        """When no running sessions exist, health check should do nothing."""
        from agent.agent_session_queue import _agent_session_health_check

        # Create only a pending session
        _create_test_session(status="pending")

        await _agent_session_health_check()

        # Nothing should change
        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_recovers_orphan_pending_with_no_running_sessions(self, monkeypatch, caplog):
        """Regression for #1124/#1126: orphan-PENDING recovery must reach _ensure_worker.

        Topology: zero RUNNING sessions + one orphan PENDING session older than
        AGENT_SESSION_HEALTH_MIN_RUNNING with a non-local worker_key.

        Pre-fix, agent/session_health.py's orphan-PENDING branch raised
        UnboundLocalError on the `from agent.agent_session_queue import _ensure_worker`
        line at what is now line 1019 — Python treats the name as function-local due
        to the RUNNING-branch import at line 948, so the import statement itself
        raised before the call at line 1021 could execute. The per-entry
        `except Exception: logger.exception(...)` at line 1023 caught the error and
        logged it, so the function did not propagate — instead, _ensure_worker was
        silently never invoked.

        This test guards the fix by asserting the spy WAS called exactly once with
        the seeded session's worker_key and is_project_keyed, AND that no log
        record mentions UnboundLocalError.
        """
        import logging

        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_MIN_RUNNING,
            _active_workers,
            _agent_session_health_check,
        )

        # Seed one PENDING session with a non-local worker_key (chat_id="789")
        # and a created_at past the 5-minute age threshold.
        seeded_session = _create_test_session(
            status="pending",
            chat_id="789",
            session_id="orphan_pending_session",
            created_at=time.time() - (AGENT_SESSION_HEALTH_MIN_RUNNING + 60),
        )

        # Pre-assertion: topology — zero RUNNING sessions.
        running_pre = AgentSession.query.filter(project_key="test", status="running")
        assert len(running_pre) == 0, (
            f"topology drift: expected zero RUNNING sessions, got {len(running_pre)}"
        )

        # Pre-assertion: worker_key is non-local. If helper defaults ever change to
        # produce a "local"-prefixed key, this test would exercise the abandoned-local
        # branch at agent/session_health.py:994 instead of the orphan-PENDING-with-
        # _ensure_worker branch, and the spy would silently never be called.
        assert not seeded_session.worker_key.startswith("local"), (
            f"topology drift: worker_key={seeded_session.worker_key!r} — this test "
            "exercises the non-local orphan-PENDING branch"
        )

        # Pre-flight cleanup of _active_workers. Mirrors the pattern at
        # test_recovers_job_with_no_worker (line 200). A leaked live worker for the
        # same worker_key would set worker_alive=True at agent/session_health.py:977
        # and cause the health check to skip the orphan-PENDING branch entirely.
        _active_workers.pop(seeded_session.worker_key, None)

        # Spy on _ensure_worker.
        # Patch on the source module — session_health re-imports _ensure_worker
        # locally on each call (agent/session_health.py:1019).
        spy_calls: list[tuple[str, bool]] = []

        def spy(worker_key: str, is_project_keyed: bool = False) -> None:
            spy_calls.append((worker_key, is_project_keyed))

        monkeypatch.setattr("agent.agent_session_queue._ensure_worker", spy)

        # Capture WARNING/ERROR-level logs for the belt-and-braces check below.
        caplog.set_level(logging.WARNING)

        await _agent_session_health_check()

        # Primary assertion: the spy was called exactly once with the derived
        # (worker_key, is_project_keyed) pair from the seeded session. On the
        # pre-fix tree, the UnboundLocalError at line 1019 prevented the call
        # site from ever being reached, so spy_calls would be empty.
        assert spy_calls == [(seeded_session.worker_key, seeded_session.is_project_keyed)], (
            f"spy calls: {spy_calls!r}"
        )

        # Belt-and-braces: no log record should mention UnboundLocalError. On the
        # pre-fix tree, the per-entry `except Exception: logger.exception(...)` at
        # agent/session_health.py:1023 would write this string to the log.
        for record in caplog.records:
            message = record.getMessage()
            assert "UnboundLocalError" not in message, (
                f"pre-fix bug regression detected in log: {message!r}"
            )
            assert "cannot access local variable '_ensure_worker'" not in message, (
                f"pre-fix bug regression detected in log: {message!r}"
            )


class TestJobHealthConstants:
    """Tests for health check constants."""

    def test_constants_exist(self):
        """Health check constants that survived the #1172 simplification."""
        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_CHECK_INTERVAL,
            AGENT_SESSION_HEALTH_MIN_RUNNING,
        )

        assert AGENT_SESSION_HEALTH_CHECK_INTERVAL == 300
        assert AGENT_SESSION_HEALTH_MIN_RUNNING == 300


class TestFormatDuration:
    """Tests for the CLI format_duration helper."""

    def test_format_none(self):
        """None input should return 'N/A'."""
        from agent.agent_session_queue import format_duration

        assert format_duration(None) == "N/A"

    def test_format_minutes(self):
        """Short durations should show minutes."""
        from agent.agent_session_queue import format_duration

        assert format_duration(120) == "2m"
        assert format_duration(300) == "5m"

    def test_format_hours(self):
        """Long durations should show hours and minutes."""
        from agent.agent_session_queue import format_duration

        assert format_duration(3600) == "1h0m"
        assert format_duration(5400) == "1h30m"

    def test_format_zero(self):
        """Zero seconds should show 0m."""
        from agent.agent_session_queue import format_duration

        assert format_duration(0) == "0m"
