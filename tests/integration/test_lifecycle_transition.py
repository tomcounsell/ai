"""Tests for AgentSession lifecycle transition logging (Part 1 of session stall diagnostics).

Covers:
1. AgentSession.log_lifecycle_transition() emits structured log, appends history
2. Duration derived from started_at/created_at
3. session_transcript.py calls log_lifecycle_transition() on start and complete
4. agent_session_queue.py calls log_lifecycle_transition() at key points
"""

import logging
import time

import pytest

# claude_agent_sdk mock is centralized in conftest.py
from models.agent_session import AgentSession

# ── AgentSession.log_lifecycle_transition() ──────────────────────────────────


class TestLogLifecycleTransition:
    """Tests for the log_lifecycle_transition() method."""

    def test_appends_to_history(self, redis_test_db):
        """log_lifecycle_transition() appends a [lifecycle] entry to history."""
        s = AgentSession.create(
            session_id="llt-test-2",
            project_key="test",
            status="pending",
            created_at=time.time(),
        )
        s.log_lifecycle_transition("running", "worker picked up")

        history = s._get_history_list()
        assert len(history) >= 1
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        assert len(lifecycle_entries) == 1
        assert "pending" in lifecycle_entries[0]
        assert "running" in lifecycle_entries[0]

    def test_includes_context_in_history(self, redis_test_db):
        """log_lifecycle_transition() includes context string in history entry."""
        s = AgentSession.create(
            session_id="llt-test-3",
            project_key="test",
            status="active",
            created_at=time.time(),
        )
        s.log_lifecycle_transition("completed", "transcript completed: completed")

        history = s._get_history_list()
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        assert any("transcript completed" in entry for entry in lifecycle_entries)

    def test_emits_structured_log(self, redis_test_db, caplog):
        """log_lifecycle_transition() emits a structured LIFECYCLE log line."""
        s = AgentSession.create(
            session_id="llt-test-4",
            project_key="testproj",
            status="pending",
            created_at=time.time(),
        )
        with caplog.at_level(logging.INFO, logger="models.agent_session"):
            s.log_lifecycle_transition("running", "test context")

        lifecycle_logs = [r for r in caplog.records if "LIFECYCLE" in r.message]
        assert len(lifecycle_logs) == 1
        msg = lifecycle_logs[0].message
        assert "session=llt-test-4" in msg
        assert "transition=pending" in msg
        assert "running" in msg
        assert "project=testproj" in msg
        assert "duration_in_prev_state=" in msg

    def test_duration_derived_from_started_at(self, redis_test_db):
        """log_lifecycle_transition() calculates duration from started_at."""
        base_time = time.time() - 10.0  # 10 seconds ago
        s = AgentSession.create(
            session_id="llt-test-5",
            project_key="test",
            status="running",
            created_at=base_time,
            started_at=base_time,
        )
        s.log_lifecycle_transition("completed", "done")

        # Verify lifecycle entry was appended (duration is in the log, not a field)
        history = s._get_history_list()
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        assert len(lifecycle_entries) >= 1
        assert "completed" in lifecycle_entries[-1]

    def test_handles_none_status_gracefully(self, redis_test_db):
        """log_lifecycle_transition() treats None status as 'none'."""
        s = AgentSession.create(
            session_id="llt-test-6",
            project_key="test",
            status="active",
            created_at=time.time(),
        )
        # Force status to None for edge case testing
        s.status = None
        s.log_lifecycle_transition("active", "recovery")

        history = s._get_history_list()
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        assert len(lifecycle_entries) >= 1
        assert "none" in lifecycle_entries[0]

    def test_no_context_omits_from_log(self, redis_test_db, caplog):
        """log_lifecycle_transition() omits context when empty string."""
        s = AgentSession.create(
            session_id="llt-test-7",
            project_key="test",
            status="pending",
            created_at=time.time(),
        )
        with caplog.at_level(logging.INFO, logger="models.agent_session"):
            s.log_lifecycle_transition("running")

        lifecycle_logs = [r for r in caplog.records if "LIFECYCLE" in r.message]
        assert len(lifecycle_logs) == 1
        assert "context=" not in lifecycle_logs[0].message


# ── session_transcript.py instrumentation ────────────────────────────────────


