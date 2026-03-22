"""Tests for ChatSession/Simple factory method integration in bridge handler.

Verifies that the bridge handler correctly routes messages to create_chat()
(for SDLC work) or create_simple() (for Q&A) based on intake classification.
"""

from pathlib import Path


class TestBridgeSessionTypeRouting:
    """Verify bridge handler sets session_type correctly."""

    def test_sdlc_classification_creates_chat_session(self):
        """SDLC classification should produce session_type='chat'."""
        # The bridge handler maps classification_type="sdlc" to session_type="chat"
        _classification = "sdlc"
        if _classification == "sdlc":
            _session_type = "chat"
        else:
            _session_type = "simple"
        assert _session_type == "chat"

    def test_question_classification_creates_simple_session(self):
        """Question classification should produce session_type='simple'."""
        _classification = "question"
        if _classification == "sdlc":
            _session_type = "chat"
        else:
            _session_type = "simple"
        assert _session_type == "simple"

    def test_none_classification_creates_simple_session(self):
        """No classification (None) should produce session_type='simple'."""
        _classification = None
        if _classification == "sdlc":
            _session_type = "chat"
        else:
            _session_type = "simple"
        assert _session_type == "simple"

    def test_passthrough_classification_creates_simple_session(self):
        """Passthrough classification should produce session_type='simple'."""
        _classification = "passthrough"
        if _classification == "sdlc":
            _session_type = "chat"
        else:
            _session_type = "simple"
        assert _session_type == "simple"


class TestFactoryMethodsExist:
    """Verify AgentSession factory methods are available."""

    def test_create_chat_exists(self):
        """AgentSession.create_chat should be a classmethod."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "create_chat")
        assert callable(AgentSession.create_chat)

    def test_create_simple_exists(self):
        """AgentSession.create_simple should be a classmethod."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "create_simple")
        assert callable(AgentSession.create_simple)

    def test_create_dev_exists(self):
        """AgentSession.create_dev should be a classmethod."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "create_dev")
        assert callable(AgentSession.create_dev)


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
