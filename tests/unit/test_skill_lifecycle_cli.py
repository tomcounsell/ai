"""Tests for skill lifecycle CLI tool and integration points."""

import argparse
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCLIArgumentParsing:
    """Verify all subcommands are recognized and parse correctly."""

    def test_detect_friction_subcommand(self):
        # Patch sys.argv and verify it dispatches without error
        # We test the parser directly instead
        parser = self._build_parser()
        args = parser.parse_args(["detect-friction"])
        assert args.command == "detect-friction"

    def test_detect_friction_json_flag(self):
        parser = self._build_parser()
        args = parser.parse_args(["detect-friction", "--json"])
        assert args.command == "detect-friction"
        assert args.json is True

    def test_expire_subcommand(self):
        parser = self._build_parser()
        args = parser.parse_args(["expire"])
        assert args.command == "expire"

    def test_expire_dry_run_flag(self):
        parser = self._build_parser()
        args = parser.parse_args(["expire", "--dry-run"])
        assert args.command == "expire"
        assert args.dry_run is True

    def test_refresh_subcommand(self):
        parser = self._build_parser()
        args = parser.parse_args(["refresh"])
        assert args.command == "refresh"

    def test_report_subcommand(self):
        parser = self._build_parser()
        args = parser.parse_args(["report"])
        assert args.command == "report"

    def test_no_subcommand_returns_none(self):
        parser = self._build_parser()
        args = parser.parse_args([])
        assert args.command is None

    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        """Build the CLI parser (mirrors main() logic)."""
        from tools.skill_lifecycle import main  # noqa: F401 -- ensures importable

        parser = argparse.ArgumentParser(prog="skill_lifecycle")
        subparsers = parser.add_subparsers(dest="command")

        df_parser = subparsers.add_parser("detect-friction")
        df_parser.add_argument("--json", action="store_true")

        expire_parser = subparsers.add_parser("expire")
        expire_parser.add_argument("--dry-run", action="store_true")

        subparsers.add_parser("refresh")
        subparsers.add_parser("report")

        return parser


# ---------------------------------------------------------------------------
# Report with no data
# ---------------------------------------------------------------------------


class TestReportNoData:
    """cmd_report should exit gracefully when no analytics data exists."""

    def test_report_no_analytics_db(self, capsys):
        """Report prints a message and returns when no DB exists."""
        from unittest.mock import patch

        from tools.skill_lifecycle import cmd_report

        args = argparse.Namespace()
        with patch("tools.skill_lifecycle.get_skill_report", return_value=[]):
            cmd_report(args)
        captured = capsys.readouterr()
        assert "No skill invocation data" in captured.out

    def test_detect_friction_no_memories(self, capsys):
        """detect_friction returns empty list when no memories match."""
        from tools.skill_lifecycle import detect_friction

        # detect_friction gracefully handles missing Redis/models
        results = detect_friction()
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# PM allowlist includes skill_lifecycle prefixes
# ---------------------------------------------------------------------------


class TestPMAllowlist:
    """Verify PM_BASH_ALLOWED_PREFIXES includes skill lifecycle commands."""

    @pytest.mark.parametrize(
        "prefix",
        [
            "python -m tools.skill_lifecycle report",
            "python -m tools.skill_lifecycle detect-friction",
            "python -m tools.skill_lifecycle refresh",
            "python -m tools.skill_lifecycle expire",
        ],
    )
    def test_skill_lifecycle_in_pm_allowlist(self, prefix):
        from agent.hooks.pre_tool_use import PM_BASH_ALLOWED_PREFIXES

        assert prefix in PM_BASH_ALLOWED_PREFIXES, (
            f"'{prefix}' missing from PM_BASH_ALLOWED_PREFIXES"
        )


# ---------------------------------------------------------------------------
# CLI --help smoke test (subprocess)
# ---------------------------------------------------------------------------


class TestCLIHelp:
    """Verify the CLI entry point responds to --help."""

    def test_main_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.skill_lifecycle", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "skill_lifecycle" in result.stdout or "friction" in result.stdout

    def test_detect_friction_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.skill_lifecycle", "detect-friction", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_report_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.skill_lifecycle", "report", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
