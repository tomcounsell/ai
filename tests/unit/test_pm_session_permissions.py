"""Tests for PM (ChatSession) permission boundaries.

Verifies that:
- PM sessions get SESSION_TYPE=chat env var
- PM sessions get SENTRY_AUTH_TOKEN injected from ~/Desktop/Valor/.env
- PM sessions use bypassPermissions (not plan mode)
- PreToolUse hook blocks PM writes outside docs/
- PreToolUse hook allows PM writes inside docs/
- Non-PM sessions are not affected by PM write restrictions
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# --- PreToolUse hook: PM write restriction tests ---


class TestPMWriteRestriction:
    """PreToolUse hook should block PM writes outside docs/."""

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock()
        ctx.session_id = "test-pm-session"
        return ctx

    def _make_write_input(self, file_path, tool_name="Write"):
        return {
            "session_id": "sdk-session-1",
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path, "content": "test"},
            "tool_use_id": "tool-use-write",
        }

    def test_pm_blocked_from_writing_source_code(self, mock_context, monkeypatch):
        """PM session cannot write to source code files."""
        monkeypatch.setenv("SESSION_TYPE", "chat")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/agent/sdk_client.py")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-1", mock_context))

        assert result.get("decision") == "block"
        assert "docs/" in result.get("reason", "")

    def test_pm_blocked_from_editing_source_code(self, mock_context, monkeypatch):
        """PM session cannot edit source code files."""
        monkeypatch.setenv("SESSION_TYPE", "chat")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input(
            "/Users/test/src/ai/bridge/telegram_bridge.py", tool_name="Edit"
        )
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-2", mock_context))

        assert result.get("decision") == "block"

    def test_pm_allowed_to_write_docs(self, mock_context, monkeypatch):
        """PM session can write to docs/ directory."""
        monkeypatch.setenv("SESSION_TYPE", "chat")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/docs/features/new-feature.md")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-3", mock_context))

        assert result.get("decision") != "block"

    def test_pm_allowed_to_write_nested_docs(self, mock_context, monkeypatch):
        """PM session can write to nested paths under docs/."""
        monkeypatch.setenv("SESSION_TYPE", "chat")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/docs/plans/my-plan.md")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-4", mock_context))

        assert result.get("decision") != "block"

    def test_non_pm_session_can_write_anywhere(self, mock_context, monkeypatch):
        """Non-PM sessions (no SESSION_TYPE) are not restricted."""
        monkeypatch.delenv("SESSION_TYPE", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/agent/sdk_client.py")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-5", mock_context))

        assert result.get("decision") != "block"

    def test_dev_session_type_can_write_anywhere(self, mock_context, monkeypatch):
        """SESSION_TYPE=dev is not restricted by PM rules."""
        monkeypatch.setenv("SESSION_TYPE", "dev")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/agent/sdk_client.py")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-6", mock_context))

        assert result.get("decision") != "block"

    def test_pm_sensitive_file_still_blocked(self, mock_context, monkeypatch):
        """PM session writing to .env is blocked by sensitive path check (not PM check)."""
        monkeypatch.setenv("SESSION_TYPE", "chat")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/.env")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-7", mock_context))

        assert result.get("decision") == "block"
        assert "sensitive" in result.get("reason", "").lower()


# --- SDK client: session_type and env var injection tests ---


class TestPMSessionEnvInjection:
    """ValorAgent should inject correct env vars for PM sessions."""

    def test_chat_session_gets_session_type_env(self):
        """ChatSession (session_type='chat') injects SESSION_TYPE=chat."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="chat")
        options = agent._create_options(session_id="test-session")

        assert options.env.get("SESSION_TYPE") == "chat"

    def test_non_chat_session_no_session_type_env(self):
        """Non-chat sessions don't inject SESSION_TYPE."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        options = agent._create_options(session_id="test-session")

        assert "SESSION_TYPE" not in options.env

    def test_sentry_token_injected_for_chat_session(self, tmp_path):
        """Chat sessions get SENTRY_AUTH_TOKEN from ~/Desktop/Valor/.env."""
        # Create a fake ~/Desktop/Valor/.env
        valor_dir = tmp_path / "Desktop" / "Valor"
        valor_dir.mkdir(parents=True)
        (valor_dir / ".env").write_text("SENTRY_PERSONAL_TOKEN=test-sentry-token-abc\n")

        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="chat")

        with patch("agent.sdk_client.Path.home", return_value=tmp_path):
            options = agent._create_options(session_id="test-session")

        assert options.env.get("SENTRY_AUTH_TOKEN") == "test-sentry-token-abc"

    def test_sentry_token_not_injected_for_non_chat(self):
        """Non-chat sessions don't get SENTRY_AUTH_TOKEN."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        options = agent._create_options(session_id="test-session")

        assert "SENTRY_AUTH_TOKEN" not in options.env

    def test_sentry_token_missing_file_no_error(self, tmp_path):
        """If ~/Desktop/Valor/.env doesn't exist, no error and no token."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="chat")

        with patch("agent.sdk_client.Path.home", return_value=tmp_path):
            options = agent._create_options(session_id="test-session")

        assert "SENTRY_AUTH_TOKEN" not in options.env

    def test_sentry_token_missing_key_no_error(self, tmp_path):
        """If .env exists but has no SENTRY_PERSONAL_TOKEN, no token injected."""
        valor_dir = tmp_path / "Desktop" / "Valor"
        valor_dir.mkdir(parents=True)
        (valor_dir / ".env").write_text("SOME_OTHER_KEY=value\n")

        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="chat")

        with patch("agent.sdk_client.Path.home", return_value=tmp_path):
            options = agent._create_options(session_id="test-session")

        assert "SENTRY_AUTH_TOKEN" not in options.env


class TestPMPermissionMode:
    """ChatSession should use bypassPermissions, not plan mode."""

    def test_chat_session_not_using_plan_mode(self):
        """Verify sdk_client does NOT set plan mode for chat sessions."""
        sdk_path = Path(__file__).parent.parent.parent / "agent" / "sdk_client.py"
        source = sdk_path.read_text()

        # Extract the chat session block (handles both enum and string forms)
        if "if _session_type == SessionType.CHAT:" in source:
            chat_block = source.split("if _session_type == SessionType.CHAT:")[1].split("elif")[0]
        else:
            chat_block = source.split('if _session_type == "chat":')[1].split("elif")[0]
        assert '"plan"' not in chat_block, (
            "ChatSession should not use plan permission mode. "
            "PM needs bypassPermissions with hook-based write restrictions."
        )

    def test_default_permission_mode_is_bypass(self):
        """Default permission mode should be bypassPermissions."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        assert agent.permission_mode == "bypassPermissions"

    def test_chat_session_inherits_default_bypass(self):
        """Chat session with no explicit permission_mode gets bypassPermissions."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="chat")
        assert agent.permission_mode == "bypassPermissions"
