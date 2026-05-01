"""Unit tests for AgentSession.chat_message_log field and append_chat_log helper.

Tests the field default, trim semantics, re-fetch semantics, save-failure tolerance,
None/empty-sender handling, and empty-content skipping (issue #1192).
"""

from unittest.mock import MagicMock, patch

from models.agent_session import (
    CHAT_LOG_DISPLAY_ENTRIES,
    CHAT_LOG_MAX_ENTRIES,
    AgentSession,
)


class TestChatLogConstants:
    """CHAT_LOG_MAX_ENTRIES and CHAT_LOG_DISPLAY_ENTRIES are present and sane."""

    def test_max_entries_is_50(self):
        assert CHAT_LOG_MAX_ENTRIES == 50

    def test_display_entries_is_20(self):
        assert CHAT_LOG_DISPLAY_ENTRIES == 20

    def test_display_entries_less_than_max(self):
        assert CHAT_LOG_DISPLAY_ENTRIES < CHAT_LOG_MAX_ENTRIES


class TestChatMessageLogField:
    """chat_message_log field defaults and field existence."""

    def test_field_exists_on_model(self):
        assert hasattr(AgentSession, "chat_message_log")

    def test_append_chat_log_method_exists(self):
        session = AgentSession.__new__(AgentSession)
        assert callable(getattr(session, "append_chat_log", None))

    def test_default_is_empty_list_for_new_session(self):
        """New sessions start with an empty chat_message_log."""
        saved = []
        original_save = AgentSession.save

        def mock_save(self):
            saved.append(self)

        AgentSession.save = mock_save
        try:
            session = AgentSession.create_local(
                session_id="test-chat-log-default",
                project_key="test",
                working_dir="/tmp/test",
            )
            log = session.chat_message_log
            assert log == [] or log is None  # Popoto may return None before first save
        finally:
            AgentSession.save = original_save


class TestAppendChatLog:
    """append_chat_log() behavior: trim, entry shape, edge cases."""

    def _make_session(self, session_id="test-session-123", existing_log=None):
        """Helper: create an in-memory AgentSession with patched query and save."""
        session = AgentSession.__new__(AgentSession)
        session.session_id = session_id
        session.chat_message_log = existing_log if existing_log is not None else []
        return session

    def test_append_adds_entry(self):
        """A single append results in one entry with the correct fields."""
        session = self._make_session()
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(
                direction="in",
                sender="Tom",
                content="Hello",
                message_id=101,
            )
        log = session.chat_message_log
        assert len(log) == 1
        entry = log[0]
        assert entry["direction"] == "in"
        assert entry["sender"] == "Tom"
        assert entry["content"] == "Hello"
        assert entry["message_id"] == 101
        assert "ts" in entry
        assert isinstance(entry["ts"], float)

    def test_trim_to_max_entries(self):
        """After exceeding CHAT_LOG_MAX_ENTRIES, only the last N are kept."""
        # Pre-populate with MAX_ENTRIES entries
        existing = [
            {
                "direction": "in",
                "sender": "Tom",
                "content": f"msg-{i}",
                "message_id": i,
                "ts": float(i),
            }
            for i in range(CHAT_LOG_MAX_ENTRIES)
        ]
        session = self._make_session(existing_log=list(existing))
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(direction="out", sender="valor", content="new-msg")
        log = session.chat_message_log
        assert len(log) == CHAT_LOG_MAX_ENTRIES
        # The oldest entry (msg-0) is gone; the newest is the appended one
        assert log[-1]["content"] == "new-msg"
        assert log[0]["content"] == "msg-1"

    def test_empty_content_is_skipped(self):
        """append_chat_log with empty or whitespace-only content does nothing."""
        session = self._make_session()
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(direction="in", sender="Tom", content="")
            session.append_chat_log(direction="in", sender="Tom", content="   ")
            session.append_chat_log(direction="in", sender="Tom", content=None)
        assert session.chat_message_log == []

    def test_none_sender_substituted_with_unknown(self):
        """None sender is stored as 'unknown'."""
        session = self._make_session()
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(direction="in", sender=None, content="Hello")
        assert session.chat_message_log[0]["sender"] == "unknown"

    def test_empty_sender_substituted_with_unknown(self):
        """Empty string sender is stored as 'unknown'."""
        session = self._make_session()
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(direction="in", sender="", content="Hello")
        assert session.chat_message_log[0]["sender"] == "unknown"

    def test_save_failure_is_tolerated(self):
        """A save() failure must not raise — the caller continues normally."""
        session = self._make_session()
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", side_effect=RuntimeError("Redis down")),
        ):
            # Should not raise
            session.append_chat_log(direction="in", sender="Tom", content="Hello")

    def test_session_not_found_in_redis_falls_back_to_self(self):
        """If the re-fetch returns empty list, append still works on self."""
        session = self._make_session()
        with (
            patch.object(AgentSession.query, "filter", return_value=[]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(direction="in", sender="Tom", content="Hello")
        # Should have appended to self (fallback path)
        assert len(session.chat_message_log) == 1

    def test_out_direction_stored_correctly(self):
        """Outbound entries store direction='out' and sender='valor'."""
        session = self._make_session()
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(direction="out", sender="valor", content="Working on it.")
        entry = session.chat_message_log[0]
        assert entry["direction"] == "out"
        assert entry["sender"] == "valor"

    def test_custom_ts_is_preserved(self):
        """When ts is explicitly provided, it is stored verbatim."""
        session = self._make_session()
        fixed_ts = 1234567890.0
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(direction="in", sender="Tom", content="Hey", ts=fixed_ts)
        assert session.chat_message_log[0]["ts"] == fixed_ts

    def test_message_id_none_is_stored(self):
        """message_id=None is valid and stored as-is."""
        session = self._make_session()
        with (
            patch.object(AgentSession.query, "filter", return_value=[session]),
            patch.object(AgentSession, "save", MagicMock()),
        ):
            session.append_chat_log(direction="in", sender="Tom", content="Hi", message_id=None)
        assert session.chat_message_log[0]["message_id"] is None
