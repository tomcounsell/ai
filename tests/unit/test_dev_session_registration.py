"""Tests for DevSession registration via PreToolUse and SubagentStop hooks.

Verifies that:
- PreToolUse detects Agent tool calls with dev-session subagent_type and creates AgentSession
- SubagentStop logs completion for dev-session agents
- Non-dev-session agents are ignored by both hooks
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_hook_context():
    """Minimal HookContext mock."""
    ctx = MagicMock()
    ctx.session_id = "test-session-123"
    return ctx


@pytest.fixture
def parent_session_env(monkeypatch):
    """Set VALOR_SESSION_ID env var to simulate a parent ChatSession."""
    monkeypatch.setenv("VALOR_SESSION_ID", "parent-chat-session-abc")


@pytest.fixture
def parent_session_registry():
    """Register a session in the session registry to simulate a parent ChatSession."""
    from agent.hooks import session_registry

    session_registry._reset_for_testing()
    # Pre-register and promote so resolve("sdk-session-1") returns the bridge session ID
    session_registry.register_pending("parent-chat-session-abc")
    session_registry.complete_registration("sdk-session-1")
    yield
    session_registry._reset_for_testing()


class TestPreToolUseDevSessionDetection:
    """PreToolUse hook should detect Agent tool calls spawning dev-sessions."""

    def _make_agent_input(self, subagent_type="dev-session", prompt="Build the feature"):
        return {
            "session_id": "sdk-session-1",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": "/Users/test/src/ai",
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {"type": subagent_type, "prompt": prompt},
            "tool_use_id": "tool-use-123",
        }

    def test_detects_agent_tool_with_dev_session_type(
        self, mock_hook_context, parent_session_registry
    ):
        """When tool_name=Agent and tool_input contains type=dev-session,
        creates an AgentSession with session_type=dev and correct parent."""
        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_agent_input()

        with patch("models.agent_session.AgentSession.create_dev") as mock_create:
            mock_create.return_value = MagicMock(agent_session_id="dev-job-1")

            result = asyncio.run(pre_tool_use_hook(input_data, "tool-use-123", mock_hook_context))

            # Should have called create_dev with parent linkage
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["parent_chat_session_id"] == "parent-chat-session-abc"

            # Should not block the tool call
            assert result.get("decision") != "block"

    def test_ignores_agent_tool_with_non_dev_session_type(
        self, mock_hook_context, parent_session_env
    ):
        """When Agent tool is called with a different subagent type, no DevSession is created."""
        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_agent_input(subagent_type="code-reviewer", prompt="Review the PR")

        with patch("models.agent_session.AgentSession.create_dev") as mock_create:
            result = asyncio.run(pre_tool_use_hook(input_data, "tool-use-456", mock_hook_context))

            mock_create.assert_not_called()
            assert result == {}

    def test_ignores_non_agent_tools(self, mock_hook_context, parent_session_env):
        """Non-Agent tools (Write, Edit, Bash) should not trigger DevSession creation."""
        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = {
            "session_id": "sdk-session-1",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": "/Users/test/src/ai",
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/test.py", "content": "print('hi')"},
            "tool_use_id": "tool-use-789",
        }

        with patch("models.agent_session.AgentSession.create_dev") as mock_create:
            asyncio.run(pre_tool_use_hook(input_data, "tool-use-789", mock_hook_context))

            mock_create.assert_not_called()

    def test_no_parent_session_id_skips_registration(self, mock_hook_context):
        """When no session is in the registry, DevSession registration is skipped."""
        from agent.hooks import session_registry
        from agent.hooks.pre_tool_use import pre_tool_use_hook

        session_registry._reset_for_testing()

        input_data = self._make_agent_input()

        with patch("models.agent_session.AgentSession.create_dev") as mock_create:
            asyncio.run(pre_tool_use_hook(input_data, "tool-use-000", mock_hook_context))

            mock_create.assert_not_called()


class TestCreateLocalFactory:
    """AgentSession.create_local() should create a local CLI session."""

    def test_creates_session_with_correct_fields(self):
        """create_local() sets session_type=dev, status, and required fields."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session = AgentSession.create_local(
                session_id="local-abc-123",
                project_key="dm",
                working_dir="/Users/test/src/ai",
            )

            assert session.session_id == "local-abc-123"
            assert session.session_type == "dev"
            assert session.project_key == "dm"
            assert session.working_dir == "/Users/test/src/ai"
            assert session.created_at is not None

    def test_telegram_fields_are_null(self):
        """Local sessions have no Telegram context."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session = AgentSession.create_local(
                session_id="local-xyz",
                project_key="dm",
                working_dir="/tmp",
            )

            assert session.chat_id.startswith("local")
            assert session.telegram_message_id is None
            assert session.sender_name is None
            assert session.parent_chat_session_id is None

    def test_accepts_kwargs(self):
        """create_local() passes extra kwargs to the model."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session = AgentSession.create_local(
                session_id="local-kw",
                project_key="dm",
                working_dir="/tmp",
                status="running",
                message_text="test prompt",
            )

            assert session.status == "running"
            assert session.message_text == "test prompt"

    def test_calls_save(self):
        """create_local() persists the session to Redis."""
        with patch("models.agent_session.AgentSession.save") as mock_save:
            from models.agent_session import AgentSession

            AgentSession.create_local(
                session_id="local-save",
                project_key="dm",
                working_dir="/tmp",
            )

            mock_save.assert_called_once()


class TestSubagentStopDevSessionCompletion:
    """SubagentStop hook should log DevSession completion and update status."""

    def _make_stop_input(self, agent_type="dev-session", agent_id="dev-agent-xyz"):
        return {
            "session_id": "sdk-session-1",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": "/Users/test/src/ai",
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "agent_id": agent_id,
            "agent_transcript_path": "/tmp/dev-transcript.jsonl",
            "agent_type": agent_type,
        }

    def test_calls_register_for_dev_session(self, mock_hook_context, parent_session_env):
        """When agent_type=dev-session, hook calls _register_dev_session_completion."""
        from agent.hooks.subagent_stop import subagent_stop_hook

        input_data = self._make_stop_input()

        with patch("agent.hooks.subagent_stop._register_dev_session_completion") as mock_register:
            result = asyncio.run(subagent_stop_hook(input_data, None, mock_hook_context))

            mock_register.assert_called_once_with(
                "dev-agent-xyz", input_data=input_data, claude_uuid="sdk-session-1"
            )
            assert result == {}

    def test_ignores_non_dev_session_agents(self, mock_hook_context):
        """When agent_type is not dev-session, no registration occurs."""
        from agent.hooks.subagent_stop import subagent_stop_hook

        input_data = self._make_stop_input(agent_type="code-reviewer", agent_id="reviewer-abc")

        with patch("agent.hooks.subagent_stop._register_dev_session_completion") as mock_register:
            result = asyncio.run(subagent_stop_hook(input_data, None, mock_hook_context))

            mock_register.assert_not_called()
            assert result == {}
