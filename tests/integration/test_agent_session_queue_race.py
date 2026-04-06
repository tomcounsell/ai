"""Tests for session queue race condition fixes and KeyField index corruption prevention.

Validates the delete-and-recreate pattern used by _pop_agent_session,
_recover_interrupted_agent_sessions_startup, and the drain guard in _worker_loop.

All tests use redis_test_db fixture (autouse=True in conftest.py) for isolation.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest

from agent.agent_session_queue import (
    DRAIN_TIMEOUT,
    _extract_agent_session_fields,
    _pop_agent_session,
    _pop_agent_session_with_fallback,
    _recover_interrupted_agent_sessions_startup,
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


class TestExtractJobFields:
    """Tests for the _extract_agent_session_fields helper."""

    def test_returns_complete_field_set(self):
        """All non-auto fields should be present in the extracted dict."""
        session = _create_test_session(
            sender_id=42,
            chat_title="Test Chat",
            revival_context="some context",
            slug="my-feature",
            task_list_id="thread-123-456",
            classification_type="bug",
            auto_continue_count=2,
        )

        fields = _extract_agent_session_fields(session)

        # agent_session_id should NOT be in extracted fields (it's AutoKeyField)
        assert "agent_session_id" not in fields

        # All other fields should be present
        assert fields["project_key"] == "test"
        assert fields["status"] == "pending"
        assert fields["priority"] == "high"
        assert fields["created_at"] is not None
        assert fields["session_id"] == "test_session"
        assert fields["working_dir"] == "/tmp/test"
        assert fields["message_text"] == "test message"
        assert fields["sender_name"] == "Test"
        assert fields["sender_id"] == 42
        assert fields["chat_id"] == "123"
        assert fields["telegram_message_id"] == 1
        assert fields["chat_title"] == "Test Chat"
        assert fields["revival_context"] == "some context"
        assert fields["slug"] == "my-feature"
        assert fields["task_list_id"] == "thread-123-456"
        assert fields["classification_type"] == "bug"
        assert fields["auto_continue_count"] == 2

    def test_extracted_fields_can_recreate_job(self):
        """Extracted fields should be usable for AgentSession.create()."""
        original = _create_test_session()
        fields = _extract_agent_session_fields(original)

        # Should create successfully without errors
        new_job = AgentSession.create(**fields)
        assert new_job.agent_session_id != original.agent_session_id  # New auto-generated ID
        assert new_job.project_key == original.project_key
        assert new_job.message_text == original.message_text


class TestPopJobDeleteAndRecreate:
    """Tests for _pop_agent_session using delete-and-recreate to prevent stale index entries."""

    @pytest.mark.asyncio
    async def test_pop_agent_session_no_stale_pending(self):
        """After popping a session, it should NOT appear in the pending index."""
        _create_test_session()

        # Verify it's in pending
        pending_before = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending_before) == 1

        # Pop the session
        session = await _pop_agent_session("123")
        assert session is not None

        # The old pending entry should be gone
        pending_after = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending_after) == 0, "Stale pending index entry found after _pop_agent_session"

        # The session should be in the running index
        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 1

    @pytest.mark.asyncio
    async def test_pop_agent_session_returns_none_on_empty_queue(self):
        """_pop_agent_session should return None when no pending sessions exist."""
        result = await _pop_agent_session("123")
        assert result is None

    @pytest.mark.asyncio
    async def test_pop_agent_session_preserves_fields(self):
        """The popped session should retain all original field values."""
        original = _create_test_session(
            session_id="preserve_test",
            sender_name="FieldCheck",
            auto_continue_count=3,
        )

        session = await _pop_agent_session("123")
        assert session is not None
        assert session.session_id == "preserve_test"
        assert session.sender_name == "FieldCheck"
        assert session.auto_continue_count == 3
        # agent_session_id changes (new object), but all other fields preserved
        assert session.agent_session_id != original.agent_session_id

    @pytest.mark.asyncio
    async def test_pop_agent_session_respects_priority_order(self):
        """High priority jobs should be popped before low priority."""
        _create_test_session(
            priority="low",
            message_text="low priority",
            created_at=datetime.now(tz=UTC),
        )
        _create_test_session(
            priority="high",
            message_text="high priority",
            created_at=datetime.now(tz=UTC) + timedelta(seconds=1),
        )

        session = await _pop_agent_session("123")
        assert session is not None
        assert session.message_text == "high priority"


class TestRecoverInterruptedJobsStartup:
    """Tests for _recover_interrupted_agent_sessions_startup with timing guard.

    The timing guard skips sessions started within AGENT_SESSION_HEALTH_MIN_RUNNING
    seconds (300s). Tests that expect recovery must set started_at to an old timestamp.
    """

    def test_no_stale_running_after_recovery(self):
        """After recovery, no sessions should remain in the running index."""
        old_started = datetime.now(tz=UTC) - timedelta(seconds=600)
        _create_test_session(
            status="running", session_id="crashed_session", started_at=old_started
        )

        running_before = AgentSession.query.filter(status="running")
        assert len(running_before) == 1

        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 1

        running_after = AgentSession.query.filter(status="running")
        assert len(running_after) == 0

        pending = AgentSession.query.filter(status="pending")
        assert len(pending) == 1
        assert pending[0].priority == "high"
        assert pending[0].session_id == "crashed_session"

    def test_recover_multiple_running_jobs(self):
        """Recovery should handle multiple running sessions."""
        old_started = datetime.now(tz=UTC) - timedelta(seconds=600)
        _create_test_session(
            status="running",
            session_id="s1",
            message_text="msg1",
            started_at=old_started,
        )
        _create_test_session(
            status="running",
            session_id="s2",
            message_text="msg2",
            started_at=old_started,
        )

        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 2

        running = AgentSession.query.filter(status="running")
        assert len(running) == 0

        pending = AgentSession.query.filter(status="pending")
        assert len(pending) == 2

    def test_recover_returns_zero_when_nothing_to_recover(self):
        """Recovery should return 0 when no running sessions exist."""
        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 0

    def test_recent_session_skipped_by_timing_guard(self):
        """A session started 10 seconds ago should NOT be recovered."""
        recent_started = datetime.now(tz=UTC) - timedelta(seconds=10)
        _create_test_session(
            status="running", session_id="recent_session", started_at=recent_started
        )

        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 0

        # Session should still be running (not reset to pending)
        running = AgentSession.query.filter(status="running")
        assert len(running) == 1
        assert running[0].session_id == "recent_session"

    def test_old_session_recovered_by_timing_guard(self):
        """A session started 600 seconds ago IS recovered."""
        old_started = datetime.now(tz=UTC) - timedelta(seconds=600)
        _create_test_session(
            status="running", session_id="old_session", started_at=old_started
        )

        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 1

        running = AgentSession.query.filter(status="running")
        assert len(running) == 0

        pending = AgentSession.query.filter(status="pending")
        assert len(pending) == 1

    def test_none_started_at_is_recovered(self):
        """A session with started_at=None (legacy/corrupt) IS recovered."""
        _create_test_session(
            status="running", session_id="legacy_session", started_at=None
        )

        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 1

        running = AgentSession.query.filter(status="running")
        assert len(running) == 0

    def test_mixed_recent_and_stale_sessions(self):
        """Only stale sessions are recovered; recent ones are skipped."""
        old_started = datetime.now(tz=UTC) - timedelta(seconds=600)
        recent_started = datetime.now(tz=UTC) - timedelta(seconds=10)

        _create_test_session(
            status="running", session_id="stale", started_at=old_started
        )
        _create_test_session(
            status="running", session_id="recent", started_at=recent_started
        )

        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 1

        running = AgentSession.query.filter(status="running")
        assert len(running) == 1
        assert running[0].session_id == "recent"

        pending = AgentSession.query.filter(status="pending")
        assert len(pending) == 1
        assert pending[0].session_id == "stale"


class TestDrainGuard:
    """Tests for the Event-based worker drain guard logic.

    Validates the drain strategy: asyncio.Event notification from enqueue_agent_session(),
    with sync Popoto fallback via _pop_agent_session_with_fallback() on timeout.
    """

    def test_drain_timeout_is_configurable_constant(self):
        """DRAIN_TIMEOUT should be a module-level constant, not hardcoded."""
        assert isinstance(DRAIN_TIMEOUT, int | float)
        assert DRAIN_TIMEOUT > 0

    @pytest.mark.asyncio
    async def test_pop_agent_session_with_fallback_finds_job_via_sync_query(self):
        """_pop_agent_session_with_fallback should find a pending session even when
        _pop_agent_session would miss it due to index visibility issues.

        Since we can't easily reproduce the index race in tests, we test
        the happy path: a session exists and _pop_agent_session_with_fallback finds it.
        """
        _create_test_session(message_text="fallback target")

        result = await _pop_agent_session_with_fallback("123")
        assert result is not None
        assert result.message_text == "fallback target"

    @pytest.mark.asyncio
    async def test_pop_agent_session_with_fallback_returns_none_when_empty(self):
        """_pop_agent_session_with_fallback should return None when no pending sessions exist."""
        result = await _pop_agent_session_with_fallback("123")
        assert result is None

    @pytest.mark.asyncio
    async def test_pop_agent_session_with_fallback_respects_priority(self):
        """Sync fallback should respect priority ordering like _pop_agent_session."""
        _create_test_session(
            priority="low", message_text="low prio", created_at=datetime.now(tz=UTC)
        )
        _create_test_session(
            priority="high",
            message_text="high prio",
            created_at=datetime.now(tz=UTC) + timedelta(seconds=1),
        )

        result = await _pop_agent_session_with_fallback("123")
        assert result is not None
        assert result.message_text == "high prio"

    @pytest.mark.asyncio
    async def test_pop_agent_session_with_fallback_transitions_to_running(self):
        """_pop_agent_session_with_fallback transitions session pending->running."""
        _create_test_session(message_text="status check")

        result = await _pop_agent_session_with_fallback("123")
        assert result is not None

        # No pending sessions should remain
        pending = AgentSession.query.filter(chat_id="123", status="pending")
        assert len(pending) == 0

        # One running session should exist
        running = AgentSession.query.filter(chat_id="123", status="running")
        assert len(running) == 1

    @pytest.mark.asyncio
    async def test_event_based_drain_catches_late_job(self):
        """When _pop_agent_session returns None, creating a session should be found by fallback."""
        # First call: no sessions
        result1 = await _pop_agent_session("123")
        assert result1 is None

        # Simulate a late-arriving session
        _create_test_session(message_text="late arrival")

        # Fallback should find it
        result2 = await _pop_agent_session_with_fallback("123")
        assert result2 is not None
        assert result2.message_text == "late arrival"

    @pytest.mark.asyncio
    async def test_drain_exits_when_truly_empty(self):
        """When queue is truly empty, both _pop_agent_session and fallback return None."""
        result1 = await _pop_agent_session("123")
        assert result1 is None

        result2 = await _pop_agent_session_with_fallback("123")
        assert result2 is None
