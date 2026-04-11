"""Tests for the persona loading system.

Tests:
- load_identity() loads from config/identity.json with private override merge
- load_persona_prompt() assembles segments + overlay
- Fallback to in-repo overlay when Desktop/Valor overlay is missing
- Missing segments raise FileNotFoundError (no SOUL.md fallback)
- _resolve_persona() correctly maps project config to persona names
- load_system_prompt() uses developer persona with WORKER_RULES
- load_pm_system_prompt() uses project-manager persona
- _resolve_overlay_path() checks Desktop/Valor first, then config/personas/
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.sdk_client import (
    IDENTITY_CONFIG_PATH,
    PERSONAS_BASE_DIR,
    PERSONAS_OVERLAY_DIR,
    PERSONAS_SEGMENTS_DIR,
    _resolve_overlay_path,
    _resolve_persona,
    load_identity,
    load_persona_prompt,
    load_pm_system_prompt,
    load_system_prompt,
)


class TestResolveOverlayPath:
    """Tests for _resolve_overlay_path()."""

    def test_prefers_desktop_valor_when_exists(self):
        """Should return ~/Desktop/Valor/personas/ path when it exists."""
        path = _resolve_overlay_path("developer")
        if PERSONAS_OVERLAY_DIR.exists():
            assert path.parent == PERSONAS_OVERLAY_DIR
        else:
            assert path.parent == PERSONAS_BASE_DIR

    def test_falls_back_to_repo_when_desktop_missing(self):
        """Should fall back to config/personas/ when ~/Desktop/Valor/ doesn't exist."""
        fake_dir = Path("/nonexistent/path/personas")
        with patch("agent.sdk_client.PERSONAS_OVERLAY_DIR", fake_dir):
            path = _resolve_overlay_path("developer")
            assert path.parent == PERSONAS_BASE_DIR

    def test_returns_correct_filename(self):
        """Should use {persona}.md as the filename."""
        path = _resolve_overlay_path("project-manager")
        assert path.name == "project-manager.md"


class TestLoadIdentity:
    """Tests for load_identity()."""

    def test_loads_identity_from_config(self):
        """Should load identity fields from config/identity.json."""
        identity = load_identity()
        assert "name" in identity
        assert "email" in identity
        assert "timezone" in identity
        assert "organization" in identity
        assert identity["name"] == "Valor Engels"

    def test_doc_field_excluded(self):
        """The _doc field should be stripped from identity data."""
        identity = load_identity()
        assert "_doc" not in identity

    def test_missing_config_raises_error(self):
        """Missing identity.json should raise FileNotFoundError."""
        with patch(
            "agent.sdk_client.IDENTITY_CONFIG_PATH",
            Path("/nonexistent/identity.json"),
        ):
            with pytest.raises(FileNotFoundError, match="Identity config not found"):
                load_identity()

    def test_private_override_merge(self, tmp_path):
        """Private identity override should merge with repo defaults."""
        private_path = tmp_path / "identity.json"
        private_path.write_text(json.dumps({"name": "Override Name", "custom_field": "custom"}))
        with patch("agent.sdk_client.PRIVATE_IDENTITY_PATH", private_path):
            identity = load_identity()
            assert identity["name"] == "Override Name"
            assert identity["custom_field"] == "custom"
            assert "email" in identity

    def test_missing_private_override_works(self):
        """Missing private override file should not cause errors."""
        with patch(
            "agent.sdk_client.PRIVATE_IDENTITY_PATH",
            Path("/nonexistent/private.json"),
        ):
            identity = load_identity()
            assert identity["name"] == "Valor Engels"

    def test_malformed_private_override_warns(self, tmp_path):
        """Malformed private override should log warning and use defaults."""
        private_path = tmp_path / "identity.json"
        private_path.write_text("not valid json {{{")
        with patch("agent.sdk_client.PRIVATE_IDENTITY_PATH", private_path):
            identity = load_identity()
            assert identity["name"] == "Valor Engels"

    def test_malformed_config_raises_error(self, tmp_path):
        """Malformed identity config should raise ValueError."""
        bad_config = tmp_path / "identity.json"
        bad_config.write_text("not valid json {{{")
        with patch("agent.sdk_client.IDENTITY_CONFIG_PATH", bad_config):
            with pytest.raises(ValueError, match="Malformed identity config"):
                load_identity()

    def test_empty_config_returns_empty(self, tmp_path):
        """Empty JSON config should return empty dict."""
        empty_config = tmp_path / "identity.json"
        empty_config.write_text("{}")
        with patch("agent.sdk_client.IDENTITY_CONFIG_PATH", empty_config):
            identity = load_identity()
            assert identity == {}


