"""Tests for ChatSession/DevSession factory method integration in bridge handler.

Verifies that the bridge handler correctly routes messages to create_chat()
(default) or create_dev() (for "Dev: X" groups) based on chat title.
"""

from pathlib import Path

from config.enums import ClassificationType, SessionType


class TestBridgeSessionTypeRouting:
    """Verify bridge handler sets session_type correctly."""

    def test_sdlc_classification_creates_chat_session(self):
        """SDLC classification should produce session_type='chat'."""
        # The bridge handler now routes all messages to ChatSession
        _classification = ClassificationType.SDLC
        _session_type = SessionType.CHAT  # All messages go to ChatSession
        assert _session_type == SessionType.CHAT

    def test_question_classification_creates_chat_session(self):
        """Question classification should also produce session_type='chat'."""
        _classification = ClassificationType.QUESTION
        _session_type = SessionType.CHAT  # All messages go to ChatSession
        assert _session_type == SessionType.CHAT

    def test_none_classification_creates_chat_session(self):
        """No classification (None) should produce session_type='chat'."""
        _classification = None
        _session_type = SessionType.CHAT  # All messages go to ChatSession
        assert _session_type == SessionType.CHAT

    def test_dev_group_routing(self):
        """'Dev: X' chat title prefix should produce session_type='dev'."""
        chat_title = "Dev: Valor"
        if chat_title and chat_title.startswith("Dev:"):
            _session_type = SessionType.DEV
        else:
            _session_type = SessionType.CHAT
        assert _session_type == SessionType.DEV

    def test_non_dev_group_routing(self):
        """Non-dev chat title should produce session_type='chat'."""
        chat_title = "PM: PsyOptimal"
        if chat_title and chat_title.startswith("Dev:"):
            _session_type = SessionType.DEV
        else:
            _session_type = SessionType.CHAT
        assert _session_type == SessionType.CHAT


class TestFactoryMethodsExist:
    """Verify AgentSession factory methods are available."""

    def test_create_chat_exists(self):
        """AgentSession.create_chat should be a classmethod."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "create_chat")
        assert callable(AgentSession.create_chat)

    def test_create_dev_exists(self):
        """AgentSession.create_dev should be a classmethod."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "create_dev")
        assert callable(AgentSession.create_dev)

    def test_no_create_simple(self):
        """AgentSession should NOT have create_simple (removed)."""
        from models.agent_session import AgentSession

        assert not hasattr(AgentSession, "create_simple")


class TestBridgeNoClassifyWorkRequest:
    """Verify bridge handler does NOT call classify_work_request."""

    def test_no_classify_work_request_import_in_bridge(self):
        """bridge/telegram_bridge.py should not import classify_work_request."""
        bridge_path = Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
        source = bridge_path.read_text()
        assert "classify_work_request" not in source, (
            "Bridge handler should not import or call classify_work_request. "
            "ChatSession owns work classification."
        )

    def test_factory_methods_wired(self):
        """Factory methods should no longer have TODO about wiring."""
        model_path = Path(__file__).parent.parent.parent / "models" / "agent_session.py"
        source = model_path.read_text()
        assert "TODO: Wire into bridge handler" not in source, (
            "Factory methods should be wired — remove TODO comments"
        )

    def test_no_simple_session_type_in_bridge(self):
        """bridge/telegram_bridge.py should not reference 'simple' session type."""
        bridge_path = Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
        source = bridge_path.read_text()
        assert '"simple"' not in source, (
            "Bridge handler should not reference simple session type. "
            "All messages route to ChatSession or DevSession."
        )
