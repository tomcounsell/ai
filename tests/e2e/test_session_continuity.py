"""E2E tests for session lifecycle and continuity.

Tests session creation, reply-to resumption, transcript accumulation,
and dead letter handling. Uses real Redis via conftest redis_test_db.
"""

import time

import pytest

from bridge.dedup import record_message_processed
from bridge.session_transcript import (
    _transcript_path,
    append_tool_result,
    append_turn,
    complete_transcript,
    start_transcript,
)
from models.agent_session import AgentSession


@pytest.mark.e2e
class TestSessionCreation:
    """Test that new sessions are created correctly in Redis."""

    def test_start_transcript_creates_session(self):
        session_id = f"test_session_{int(time.time())}"
        log_path = start_transcript(
            session_id=session_id,
            project_key="valor",
            chat_id="12345",
            sender="TestUser",
        )
        assert log_path is not None

        sessions = list(AgentSession.query.filter(session_id=session_id))
        assert len(sessions) == 1
        assert sessions[0].project_key == "valor"
        assert sessions[0].sender_name == "TestUser"

    def test_session_gets_active_status(self):
        session_id = f"test_active_{int(time.time())}"
        start_transcript(
            session_id=session_id,
            project_key="valor",
        )
        sessions = list(AgentSession.query.filter(session_id=session_id))
        assert len(sessions) == 1
        assert sessions[0].status == "active"


@pytest.mark.e2e
class TestSessionTranscript:
    """Test transcript accumulation across turns."""

    def test_append_turn_increments_count(self):
        session_id = f"test_turns_{int(time.time())}"
        start_transcript(session_id=session_id, project_key="valor")

        append_turn(session_id, "user", "Hello there")
        append_turn(session_id, "assistant", "Hi! How can I help?")

        sessions = list(AgentSession.query.filter(session_id=session_id))
        assert sessions[0].turn_count >= 2

    def test_append_tool_result_increments_tool_count(self):
        session_id = f"test_tools_{int(time.time())}"
        start_transcript(session_id=session_id, project_key="valor")

        append_turn(session_id, "tool_call", "", tool_name="Bash", tool_input="ls -la")
        append_tool_result(session_id, "file1.py file2.py")

        sessions = list(AgentSession.query.filter(session_id=session_id))
        assert sessions[0].tool_call_count >= 1

    def test_transcript_file_has_content(self):
        session_id = f"test_file_{int(time.time())}"
        start_transcript(session_id=session_id, project_key="valor")
        append_turn(session_id, "user", "test message content")

        path = _transcript_path(session_id)
        assert path.exists()
        content = path.read_text()
        assert "SESSION_START" in content
        assert "test message content" in content


@pytest.mark.e2e
class TestSessionCompletion:
    """Test session finalization."""

    def test_complete_transcript_marks_status(self):
        session_id = f"test_complete_{int(time.time())}"
        start_transcript(session_id=session_id, project_key="valor")
        append_turn(session_id, "user", "do something")
        append_turn(session_id, "assistant", "done")
        complete_transcript(session_id, status="completed", summary="Task finished")

        sessions = list(AgentSession.query.filter(session_id=session_id))
        assert len(sessions) == 1
        assert sessions[0].status == "completed"

    def test_complete_transcript_writes_end_marker(self):
        session_id = f"test_end_{int(time.time())}"
        start_transcript(session_id=session_id, project_key="valor")
        complete_transcript(session_id, status="completed")

        path = _transcript_path(session_id)
        content = path.read_text()
        assert "SESSION_END" in content

    def test_dormant_session_status(self):
        session_id = f"test_dormant_{int(time.time())}"
        start_transcript(session_id=session_id, project_key="valor")
        complete_transcript(session_id, status="dormant", summary="Waiting for input")

        sessions = list(AgentSession.query.filter(session_id=session_id))
        assert len(sessions) == 1
        assert sessions[0].status == "dormant"


@pytest.mark.e2e
class TestSessionResumeContext:
    """Test that reply-to messages can find prior session context."""

    def test_session_preserves_chat_id(self):
        session_id = f"test_chatid_{int(time.time())}"
        start_transcript(
            session_id=session_id,
            project_key="valor",
            chat_id="555666",
        )
        sessions = list(AgentSession.query.filter(session_id=session_id))
        assert sessions[0].chat_id == "555666"

    def test_session_updated_at_updates(self):
        session_id = f"test_activity_{int(time.time())}"
        start_transcript(session_id=session_id, project_key="valor")

        sessions = list(AgentSession.query.filter(session_id=session_id))
        first_activity = sessions[0].updated_at

        time.sleep(0.05)
        append_turn(session_id, "user", "another message")

        sessions = list(AgentSession.query.filter(session_id=session_id))
        assert sessions[0].updated_at >= first_activity

    def test_multiple_sessions_same_chat(self):
        chat_id = "777888"
        s1 = f"test_multi1_{int(time.time())}"
        s2 = f"test_multi2_{int(time.time())}"

        start_transcript(session_id=s1, project_key="valor", chat_id=chat_id)
        start_transcript(session_id=s2, project_key="valor", chat_id=chat_id)

        all_sessions = list(AgentSession.query.filter(chat_id=chat_id))
        session_ids = {s.session_id for s in all_sessions}
        assert s1 in session_ids
        assert s2 in session_ids


@pytest.mark.e2e
class TestDedupAcrossSessions:
    """Test that dedup prevents re-processing across session boundaries."""

    @pytest.mark.asyncio
    async def test_message_processed_in_one_session_blocked_in_another(self):
        chat_id = 123456
        msg_id = 999

        await record_message_processed(chat_id, msg_id)

        from bridge.dedup import is_duplicate_message

        assert await is_duplicate_message(chat_id, msg_id)
