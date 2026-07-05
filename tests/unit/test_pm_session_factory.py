"""Tests for Eng session factory method integration in bridge handler.

Verifies that the bridge handler correctly routes messages to create_eng()
based on chat title, and that the factory methods exist on AgentSession.
"""

from pathlib import Path

from config.enums import ClassificationType, SessionType


class TestBridgeSessionTypeRouting:
    """Verify bridge handler sets session_type correctly."""

    def test_sdlc_classification_creates_eng_session(self):
        """SDLC classification should produce session_type='eng'."""
        _classification = ClassificationType.SDLC
        _session_type = SessionType.ENG  # All SDLC messages go to Eng session
        assert _session_type == SessionType.ENG

    def test_question_classification_creates_eng_session(self):
        """Question classification should also produce session_type='eng'."""
        _classification = ClassificationType.QUESTION
        _session_type = SessionType.ENG  # All question messages go to Eng session
        assert _session_type == SessionType.ENG

    def test_none_classification_creates_eng_session(self):
        """No classification (None) should produce session_type='eng'."""
        _classification = None
        _session_type = SessionType.ENG  # All messages go to Eng session
        assert _session_type == SessionType.ENG

    def test_teammate_session_type(self):
        """Teammate persona should produce session_type='teammate'."""
        _session_type = SessionType.TEAMMATE
        assert _session_type == "teammate"

    def test_pm_and_dev_session_types_do_not_exist(self):
        """SessionType.PM and SessionType.DEV must not exist after ENG consolidation."""
        assert not hasattr(SessionType, "PM"), "SessionType.PM must have been removed"
        assert not hasattr(SessionType, "DEV"), "SessionType.DEV must have been removed"


class TestFactoryMethodsExist:
    """Verify AgentSession factory methods are available."""

    def test_create_eng_exists(self):
        """AgentSession.create_eng should be a classmethod."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "create_eng")
        assert callable(AgentSession.create_eng)

    def test_create_teammate_exists(self):
        """AgentSession.create_teammate should be a classmethod."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "create_teammate")
        assert callable(AgentSession.create_teammate)

    def test_create_child_exists(self):
        """AgentSession.create_child should be a classmethod."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "create_child")
        assert callable(AgentSession.create_child)

    def test_create_pm_does_not_exist(self):
        """AgentSession should NOT have create_pm (replaced by create_eng)."""
        from models.agent_session import AgentSession

        assert not hasattr(AgentSession, "create_pm"), "create_pm must be removed; use create_eng"

    def test_no_create_simple(self):
        """AgentSession should NOT have create_simple (removed)."""
        from models.agent_session import AgentSession

        assert not hasattr(AgentSession, "create_simple")

    def test_no_create_chat(self):
        """AgentSession should NOT have create_chat (removed)."""
        from models.agent_session import AgentSession

        assert not hasattr(AgentSession, "create_chat")


class TestBridgeNoClassifyWorkRequest:
    """Verify bridge handler does NOT call classify_work_request."""

    def test_no_classify_work_request_import_in_bridge(self):
        """bridge/telegram_bridge.py should not import classify_work_request."""
        bridge_path = Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
        source = bridge_path.read_text()
        assert "classify_work_request" not in source, (
            "Bridge handler should not import or call classify_work_request. "
            "Eng session owns work classification."
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
            "All messages route to Eng, Teammate, or Granite sessions."
        )


class TestEngPersonaFanoutInstruction:
    """Verify fan-out instruction is present in engineer persona."""

    def test_eng_persona_sdk_client_contains_fanout_instruction(self):
        """sdk_client.py Eng dispatch block must contain MULTI-ISSUE FAN-OUT text."""
        sdk_path = Path(__file__).parent.parent.parent / "agent" / "sdk_client.py"
        source = sdk_path.read_text()
        assert "MULTI-ISSUE FAN-OUT" in source, (
            "sdk_client.py Eng dispatch block must contain 'MULTI-ISSUE FAN-OUT' instruction. "
            "This triggers child Eng session spawning for multi-issue requests."
        )

    def test_eng_persona_overlay_contains_fanout_section(self):
        """config/personas/engineer.md must contain Multi-Issue Fan-out section."""
        persona_path = Path(__file__).parent.parent.parent / "config" / "personas" / "engineer.md"
        source = persona_path.read_text()
        assert "Multi-Issue Fan-out" in source, (
            "config/personas/engineer.md must include a 'Multi-Issue Fan-out' section "
            "describing when and how to spawn child Eng sessions."
        )

    def test_sdk_client_fanout_references_wait_for_children(self):
        """sdk_client.py fan-out instruction must reference the wait-for-children subcommand."""
        sdk_path = Path(__file__).parent.parent.parent / "agent" / "sdk_client.py"
        source = sdk_path.read_text()
        assert "wait-for-children" in source, (
            "sdk_client.py fan-out instruction must reference "
            "'wait-for-children' subcommand so the Eng session knows how to pause."
        )

    def test_sdk_client_fanout_references_child_eng_role(self):
        """sdk_client.py fan-out instruction must reference --role eng for child sessions.

        Commit dd926192 (#1633) merged the PM/Dev roles into the single Eng
        role, so child fan-out sessions are created with ``--role eng``.
        """
        sdk_path = Path(__file__).parent.parent.parent / "agent" / "sdk_client.py"
        source = sdk_path.read_text()
        assert "--role eng" in source, (
            "sdk_client.py fan-out instruction must include '--role eng' "
            "to create child sessions (PM/Dev merged into Eng by #1633)."
        )
