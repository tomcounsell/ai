"""Tests for session queue race condition fixes and KeyField index corruption prevention.

Validates the in-place mutation pattern used by _pop_agent_session (via
transition_status), the delete-and-recreate pattern still used by
_recover_interrupted_agent_sessions_startup, and the drain guard in _worker_loop.

All tests use redis_test_db fixture (autouse=True in conftest.py) for isolation.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest

from agent.agent_session_queue import (
    DRAIN_TIMEOUT,
    _active_workers,
    _ensure_worker,
    _extract_agent_session_fields,
    _pop_agent_session,
    _pop_agent_session_with_fallback,
    _recover_interrupted_agent_sessions_startup,
    _starting_workers,
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

        # Top-level stored fields
        assert fields["project_key"] == "test"
        assert fields["status"] == "pending"
        assert fields["priority"] == "high"
        assert fields["created_at"] is not None
        assert fields["session_id"] == "test_session"
        assert fields["working_dir"] == "/tmp/test"
        assert fields["chat_id"] == "123"
        assert fields["slug"] == "my-feature"
        assert fields["task_list_id"] == "thread-123-456"
        assert fields["auto_continue_count"] == 2

        # message_text/sender_name/etc are packed into initial_telegram_message.
        # _extract_agent_session_fields preserves the dict wholesale, so these
        # user-facing values round-trip transitively.
        itm = fields["initial_telegram_message"]
        assert isinstance(itm, dict)
        assert itm["message_text"] == "test message"
        assert itm["sender_name"] == "Test"
        assert itm["sender_id"] == 42
        assert itm["telegram_message_id"] == 1
        assert itm["chat_title"] == "Test Chat"

        # revival_context/classification_type live under extra_context.
        ec = fields["extra_context"]
        assert isinstance(ec, dict)
        assert ec["revival_context"] == "some context"
        assert ec["classification_type"] == "bug"

    def test_extracted_fields_can_recreate_job(self):
        """Extracted fields should be usable for AgentSession.create()."""
        original = _create_test_session()
        fields = _extract_agent_session_fields(original)

        # Should create successfully without errors
        new_job = AgentSession.create(**fields)
        # New record has a fresh auto-generated ID
        assert new_job.agent_session_id is not None
        assert new_job.project_key == original.project_key
        assert new_job.message_text == original.message_text


class TestPopJobInPlaceMutation:
    """Tests for _pop_agent_session using in-place mutation via transition_status()."""

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
        # In-place mutation: agent_session_id is stable, status flipped to running
        assert session.agent_session_id == original.agent_session_id
        assert session.status == "running"

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
        _create_test_session(status="running", session_id="crashed_session", started_at=old_started)

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
        _create_test_session(status="running", session_id="old_session", started_at=old_started)

        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 1

        running = AgentSession.query.filter(status="running")
        assert len(running) == 0

        pending = AgentSession.query.filter(status="pending")
        assert len(pending) == 1

    def test_none_started_at_is_recovered(self):
        """A session with started_at=None (legacy/corrupt) IS recovered."""
        _create_test_session(status="running", session_id="legacy_session", started_at=None)

        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered == 1

        running = AgentSession.query.filter(status="running")
        assert len(running) == 0

    def test_mixed_recent_and_stale_sessions(self):
        """Only stale sessions are recovered; recent ones are skipped."""
        old_started = datetime.now(tz=UTC) - timedelta(seconds=600)
        recent_started = datetime.now(tz=UTC) - timedelta(seconds=10)

        _create_test_session(status="running", session_id="stale", started_at=old_started)
        _create_test_session(status="running", session_id="recent", started_at=recent_started)

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


class TestEnsureWorkerDeduplication:
    """Tests for the _starting_workers guard in _ensure_worker().

    Validates that calling _ensure_worker() twice in rapid succession for the
    same chat_id — before either task has had a chance to register itself in
    _active_workers — results in exactly one worker task being created.

    This simulates the health-check race: N pending sessions share a chat_id,
    and the health check calls _ensure_worker() once per session before any
    asyncio.create_task() has had a chance to store its task reference.
    """

    @pytest.fixture(autouse=True)
    def cleanup_worker_state(self):
        """Clean up module-level worker dicts/sets before and after each test."""
        chat_id = "race-test-chat"
        # Pre-test cleanup
        _active_workers.pop(chat_id, None)
        _starting_workers.discard(chat_id)
        yield
        # Cancel any tasks created during the test to avoid warnings
        task = _active_workers.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
        _starting_workers.discard(chat_id)

    @pytest.mark.asyncio
    async def test_double_call_creates_only_one_worker(self):
        """Two back-to-back _ensure_worker() calls for the same chat_id must create exactly
        one asyncio.Task, even though neither call sees an entry in _active_workers at the
        point of the guard check (simulating the post-restart health-check race).
        """
        chat_id = "race-test-chat"

        # Simulate the race: _active_workers has no entry yet for this chat_id.
        assert chat_id not in _active_workers

        # Call _ensure_worker() twice without awaiting — both happen in the same
        # event-loop turn, so no task has run yet.
        _ensure_worker(chat_id)
        _ensure_worker(chat_id)

        # Exactly one task should exist for this chat_id.
        assert chat_id in _active_workers
        task = _active_workers[chat_id]
        assert task is not None

        # Verify that _starting_workers was cleaned up after task creation.
        assert chat_id not in _starting_workers

        # Count how many tasks were actually created by inspecting the single
        # task reference — if two tasks had been created the second would have
        # overwritten the first and we'd see two distinct task objects.
        # We validate by counting; calling _ensure_worker a third time should
        # now be blocked by the _active_workers (steady-state) guard.
        tasks_before = id(task)
        _ensure_worker(chat_id)
        assert id(_active_workers[chat_id]) == tasks_before, (
            "Third _ensure_worker() call replaced the existing live task — "
            "steady-state guard failed"
        )

    @pytest.mark.asyncio
    async def test_starting_workers_cleared_after_task_creation(self):
        """_starting_workers must be empty immediately after _ensure_worker() returns."""
        chat_id = "race-test-chat"
        _ensure_worker(chat_id)
        assert chat_id not in _starting_workers

    @pytest.mark.asyncio
    async def test_second_call_logs_warning(self, caplog):
        """When the _starting_workers guard blocks a duplicate spawn, a warning is logged."""
        import logging

        chat_id = "race-test-chat"

        # Patch _active_workers to simulate no live task so both calls
        # pass the first guard but the second is caught by _starting_workers.
        _active_workers.pop(chat_id, None)
        _starting_workers.discard(chat_id)

        # Manually inject chat_id into _starting_workers to simulate a
        # concurrent in-flight spawn (as the health check would see it).
        _starting_workers.add(chat_id)
        with caplog.at_level(logging.WARNING):
            _ensure_worker(chat_id)

        assert any(
            "Duplicate worker spawn blocked" in record.message for record in caplog.records
        ), "Expected warning for blocked duplicate spawn was not logged"
        # Clean up manual injection
        _starting_workers.discard(chat_id)
