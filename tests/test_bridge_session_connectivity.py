"""Integration tests for Bridge <-> AgentSession <-> SDK connectivity.

Tests the full chain without mocking internal lookup functions:
1. _find_session() resolves via task_list_id when session_id doesn't match
2. complete_transcript() preserves ALL fields through status change
3. Only one AgentSession exists per session_id after enqueue + transcript start

These tests use real Redis (db=1 via conftest) — no mocks on _find_session().
"""

import time

import pytest

from models.agent_session import AgentSession
from tools.session_progress import _find_session


class TestFindSessionViaTaskListId:
    """_find_session() should resolve a Claude Code UUID to the correct
    AgentSession via the task_list_id fallback path."""

    def test_find_session_via_task_list_id(self, redis_test_db):
        """Create a session with a Telegram-style session_id and a task_list_id.
        Call _find_session() with the task_list_id value (simulating a hook
        that passes Claude Code's internal UUID). Verify it finds the session."""
        # Create session as the job queue would — Telegram-style session_id
        session = AgentSession.create(
            session_id="telegram_chat_12345_msg_67890",
            project_key="test",
            status="running",
            chat_id="12345",
            sender_name="Tom",
            created_at=time.time(),
            started_at=time.time(),
            last_activity=time.time(),
            task_list_id="thread-12345-67890",  # The computed task_list_id
        )

        # Hook fires with the task_list_id value (Claude Code's session ID)
        found = _find_session("thread-12345-67890")

        assert found is not None
        assert found.session_id == "telegram_chat_12345_msg_67890"
        assert found.task_list_id == "thread-12345-67890"

    def test_find_session_prefers_session_id_over_task_list_id(self, redis_test_db):
        """If a session matches by session_id directly, that takes precedence
        over the task_list_id fallback scan."""
        AgentSession.create(
            session_id="direct-match",
            project_key="test",
            status="running",
            created_at=time.time(),
            task_list_id="some-other-id",
        )

        found = _find_session("direct-match")
        assert found is not None
        assert found.session_id == "direct-match"

    def test_find_session_returns_none_when_no_match(self, redis_test_db):
        """If neither session_id nor task_list_id matches, return None."""
        AgentSession.create(
            session_id="unrelated-session",
            project_key="test",
            status="running",
            created_at=time.time(),
            task_list_id="unrelated-task-list",
        )

        found = _find_session("nonexistent-uuid-from-hook")
        assert found is None


class TestCompleteTranscriptPreservesAllFields:
    """complete_transcript() must preserve ALL AgentSession fields through
    the delete-and-recreate status change, not just a hardcoded subset."""

    def test_complete_transcript_preserves_all_fields(self, redis_test_db, tmp_path):
        """Create an AgentSession with all fields populated, call
        complete_transcript(), verify every field survives."""
        from bridge.session_transcript import complete_transcript

        session_id = "preserve-test-session"

        # Create session with ALL fields populated
        session = AgentSession.create(
            session_id=session_id,
            project_key="test",
            status="running",
            priority="high",
            created_at=1700000000.0,
            started_at=1700000001.0,
            last_activity=1700000002.0,
            working_dir="/tmp/test-working-dir",
            message_text="Test message text for preservation",
            sender_name="TestUser",
            sender_id=42,
            chat_id="999",
            message_id=123,
            chat_title="Test Chat",
            task_list_id="thread-999-123",
            turn_count=5,
            tool_call_count=3,
            log_path=str(tmp_path / "transcript.txt"),
            branch_name="session/test-branch",
            work_item_slug="test-slug",
            classification_type="feature",
            classification_confidence=0.95,
        )
        # Add history and links
        session.append_history("user", "Test message")
        session.append_history("stage", "BUILD completed")
        session.set_link("issue", "https://github.com/test/issues/1")
        session.set_link("pr", "https://github.com/test/pull/2")

        # Create the transcript file so complete_transcript doesn't fail
        transcript_file = tmp_path / "transcript.txt"
        transcript_file.write_text("test transcript\n")

        # Complete the transcript — this triggers delete-and-recreate
        complete_transcript(session_id, status="completed", summary="Test summary")

        # Find the recreated session
        completed_sessions = list(
            AgentSession.query.filter(session_id=session_id)
        )
        assert len(completed_sessions) == 1
        s = completed_sessions[0]

        # Verify status changed
        assert s.status == "completed"

        # Verify ALL fields survived the transition
        assert s.session_id == session_id
        assert s.project_key == "test"
        assert s.priority == "high"
        assert s.created_at == 1700000000.0
        assert s.started_at == 1700000001.0
        assert s.working_dir == "/tmp/test-working-dir"
        assert s.message_text == "Test message text for preservation"
        assert s.sender_name == "TestUser"
        assert s.sender_id == 42
        assert s.chat_id == "999"
        assert s.message_id == 123
        assert s.chat_title == "Test Chat"
        assert s.task_list_id == "thread-999-123"
        assert s.turn_count == 5
        assert s.tool_call_count == 3
        assert s.branch_name == "session/test-branch"
        assert s.work_item_slug == "test-slug"
        assert s.classification_type == "feature"
        assert s.classification_confidence == 0.95
        assert s.summary == "Test summary"

        # Verify history survived
        history = s._get_history_list()
        assert any("Test message" in h for h in history)
        assert any("BUILD" in h for h in history)

        # Verify links survived
        assert s.issue_url == "https://github.com/test/issues/1"
        assert s.pr_url == "https://github.com/test/pull/2"

        # Verify completion timestamps were set
        assert s.completed_at is not None
        assert s.last_activity is not None