class TestLoadPersonaPrompt:
    """Tests for load_persona_prompt()."""

    @pytest.fixture(autouse=True)
    def _mock_overlay_dir(self, tmp_path, monkeypatch):
        """Create mock overlay files so tests work on any machine."""
        import agent.sdk_client as sdk_mod

        overlay_dir = tmp_path / "personas"
        overlay_dir.mkdir()
        (overlay_dir / "developer.md").write_text(
            "# Developer Persona\n\n"
            "## Permissions\n\nFull System Access granted. You have unrestricted "
            "read/write access to all project files and systems.\n\n"
            "## Guidelines\n\nFocus on shipping quality code with proper testing."
        )
        (overlay_dir / "project-manager.md").write_text(
            "# Project Manager Persona\n\n"
            "## Responsibilities\n\nTriage incoming work requests and prioritize "
            "based on impact and urgency.\n\n"
            "## Guidelines\n\nCoordinate work across team members effectively."
        )
        (overlay_dir / "teammate.md").write_text(
            "# Teammate Persona\n\n"
            "## Communication Style\n\nKeep it casual and friendly. Use a "
            "conversational tone without being overly formal.\n\n"
            "## Guidelines\n\nBe helpful and approachable in all interactions."
        )
        monkeypatch.setattr(sdk_mod, "PERSONAS_OVERLAY_DIR", overlay_dir)

    def test_developer_persona_loads(self):
        """Developer persona should include segments + developer overlay."""
        prompt = load_persona_prompt("developer")
        assert "Valor" in prompt
        assert "Full System Access" in prompt

    def test_project_manager_persona_loads(self):
        """Project-manager persona should include segments + PM overlay."""
        prompt = load_persona_prompt("project-manager")
        assert "Valor" in prompt
        assert "Triage" in prompt

    def test_teammate_persona_loads(self):
        """Teammate persona should include segments + teammate overlay."""
        prompt = load_persona_prompt("teammate")
        assert "Valor" in prompt
        assert "casual" in prompt.lower()

    def test_separator_between_segments_and_overlay(self):
        """Segments and overlay should be separated by ---."""
        prompt = load_persona_prompt("developer")
        assert "\n\n---\n\n" in prompt

    def test_nonexistent_persona_falls_back_to_developer(self):
        """Unknown persona name should fall back to developer overlay."""
        prompt = load_persona_prompt("nonexistent")
        assert "Full System Access" in prompt

    def test_missing_overlay_raises_error(self):
        """Missing overlay file for known persona should raise FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch(
                    "agent.sdk_client.PERSONAS_OVERLAY_DIR",
                    Path("/nonexistent/overlay"),
                ),
                patch("agent.sdk_client.PERSONAS_BASE_DIR", Path(tmpdir)),
            ):
                with pytest.raises(FileNotFoundError, match="Persona overlay"):
                    load_persona_prompt("developer")

    def test_segment_files_exist_in_repo(self):
        """All segment files should exist in config/personas/segments/."""
        for name in ["identity.md", "work-patterns.md", "tools.md"]:
            seg_path = PERSONAS_SEGMENTS_DIR / name
            assert seg_path.exists(), f"Segment {name} not found at {seg_path}"
            content = seg_path.read_text()
            assert len(content) > 100, f"{name} is too short ({len(content)} chars)"

    def test_manifest_exists_and_valid(self):
        """manifest.json should exist and contain all 3 segments."""
        manifest_path = PERSONAS_SEGMENTS_DIR / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert "segments" in manifest
        assert manifest["segments"] == [
            "identity.md",
            "work-patterns.md",
            "tools.md",
        ]

    def test_identity_fields_injected(self):
        """Identity fields should be injected into segment content."""
        prompt = load_persona_prompt("developer")
        assert "{{identity." not in prompt
        assert "Valor Engels" in prompt
        assert "valor@yuda.me" in prompt

    def test_overlay_files_exist(self):
        """All persona overlay files should exist."""
        for name in ["developer.md", "project-manager.md", "teammate.md"]:
            path = _resolve_overlay_path(name.replace(".md", ""))
            assert path.exists(), f"{name} not found at {path}"
            content = path.read_text()
            assert len(content) > 100, f"{name} is too short"

    def test_identity_config_exists(self):
        """config/identity.json should exist and be valid JSON."""
        assert IDENTITY_CONFIG_PATH.exists()
        data = json.loads(IDENTITY_CONFIG_PATH.read_text())
        assert "name" in data
        assert "email" in data

    def test_segments_assembled_in_order(self):
        """Segments should be assembled in manifest order."""
        prompt = load_persona_prompt("developer")
        identity_pos = prompt.find("Who I Am")
        work_pos = prompt.find("How I Work")
        tools_pos = prompt.find("MCP Servers")
        assert identity_pos < work_pos < tools_pos


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
        project = {"telegram": {"groups": {"Dev: Valor": {"chat_id": 123, "persona": "developer"}}}}
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
        """load_system_prompt should include persona content from segments."""
        prompt = load_system_prompt()
        assert "Valor" in prompt

    def test_load_pm_system_prompt_uses_pm_persona(self):
        """load_pm_system_prompt should use project-manager persona."""
        prompt = load_pm_system_prompt("/tmp/nonexistent")
        assert "Valor" in prompt
        assert "Worker Safety Rails" not in prompt

    def test_no_soul_md_fallback(self):
        """SOUL.md should not exist -- no fallback available."""
        soul_path = PERSONAS_BASE_DIR.parent / "SOUL.md"
        assert not soul_path.exists(), "SOUL.md should have been deleted"

    def test_no_base_md_exists(self):
        """_base.md should not exist -- replaced by segments."""
        base_path = PERSONAS_BASE_DIR / "_base.md"
        assert not base_path.exists(), "_base.md should have been deleted"
