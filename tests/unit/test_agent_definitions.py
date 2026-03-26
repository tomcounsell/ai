"""Tests for agent/agent_definitions.py — graceful fallback on missing files."""

from unittest.mock import patch

import pytest

from agent.agent_definitions import (
    _AGENTS_DIR,
    _parse_agent_markdown,
    get_agent_definitions,
    validate_agent_files,
)


class TestParseAgentMarkdown:
    """Tests for _parse_agent_markdown()."""

    def test_normal_load(self):
        """Parsing a real agent file returns frontmatter and body."""
        path = _AGENTS_DIR / "builder.md"
        if not path.exists():
            pytest.skip("builder.md not on disk")
        result = _parse_agent_markdown(path)
        assert "frontmatter" in result
        assert "body" in result
        assert isinstance(result["frontmatter"], dict)
        assert len(result["body"]) > 0

    def test_missing_file_returns_fallback(self, tmp_path):
        """Missing file returns a fallback dict instead of raising."""
        missing = tmp_path / "nonexistent.md"
        result = _parse_agent_markdown(missing)
        assert "frontmatter" in result
        assert "body" in result
        assert "nonexistent.md" in result["body"]
        assert "Fallback" in result["frontmatter"]["description"]

    def test_missing_file_logs_warning(self, tmp_path, caplog):
        """Missing file produces a logger.warning."""
        import logging

        missing = tmp_path / "gone.md"
        with caplog.at_level(logging.WARNING, logger="agent.agent_definitions"):
            _parse_agent_markdown(missing)
        assert any("not found" in record.message for record in caplog.records)


class TestGetAgentDefinitions:
    """Tests for get_agent_definitions()."""

    def test_returns_all_agents_when_files_exist(self):
        """Normal case: all expected agents are returned."""
        defs = get_agent_definitions()
        for name in ("builder", "validator", "code-reviewer", "dev-session"):
            assert name in defs, f"Missing agent definition: {name}"

    def test_returns_complete_dict_when_all_files_missing(self, tmp_path):
        """Even if every agent file is missing, returns a full dict (degraded)."""
        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            defs = get_agent_definitions()
        # All four agents should still be present with fallback prompts
        for name in ("builder", "validator", "code-reviewer", "dev-session"):
            assert name in defs, f"Missing agent definition: {name}"
            assert len(defs[name].prompt) > 0, f"Empty prompt for: {name}"

    def test_single_missing_file_does_not_crash(self, tmp_path):
        """If one file is missing but others exist, all agents are returned."""
        # Copy real files to tmp_path except builder.md
        import shutil

        for name in ("validator.md", "code-reviewer.md", "dev-session.md"):
            src = _AGENTS_DIR / name
            if src.exists():
                shutil.copy(src, tmp_path / name)

        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            defs = get_agent_definitions()
        assert "builder" in defs
        assert (
            "Fallback" in defs["builder"].description
            or "fallback" in defs["builder"].description.lower()
            or len(defs["builder"].prompt) > 0
        )


class TestValidateAgentFiles:
    """Tests for validate_agent_files()."""

    def test_no_missing_files(self):
        """All expected agent files exist in the repo."""
        missing = validate_agent_files()
        assert missing == [], f"Missing agent files: {missing}"

    def test_detects_missing_files(self, tmp_path):
        """Reports missing files when agents dir is empty."""
        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            missing = validate_agent_files()
        assert len(missing) == 4
        assert any("builder.md" in p for p in missing)
        assert any("validator.md" in p for p in missing)
        assert any("code-reviewer.md" in p for p in missing)
        assert any("dev-session.md" in p for p in missing)

    def test_partial_missing(self, tmp_path):
        """Reports only the files that are actually missing."""
        # Create just builder.md
        (tmp_path / "builder.md").write_text("---\ndescription: test\n---\nBody")
        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            missing = validate_agent_files()
        assert len(missing) == 3
        assert not any("builder.md" in p for p in missing)


class TestAgentFilesCICheck:
    """CI-style test: verify all agent file paths referenced in code actually exist."""

    def test_all_referenced_agent_files_exist_on_disk(self):
        """Every agent file that get_agent_definitions() references must exist in the repo."""
        from agent.agent_definitions import _EXPECTED_AGENT_FILES

        for filename in _EXPECTED_AGENT_FILES:
            path = _AGENTS_DIR / filename
            assert path.exists(), (
                f"Agent definition file referenced in code but missing from repo: {path}"
            )
