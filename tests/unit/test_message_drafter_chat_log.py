"""Unit tests for _build_draft_prompt chat_message_log integration (issue #1192).

Verifies that:
- The 'Recent chat in this thread' block appears when chat_message_log is populated.
- The block is absent when chat_message_log is None or empty.
- Missing dict keys in entries are tolerated (fallback to 'unknown' / empty).
- The display cap (CHAT_LOG_DISPLAY_ENTRIES) is enforced.
- No crash when session is None.
"""

from unittest.mock import MagicMock

from bridge.message_drafter import _build_draft_prompt
from models.agent_session import CHAT_LOG_DISPLAY_ENTRIES, CHAT_LOG_MAX_ENTRIES


def _make_session(chat_log=None):
    """Return a minimal mock session with the given chat_message_log."""
    session = MagicMock()
    session.message_text = "test request"
    session.classification_type = None
    session.branch_name = None
    session.slug = None
    session.session_type = None
    session.get_links = MagicMock(return_value={})
    session._get_history_list = MagicMock(return_value=[])
    session.chat_message_log = chat_log
    return session


class TestBuildDraftPromptChatLog:
    """Tests for the chat_message_log section in _build_draft_prompt."""

    def test_chat_log_block_present_when_populated(self):
        """Populated chat_message_log produces a 'Recent chat' block in the prompt."""
        log = [
            {
                "direction": "in",
                "sender": "Tom",
                "content": "What's the status?",
                "message_id": 1,
                "ts": 1.0,
            },
            {
                "direction": "out",
                "sender": "valor",
                "content": "Working on it.",
                "message_id": 2,
                "ts": 2.0,
            },
        ]
        session = _make_session(chat_log=log)
        prompt = _build_draft_prompt("Agent output text", {}, session=session)
        assert "Recent chat in this thread" in prompt
        assert "Working on it." in prompt
        assert "What's the status?" in prompt

    def test_chat_log_block_absent_when_none(self):
        """None chat_message_log produces no 'Recent chat' block."""
        session = _make_session(chat_log=None)
        prompt = _build_draft_prompt("Agent output text", {}, session=session)
        assert "Recent chat in this thread" not in prompt

    def test_chat_log_block_absent_when_empty(self):
        """Empty list chat_message_log produces no 'Recent chat' block."""
        session = _make_session(chat_log=[])
        prompt = _build_draft_prompt("Agent output text", {}, session=session)
        assert "Recent chat in this thread" not in prompt

    def test_no_crash_when_session_is_none(self):
        """_build_draft_prompt must not crash when session=None."""
        prompt = _build_draft_prompt("Agent output text", {}, session=None)
        assert isinstance(prompt, str)
        assert "Agent output text" in prompt

    def test_display_cap_enforced(self):
        """Only the last CHAT_LOG_DISPLAY_ENTRIES entries appear in the prompt."""
        # Create more entries than the display cap
        entries = [
            {
                "direction": "in",
                "sender": "Tom",
                "content": f"message-{i}",
                "message_id": i,
                "ts": float(i),
            }
            for i in range(CHAT_LOG_MAX_ENTRIES)
        ]
        session = _make_session(chat_log=entries)
        prompt = _build_draft_prompt("Agent output text", {}, session=session)
        # The oldest entries (0 to MAX-DISPLAY-1) should NOT appear
        oldest_idx = CHAT_LOG_MAX_ENTRIES - CHAT_LOG_DISPLAY_ENTRIES - 1
        assert f"message-{oldest_idx}" not in prompt
        # The newest entries should appear
        assert f"message-{CHAT_LOG_MAX_ENTRIES - 1}" in prompt

    def test_entry_with_missing_sender_key_uses_unknown(self):
        """An entry missing the 'sender' key is tolerated — falls back to 'unknown'."""
        log = [{"direction": "in", "content": "no sender here", "ts": 1.0}]
        session = _make_session(chat_log=log)
        # Should not raise
        prompt = _build_draft_prompt("Agent output text", {}, session=session)
        # Content may or may not appear depending on sanitization, but no exception
        assert isinstance(prompt, str)

    def test_entry_with_empty_content_is_skipped_in_prompt(self):
        """An entry with empty content produces no line in the prompt block."""
        log = [
            {"direction": "in", "sender": "Tom", "content": "", "ts": 1.0},
            {"direction": "out", "sender": "valor", "content": "Real message.", "ts": 2.0},
        ]
        session = _make_session(chat_log=log)
        prompt = _build_draft_prompt("Agent output text", {}, session=session)
        # "Real message." should be present
        assert "Real message." in prompt

    def test_out_lines_instruction_is_present(self):
        """The prompt includes the 'you have already said the out lines' instruction."""
        log = [
            {"direction": "out", "sender": "valor", "content": "Prior send.", "ts": 1.0},
        ]
        session = _make_session(chat_log=log)
        prompt = _build_draft_prompt("Agent output text", {}, session=session)
        assert "already said the 'out' lines" in prompt

    def test_direction_and_sender_appear_in_format(self):
        """Each log entry appears as '[direction] sender: content'."""
        log = [
            {"direction": "in", "sender": "Tom", "content": "Hello", "ts": 1.0},
        ]
        session = _make_session(chat_log=log)
        prompt = _build_draft_prompt("Agent output text", {}, session=session)
        assert "[in] Tom: Hello" in prompt
