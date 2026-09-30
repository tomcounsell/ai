"""Tests for CLI-harness permission mode and system-prompt configuration."""

import pytest

from agent.sdk_client import _HARNESS_COMMANDS, load_system_prompt


class TestHarnessPermissionMode:
    """The claude-cli harness always runs bypassPermissions (YOLO mode) --
    there is no per-session customization (plan #2000 Task 2.2: the
    deleted ValorAgent's configurable ``permission_mode`` constructor param
    had no CLI-harness equivalent; ``--permission-mode`` is a fixed
    constant baked into the argv template)."""

    def test_default_permission_mode_is_bypass(self):
        """The claude-cli harness command template always passes
        --permission-mode bypassPermissions."""
        cmd = _HARNESS_COMMANDS["claude-cli"]
        assert "--permission-mode" in cmd
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "bypassPermissions"


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
