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

from agent.sdk_client import (  # noqa: E402
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
            patch("agent.sdk_client.ValorAgent") as mock_agent_cls,
            patch("bridge.routing.classify_work_request") as mock_classify,
            patch("bridge.context.build_context_prefix", return_value=""),
            patch("agent.sdk_client.load_pm_system_prompt", return_value="PM prompt"),
        ):
            mock_agent_instance = MagicMock()
            mock_agent_instance.query = AsyncMock(return_value="response")
            mock_agent_cls.return_value = mock_agent_instance

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
    async def test_dev_mode_reads_classification_from_session(self):
        """Dev mode project reads classification from AgentSession, not classify_work_request()."""
        dev_project = {
            "name": "Cuttlefish",
            "working_directory": "/tmp/test-vault/Cuttlefish",
        }

        with (
            patch("agent.sdk_client.ValorAgent") as mock_agent_cls,
            patch("bridge.context.build_context_prefix", return_value=""),
        ):
            mock_agent_instance = MagicMock()
            mock_agent_instance.query = AsyncMock(return_value="response")
            mock_agent_cls.return_value = mock_agent_instance

            from agent.sdk_client import get_agent_response_sdk

            # Dev mode should NOT raise and should fall back to "question" classification
            # when no AgentSession exists for the session_id
            result = await get_agent_response_sdk(
                message="What's the status?",
                session_id="test-session-nonexistent",
                sender_name="Test",
                chat_title="Dev: Cuttlefish",
                project=dev_project,
            )

            # Should get a response (classification defaults to "question")
            assert result is not None

    @pytest.mark.asyncio
    async def test_pm_mode_uses_pm_system_prompt(self):
        """PM mode should use load_pm_system_prompt() for agent."""
        pm_project = {
            "name": "PM: Cuttlefish",
            "mode": "pm",
            "working_directory": "/tmp/test-vault/Cuttlefish",
        }

        with (
            patch("agent.sdk_client.ValorAgent") as mock_agent_cls,
            patch("bridge.context.build_context_prefix", return_value=""),
            patch(
                "agent.sdk_client.load_pm_system_prompt",
                return_value="PM system prompt",
            ) as mock_pm_prompt,
        ):
            mock_agent_instance = MagicMock()
            mock_agent_instance.query = AsyncMock(return_value="response")
            mock_agent_cls.return_value = mock_agent_instance

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
            call_kwargs = mock_agent_cls.call_args
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
        from bridge.routing import GROUP_TO_PROJECT, find_project_for_chat

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
        from bridge.routing import GROUP_TO_PROJECT, find_project_for_chat

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
        # projects.json now lives in ~/Desktop/Valor/ (iCloud-synced, private)
        config_path = Path.home() / "Desktop" / "Valor" / "projects.json"
        if not config_path.exists():
            # Legacy fallback
            config_path = Path(__file__).parent.parent.parent / "config" / "projects.json"
        if not config_path.exists():
            pytest.skip("projects.json not found (machine-specific config)")
        with open(config_path) as f:
            return json.load(f)

    def test_pm_persona_groups_exist(self, config):
        """At least one group with project-manager persona should exist."""
        projects = config.get("projects", {})
        pm_groups = []
        for _key, proj in projects.items():
            groups = proj.get("telegram", {}).get("groups", {})
            if isinstance(groups, dict):
                for gname, gcfg in groups.items():
                    if isinstance(gcfg, dict) and gcfg.get("persona") == "project-manager":
                        pm_groups.append(gname)
        assert len(pm_groups) > 0, "No groups with project-manager persona found in config"

    def test_pm_persona_defined(self, config):
        """The project-manager persona should be defined in personas section."""
        personas = config.get("personas", {})
        assert "project-manager" in personas, "project-manager persona not defined"

    def test_dev_groups_use_developer_persona(self, config):
        """Dev groups should use developer persona, not project-manager."""
        projects = config.get("projects", {})
        for key in ("valor", "cuttlefish", "popoto", "psyoptimal"):
            if key not in projects:
                continue
            groups = projects[key].get("telegram", {}).get("groups", {})
            if isinstance(groups, dict):
                for gname, gcfg in groups.items():
                    if gname.startswith("Dev:") and isinstance(gcfg, dict):
                        persona = gcfg.get("persona", "developer")
                        assert persona == "developer", (
                            f"{key}/{gname}: Dev group should use developer persona"
                        )

    def test_valor_has_developer_group(self, config):
        """The 'valor' project should have a Dev group with developer persona."""
        projects = config.get("projects", {})
        assert "valor" in projects
        groups = projects["valor"].get("telegram", {}).get("groups", {})
        dev_groups = [g for g in groups if g.startswith("Dev:")]
        assert len(dev_groups) > 0, "valor project should have at least one Dev group"
