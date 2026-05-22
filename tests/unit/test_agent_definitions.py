"""Tests for agent/agent_definitions.py — graceful fallback on missing files."""

from pathlib import Path
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

    def test_malformed_yaml_returns_fallback(self, tmp_path):
        """File with body but no YAML frontmatter delimiters falls back."""
        bad = tmp_path / "no_frontmatter.md"
        bad.write_text("Just a body with no '---' delimiters at all.\n")
        result = _parse_agent_markdown(bad)
        assert result.get("_is_fallback") is True
        assert "no_frontmatter.md" in result["body"]
        assert "ValueError" in result["frontmatter"]["description"]

    def test_malformed_yaml_logs_warning(self, tmp_path, caplog):
        """Malformed-frontmatter fallback log names the exception class."""
        import logging

        bad = tmp_path / "no_frontmatter.md"
        bad.write_text("Just a body with no delimiters.\n")
        with caplog.at_level(logging.WARNING, logger="agent.agent_definitions"):
            _parse_agent_markdown(bad)
        messages = [r.getMessage() for r in caplog.records]
        # The exception class name must appear in the log so operators can diagnose.
        assert any("ValueError" in m and "unusable" in m for m in messages), messages

    def test_empty_file_returns_fallback(self, tmp_path):
        """Empty file falls back via ValueError ('No YAML frontmatter found')."""
        empty = tmp_path / "empty.md"
        empty.write_text("")
        result = _parse_agent_markdown(empty)
        assert result.get("_is_fallback") is True

    def test_whitespace_only_file_returns_fallback(self, tmp_path):
        """Whitespace-only file falls back via ValueError."""
        ws = tmp_path / "ws.md"
        ws.write_text("   \n\n\t \n")
        result = _parse_agent_markdown(ws)
        assert result.get("_is_fallback") is True

    def test_empty_frontmatter_does_not_fallback(self, tmp_path):
        """Valid empty frontmatter (blank line between delimiters) is NOT a fallback.

        The existing regex (``^---\\n(.*?)\\n---\\n?(.*)``) requires at least
        one character (here, a newline) between the delimiters. A file with
        ``---\\n\\n---\\n`` is the canonical empty-frontmatter case — confirm
        the widened error handling does not mistake it for malformed input.
        """
        ok = tmp_path / "ok.md"
        ok.write_text("---\n\n---\n")
        result = _parse_agent_markdown(ok)
        # No _is_fallback marker — this is a legitimate (if degenerate) parse.
        assert result.get("_is_fallback") is not True
        assert result["frontmatter"] == {}
        assert result["body"] == ""

    def test_oserror_returns_fallback(self, tmp_path):
        """PermissionError (OSError subclass) from read_text falls back."""
        path = tmp_path / "denied.md"
        path.write_text("---\ndescription: ok\n---\nBody")
        with patch.object(
            Path, "read_text", side_effect=PermissionError("permission denied")
        ):
            result = _parse_agent_markdown(path)
        assert result.get("_is_fallback") is True
        assert "PermissionError" in result["frontmatter"]["description"]

    def test_oserror_logs_warning(self, tmp_path, caplog):
        """OSError fallback log names the exception class."""
        import logging

        path = tmp_path / "denied.md"
        path.write_text("---\ndescription: ok\n---\nBody")
        with (
            caplog.at_level(logging.WARNING, logger="agent.agent_definitions"),
            patch.object(Path, "read_text", side_effect=PermissionError("nope")),
        ):
            _parse_agent_markdown(path)
        messages = [r.getMessage() for r in caplog.records]
        assert any("PermissionError" in m and "unusable" in m for m in messages), messages

    def test_unicode_decode_error_returns_fallback(self, tmp_path):
        """A file with invalid UTF-8 bytes falls back via UnicodeDecodeError."""
        bad = tmp_path / "bad_utf8.md"
        # 0x80 is a continuation byte without a leading byte — invalid UTF-8.
        bad.write_bytes(b"---\ndescription: \x80 bad\n---\nBody\n")
        result = _parse_agent_markdown(bad)
        assert result.get("_is_fallback") is True
        # UnicodeDecodeError is a ValueError subclass; either name is acceptable
        # in the description, but the fallback marker must be set.
        assert (
            "UnicodeDecodeError" in result["frontmatter"]["description"]
            or "ValueError" in result["frontmatter"]["description"]
        )

    def test_unrelated_exception_still_propagates(self, tmp_path):
        """KeyError (not OSError/ValueError) escapes — no silent fallback."""
        path = tmp_path / "ok.md"
        path.write_text("---\ndescription: ok\n---\nBody")
        with patch.object(Path, "read_text", side_effect=KeyError("not handled")):
            with pytest.raises(KeyError):
                _parse_agent_markdown(path)


