"""Tests for AgentSession lifecycle transition logging (Part 1 of session stall diagnostics).

Covers:
1. AgentSession.last_transition_at field exists and is settable
2. AgentSession.log_lifecycle_transition() emits structured log, updates field, appends history
3. session_transcript.py calls log_lifecycle_transition() on start and complete
4. job_queue.py calls log_lifecycle_transition() at key points
"""

import logging
import time
from unittest.mock import MagicMock

import pytest

# claude_agent_sdk mock is centralized in conftest.py

from models.agent_session import AgentSession

# ── AgentSession.last_transition_at field ────────────────────────────────────


class TestLastTransitionAtField:
    """Tests for the last_transition_at field on AgentSession."""

    def test_field_defaults_to_none(self, redis_test_db):
        """last_transition_at is None by default when not set."""
        s = AgentSession.create(
            session_id="lta-test-1",
            project_key="test",
            status="active",
            created_at=time.time(),
        )
        assert s.last_transition_at is None

    def test_field_is_settable(self, redis_test_db):
        """last_transition_at can be set to a float timestamp."""
        now = time.time()
        s = AgentSession.create(
            session_id="lta-test-2",
            project_key="test",
            status="active",
            created_at=now,
            last_transition_at=now,
        )
        assert s.last_transition_at == pytest.approx(now, abs=1.0)

    def test_field_persists_after_save(self, redis_test_db):
        """last_transition_at persists through save and reload."""
        now = time.time()
        s = AgentSession.create(
            session_id="lta-test-3",
            project_key="test",
            status="active",
            created_at=now,
        )
        s.last_transition_at = now
        s.save()

        # Reload from Redis
        reloaded = list(AgentSession.query.filter(session_id="lta-test-3"))
        assert len(reloaded) == 1
        assert reloaded[0].last_transition_at == pytest.approx(now, abs=1.0)


# ── AgentSession.log_lifecycle_transition() ──────────────────────────────────


class TestLogLifecycleTransition:
    """Tests for the log_lifecycle_transition() method."""

    def test_updates_last_transition_at(self, redis_test_db):
        """log_lifecycle_transition() sets last_transition_at to current time."""
        s = AgentSession.create(
            session_id="llt-test-1",
            project_key="test",
            status="pending",
            created_at=time.time(),
        )
        before = time.time()
        s.log_lifecycle_transition("running", "worker picked up")
        after = time.time()

        assert s.last_transition_at is not None
        assert before <= s.last_transition_at <= after

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

    def test_duration_calculation(self, redis_test_db):
        """log_lifecycle_transition() calculates duration from last_transition_at."""
        base_time = time.time() - 10.0  # 10 seconds ago
        s = AgentSession.create(
            session_id="llt-test-5",
            project_key="test",
            status="running",
            created_at=base_time,
            started_at=base_time,
            last_transition_at=base_time,
        )
        s.log_lifecycle_transition("completed", "done")

        # last_transition_at should now be updated to ~now
        assert s.last_transition_at > base_time

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

    def test_complete_transcript_preserves_last_transition_at(self, redis_test_db):
        """complete_transcript() preserves last_transition_at in old_data dict."""
        from bridge.session_transcript import complete_transcript, start_transcript

        start_transcript(
            session_id="st-lc-test-3",
            project_key="test",
        )

        # Set a known last_transition_at
        sessions = list(AgentSession.query.filter(session_id="st-lc-test-3"))
        assert len(sessions) == 1
        s = sessions[0]
        s.last_transition_at = time.time()
        s.save()

        # Complete transcript (this triggers delete-and-recreate for status change)
        complete_transcript(
            session_id="st-lc-test-3",
            status="completed",
        )

        # Verify the new session has last_transition_at
        sessions = list(AgentSession.query.filter(session_id="st-lc-test-3"))
        assert len(sessions) == 1
        assert sessions[0].last_transition_at is not None


# ── job_queue.py instrumentation ─────────────────────────────────────────────


class TestJobQueueLifecycleLogging:
    """Tests that job_queue functions log lifecycle transitions."""

    @pytest.mark.asyncio
    async def test_push_job_logs_pending_transition(self, redis_test_db):
        """_push_job() logs lifecycle transition to 'pending'."""
        from agent.job_queue import _push_job

        await _push_job(
            project_key="test",
            session_id="jq-lc-test-1",
            working_dir="/tmp/test",
            message_text="hello",
            sender_name="tester",
            chat_id="100",
            message_id=1,
        )

        sessions = list(AgentSession.query.filter(session_id="jq-lc-test-1"))
        assert len(sessions) == 1
        history = sessions[0]._get_history_list()
        lifecycle_entries = [h for h in history if "[lifecycle]" in h]
        assert len(lifecycle_entries) >= 1
        assert any("pending" in entry for entry in lifecycle_entries)

    @pytest.mark.asyncio
    async def test_pop_job_logs_running_transition(self, redis_test_db):
        """_pop_job() logs lifecycle transition to 'running'."""
        from agent.job_queue import _pop_job, _push_job

        await _push_job(
            project_key="test",
            session_id="jq-lc-test-2",
            working_dir="/tmp/test",
            message_text="hello",
            sender_name="tester",
            chat_id="100",
            message_id=1,
        )

        job = await _pop_job("test")
        assert job is not None

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

    def test_job_fields_includes_last_transition_at(self):
        """_JOB_FIELDS includes last_transition_at for delete-and-recreate."""
        from agent.job_queue import _JOB_FIELDS

        assert "last_transition_at" in _JOB_FIELDS
