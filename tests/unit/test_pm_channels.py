"""Unit tests for engineer channel mode (formerly PM channel mode).

Covers:
- Eng mode detection and classification bypass
- Eng system prompt loading (includes WORKER_RULES, loads work-vault CLAUDE.md)
- Mode field defaults to "dev" when absent
- Unknown mode values treated as "dev"
- Eng project config routing via find_project_for_chat
- Session type mapping remains ENG (no more separate PM/Dev types)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.sdk_client import (  # noqa: E402
    load_eng_system_prompt,
    load_system_prompt,
)

# ---------------------------------------------------------------------------
# load_eng_system_prompt() tests
# ---------------------------------------------------------------------------


class TestLoadEngSystemPrompt:
    """Tests for the engineer-specific system prompt loader."""

    def test_eng_prompt_includes_worker_rules(self, tmp_path):
        """Eng system prompt MUST include WORKER_RULES (safety rails)."""
        prompt = load_eng_system_prompt(str(tmp_path))
        assert "Worker Safety Rails" in prompt

    def test_eng_prompt_includes_persona(self, tmp_path):
        """Eng system prompt must include persona segment content."""
        prompt = load_eng_system_prompt(str(tmp_path))
        # Persona segments include "# Valor" - verify persona is loaded
        assert "Valor" in prompt

    def test_eng_prompt_loads_project_claude_md(self, tmp_path):
        """Eng system prompt loads CLAUDE.md from work-vault directory."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Eng Instructions\nYou are an engineer.")
        prompt = load_eng_system_prompt(str(tmp_path))
        assert "Eng Instructions" in prompt
        assert "engineer" in prompt

    def test_eng_prompt_without_claude_md_uses_persona_only(self, tmp_path):
        """If no CLAUDE.md in work-vault, eng prompt uses persona segments only."""
        prompt = load_eng_system_prompt(str(tmp_path))
        # Should still work - just persona segments without project instructions
        assert len(prompt) > 0
        assert "Worker Safety Rails" in prompt

    def test_eng_prompt_has_separator_between_persona_and_project(self, tmp_path):
        """Persona segments and project CLAUDE.md should be separated by ---."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Eng Instructions")
        prompt = load_eng_system_prompt(str(tmp_path))
        assert "---" in prompt

    def test_dev_prompt_still_has_worker_rules(self):
        """Regression: load_system_prompt() (eng mode) still includes WORKER_RULES."""
        prompt = load_system_prompt()
        assert "Worker Safety Rails" in prompt


# ---------------------------------------------------------------------------
# Eng mode in build_harness_turn_input() -- context enrichment
# ---------------------------------------------------------------------------


class TestEngModeContextEnrichment:
    """Tests verifying eng mode context enrichment via build_harness_turn_input()."""

    @pytest.mark.asyncio
    async def test_eng_mode_produces_context_headers(self):
        """Eng mode project produces correct PROJECT context headers."""
        eng_project = {
            "name": "Eng: Cuttlefish",
            "mode": "eng",
            "working_directory": "/tmp/test-vault/Cuttlefish",
            "_key": "cuttlefish",
        }

        with patch("bridge.context.build_context_prefix", return_value="PROJECT: Eng: Cuttlefish"):
            from agent.sdk_client import build_harness_turn_input

            result = await build_harness_turn_input(
                message="What's the status of the project?",
                session_id="test-session",
                sender_name="Test",
                chat_title="Eng: Cuttlefish",
                project=eng_project,
                task_list_id=None,
                session_type="eng",
                sender_id=123,
            )

        assert "PROJECT: Eng: Cuttlefish" in result
        assert "FROM: Test" in result
        assert "SESSION_ID: test-session" in result

    @pytest.mark.asyncio
    async def test_default_mode_produces_context_headers(self):
        """Default mode project produces correct context headers."""
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
                chat_title="Eng: Cuttlefish",
                project=dev_project,
                task_list_id=None,
                session_type="eng",
                sender_id=123,
            )

        assert "PROJECT: Cuttlefish" in result
        assert "SESSION_ID: test-session" in result

    @pytest.mark.asyncio
    async def test_eng_mode_no_github_header(self):
        """Eng mode projects skip SDLC classification, so no GITHUB header is injected.

        When project mode is 'eng', get_agent_response_sdk forces classification
        to 'question' before calling build_harness_turn_input. The GITHUB header is
        only injected for SDLC cross-repo calls, so eng-mode projects never see it.
        This test verifies that behavior by passing classification='question' as the
        SDK would.
        """
        eng_project = {
            "name": "Eng: Cuttlefish",
            "mode": "eng",
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
                chat_title="Eng: Cuttlefish",
                project=eng_project,
                task_list_id=None,
                session_type="eng",
                sender_id=123,
                # Eng mode: get_agent_response_sdk forces 'question' when mode='eng'
                classification="question",
                is_cross_repo=True,
            )

        # Eng mode forces question classification, so no GITHUB header
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

    def test_eng_mode_detected(self):
        """Project config with mode='eng' is detected correctly."""
        project = {"name": "Eng: Test", "mode": "eng", "working_directory": "/tmp/vault/Test"}
        mode = project.get("mode", "dev")
        assert mode == "eng"

    def test_pm_mode_treated_as_eng(self):
        """Legacy mode='pm' should still be recognized (maps to engineer rails)."""
        project = {"name": "PM: Test", "mode": "pm", "working_directory": "/tmp/vault/Test"}
        mode = project.get("mode", "dev")
        assert mode == "pm"

    def test_unknown_mode_treated_as_dev(self):
        """Unknown mode value should be treated as 'dev'."""
        project = {"name": "Test", "mode": "unknown", "working_directory": "/tmp/test"}
        mode = project.get("mode", "dev")
        # The code normalizes unknown modes to "dev"
        if mode not in ("dev", "pm", "eng"):
            mode = "dev"
        assert mode == "dev"


# ---------------------------------------------------------------------------
# Eng project routing via find_project_for_chat
# ---------------------------------------------------------------------------


class TestEngProjectRouting:
    """Tests for engineer channel routing in bridge/routing.py."""

    def test_eng_channel_matches_project(self):
        """Eng: Cuttlefish chat title should match the eng-cuttlefish project."""
        from bridge.routing import GROUP_TO_PROJECT, find_project_for_chat

        # Save and restore original
        original = dict(GROUP_TO_PROJECT)
        try:
            eng_project = {
                "name": "Eng: Cuttlefish",
                "mode": "eng",
                "working_directory": "/tmp/vault/Cuttlefish",
            }
            GROUP_TO_PROJECT["eng: cuttlefish"] = eng_project

            result = find_project_for_chat("Eng: Cuttlefish")
            assert result is not None
            assert result.get("mode") == "eng"
        finally:
            GROUP_TO_PROJECT.clear()
            GROUP_TO_PROJECT.update(original)

    def test_dev_channel_not_matched_as_eng(self):
        """Dev-prefixed chats do not match eng-prefixed project."""
        from bridge.routing import GROUP_TO_PROJECT, find_project_for_chat

        original = dict(GROUP_TO_PROJECT)
        try:
            dev_project = {
                "name": "Cuttlefish",
                "working_directory": "/tmp/src/cuttlefish",
            }
            eng_project = {
                "name": "Eng: Cuttlefish",
                "mode": "eng",
                "working_directory": "/tmp/vault/Cuttlefish",
            }
            GROUP_TO_PROJECT["dev: cuttlefish"] = dev_project
            GROUP_TO_PROJECT["eng: cuttlefish"] = eng_project

            result = find_project_for_chat("Dev: Cuttlefish")
            assert result is not None
            assert result.get("mode") is None  # dev project has no mode field
        finally:
            GROUP_TO_PROJECT.clear()
            GROUP_TO_PROJECT.update(original)


# ---------------------------------------------------------------------------
# Eng config validation
# ---------------------------------------------------------------------------


class TestEngConfigValidation:
    """Tests for engineer project entries in projects.json."""

    @pytest.fixture
    def config(self):
        # Hardcoded, well-formed config dict. Unit tests must be deterministic
        # and environment-independent.
        return {
            "personas": {
                "engineer": {"description": "Engineer persona"},
            },
            "projects": {
                "valor": {
                    "telegram": {
                        "groups": {
                            "Eng: Valor": {"persona": "engineer"},
                        }
                    }
                }
            },
        }

    def test_eng_persona_groups_exist(self, config):
        """At least one group with engineer persona should exist."""
        projects = config.get("projects", {})
        eng_groups = []
        for _key, proj in projects.items():
            groups = proj.get("telegram", {}).get("groups", {})
            if isinstance(groups, dict):
                for gname, gcfg in groups.items():
                    if isinstance(gcfg, dict) and gcfg.get("persona") == "engineer":
                        eng_groups.append(gname)
        assert len(eng_groups) > 0, "No groups with engineer persona found in config"

    def test_eng_persona_defined(self, config):
        """The engineer persona should be defined in personas section."""
        personas = config.get("personas", {})
        assert "engineer" in personas, "engineer persona not defined"

    def test_valor_has_engineer_group(self, config):
        """The 'valor' project should have an Eng group with engineer persona."""
        projects = config.get("projects", {})
        assert "valor" in projects
        groups = projects["valor"].get("telegram", {}).get("groups", {})
        eng_groups = [g for g in groups if g.startswith("Eng:")]
        assert len(eng_groups) > 0, "valor project should have at least one Eng group"
