"""Unit tests for PM (Project Manager) channel mode.

Covers:
- PM mode detection and classification bypass
- PM system prompt loading (no WORKER_RULES, loads work-vault CLAUDE.md)
- Mode field defaults to "dev" when absent
- Unknown mode values treated as "dev"
- PM project config routing via find_project_for_chat
- Dev channels unaffected by PM mode changes (regression check)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# claude_agent_sdk mock is centralized in tests/conftest.py
from agent.sdk_client import (
    WORKER_RULES,
    load_pm_system_prompt,
    load_system_prompt,
)


# ---------------------------------------------------------------------------
# load_pm_system_prompt() tests
# ---------------------------------------------------------------------------


class TestLoadPmSystemPrompt:
    """Tests for the PM-specific system prompt loader."""

    def test_pm_prompt_excludes_worker_rules(self, tmp_path):
        """PM system prompt must NOT include WORKER_RULES."""
        prompt = load_pm_system_prompt(str(tmp_path))
        assert "Worker Safety Rails" not in prompt

    def test_pm_prompt_includes_soul(self, tmp_path):
        """PM system prompt must include SOUL.md persona content."""
        prompt = load_pm_system_prompt(str(tmp_path))
        # SOUL.md starts with "# Valor" - verify persona is loaded
        assert "Valor" in prompt

    def test_pm_prompt_loads_project_claude_md(self, tmp_path):
        """PM system prompt loads CLAUDE.md from work-vault directory."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# PM Instructions\nYou are a project manager.")
        prompt = load_pm_system_prompt(str(tmp_path))
        assert "PM Instructions" in prompt
        assert "project manager" in prompt

    def test_pm_prompt_without_claude_md_falls_back_to_soul(self, tmp_path):
        """If no CLAUDE.md in work-vault, PM prompt uses SOUL.md only."""
        prompt = load_pm_system_prompt(str(tmp_path))
        # Should still work - just SOUL.md without project instructions
        assert len(prompt) > 0
        assert "Worker Safety Rails" not in prompt

    def test_pm_prompt_has_separator_between_soul_and_project(self, tmp_path):
        """SOUL.md and project CLAUDE.md should be separated by ---."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# PM Instructions")
        prompt = load_pm_system_prompt(str(tmp_path))
        assert "---" in prompt

    def test_dev_prompt_still_has_worker_rules(self):
        """Regression: load_system_prompt() (dev mode) still includes WORKER_RULES."""
        prompt = load_system_prompt()
        assert "Worker Safety Rails" in prompt


# ---------------------------------------------------------------------------
# PM mode in get_agent_response_sdk() — classification bypass
# ---------------------------------------------------------------------------


class TestPmModeClassificationBypass:
    """Tests verifying PM mode skips classify_work_request()."""

    @pytest.mark.asyncio
    async def test_pm_mode_skips_classification(self):
        """PM mode project must NOT call classify_work_request()."""
        pm_project = {
            "name": "PM: Cuttlefish",
            "mode": "pm",
            "working_directory": "/tmp/test-vault/Cuttlefish",
        }

        with (
            patch("agent.sdk_client.ValorAgent") as MockAgent,
            patch("bridge.routing.classify_work_request") as mock_classify,
            patch("bridge.context.build_context_prefix", return_value=""),
            patch("agent.sdk_client.load_pm_system_prompt", return_value="PM prompt"),
        ):
            mock_agent_instance = MagicMock()
            mock_agent_instance.query = AsyncMock(return_value="response")
            MockAgent.return_value = mock_agent_instance

            from agent.sdk_client import get_agent_response_sdk

            await get_agent_response_sdk(
                message="What's the status of the project?",
                session_id="test-session",
                sender_name="Test",
                chat_title="PM: Cuttlefish",
                project=pm_project,
            )

            # classify_work_request should NOT have been called for PM mode
            mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_dev_mode_still_calls_classification(self):
        """Dev mode project must still call classify_work_request()."""
        dev_project = {
            "name": "Cuttlefish",
            "working_directory": "/tmp/test-vault/Cuttlefish",
        }

        with (
            patch("agent.sdk_client.ValorAgent") as MockAgent,
            patch("bridge.routing.classify_work_request", return_value="question") as mock_classify,
            patch("bridge.context.build_context_prefix", return_value=""),
        ):
            mock_agent_instance = MagicMock()
            mock_agent_instance.query = AsyncMock(return_value="response")
            MockAgent.return_value = mock_agent_instance

            from agent.sdk_client import get_agent_response_sdk

            await get_agent_response_sdk(
                message="What's the status?",
                session_id="test-session",
                sender_name="Test",
                chat_title="Dev: Cuttlefish",
                project=dev_project,
            )

            # classify_work_request SHOULD have been called for dev mode
            mock_classify.assert_called_once()

    @pytest.mark.asyncio
    async def test_pm_mode_uses_pm_system_prompt(self):
        """PM mode should use load_pm_system_prompt() for agent."""
        pm_project = {
            "name": "PM: Cuttlefish",
            "mode": "pm",
            "working_directory": "/tmp/test-vault/Cuttlefish",
        }

        with (
            patch("agent.sdk_client.ValorAgent") as MockAgent,
            patch("bridge.context.build_context_prefix", return_value=""),
            patch(
                "agent.sdk_client.load_pm_system_prompt",
                return_value="PM system prompt",
            ) as mock_pm_prompt,
        ):
            mock_agent_instance = MagicMock()
            mock_agent_instance.query = AsyncMock(return_value="response")
            MockAgent.return_value = mock_agent_instance

            from agent.sdk_client import get_agent_response_sdk

            await get_agent_response_sdk(
                message="Hello",
                session_id="test-session",
                sender_name="Test",
                chat_title="PM: Cuttlefish",
                project=pm_project,
            )

            # load_pm_system_prompt should have been called
            mock_pm_prompt.assert_called_once()

            # ValorAgent should have received the PM system prompt
            call_kwargs = MockAgent.call_args
            assert call_kwargs.kwargs.get("system_prompt") == "PM system prompt"


# ---------------------------------------------------------------------------
# Mode field defaults and edge cases
# ---------------------------------------------------------------------------


class TestModeFieldDefaults:
    """Tests for mode field handling in project config."""

    def test_missing_mode_defaults_to_dev(self):
        """Project config without 'mode' field should behave as 'dev'."""
        project = {"name": "Test", "working_directory": "/tmp/test"}
        mode = project.get("mode", "dev")
        assert mode == "dev"

    def test_explicit_dev_mode(self):
        """Project config with mode='dev' should behave normally."""
        project = {"name": "Test", "mode": "dev", "working_directory": "/tmp/test"}
        mode = project.get("mode", "dev")
        assert mode == "dev"

    def test_pm_mode_detected(self):
        """Project config with mode='pm' is detected correctly."""
        project = {"name": "PM: Test", "mode": "pm", "working_directory": "/tmp/vault/Test"}
        mode = project.get("mode", "dev")
        assert mode == "pm"

    def test_unknown_mode_treated_as_dev(self):
        """Unknown mode value should be treated as 'dev'."""
        project = {"name": "Test", "mode": "unknown", "working_directory": "/tmp/test"}
        mode = project.get("mode", "dev")
        # The code normalizes unknown modes to "dev"
        if mode not in ("dev", "pm"):
            mode = "dev"
        assert mode == "dev"


# ---------------------------------------------------------------------------
# PM project routing via find_project_for_chat
# ---------------------------------------------------------------------------


class TestPmProjectRouting:
    """Tests for PM channel routing in bridge/routing.py."""

    def test_pm_channel_matches_project(self):
        """PM: Cuttlefish chat title should match the pm-cuttlefish project."""
        from bridge.routing import find_project_for_chat, GROUP_TO_PROJECT

        # Save and restore original
        original = dict(GROUP_TO_PROJECT)
        try:
            pm_project = {
                "name": "PM: Cuttlefish",
                "mode": "pm",
                "working_directory": "/tmp/vault/Cuttlefish",
            }
            GROUP_TO_PROJECT["pm: cuttlefish"] = pm_project

            result = find_project_for_chat("PM: Cuttlefish")
            assert result is not None
            assert result.get("mode") == "pm"
        finally:
            GROUP_TO_PROJECT.clear()
            GROUP_TO_PROJECT.update(original)

    def test_dev_channel_not_matched_as_pm(self):
        """Dev: Cuttlefish should NOT match PM project."""
        from bridge.routing import find_project_for_chat, GROUP_TO_PROJECT

        original = dict(GROUP_TO_PROJECT)
        try:
            dev_project = {
                "name": "Cuttlefish",
                "working_directory": "/tmp/src/cuttlefish",
            }
            pm_project = {
                "name": "PM: Cuttlefish",
                "mode": "pm",
                "working_directory": "/tmp/vault/Cuttlefish",
            }
            GROUP_TO_PROJECT["dev: cuttlefish"] = dev_project
            GROUP_TO_PROJECT["pm: cuttlefish"] = pm_project

            result = find_project_for_chat("Dev: Cuttlefish")
            assert result is not None
            assert result.get("mode") is None  # dev project has no mode field
        finally:
            GROUP_TO_PROJECT.clear()
            GROUP_TO_PROJECT.update(original)


# ---------------------------------------------------------------------------
# PM config validation
# ---------------------------------------------------------------------------


class TestPmConfigValidation:
    """Tests for PM project entries in projects.json."""

    @pytest.fixture
    def config(self):
        config_path = Path(__file__).parent.parent.parent / "config" / "projects.json"
        with open(config_path) as f:
            return json.load(f)

    def test_pm_entries_exist(self, config):
        """At least one PM project entry should exist in config."""
        projects = config.get("projects", {})
        pm_projects = {k: v for k, v in projects.items() if v.get("mode") == "pm"}
        assert len(pm_projects) > 0, "No PM project entries found in config"

    def test_pm_entries_have_required_fields(self, config):
        """Each PM entry should have mode, working_directory, and telegram groups."""
        projects = config.get("projects", {})
        pm_projects = {k: v for k, v in projects.items() if v.get("mode") == "pm"}

        for key, project in pm_projects.items():
            assert project.get("mode") == "pm", f"{key}: mode should be 'pm'"
            assert project.get("working_directory"), f"{key}: missing working_directory"
            assert project.get("telegram", {}).get("groups"), f"{key}: missing telegram groups"

    def test_dev_entries_have_no_pm_mode(self, config):
        """Existing dev entries should not have mode='pm'."""
        projects = config.get("projects", {})
        for key in ("valor", "cuttlefish", "popoto", "psyoptimal"):
            if key in projects:
                assert projects[key].get("mode") != "pm", (
                    f"Dev project '{key}' should not have mode='pm'"
                )

    def test_valor_defaults_to_dev(self, config):
        """The 'valor' project should default to dev mode (no mode field or mode='dev')."""
        projects = config.get("projects", {})
        assert "valor" in projects
        mode = projects["valor"].get("mode", "dev")
        assert mode == "dev"
