"""Unit tests for agent.agent_session_queue helpers.

Focused on field-extraction semantics used by delete-and-recreate callers
(retry, orphan fix, continuation fallback). _pop_agent_session itself uses
in-place mutation via transition_status() and does NOT go through
_extract_agent_session_fields.
"""

from datetime import UTC, datetime

from agent.agent_session_queue import _AGENT_SESSION_FIELDS, _extract_agent_session_fields
from models.agent_session import AgentSession


def _make_session(**overrides) -> AgentSession:
    """Build an unsaved AgentSession with sensible defaults."""
    defaults = {
        "project_key": "test",
        "status": "pending",
        "priority": "normal",
        "created_at": datetime.now(tz=UTC),
        "session_id": "unit-test",
        "working_dir": "/tmp/test",
        "chat_id": "123",
        "message_text": "hello",
        "sender_name": "Tester",
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession(**defaults)


class TestExtractFieldsMessageTextRoundTrip:
    """_extract_agent_session_fields must preserve message_text across
    delete-and-recreate via the initial_telegram_message dict.

    message_text is a virtual @property on AgentSession that reads from
    initial_telegram_message["message_text"]. _AGENT_SESSION_FIELDS does not
    include message_text directly -- it includes initial_telegram_message,
    so the value is preserved transitively when the dict is copied.
    """

    def test_message_text_roundtrips_via_initial_telegram_message(self):
        """Round-trip: extract -> create new record -> .message_text matches."""
        original = _make_session(message_text="the-original-message")
        assert original.message_text == "the-original-message"

        fields = _extract_agent_session_fields(original)

        # message_text is NOT a top-level key in the extracted dict; it lives
        # inside initial_telegram_message.
        assert "message_text" not in fields
        assert "initial_telegram_message" in fields
        assert fields["initial_telegram_message"]["message_text"] == "the-original-message"

        # Recreate and verify the virtual property resolves correctly.
        recreated = AgentSession(**fields)
        assert recreated.message_text == "the-original-message"

    def test_message_text_none_roundtrips_safely(self):
        """When message_text is None / unset, extraction and recreation
        must not raise."""
        original = _make_session()
        # Clear the text explicitly
        original.initial_telegram_message = None

        fields = _extract_agent_session_fields(original)
        # initial_telegram_message may be None; recreation should still work
        recreated = AgentSession(**fields)
        # No crash; .message_text returns None for empty dict
        assert recreated.message_text in (None, "")

    def test_scheduling_depth_intentionally_omitted(self):
        """_AGENT_SESSION_FIELDS must NOT include scheduling_depth.

        scheduling_depth is a derived @property that walks the
        parent_agent_session_id chain at read time. Including it in the
        extracted dict would attempt to set a read-only property on recreate.
        """
        assert "scheduling_depth" not in _AGENT_SESSION_FIELDS

    def test_agent_session_id_intentionally_omitted(self):
        """_AGENT_SESSION_FIELDS must NOT include agent_session_id / id.

        agent_session_id is the AutoKeyField; delete-and-recreate callers
        rely on a fresh auto-generated ID for the new record.
        """
        assert "agent_session_id" not in _AGENT_SESSION_FIELDS
        assert "id" not in _AGENT_SESSION_FIELDS
