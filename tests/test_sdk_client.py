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
    """Test ValorAgent with custom working directory."""
    agent = ValorAgent(working_dir="/tmp")
    assert str(agent.working_dir) == "/tmp"


def test_valor_agent_custom_permission_mode():
    """Test ValorAgent with custom permission mode."""
    agent = ValorAgent(permission_mode="default")
    assert agent.permission_mode == "default"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"
)
async def test_sdk_query_simple():
    """Test a simple SDK query (requires API key)."""
    agent = ValorAgent()
    response = await agent.query("What is 2 + 2? Reply with just the number.")
    assert response is not None
    assert "4" in response


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"
)
async def test_get_agent_response_sdk():
    """Test the bridge-compatible function (requires API key)."""
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
