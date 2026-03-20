"""Tests for the persona loading system (config/personas/).

Tests:
- load_persona_prompt() loads base + overlay for each persona
- Fallback to SOUL.md when persona overlay is missing
- Missing _base.md raises FileNotFoundError
- _resolve_persona() correctly maps project config to persona names
- load_system_prompt() uses developer persona with WORKER_RULES
- load_pm_system_prompt() uses project-manager persona
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.sdk_client import (
    PERSONAS_DIR,
    SOUL_PATH,
    _resolve_persona,
    load_persona_prompt,
    load_pm_system_prompt,
    load_system_prompt,
)


class TestLoadPersonaPrompt:
    """Tests for load_persona_prompt()."""

    def test_developer_persona_loads(self):
        """Developer persona should include base + developer overlay."""
        prompt = load_persona_prompt("developer")
        assert "Valor" in prompt  # From base
        assert "Full System Access" in prompt  # From developer overlay

    def test_project_manager_persona_loads(self):
        """Project-manager persona should include base + PM overlay."""
        prompt = load_persona_prompt("project-manager")
        assert "Valor" in prompt  # From base
        assert "Triage" in prompt  # From PM overlay

    def test_teammate_persona_loads(self):
        """Teammate persona should include base + teammate overlay."""
        prompt = load_persona_prompt("teammate")
        assert "Valor" in prompt  # From base
        assert "casual" in prompt.lower()  # From teammate overlay

    def test_separator_between_base_and_overlay(self):
        """Base and overlay should be separated by ---."""
        prompt = load_persona_prompt("developer")
        assert "\n\n---\n\n" in prompt

    def test_nonexistent_persona_falls_back(self):
        """Unknown persona name should fall back to developer overlay."""
        prompt = load_persona_prompt("nonexistent")
        # Should fall back to developer (which includes Full System Access)
        assert "Full System Access" in prompt

    def test_missing_base_raises_error(self):
        """Missing _base.md should raise FileNotFoundError."""
        with patch("agent.sdk_client.PERSONAS_DIR", Path(tempfile.mkdtemp())):
            with pytest.raises(FileNotFoundError, match="base file not found"):
                load_persona_prompt("developer")

    def test_missing_overlay_falls_back_to_soul(self):
        """Missing overlay file should fall back to SOUL.md."""
        # Create a temp dir with only _base.md
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir) / "_base.md"
            base_path.write_text("# Base persona content")

            with patch("agent.sdk_client.PERSONAS_DIR", Path(tmpdir)):
                # "developer" overlay doesn't exist in tmpdir
                prompt = load_persona_prompt("developer")
                # Should fall back to SOUL.md
                assert "Valor" in prompt  # SOUL.md contains Valor

    def test_all_persona_files_exist(self):
        """All expected persona files should exist."""
        for name in ["_base.md", "developer.md", "project-manager.md", "teammate.md"]:
            path = PERSONAS_DIR / name
            assert path.exists(), f"{name} not found at {path}"
            content = path.read_text()
            assert len(content) > 100, f"{name} is too short ({len(content)} chars)"


class TestResolvePersona:
    """Tests for _resolve_persona()."""

    def test_dm_no_project(self):
        """DM with no project should use teammate."""
        assert _resolve_persona(None, None, is_dm=True) == "teammate"

    def test_dm_with_project_config(self):
        """DM with project config should use dm_persona."""
        project = {"telegram": {"dm_persona": "teammate"}}
        assert _resolve_persona(project, None, is_dm=True) == "teammate"

    def test_dm_custom_persona(self):
        """DM with custom dm_persona should use that."""
        project = {"telegram": {"dm_persona": "developer"}}
        assert _resolve_persona(project, None, is_dm=True) == "developer"

    def test_pm_mode_project(self):
        """PM mode project should use project-manager."""
        project = {"mode": "pm", "telegram": {}}
        assert _resolve_persona(project, "PM: Test", is_dm=False) == "project-manager"

    def test_dev_group_with_persona(self):
        """Dev group with persona config should use that persona."""
        project = {
            "telegram": {
                "groups": {"Dev: Valor": {"chat_id": 123, "persona": "developer"}}
            }
        }
        assert _resolve_persona(project, "Dev: Valor", is_dm=False) == "developer"

    def test_group_no_project(self):
        """Group with no project should default to developer."""
        assert _resolve_persona(None, "Some Group", is_dm=False) == "developer"

    def test_group_no_persona_in_config(self):
        """Group without persona in config should default to developer."""
        project = {"telegram": {"groups": {"Dev: Test": {"chat_id": 123}}}}
        assert _resolve_persona(project, "Dev: Test", is_dm=False) == "developer"

    def test_dm_default_without_config(self):
        """DM with project but no dm_persona should default to teammate."""
        project = {"telegram": {}}
        assert _resolve_persona(project, None, is_dm=True) == "teammate"


class TestLoadSystemPromptIntegration:
    """Tests that load_system_prompt uses persona system."""

    def test_load_system_prompt_includes_worker_rules(self):
        """load_system_prompt should include WORKER_RULES."""
        prompt = load_system_prompt()
        assert "Worker Safety Rails" in prompt

    def test_load_system_prompt_includes_persona_content(self):
        """load_system_prompt should include developer persona content."""
        prompt = load_system_prompt()
        assert "Valor" in prompt

    def test_load_pm_system_prompt_uses_pm_persona(self):
        """load_pm_system_prompt should use project-manager persona."""
        prompt = load_pm_system_prompt("/tmp/nonexistent")
        assert "Valor" in prompt
        # Should NOT include WORKER_RULES
        assert "Worker Safety Rails" not in prompt

    def test_soul_md_still_exists(self):
        """SOUL.md should still exist as fallback."""
        assert SOUL_PATH.exists()
