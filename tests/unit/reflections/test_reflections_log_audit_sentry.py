"""Unit tests for the Sentry-counts helper used by the log_audit slot.

The helper originally lived in ``reflections/auditing.py`` (where it was
called by ``run_log_review``). Issue #1292 retired the legacy
``daily-log-review`` reflection and inlined this helper into the slot
module. The tests now point at the new home.

All tests use ``unittest.mock.patch`` on ``subprocess.run`` and
``shutil.which`` — no real Sentry calls.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from reflections.pm_briefings import log_audit


class TestCollectSentryCounts:
    """Per-project Sentry helper must return None on every error path."""

    def test_returns_none_when_sentry_cli_missing(self):
        """Missing sentry-cli on PATH → None (no subprocess invocation)."""
        with patch("reflections.pm_briefings.log_audit.shutil.which", return_value=None):
            result = log_audit._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": "/tmp"}
            )
        assert result is None

    def test_returns_none_when_dsn_missing(self, tmp_path):
        """Project with no SENTRY_DSN in .env → None."""
        # Make sentry-cli "available" but ensure no .env file exists.
        with patch(
            "reflections.pm_briefings.log_audit.shutil.which",
            return_value="/usr/local/bin/sentry-cli",
        ):
            result = log_audit._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_none_when_dsn_blank(self, tmp_path):
        """Empty SENTRY_DSN= line → None."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=\nOTHER=value\n")
        with patch(
            "reflections.pm_briefings.log_audit.shutil.which",
            return_value="/usr/local/bin/sentry-cli",
        ):
            result = log_audit._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_none_when_subprocess_times_out(self, tmp_path):
        """sentry-cli hanging → TimeoutExpired → None."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        with (
            patch(
                "reflections.pm_briefings.log_audit.shutil.which",
                return_value="/usr/local/bin/sentry-cli",
            ),
            patch(
                "reflections.pm_briefings.log_audit.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="sentry-cli", timeout=10),
            ),
        ):
            result = log_audit._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_none_when_subprocess_returns_nonzero(self, tmp_path):
        """sentry-cli exits non-zero → None."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        with (
            patch(
                "reflections.pm_briefings.log_audit.shutil.which",
                return_value="/usr/local/bin/sentry-cli",
            ),
            patch(
                "reflections.pm_briefings.log_audit.subprocess.run",
                return_value=MagicMock(returncode=1, stdout="", stderr="auth failed"),
            ),
        ):
            result = log_audit._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        """sentry-cli prints non-JSON → JSONDecodeError → None."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        with (
            patch(
                "reflections.pm_briefings.log_audit.shutil.which",
                return_value="/usr/local/bin/sentry-cli",
            ),
            patch(
                "reflections.pm_briefings.log_audit.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="not json{{{", stderr=""),
            ),
        ):
            result = log_audit._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_summary_string_on_success(self, tmp_path):
        """Happy path: returns one-line summary with project slug and count."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        issues = [{"id": "i1"}, {"id": "i2"}, {"id": "i3"}]
        with (
            patch(
                "reflections.pm_briefings.log_audit.shutil.which",
                return_value="/usr/local/bin/sentry-cli",
            ),
            patch(
                "reflections.pm_briefings.log_audit.subprocess.run",
                return_value=MagicMock(returncode=0, stdout=json.dumps(issues), stderr=""),
            ),
        ):
            result = log_audit._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is not None
        assert "[proj-a]" in result
        assert "3 unresolved" in result

    def test_returns_none_on_zero_unresolved(self, tmp_path):
        """When unresolved count is 0, return None (no noise in findings)."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        with (
            patch(
                "reflections.pm_briefings.log_audit.shutil.which",
                return_value="/usr/local/bin/sentry-cli",
            ),
            patch(
                "reflections.pm_briefings.log_audit.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="[]", stderr=""),
            ),
        ):
            result = log_audit._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None


class TestReadLogTextBounded:
    """Tail-read guard: large files must not be loaded fully into memory."""

    def test_tails_files_over_size_cap(self, tmp_path):
        """_read_log_text_bounded returns only the tail for files over the size cap."""
        log_file = tmp_path / "huge.log"
        # Build a file larger than the 50 MB trip point cheaply.
        chunk = b"x" * (1024 * 1024)  # 1 MB of 'x'
        size_mb = 55
        with open(log_file, "wb") as f:
            for _ in range(size_mb):
                f.write(chunk)
            # Trailing marker we expect to see after the truncation header.
            f.write(b"TAIL_MARKER\n")

        text = log_audit._read_log_text_bounded(log_file)
        # Truncation notice is present and we still saw the final marker.
        assert "truncated: showing last" in text
        assert "TAIL_MARKER" in text
        # The returned string is bounded: 1 MB tail + a short header, not 55 MB.
        assert len(text) < 2 * 1024 * 1024
