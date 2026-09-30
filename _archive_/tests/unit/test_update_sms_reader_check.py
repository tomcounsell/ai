"""Tests for the sms_reader health check in scripts/update/verify.py.

Context: on a machine without macOS Full Disk Access, the process running
/update cannot open ~/Library/Messages/chat.db. The sms_reader package raises
a clean SMSReaderError, but historically the CLI let it propagate as a raw
multi-line Python traceback, and verify.py captured that whole traceback into
the /update warning line (which then got truncated in the report).

The fix keeps the check honest but tidy:
- tools/sms_reader/cli.py catches SMSReaderError and prints ONE actionable line.
- verify._classify_sms_reader() turns the CLI result into a ToolCheck, degrading
  the known Full-Disk-Access / DB-not-found environment condition to a clean,
  single-line WARNING (like the `gws auth` line) and never surfacing a raw
  traceback for any other failure either.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from scripts.update.verify import _classify_sms_reader


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["python", "-m", "tools.sms_reader.cli", "recent", "--limit", "1"],
        returncode=returncode,
        stdout="",
        stderr=stderr,
    )


class TestClassifySmsReader:
    def test_success_is_available(self) -> None:
        """A zero exit means the Messages DB was read — tool is available."""
        check = _classify_sms_reader(_completed(0, stderr=""))
        assert check.name == "sms_reader"
        assert check.available is True
        assert check.error is None

    def test_full_disk_access_degrades_to_clean_one_liner(self) -> None:
        """Full Disk Access condition → single actionable line, no traceback."""
        stderr = (
            "sms_reader unavailable: Cannot open Messages database. Grant Full "
            "Disk Access to your terminal. System Preferences > Security & "
            "Privacy > Privacy > Full Disk Access"
        )
        check = _classify_sms_reader(_completed(1, stderr=stderr))
        assert check.available is False
        assert check.error is not None
        # Exactly one line — never a multi-line traceback dump.
        assert "\n" not in check.error
        assert "Full Disk Access" in check.error
        assert "Traceback" not in check.error

    def test_db_not_found_degrades_to_clean_one_liner(self) -> None:
        """Missing chat.db (Messages never used) → clean single-line warning."""
        stderr = (
            "sms_reader unavailable: Messages database not found at "
            "/Users/x/Library/Messages/chat.db. Make sure Messages app has "
            "been used."
        )
        check = _classify_sms_reader(_completed(1, stderr=stderr))
        assert check.available is False
        assert check.error is not None
        assert "\n" not in check.error
        assert "Full Disk Access" in check.error

    def test_raw_traceback_is_collapsed_to_first_line(self) -> None:
        """Even an unexpected multi-line stderr must never dump a traceback."""
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "/Users/x/tools/sms_reader/__init__.py", line 107\n'
            "    conn = sqlite3.connect(...)\n"
            "sqlite3.OperationalError: something unexpected"
        )
        check = _classify_sms_reader(_completed(1, stderr=stderr))
        assert check.available is False
        assert check.error is not None
        assert "\n" not in check.error
        # Genuine failures keep only the first stderr line for triage.
        assert check.error == "Traceback (most recent call last):"

    def test_empty_stderr_failure_has_placeholder(self) -> None:
        """A non-zero exit with no stderr still yields a usable one-liner."""
        check = _classify_sms_reader(_completed(1, stderr=""))
        assert check.available is False
        assert check.error == "no error output"


class TestCliCleanError:
    """The CLI itself must emit one clean line for SMSReaderError, not a trace."""

    def test_cli_prints_single_line_for_sms_reader_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import tools.sms_reader.cli as cli

        def _boom(*args, **kwargs):
            raise cli.SMSReaderError(
                "Cannot open Messages database. Grant Full Disk Access.",
                category="permission_denied",
            )

        monkeypatch.setattr(cli, "get_recent_messages", _boom)
        monkeypatch.setattr(sys, "argv", ["sms", "recent", "--limit", "1"])

        with pytest.raises(SystemExit) as exc:
            cli.main()

        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        # Single actionable line on stderr.
        assert captured.err.strip().count("\n") == 0
        assert "sms_reader unavailable" in captured.err
        assert "Full Disk Access" in captured.err
