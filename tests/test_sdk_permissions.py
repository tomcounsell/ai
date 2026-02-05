"""Tests for SDK client permissions and configuration."""

import importlib.util
import sys
from pathlib import Path

import pytest

# Add user site-packages for claude_agent_sdk
user_site = Path.home() / "Library/Python/3.12/lib/python/site-packages"
if user_site.exists() and str(user_site) not in sys.path:
    sys.path.insert(0, str(user_site))

# Direct import to avoid __init__.py dependency issues
sdk_client_path = Path(__file__).parent.parent / "agent" / "sdk_client.py"
spec = importlib.util.spec_from_file_location("sdk_client", sdk_client_path)
sdk_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk_client)
ValorAgent = sdk_client.ValorAgent
load_system_prompt = sdk_client.load_system_prompt


class TestValorAgentConfig:
    """Test ValorAgent configuration."""

    def test_default_permission_mode_is_bypass(self):
        """Test that default permission mode is bypassPermissions (YOLO mode)."""
        agent = ValorAgent()
        assert agent.permission_mode == "bypassPermissions"

    def test_can_set_custom_permission_mode(self):
        """Test that permission mode can be customized."""
        agent = ValorAgent(permission_mode="acceptEdits")
        assert agent.permission_mode == "acceptEdits"

    def test_system_prompt_loaded(self):
        """Test that system prompt is loaded from SOUL.md."""
        agent = ValorAgent()
        assert agent.system_prompt is not None
        assert len(agent.system_prompt) > 100
        # Should contain key Valor identity markers
        assert "Valor" in agent.system_prompt

    def test_working_dir_defaults_to_repo_root(self):
        """Test that working directory defaults to ai/ repo root."""
        agent = ValorAgent()
        assert agent.working_dir.exists()
        assert (agent.working_dir / "CLAUDE.md").exists()

    def test_create_options_has_correct_settings(self):
        """Test that _create_options creates valid ClaudeAgentOptions."""
        agent = ValorAgent()
        options = agent._create_options(session_id="test_session")

        assert options.permission_mode == "bypassPermissions"
        assert options.system_prompt is not None
        assert options.cwd is not None
        assert options.continue_conversation is True
        assert options.resume == "test_session"

    def test_create_options_without_session(self):
        """Test options creation without session ID."""
        agent = ValorAgent()
        options = agent._create_options(session_id=None)

        assert options.continue_conversation is False
        assert options.resume is None


class TestSystemPrompt:
    """Test system prompt loading."""

    def test_load_system_prompt_returns_string(self):
        """Test that load_system_prompt returns a non-empty string."""
        prompt = load_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_system_prompt_contains_git_autonomy(self):
        """Test that system prompt contains git autonomy language."""
        prompt = load_system_prompt()
        # Should have explicit override of Claude Code defaults
        assert "git" in prompt.lower()
        assert "autonomous" in prompt.lower() or "YOLO" in prompt

    def test_system_prompt_contains_full_access(self):
        """Test that system prompt grants full system access."""
        prompt = load_system_prompt()
        assert "full" in prompt.lower()
        assert "access" in prompt.lower() or "permission" in prompt.lower()


class TestClaudeAgentOptionsValidity:
    """Test that ClaudeAgentOptions accepts our configuration."""

    def test_bypass_permissions_is_valid_mode(self):
        """Test that bypassPermissions is a valid permission mode."""
        try:
            from claude_agent_sdk import ClaudeAgentOptions
        except ImportError:
            pytest.skip("claude_agent_sdk not available")

        # Should not raise
        options = ClaudeAgentOptions(permission_mode="bypassPermissions")
        assert options.permission_mode == "bypassPermissions"

    def test_options_creation_no_errors(self):
        """Test that creating options doesn't raise any errors."""
        try:
            from claude_agent_sdk import ClaudeAgentOptions
        except ImportError:
            pytest.skip("claude_agent_sdk not available")

        agent = ValorAgent()
        # Should not raise
        options = agent._create_options()
        assert options is not None

    def test_all_options_are_valid_parameters(self):
        """Test that all options we set are valid ClaudeAgentOptions parameters."""
        try:
            import inspect

            from claude_agent_sdk import ClaudeAgentOptions
        except ImportError:
            pytest.skip("claude_agent_sdk not available")

        # Get valid parameters
        sig = inspect.signature(ClaudeAgentOptions.__init__)
        valid_params = set(sig.parameters.keys()) - {"self"}

        # Our options
        agent = ValorAgent()
        options = agent._create_options(session_id="test")

        # Check that we're not setting any invalid attributes
        # (This would have raised an error during creation, but let's be explicit)
        our_params = {
            "system_prompt",
            "cwd",
            "permission_mode",
            "continue_conversation",
            "resume",
            "env",
        }

        for param in our_params:
            assert param in valid_params, f"Invalid parameter: {param}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
