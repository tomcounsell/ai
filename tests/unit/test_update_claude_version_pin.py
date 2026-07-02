"""Unit tests for check_claude_version_pin() in scripts/update/verify.py (D1a, issue #1817).

The `claude` CLI is installed via the NATIVE installer
(~/.local/bin/claude -> ~/.local/share/claude/versions/<version>/), not npm.
This check compares the installed version to a pinned constant so a fleet-wide
auto-update is visible instead of silently drifting the scraped TUI contract
(D1b) out from under the PTY driver.

Default: a drift logs a WARNING and stays non-blocking (available=True).
CLAUDE_CONTRACT_CHECK_ENFORCE=1: a drift logs CRITICAL and hard-fails
(available=False), matching the check_projects_json / check_sdlc_tool
green-light-gate convention already used in this module.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from scripts.update.verify import (
    CLAUDE_CONTRACT_CHECK_ENFORCE_ENV,
    PINNED_CLAUDE_VERSION,
    check_claude_version_pin,
)


def _run_cmd_stub(version_str: str):
    def _stub(cmd, *a, **k):
        return type("R", (), {"stdout": version_str, "stderr": "", "returncode": 0})()

    return _stub


class TestVersionMatch:
    def test_matching_version_available_no_warning(self, caplog):
        """Installed version == pin: available, no warning logged."""
        with patch(
            "scripts.update.verify.run_cmd",
            side_effect=_run_cmd_stub(f"{PINNED_CLAUDE_VERSION} (Claude Code)"),
        ):
            with caplog.at_level(logging.WARNING, logger="scripts.update.verify"):
                check = check_claude_version_pin()

        assert check.available is True
        assert PINNED_CLAUDE_VERSION in (check.version or "")
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)


class TestVersionDriftDefault:
    def test_drift_warns_and_stays_non_blocking_by_default(self, monkeypatch, caplog):
        """A version mismatch with no enforce flag logs WARNING but available=True."""
        monkeypatch.delenv(CLAUDE_CONTRACT_CHECK_ENFORCE_ENV, raising=False)
        with patch(
            "scripts.update.verify.run_cmd",
            side_effect=_run_cmd_stub("9.9.9 (Claude Code)"),
        ):
            with caplog.at_level(logging.WARNING, logger="scripts.update.verify"):
                check = check_claude_version_pin()

        assert check.available is True
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        assert any("9.9.9" in r.message for r in caplog.records)


class TestVersionDriftEnforced:
    def test_drift_hard_fails_when_enforce_flag_set(self, monkeypatch, caplog):
        """A version mismatch with CLAUDE_CONTRACT_CHECK_ENFORCE=1 hard-fails."""
        monkeypatch.setenv(CLAUDE_CONTRACT_CHECK_ENFORCE_ENV, "1")
        with patch(
            "scripts.update.verify.run_cmd",
            side_effect=_run_cmd_stub("9.9.9 (Claude Code)"),
        ):
            with caplog.at_level(logging.CRITICAL, logger="scripts.update.verify"):
                check = check_claude_version_pin()

        assert check.available is False
        assert check.error is not None
        assert "9.9.9" in check.error
        assert any(r.levelno == logging.CRITICAL for r in caplog.records)

    def test_enforce_flag_only_triggers_on_literal_1(self, monkeypatch):
        """Any value other than the literal string '1' is treated as off."""
        monkeypatch.setenv(CLAUDE_CONTRACT_CHECK_ENFORCE_ENV, "true")
        with patch(
            "scripts.update.verify.run_cmd",
            side_effect=_run_cmd_stub("9.9.9 (Claude Code)"),
        ):
            check = check_claude_version_pin()

        assert check.available is True


class TestUnresolvableVersion:
    def test_unresolvable_version_skips_without_raising(self):
        """When neither `claude --version` nor the native symlink resolve, skip cleanly."""
        with (
            patch(
                "scripts.update.verify.run_cmd",
                side_effect=RuntimeError("claude not found"),
            ),
            patch("scripts.update.verify.Path.is_symlink", return_value=False),
        ):
            check = check_claude_version_pin()

        assert check.available is True
        assert check.version is not None
        assert "skipped" in check.version


class TestNativeInstallerNotManagedByNpm:
    def test_claude_not_in_npm_managed_packages(self):
        """claude must never be added to npm_tools.MANAGED_PACKAGES (native install only)."""
        from scripts.update.npm_tools import MANAGED_PACKAGES

        names = {pkg for pkg, _version in MANAGED_PACKAGES}
        assert "claude" not in names
        assert "@anthropic-ai/claude-code" not in names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