class TestGetAgentDefinitions:
    """Tests for get_agent_definitions()."""

    def test_returns_all_agents_when_files_exist(self):
        """Normal case: all expected agents are returned.

        Phase 5 note: dev-session was removed from get_agent_definitions().
        Dev sessions are now created via valor_session create, not Agent tool.
        """
        defs = get_agent_definitions()
        for name in ("builder", "validator", "code-reviewer"):
            assert name in defs, f"Missing agent definition: {name}"
        assert "dev-session" not in defs, "dev-session should be removed (Phase 5)"

    def test_returns_complete_dict_when_all_files_missing(self, tmp_path):
        """Even if every agent file is missing, returns a full dict (degraded)."""
        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            defs = get_agent_definitions()
        # Three agents (dev-session removed in Phase 5)
        for name in ("builder", "validator", "code-reviewer"):
            assert name in defs, f"Missing agent definition: {name}"
            assert len(defs[name].prompt) > 0, f"Empty prompt for: {name}"

    def test_returns_complete_dict_when_one_malformed(self, tmp_path):
        """A mix of missing AND malformed files still yields a complete dict."""
        # validator.md is malformed (no frontmatter); the others are absent.
        (tmp_path / "validator.md").write_text("body with no frontmatter delimiters")
        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            defs = get_agent_definitions()
        for name in ("builder", "validator", "code-reviewer"):
            assert name in defs, f"Missing agent definition: {name}"
            assert len(defs[name].prompt) > 0, f"Empty prompt for: {name}"

    def test_single_missing_file_does_not_crash(self, tmp_path):
        """If one file is missing but others exist, all agents are returned."""
        # Copy real files to tmp_path except builder.md
        import shutil

        for name in ("validator.md", "code-reviewer.md"):
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
        """Reports missing files when agents dir is empty.

        Phase 5: dev-session.md removed from _EXPECTED_AGENT_FILES.
        """
        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            missing = validate_agent_files()
        assert len(missing) == 3
        assert any("builder.md" in p for p in missing)
        assert any("validator.md" in p for p in missing)
        assert any("code-reviewer.md" in p for p in missing)

    def test_partial_missing(self, tmp_path):
        """Reports only the files that are actually missing."""
        # Create just builder.md
        (tmp_path / "builder.md").write_text("---\ndescription: test\n---\nBody")
        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            missing = validate_agent_files()
        assert len(missing) == 2
        assert not any("builder.md" in p for p in missing)

    def test_partial_missing_with_malformed(self, tmp_path):
        """Both missing AND malformed files appear in the returned list."""
        # builder.md is well-formed; validator.md is malformed (no frontmatter);
        # code-reviewer.md is absent entirely.
        (tmp_path / "builder.md").write_text("---\ndescription: test\n---\nBody")
        (tmp_path / "validator.md").write_text("not valid frontmatter at all")
        with patch("agent.agent_definitions._AGENTS_DIR", tmp_path):
            problematic = validate_agent_files()
        assert len(problematic) == 2
        # builder.md must NOT be flagged (it parses cleanly).
        assert not any(p.endswith("builder.md") for p in problematic)
        # validator.md (malformed) AND code-reviewer.md (missing) must be flagged.
        assert any(p.endswith("validator.md") for p in problematic)
        assert any(p.endswith("code-reviewer.md") for p in problematic)


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

    def test_dev_session_md_not_in_repo(self):
        """Phase 5 follow-up cleanup (#1360): dev-session.md must not be in the repo.

        The file was deleted because (a) get_agent_definitions() does not
        reference it, and (b) the SDK loads .claude/agents/*.md from disk
        via setting_sources, which would let a stale Agent(subagent_type=
        "dev-session") dispatch resolve to it. Deletion makes stale
        dispatches fail-fast with an SDK 'unknown subagent' error.
        """
        assert not (_AGENTS_DIR / "dev-session.md").exists(), (
            ".claude/agents/dev-session.md must not exist "
            "(Phase 5 follow-up cleanup, #1360). "
            "If a skill template or merge re-added it, delete it again."
        )
