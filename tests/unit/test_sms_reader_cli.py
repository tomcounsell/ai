"""Unit tests for the sms_reader CLI error handling.

Regression coverage for the `/update` verifier warning that surfaced a raw
`Traceback (most recent call last)` instead of the actionable SMSReaderError
message when Full Disk Access is not granted (or the Messages DB is absent).
"""

from unittest import mock

import pytest

from tools.sms_reader import SMSReaderError
from tools.sms_reader import cli as sms_cli

pytestmark = pytest.mark.sdlc


def test_dispatch_smsreadererror_becomes_clean_stderr(capsys):
    """A raised SMSReaderError exits 1 with a clean `sms: <message>` line and no traceback."""
    with mock.patch.object(
        sms_cli,
        "get_recent_messages",
        side_effect=SMSReaderError(
            "Cannot open Messages database. Grant Full Disk Access to your terminal.",
            category="permission",
        ),
    ):
        with mock.patch.object(sms_cli.sys, "argv", ["sms", "recent", "--limit", "1"]):
            with pytest.raises(SystemExit) as excinfo:
                sms_cli.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert captured.err.strip() == (
        "sms: Cannot open Messages database. Grant Full Disk Access to your terminal."
    )


def test_dispatch_wraps_all_subcommands(capsys):
    """The error wrapper covers every subcommand, not just `recent`."""
    with mock.patch.object(
        sms_cli,
        "list_senders",
        side_effect=SMSReaderError("Database error: locked", category="database"),
    ):
        with mock.patch.object(sms_cli.sys, "argv", ["sms", "senders"]):
            with pytest.raises(SystemExit) as excinfo:
                sms_cli.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.strip() == "sms: Database error: locked"


def test_dispatch_success_path_not_swallowed(capsys):
    """A normal (non-error) invocation still prints JSON and does not exit non-zero."""
    with mock.patch.object(sms_cli, "get_recent_messages", return_value=[]):
        with mock.patch.object(sms_cli.sys, "argv", ["sms", "recent", "--limit", "1"]):
            # No SystemExit expected on the success path.
            sms_cli.main()

    captured = capsys.readouterr()
    assert captured.out.strip() == "[]"
    assert captured.err == ""
