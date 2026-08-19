"""Unit tests for check_env_completeness() in scripts/update/verify.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.update.verify import check_env_completeness


@pytest.fixture()
def env_dir(tmp_path: Path) -> Path:
    """Return a tmp directory pre-configured with minimal .env.example and .env files."""
    return tmp_path


def write_example(env_dir: Path, content: str) -> None:
    (env_dir / ".env.example").write_text(content)


def write_env(env_dir: Path, content: str) -> None:
    (env_dir / ".env").write_text(content)


class TestMissingKeyReported:
    def test_missing_key_reported(self, env_dir: Path) -> None:
        """A key declared in .env.example but absent from .env surfaces as unavailable."""
        write_example(env_dir, "# Redis connection URL\nREDIS_URL=redis://localhost\n")
        write_env(env_dir, "# empty env\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "REDIS_URL" in result.error
        assert "missing" in result.error.lower()

    def test_description_extracted_from_comment(self, env_dir: Path) -> None:
        """The description from the comment above a key appears in the error message."""
        write_example(
            env_dir,
            "# Redis connection URL for the cache layer\nREDIS_URL=redis://localhost\n",
        )
        write_env(env_dir, "OTHER_KEY=value\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "Redis connection URL for the cache layer" in result.error

    def test_multiple_missing_keys_semicolon_separated(self, env_dir: Path) -> None:
        """Multiple missing keys are semicolon-separated in a single error string."""
        write_example(
            env_dir,
            "# Key A\nKEY_A=val\n# Key B\nKEY_B=val\n",
        )
        write_env(env_dir, "# nothing\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "KEY_A" in result.error
        assert "KEY_B" in result.error
        assert "2 missing" in result.error


class TestBlankValueIsPresent:
    def test_blank_value_is_present(self, env_dir: Path) -> None:
        """A key present in .env with a blank value is treated as present (no warning)."""
        write_example(env_dir, "# Redis URL\nREDIS_URL=redis://localhost\n")
        write_env(env_dir, "REDIS_URL=\n")

        result = check_env_completeness(env_dir)

        assert result.available is True
        assert result.error is None

    def test_blank_value_with_spaces(self, env_dir: Path) -> None:
        """A key with whitespace-only value is still considered present."""
        write_example(env_dir, "# API key\nAPI_KEY=sk-****\n")
        write_env(env_dir, "API_KEY=   \n")

        # The key name itself exists in .env — the parser splits on first '='
        result = check_env_completeness(env_dir)

        assert result.available is True


class TestAllPresent:
    def test_all_present(self, env_dir: Path) -> None:
        """When .env contains all declared keys the check returns available=True."""
        write_example(
            env_dir,
            "# Key A\nKEY_A=val\n# Key B\nKEY_B=val\n",
        )
        write_env(env_dir, "KEY_A=something\nKEY_B=other\n")

        result = check_env_completeness(env_dir)

        assert result.available is True
        assert "all" in (result.version or "").lower()
        assert result.error is None

    def test_all_present_version_includes_count(self, env_dir: Path) -> None:
        """Version string includes the total variable count."""
        write_example(env_dir, "# Key A\nKEY_A=val\n")
        write_env(env_dir, "KEY_A=x\n")

        result = check_env_completeness(env_dir)

        assert result.available is True
        assert "1" in (result.version or "")


class TestEnvNotFoundReturnsSkipped:
    def test_env_not_found_returns_skipped(self, env_dir: Path) -> None:
        """When .env is absent the check returns available=True with a skipped version."""
        write_example(env_dir, "# Key\nKEY_A=val\n")
        # Deliberately do NOT create .env

        result = check_env_completeness(env_dir)

        assert result.available is True
        assert result.version is not None
        assert "skipped" in result.version.lower()
        assert ".env not found" in result.version.lower()

    def test_env_example_not_found_returns_skipped(self, env_dir: Path) -> None:
        """When .env.example is absent the check returns available=True with skipped."""
        # Neither file exists
        result = check_env_completeness(env_dir)

        assert result.available is True
        assert result.version is not None
        assert "skipped" in result.version.lower()


class TestUnreadableEnvReturnsSkipped:
    def test_unreadable_env_returns_skipped(self, env_dir: Path) -> None:
        """If reading a file raises OSError the function returns a skipped ToolCheck."""
        write_example(env_dir, "# Key\nKEY_A=val\n")
        write_env(env_dir, "KEY_A=x\n")

        with patch(
            "scripts.update.verify._parse_env_example",
            side_effect=OSError("permission denied"),
        ):
            result = check_env_completeness(env_dir)

        assert result.available is True
        assert result.version is not None
        assert "skipped" in result.version.lower()
        assert "read error" in result.version.lower()


class TestParsingEdgeCases:
    def test_multiline_comment_block_uses_first_line(self, env_dir: Path) -> None:
        """Multi-line comment blocks use the first non-empty line — the topic
        sentence, not a wrapped fragment's tail (#2845)."""
        write_example(
            env_dir,
            ("# First line of comment\n# Second line — the actual description\nMY_KEY=value\n"),
        )
        write_env(env_dir, "# empty\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "First line of comment" in result.error
        assert "Second line" not in result.error

    def test_section_separator_does_not_bleed_into_description(self, env_dir: Path) -> None:
        """Section separator lines (# ===) are not used as descriptions."""
        write_example(
            env_dir,
            (
                "# ==============================\n"
                "# Infrastructure\n"
                "# ==============================\n"
                "# Actual description\n"
                "REDIS_URL=redis://localhost\n"
            ),
        )
        write_env(env_dir, "# empty\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        # Section separators should not appear; actual description should
        assert "Actual description" in result.error
        assert "===" not in result.error

    def test_comment_lines_in_env_are_ignored(self, env_dir: Path) -> None:
        """Comment lines in .env are not treated as keys."""
        write_example(env_dir, "# API key\nAPI_KEY=sk-****\n")
        write_env(env_dir, "# API_KEY=should-not-match\nAPI_KEY=real-value\n")

        result = check_env_completeness(env_dir)

        assert result.available is True

    def test_no_keys_in_env_example_returns_available(self, env_dir: Path) -> None:
        """.env.example with no KEY= declarations yields available=True (nothing to check)."""
        write_example(env_dir, "# Just comments\n# No keys here\n")
        write_env(env_dir, "SOMETHING=value\n")

        result = check_env_completeness(env_dir)

        assert result.available is True


class TestOptionalSigil:
    def test_missing_optional_key_not_reported(self, env_dir: Path) -> None:
        """A key marked @optional and absent from .env is NOT in the missing report."""
        write_example(
            env_dir,
            "# A tunable with an in-code default\n# @optional\nZZ_FIXTURE_OPTIONAL=1\n",
        )
        write_env(env_dir, "# nothing\n")

        result = check_env_completeness(env_dir)

        assert result.available is True
        assert "1 optional unset" in (result.version or "")

    def test_missing_unmarked_key_still_reported_required_by_default(self, env_dir: Path) -> None:
        """A key with no @optional marker stays required — the fail-closed default."""
        write_example(env_dir, "# A required secret\nZZ_FIXTURE_REQUIRED=1\n")
        write_env(env_dir, "# nothing\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "ZZ_FIXTURE_REQUIRED" in result.error

    def test_prose_optional_does_not_mark_key_optional(self, env_dir: Path) -> None:
        """A comment merely containing the word "Optional" must NOT set the axis —
        only the bare `@optional` sigil does. Guards a real credential sitting
        beneath prose like '# Optional. GitHub PAT for ...'."""
        write_example(
            env_dir,
            "# Optional. GitHub PAT for a dedicated bot account\nZZ_FIXTURE_CREDENTIAL=1\n",
        )
        write_env(env_dir, "# nothing\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "ZZ_FIXTURE_CREDENTIAL" in result.error
        assert "0 optional unset" in result.error

    def test_missing_required_key_reports_residual_on_error_branch(self, env_dir: Path) -> None:
        """The optional-unset residual rides on BOTH return branches, not just success."""
        write_example(
            env_dir,
            (
                "# A required secret\nZZ_FIXTURE_REQUIRED=1\n"
                "# A tunable with an in-code default\n# @optional\nZZ_FIXTURE_OPTIONAL=1\n"
            ),
        )
        write_env(env_dir, "# nothing\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "1 missing" in result.error
        assert "ZZ_FIXTURE_REQUIRED" in result.error
        assert "ZZ_FIXTURE_OPTIONAL" not in result.error
        assert "1 optional unset" in result.error

    def test_all_present_success_string_counts_required_not_declared(self, env_dir: Path) -> None:
        """The success string's count excludes optional keys — it would otherwise
        disagree with itself in one sentence."""
        write_example(
            env_dir,
            "# A required secret\nZZ_FIXTURE_REQUIRED=1\n# @optional\nZZ_FIXTURE_OPTIONAL=1\n",
        )
        write_env(env_dir, "ZZ_FIXTURE_REQUIRED=1\nZZ_FIXTURE_OPTIONAL=1\n")

        result = check_env_completeness(env_dir)

        assert result.available is True
        assert "all 1 required vars present" in (result.version or "")
        assert "0 optional unset" in (result.version or "")


class TestPassthroughSigil:
    def test_passthrough_key_still_reported_missing(self, env_dir: Path) -> None:
        """@passthrough marks a key as having no Python reader — it is still
        required (an external binary authenticates with it). The two axes
        are orthogonal."""
        write_example(
            env_dir,
            "# Service account token\n# @passthrough op\nZZ_FIXTURE_PASSTHROUGH=1\n",
        )
        write_env(env_dir, "# nothing\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "ZZ_FIXTURE_PASSTHROUGH" in result.error

    def test_passthrough_marker_excluded_from_description(self, env_dir: Path) -> None:
        write_example(
            env_dir,
            "# Service account token\n# @passthrough op\nZZ_FIXTURE_PASSTHROUGH=1\n",
        )
        write_env(env_dir, "# nothing\n")

        result = check_env_completeness(env_dir)

        assert result.error is not None
        assert "Service account token" in result.error
        assert "@passthrough" not in result.error


class TestInlineCap:
    def test_inline_key_list_capped_with_more_suffix(self, env_dir: Path) -> None:
        example_lines = [f"# Key {i}\nZZ_FIXTURE_KEY_{i}=x" for i in range(8)]
        write_example(env_dir, "\n".join(example_lines) + "\n")
        write_env(env_dir, "# nothing\n")

        result = check_env_completeness(env_dir)

        assert result.available is False
        assert result.error is not None
        assert "8 missing" in result.error
        assert "+3 more" in result.error