class TestSingleSessionPerMessage:
    """After enqueue (_push_job creates session) + start_transcript(),
    only ONE AgentSession should exist for the session_id."""

    def test_single_session_after_enqueue_and_transcript(self, redis_test_db):
        """Simulate the real flow: _push_job creates a pending session,
        _pop_job converts to running, then start_transcript is called.
        Verify only one AgentSession exists."""
        from bridge.session_transcript import start_transcript

        session_id = "single-session-test"

        # Step 1: Simulate what _push_job does — create a pending session
        AgentSession.create(
            session_id=session_id,
            project_key="test",
            status="pending",
            chat_id="100",
            sender_name="Tom",
            created_at=time.time(),
            message_text="Test message",
        )

        # Step 2: Simulate what _pop_job does — delete and recreate as running
        pending = list(AgentSession.query.filter(session_id=session_id))
        assert len(pending) == 1
        fields = {
            "session_id": pending[0].session_id,
            "project_key": pending[0].project_key,
            "chat_id": pending[0].chat_id,
            "sender_name": pending[0].sender_name,
            "created_at": pending[0].created_at,
            "message_text": pending[0].message_text,
        }
        pending[0].delete()
        AgentSession.create(
            status="running",
            started_at=time.time(),
            **fields,
        )

        # Step 3: start_transcript is called
        start_transcript(
            session_id=session_id,
            project_key="test",
            chat_id="100",
            sender="Tom",
        )

        # Count all AgentSession objects with this session_id across ALL statuses
        all_sessions = AgentSession.query.all()
        matching = [s for s in all_sessions if s.session_id == session_id]

        # Should be exactly 1 session, not 2
        assert len(matching) == 1, (
            f"Expected 1 session for {session_id}, found {len(matching)}. "
            f"Statuses: {[s.status for s in matching]}"
        )

    def test_start_transcript_updates_existing_session(self, redis_test_db):
        """When start_transcript finds an existing session, it should update
        the log_path on it rather than creating a new one."""
        from bridge.session_transcript import start_transcript

        session_id = "update-existing-test"

        # Create session as the job queue would
        AgentSession.create(
            session_id=session_id,
            project_key="test",
            status="running",
            chat_id="200",
            sender_name="Tom",
            created_at=time.time(),
            started_at=time.time(),
        )

        # start_transcript should find and update, not create
        log_path = start_transcript(
            session_id=session_id,
            project_key="test",
            chat_id="200",
            sender="Tom",
        )

        # Should return a valid log path
        assert log_path is not None

        # Should still be exactly 1 session
        all_sessions = AgentSession.query.all()
        matching = [s for s in all_sessions if s.session_id == session_id]
        assert len(matching) == 1

        # The existing session should have log_path set
        s = matching[0]
        assert s.log_path is not None
        assert "transcript.txt" in s.log_path
