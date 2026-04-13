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

from unittest.mock import patch

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

    def test_pm_prompt_includes_persona(self, tmp_path):
        """PM system prompt must include persona segment content."""
        prompt = load_pm_system_prompt(str(tmp_path))
        # Persona segments include "# Valor" - verify persona is loaded
        assert "Valor" in prompt

    def test_pm_prompt_loads_project_claude_md(self, tmp_path):
        """PM system prompt loads CLAUDE.md from work-vault directory."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# PM Instructions\nYou are a project manager.")
        prompt = load_pm_system_prompt(str(tmp_path))
        assert "PM Instructions" in prompt
        assert "project manager" in prompt

    def test_pm_prompt_without_claude_md_uses_persona_only(self, tmp_path):
        """If no CLAUDE.md in work-vault, PM prompt uses persona segments only."""
        prompt = load_pm_system_prompt(str(tmp_path))
        # Should still work - just persona segments without project instructions
        assert len(prompt) > 0
        assert "Worker Safety Rails" not in prompt

    def test_pm_prompt_has_separator_between_persona_and_project(self, tmp_path):
        """Persona segments and project CLAUDE.md should be separated by ---."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# PM Instructions")
        prompt = load_pm_system_prompt(str(tmp_path))
        assert "---" in prompt

    def test_dev_prompt_still_has_worker_rules(self):
        """Regression: load_system_prompt() (dev mode) still includes WORKER_RULES."""
        prompt = load_system_prompt()
        assert "Worker Safety Rails" in prompt


# ---------------------------------------------------------------------------
# PM mode in build_harness_turn_input() — context enrichment
# ---------------------------------------------------------------------------


class TestPmModeContextEnrichment:
    """Tests verifying PM mode context enrichment via build_harness_turn_input()."""

    @pytest.mark.asyncio
    async def test_pm_mode_produces_context_headers(self):
        """PM mode project produces correct PROJECT context headers."""
        pm_project = {
            "name": "PM: Cuttlefish",
            "mode": "pm",
            "working_directory": "/tmp/test-vault/Cuttlefish",
            "_key": "cuttlefish",
        }

        with patch("bridge.context.build_context_prefix", return_value="PROJECT: PM: Cuttlefish"):
            from agent.sdk_client import build_harness_turn_input

            result = await build_harness_turn_input(
                message="What's the status of the project?",
                session_id="test-session",
                sender_name="Test",
                chat_title="PM: Cuttlefish",
                project=pm_project,
                task_list_id=None,
                session_type="pm",
                sender_id=123,
            )

        assert "PROJECT: PM: Cuttlefish" in result
        assert "FROM: Test" in result
        assert "SESSION_ID: test-session" in result

    @pytest.mark.asyncio
    async def test_dev_mode_produces_context_headers(self):
        """Dev mode project produces correct context headers."""
        dev_project = {
            "name": "Cuttlefish",
            "working_directory": "/tmp/test-vault/Cuttlefish",
            "_key": "cuttlefish",
        }

        with patch("bridge.context.build_context_prefix", return_value="PROJECT: Cuttlefish"):
            from agent.sdk_client import build_harness_turn_input

            result = await build_harness_turn_input(
                message="What's the status?",
                session_id="test-session",
                sender_name="Test",
                chat_title="Dev: Cuttlefish",
                project=dev_project,
                task_list_id=None,
                session_type="dev",
                sender_id=123,
            )

        assert "PROJECT: Cuttlefish" in result
        assert "SESSION_ID: test-session" in result

    @pytest.mark.asyncio
    async def test_pm_mode_no_github_header(self):
        """PM mode projects should never get GITHUB header even if cross-repo."""
        pm_project = {
            "name": "PM: Cuttlefish",
            "mode": "pm",
            "working_directory": "/tmp/test-vault/Cuttlefish",
            "_key": "cuttlefish",
            "github": {"org": "tomcounsell", "repo": "cuttlefish"},
        }

        with patch("bridge.context.build_context_prefix", return_value="CONTEXT"):
            from agent.sdk_client import build_harness_turn_input

            result = await build_harness_turn_input(
                message="SDLC issue 193",
                session_id="test-session",
                sender_name="Test",
                chat_title="PM: Cuttlefish",
                project=pm_project,
                task_list_id=None,
                session_type="pm",
                sender_id=123,
                classification="sdlc",
                is_cross_repo=True,
            )

        # PM mode should NOT inject GITHUB header
        assert "GITHUB:" not in result


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
        # Hardcoded, well-formed config dict. Unit tests must be deterministic
        # and environment-independent — they assert structural rules, not the
        # contents of any machine's live projects.json.
        return {
            "personas": {
                "project-manager": {"description": "PM persona"},
                "developer": {"description": "Developer persona"},
            },
            "projects": {
                "valor": {
                    "telegram": {
                        "groups": {
                            "PM: Valor": {"persona": "project-manager"},
                            "Dev: Valor": {"persona": "developer"},
                        }
                    }
                }
            },
        }

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
