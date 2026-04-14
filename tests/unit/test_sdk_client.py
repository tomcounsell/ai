"""
Test Claude Agent SDK client integration.

Run with: pytest tests/test_sdk_client.py -v
"""

import os
import sys

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.sdk_client import ValorAgent, load_system_prompt


def test_load_system_prompt():
    """Test that system prompt can be loaded from persona segments."""
    prompt = load_system_prompt()
    assert prompt is not None
    assert len(prompt) > 100
    assert "Valor" in prompt


def test_valor_agent_init():
    """Test ValorAgent initialization."""
    agent = ValorAgent()
    assert agent.system_prompt is not None
    assert agent.working_dir.exists()
    assert agent.permission_mode == "bypassPermissions"


def test_valor_agent_custom_working_dir():
    """Test ValorAgent with custom working directory within allowed root."""
    from pathlib import Path

    ai_dir = str(Path.home() / "src/ai")
    agent = ValorAgent(working_dir=ai_dir)
    assert str(agent.working_dir) == ai_dir


def test_valor_agent_rejects_unsafe_working_dir():
    """Test ValorAgent falls back to safe default for paths outside allowed root."""
    from pathlib import Path

    agent = ValorAgent(working_dir="/tmp")
    # Safety invariant should reject /tmp and fall back to allowed root
    assert str(agent.working_dir) == str(Path.home() / "src")


def test_valor_agent_custom_permission_mode():
    """Test ValorAgent with custom permission mode."""
    agent = ValorAgent(permission_mode="default")
    assert agent.permission_mode == "default"


def _sdk_available():
    """Check if the real Claude Agent SDK binary is usable (not just importable)."""
    import shutil

    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    if not shutil.which("claude"):
        return False
    try:
        import claude_agent_sdk

        # If it's a MagicMock (from conftest), not the real SDK
        if not hasattr(claude_agent_sdk, "create_session"):
            return False
    except ImportError:
        return False
    return True


@pytest.mark.asyncio
@pytest.mark.skipif(not _sdk_available(), reason="Claude Agent SDK binary not available")
async def test_sdk_query_simple():
    """Test a simple SDK query (requires API key and claude CLI)."""
    agent = ValorAgent()
    response = await agent.query("What is 2 + 2? Reply with just the number.")
    assert response is not None
    assert "4" in response


class TestTelegramEnvInjection:
    """Tests for TELEGRAM_CHAT_ID and TELEGRAM_REPLY_TO env var injection (issue #497)."""

    def test_chat_session_injects_telegram_chat_id(self):
        """PM session should inject TELEGRAM_CHAT_ID from chat_id."""
        agent = ValorAgent(
            chat_id="12345",
            session_type="pm",
        )
        options = agent._create_options(session_id=None)
        assert options.env.get("TELEGRAM_CHAT_ID") == "12345"

    def test_non_chat_session_no_telegram_chat_id(self):
        """Non-chat sessions should not inject TELEGRAM_CHAT_ID."""
        agent = ValorAgent(
            chat_id="12345",
            session_type=None,
        )
        options = agent._create_options(session_id=None)
        assert "TELEGRAM_CHAT_ID" not in options.env

    def test_chat_session_without_chat_id_no_injection(self):
        """PM session without chat_id should not inject TELEGRAM_CHAT_ID."""
        agent = ValorAgent(
            chat_id=None,
            session_type="pm",
        )
        options = agent._create_options(session_id=None)
        assert "TELEGRAM_CHAT_ID" not in options.env

    def test_session_type_injected(self):
        """SESSION_TYPE env var should be set for chat sessions."""
        agent = ValorAgent(session_type="pm")
        options = agent._create_options(session_id=None)
        assert options.env.get("SESSION_TYPE") == "pm"


@pytest.mark.asyncio
async def test_build_harness_turn_input_basic():
    """Test build_harness_turn_input produces correct context headers."""
    from unittest.mock import patch

    with patch("bridge.context.build_context_prefix", return_value="PROJECT: test"):
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="Hello world",
            session_id="test-session-123",
            sender_name="Test User",
            chat_title="Test Chat",
            project={"name": "Test", "_key": "test"},
            task_list_id="task-list-1",
            session_type="dev",
            sender_id=12345,
        )

    assert "PROJECT: test" in result
    assert "FROM: Test User" in result
    assert "SESSION_ID: test-session-123" in result
    assert "TASK_SCOPE: task-list-1" in result
    assert "SCOPE:" in result
    assert "MESSAGE: Hello world" in result


@pytest.mark.asyncio
async def test_build_harness_turn_input_none_sender():
    """build_harness_turn_input with sender_name=None must not produce FROM: None."""
    from unittest.mock import patch

    with patch("bridge.context.build_context_prefix", return_value="CONTEXT"):
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="Hello",
            session_id="test-session",
            sender_name=None,
            chat_title=None,
            project=None,
            task_list_id=None,
            session_type="teammate",
            sender_id=None,
        )

    assert "FROM: None" not in result
    assert "FROM:" not in result


class TestApplyContextBudget:
    """Tests for _apply_context_budget() harness input trimming (issue #958)."""

    def test_noop_when_under_budget(self):
        """Messages under the budget are returned unchanged."""
        from agent.sdk_client import _apply_context_budget

        msg = "SHORT MESSAGE"
        assert _apply_context_budget(msg, max_chars=1000) == msg

    def test_trim_removes_oldest_prefix(self):
        """When over budget, oldest content (start of string) is trimmed."""
        from agent.sdk_client import _apply_context_budget

        msg = "A" * 500 + "\nMESSAGE: keep this"
        result = _apply_context_budget(msg, max_chars=100)
        assert "keep this" in result
        assert len(result) <= 100 + len(
            "[CONTEXT TRIMMED — oldest context omitted to fit harness budget]\n"
        )

    def test_message_boundary_preserved(self):
        """Everything from the final MESSAGE: marker onward is preserved."""
        from agent.sdk_client import _apply_context_budget

        prefix = "X" * 1000
        tail = "\nMESSAGE: do the thing"
        msg = prefix + tail
        result = _apply_context_budget(msg, max_chars=100)
        assert result.endswith(tail)

    def test_trim_marker_injected(self):
        """Trimmed messages get a trim marker prepended."""
        from agent.sdk_client import _apply_context_budget

        msg = "A" * 500 + "\nMESSAGE: keep"
        result = _apply_context_budget(msg, max_chars=100)
        assert result.startswith("[CONTEXT TRIMMED")

    def test_empty_input_passthrough(self):
        """Empty string returns empty string."""
        from agent.sdk_client import _apply_context_budget

        assert _apply_context_budget("", max_chars=100) == ""

    def test_steering_only_exceeds_budget_passthrough(self):
        """If MESSAGE: tail alone exceeds budget, pass through unchanged."""
        from agent.sdk_client import _apply_context_budget

        msg = "CTX\nMESSAGE: " + "B" * 200
        result = _apply_context_budget(msg, max_chars=50)
        # Should pass through unchanged because tail alone exceeds budget
        assert result == msg

    def test_no_marker_trim_from_start(self):
        """Without a MESSAGE: marker, trim from start of string."""
        from agent.sdk_client import _apply_context_budget

        msg = "A" * 200
        result = _apply_context_budget(msg, max_chars=50)
        assert result.startswith("[CONTEXT TRIMMED]")
        assert len(result) <= 50 + len("[CONTEXT TRIMMED]\n")