class TestSessionTranscriptLifecycleLogging:
    """Tests that session_transcript functions call log_lifecycle_transition()."""

    def test_start_transcript_logs_lifecycle(self, redis_test_db):
        """start_transcript() calls log_lifecycle_transition('active', ...)."""
        from bridge.session_transcript import start_transcript

        log_path = start_transcript(
            session_id="st-lc-test-1",
            project_key="test",
        )
        assert log_path is not None

        # Verify the lifecycle transition was logged
        sessions = list(AgentSession.query.filter(session_id="st-lc-test-1"))
        assert len(sessions) == 1
        history = sessions[0]._get_history_list()
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        assert len(lifecycle_entries) >= 1
        assert "active" in lifecycle_entries[0]

    def test_complete_transcript_logs_lifecycle(self, redis_test_db):
        """complete_transcript() calls log_lifecycle_transition(status, ...)."""
        from bridge.session_transcript import complete_transcript, start_transcript

        start_transcript(
            session_id="st-lc-test-2",
            project_key="test",
        )
        complete_transcript(
            session_id="st-lc-test-2",
            status="completed",
            summary="All done",
        )

        # Verify lifecycle transition was logged for completion
        sessions = list(AgentSession.query.filter(session_id="st-lc-test-2"))
        assert len(sessions) == 1
        history = sessions[0]._get_history_list()
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        # Should have at least 2: one from start, one from complete
        assert len(lifecycle_entries) >= 2
        # Last lifecycle entry should reference completed
        assert any("completed" in entry for entry in lifecycle_entries)

    def test_complete_transcript_preserves_history(self, redis_test_db):
        """complete_transcript() preserves history entries across delete-and-recreate."""
        from bridge.session_transcript import complete_transcript, start_transcript

        start_transcript(
            session_id="st-lc-test-3",
            project_key="test",
        )

        # Add some history entries
        sessions = list(AgentSession.query.filter(session_id="st-lc-test-3"))
        assert len(sessions) == 1
        s = sessions[0]
        s.append_history("user", "test message")
        s.save()

        # Complete transcript (this triggers delete-and-recreate for status change)
        complete_transcript(
            session_id="st-lc-test-3",
            status="completed",
        )

        # Verify history was preserved
        sessions = list(AgentSession.query.filter(session_id="st-lc-test-3"))
        assert len(sessions) == 1
        history = sessions[0]._get_history_list()
        assert len(history) >= 1


# ── agent_session_queue.py instrumentation ─────────────────────────────────────────────


class TestJobQueueLifecycleLogging:
    """Tests that agent_session_queue functions log lifecycle transitions."""

    @pytest.mark.asyncio
    async def test_push_agent_session_logs_pending_transition(self, redis_test_db):
        """_push_agent_session() logs lifecycle transition to 'pending'."""
        from agent.agent_session_queue import _push_agent_session

        await _push_agent_session(
            project_key="test",
            session_id="jq-lc-test-1",
            working_dir="/tmp/test",
            message_text="hello",
            sender_name="tester",
            chat_id="100",
            telegram_message_id=1,
        )

        sessions = list(AgentSession.query.filter(session_id="jq-lc-test-1"))
        assert len(sessions) == 1
        history = sessions[0]._get_history_list()
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        assert len(lifecycle_entries) >= 1
        assert any("pending" in entry for entry in lifecycle_entries)

    @pytest.mark.asyncio
    async def test_pop_agent_session_logs_running_transition(self, redis_test_db):
        """_pop_agent_session() logs lifecycle transition to 'running'."""
        from agent.agent_session_queue import _pop_agent_session, _push_agent_session

        await _push_agent_session(
            project_key="test",
            session_id="jq-lc-test-2",
            working_dir="/tmp/test",
            message_text="hello",
            sender_name="tester",
            chat_id="100",
            telegram_message_id=1,
        )

        session = await _pop_agent_session("100")
        assert session is not None

        # The running session should have a lifecycle entry
        sessions = list(AgentSession.query.filter(status="running"))
        assert len(sessions) >= 1
        running_session = None
        for s in sessions:
            if s.session_id == "jq-lc-test-2":
                running_session = s
                break
        assert running_session is not None
        history = running_session._get_history_list()
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        assert any("running" in entry for entry in lifecycle_entries)

    def test_session_fields_includes_history(self):
        """_AGENT_SESSION_FIELDS includes history for lifecycle entry preservation."""
        from agent.agent_session_queue import _AGENT_SESSION_FIELDS

        assert "history" in _AGENT_SESSION_FIELDS
