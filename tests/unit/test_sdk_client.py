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
    """Test that SOUL.md can be loaded."""
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
@pytest.mark.skipif(not _sdk_available(), reason="Claude Agent SDK binary not available")
async def test_get_agent_response_sdk():
    """Test the bridge-compatible function (requires API key and claude CLI)."""
    from agent.sdk_client import get_agent_response_sdk

    response = await get_agent_response_sdk(
        message="What is the capital of France? Reply with just the city name.",
        session_id="test_session_123",
        sender_name="Test User",
        chat_title="Test Chat",
        project=None,
        chat_id="12345",
    )
    assert response is not None
    assert "Paris" in response
