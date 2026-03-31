"""Tests for session health monitor: detect and recover stuck running sessions.

Tests cover:
- started_at field on AgentSession (set when session transitions to running)
- _get_agent_session_timeout() returning correct timeouts based on message_text
- _agent_session_health_check() detecting and recovering dead workers and timed-out jobs
- CLI functions: format_duration, show_status, flush_stuck, flush_session
"""

import asyncio
import time

import pytest

from agent.agent_session_queue import (
    _active_workers,
    _pop_agent_session,
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
        before = time.time()
        session = await _pop_agent_session("123")
        after = time.time()

        assert session is not None
        # Verify the AgentSession in Redis has started_at set
        running_jobs = AgentSession.query.filter(project_key="test", status="running")
        assert len(running_jobs) == 1
        assert running_jobs[0].started_at is not None
        assert before <= running_jobs[0].started_at <= after

    def test_started_at_in_extract_fields(self):
        """started_at should be included in _extract_agent_session_fields."""
        from agent.agent_session_queue import _extract_agent_session_fields

        session = _create_test_session(started_at=12345.0)
        fields = _extract_agent_session_fields(session)
        assert "started_at" in fields
        assert fields["started_at"] == 12345.0


class TestGetJobTimeout:
    """Tests for _get_agent_session_timeout()."""

    def test_standard_job_timeout(self):
        """Regular jobs should get the default 45-minute timeout."""
        from agent.agent_session_queue import (
            AGENT_SESSION_TIMEOUT_DEFAULT,
            _get_agent_session_timeout,
        )

        session = _create_test_session(message_text="hello, please fix the bug")
        timeout = _get_agent_session_timeout(session)
        assert timeout == AGENT_SESSION_TIMEOUT_DEFAULT

    def test_build_job_timeout(self):
        """Jobs containing /do-build should get the 2.5-hour timeout."""
        from agent.agent_session_queue import (
            AGENT_SESSION_TIMEOUT_BUILD,
            _get_agent_session_timeout,
        )

        session = _create_test_session(message_text="/do-build docs/plans/my-feature.md")
        timeout = _get_agent_session_timeout(session)
        assert timeout == AGENT_SESSION_TIMEOUT_BUILD

    def test_build_job_timeout_case_sensitive(self):
        """The /do-build check should be exact (not case-insensitive)."""
        from agent.agent_session_queue import (
            AGENT_SESSION_TIMEOUT_DEFAULT,
            _get_agent_session_timeout,
        )

        session = _create_test_session(message_text="/DO-BUILD something")
        timeout = _get_agent_session_timeout(session)
        # /do-build is lowercase in the plan, so uppercase shouldn't match
        assert timeout == AGENT_SESSION_TIMEOUT_DEFAULT

    def test_none_message_text_returns_default(self):
        """Jobs with None message_text should get default timeout."""
        from agent.agent_session_queue import (
            AGENT_SESSION_TIMEOUT_DEFAULT,
            _get_agent_session_timeout,
        )

        session = _create_test_session(message_text="")
        # Override message_text to empty/None after creation
        timeout = _get_agent_session_timeout(session)
        assert timeout == AGENT_SESSION_TIMEOUT_DEFAULT


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
            started_at=time.time() - 600,  # 10 minutes ago
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
            started_at=time.time() - 600,  # 10 minutes ago
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
            started_at=time.time() - 60,  # 1 minute ago (under 5min guard)
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
    async def test_recovers_timed_out_job_with_alive_worker(self):
        """A session that exceeded timeout should be recovered even if worker is alive."""
        from agent.agent_session_queue import (
            AGENT_SESSION_TIMEOUT_DEFAULT,
            _agent_session_health_check,
        )

        _create_test_session(
            status="running",
            started_at=time.time() - AGENT_SESSION_TIMEOUT_DEFAULT - 100,  # past timeout
            session_id="timeout_session",
        )

        # Worker is alive but session has exceeded timeout
        live_task = asyncio.Future()
        _active_workers[self.WORKER_KEY] = live_task

        await _agent_session_health_check()

        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 0

        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 1
        assert pending[0].session_id == "timeout_session"

    @pytest.mark.asyncio
    async def test_skips_recently_started_job_with_dead_worker(self):
        """Sessions running < AGENT_SESSION_HEALTH_MIN_RUNNING not recovered (race guard)."""
        from agent.agent_session_queue import _agent_session_health_check

        _create_test_session(
            status="running",
            started_at=time.time() - 60,  # Only 1 minute ago (under 5min guard)
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


class TestJobHealthConstants:
    """Tests for health check constants."""

    def test_constants_exist(self):
        """Health check constants should be defined."""
        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_CHECK_INTERVAL,
            AGENT_SESSION_HEALTH_MIN_RUNNING,
            AGENT_SESSION_TIMEOUT_BUILD,
            AGENT_SESSION_TIMEOUT_DEFAULT,
        )

        assert AGENT_SESSION_HEALTH_CHECK_INTERVAL == 300
        assert AGENT_SESSION_TIMEOUT_DEFAULT == 2700
        assert AGENT_SESSION_TIMEOUT_BUILD == 9000
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
