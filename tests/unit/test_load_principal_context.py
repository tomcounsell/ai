"""
Tests for load_principal_context and PRINCIPAL_PATH in agent/sdk_client.py.

Verifies the hotfix for issue #416 — Observer crash due to missing function.

Run with: pytest tests/unit/test_load_principal_context.py -v
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.sdk_client import PRINCIPAL_PATH, load_principal_context, load_system_prompt


class TestPrincipalPathConstant:
    """Verify PRINCIPAL_PATH is defined and points to the right location."""

    def test_principal_path_exists_as_constant(self):
        assert PRINCIPAL_PATH is not None

    def test_principal_path_points_to_config_dir(self):
        assert PRINCIPAL_PATH.name == "PRINCIPAL.md"
        assert PRINCIPAL_PATH.parent.name == "config"


class TestLoadPrincipalContextMissing:
    """Behavior when PRINCIPAL.md doesn't exist."""

    def test_returns_empty_string_when_file_missing(self, tmp_path):
        fake_path = tmp_path / "nonexistent" / "PRINCIPAL.md"
        with patch("agent.sdk_client.PRINCIPAL_PATH", fake_path):
            result = load_principal_context(condensed=True)
            assert result == ""

    def test_returns_empty_string_when_file_empty(self, tmp_path):
        empty_file = tmp_path / "PRINCIPAL.md"
        empty_file.write_text("")
        with patch("agent.sdk_client.PRINCIPAL_PATH", empty_file):
            result = load_principal_context(condensed=True)
            assert result == ""

    def test_returns_empty_string_whitespace_only(self, tmp_path):
        ws_file = tmp_path / "PRINCIPAL.md"
        ws_file.write_text("   \n\n  ")
        with patch("agent.sdk_client.PRINCIPAL_PATH", ws_file):
            result = load_principal_context(condensed=True)
            assert result == ""


class TestLoadPrincipalContextFull:
    """Full (non-condensed) mode returns entire file."""

    def test_full_mode_returns_entire_content(self, tmp_path):
        content = "# Principal\n\n## Mission\n\nBuild great things.\n\n## Other\n\nStuff."
        f = tmp_path / "PRINCIPAL.md"
        f.write_text(content)
        with patch("agent.sdk_client.PRINCIPAL_PATH", f):
            result = load_principal_context(condensed=False)
            assert result == content


class TestLoadPrincipalContextCondensed:
    """Condensed mode extracts only Mission + Goals + Projects sections."""

    SAMPLE = (
        "# Tom's Operating Context\n\n"
        "## Mission\n\n"
        "Build impactful AI systems.\n"
        "\n## Goals Q1 2026\n\n"
        "- Ship v2\n- Hire 3 engineers\n"
        "\n## Projects Active\n\n"
        "- Valor AI\n- Community platform\n"
        "\n## Private Notes\n\n"
        "Secret stuff here.\n"
    )

    def test_condensed_includes_mission(self, tmp_path):
        f = tmp_path / "PRINCIPAL.md"
        f.write_text(self.SAMPLE)
        with patch("agent.sdk_client.PRINCIPAL_PATH", f):
            result = load_principal_context(condensed=True)
            assert "Mission" in result
            assert "Build impactful AI systems" in result

    def test_condensed_includes_goals(self, tmp_path):
        f = tmp_path / "PRINCIPAL.md"
        f.write_text(self.SAMPLE)
        with patch("agent.sdk_client.PRINCIPAL_PATH", f):
            result = load_principal_context(condensed=True)
            assert "Goals" in result
            assert "Ship v2" in result

    def test_condensed_includes_projects(self, tmp_path):
        f = tmp_path / "PRINCIPAL.md"
        f.write_text(self.SAMPLE)
        with patch("agent.sdk_client.PRINCIPAL_PATH", f):
            result = load_principal_context(condensed=True)
            assert "Projects" in result
            assert "Valor AI" in result

    def test_condensed_excludes_private_sections(self, tmp_path):
        f = tmp_path / "PRINCIPAL.md"
        f.write_text(self.SAMPLE)
        with patch("agent.sdk_client.PRINCIPAL_PATH", f):
            result = load_principal_context(condensed=True)
            assert "Secret stuff" not in result

    def test_condensed_fallback_when_no_sections_match(self, tmp_path):
        f = tmp_path / "PRINCIPAL.md"
        f.write_text("Just some plain text without any ## headers matching patterns.")
        with patch("agent.sdk_client.PRINCIPAL_PATH", f):
            result = load_principal_context(condensed=True)
            # Should fallback to first 500 chars
            assert len(result) > 0
            assert len(result) <= 500


class TestSystemPromptIncludesPrincipal:
    """Verify load_system_prompt integrates principal context."""

    def test_system_prompt_includes_principal_when_available(self, tmp_path):
        f = tmp_path / "PRINCIPAL.md"
        f.write_text("## Mission\n\nTest mission statement.\n")
        with patch("agent.sdk_client.PRINCIPAL_PATH", f):
            prompt = load_system_prompt()
            assert "Principal Context" in prompt
            assert "Test mission statement" in prompt

    def test_system_prompt_works_without_principal(self, tmp_path):
        fake = tmp_path / "nonexistent" / "PRINCIPAL.md"
        with patch("agent.sdk_client.PRINCIPAL_PATH", fake):
            prompt = load_system_prompt()
            assert "Valor" in prompt
            assert "Principal Context" not in prompt


class TestImportability:
    """Verify the function can be imported from all call sites."""

    def test_import_from_sdk_client(self):
        from agent.sdk_client import load_principal_context

        assert callable(load_principal_context)

    def test_import_principal_path(self):
        from agent.sdk_client import PRINCIPAL_PATH

        assert isinstance(PRINCIPAL_PATH, Path)
