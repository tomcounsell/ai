"""Unit tests for AgentSession model methods.

Tests for create_local() behavior, including the chat_id defaulting logic.
"""

from unittest.mock import MagicMock, patch

import pytest

from models.agent_session import AgentSession


class TestCreateLocalChatId:
    """Tests for create_local() chat_id assignment."""

    def test_create_local_uses_session_id_as_default_chat_id(self):
        """When no chat_id is provided, create_local() must use session_id as chat_id.

        Previously used f"local{int(now.timestamp()) % 10000}" which caused collisions
        between CLI sessions created within the same 10,000-second window (~2.7 hours).
        Now uses session_id (the Claude Code UUID) which is guaranteed unique.
        """
        session_uuid = "claude-session-abc123-unique-uuid"
        with patch.object(AgentSession, "save", MagicMock()):
            session = AgentSession.__new__(AgentSession)
            # Call the classmethod logic by testing the internal assignment
            # We test create_local's output by checking the chat_id field
            pass

        # Test via the actual method with a mocked save
        saved_sessions = []
        original_save = AgentSession.save

        def mock_save(self):
            saved_sessions.append(self)

        AgentSession.save = mock_save
        try:
            session = AgentSession.create_local(
                session_id=session_uuid,
                project_key="test",
                working_dir="/tmp/test",
            )
            assert session.chat_id == session_uuid, (
                f"Expected chat_id={session_uuid!r}, got {session.chat_id!r}. "
                "create_local() must use session_id as the default chat_id."
            )
        finally:
            AgentSession.save = original_save

    def test_create_local_explicit_chat_id_overrides_session_id(self):
        """Callers that provide an explicit chat_id must not have it overridden."""
        session_uuid = "claude-session-def456"
        explicit_chat_id = "telegram-chat-99999"
        saved_sessions = []

        original_save = AgentSession.save

        def mock_save(self):
            saved_sessions.append(self)

        AgentSession.save = mock_save
        try:
            session = AgentSession.create_local(
                session_id=session_uuid,
                project_key="test",
                working_dir="/tmp/test",
                chat_id=explicit_chat_id,
            )
            assert session.chat_id == explicit_chat_id, (
                f"Explicit chat_id={explicit_chat_id!r} must not be overridden by session_id."
            )
        finally:
            AgentSession.save = original_save

    def test_create_local_no_timestamp_modulo_in_chat_id(self):
        """chat_id must not contain a timestamp modulo pattern.

        Verifies the old collision-prone code path is gone.
        """
        import time

        session_uuid = "claude-session-xyz789"
        original_save = AgentSession.save

        def mock_save(self):
            pass

        AgentSession.save = mock_save
        try:
            session = AgentSession.create_local(
                session_id=session_uuid,
                project_key="test",
                working_dir="/tmp/test",
            )
            # The old pattern was f"local{int(now.timestamp()) % 10000}"
            # The new chat_id should be the session_uuid, not a local+number string
            assert not session.chat_id.startswith("local"), (
                f"chat_id={session.chat_id!r} must not start with 'local' — "
                "this was the old collision-prone timestamp pattern."
            )
            assert session.chat_id == session_uuid
        finally:
            AgentSession.save = original_save

    def test_create_local_two_sessions_same_second_get_different_chat_ids(self):
        """Two sessions created in the same second must have different chat_ids.

        This was broken with the old timestamp-modulo approach: two sessions
        created at the same second would share the same chat_id, causing them
        to serialize in the same worker queue instead of running independently.
        """
        uuid_a = "claude-session-aaaa-1111"
        uuid_b = "claude-session-bbbb-2222"

        original_save = AgentSession.save

        def mock_save(self):
            pass

        AgentSession.save = mock_save
        try:
            session_a = AgentSession.create_local(
                session_id=uuid_a,
                project_key="test",
                working_dir="/tmp/test",
            )
            session_b = AgentSession.create_local(
                session_id=uuid_b,
                project_key="test",
                working_dir="/tmp/test",
            )
            assert session_a.chat_id != session_b.chat_id, (
                "Two different CLI sessions must have different chat_ids "
                f"(got {session_a.chat_id!r} and {session_b.chat_id!r})."
            )
        finally:
            AgentSession.save = original_save
