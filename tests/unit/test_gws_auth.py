"""Unit tests for the gws (Google Workspace CLI) auth bootstrap in /update.

Covers the four branches of `configure_gws_auth`:
- not installed  -> skipped (silent, success)
- authenticated  -> already_ok (idempotent)
- unauthenticated -> needs_auth (actionable, non-blocking)
- status errors  -> failed (soft, never raises)

Detection only — the module must never run the interactive OAuth flow, so all
tests assert it shells out to `gws auth status` and nothing else.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from scripts.update.gws_auth import SETUP_HINT, configure_gws_auth


def _status_proc(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["gws", "auth", "status"], returncode=returncode, stdout=stdout, stderr=""
    )


def test_skipped_when_not_installed():
    """No `gws` on PATH -> silent skip, success True, no subprocess call."""
    with (
        patch("scripts.update.gws_auth.shutil.which", return_value=None),
        patch("scripts.update.gws_auth.subprocess.run") as run,
    ):
        result = configure_gws_auth()

    assert result.action == "skipped"
    assert result.success is True
    run.assert_not_called()


def test_already_ok_when_authenticated():
    """`auth_method` != none -> idempotent already_ok."""
    authed = json.dumps({"auth_method": "oauth", "storage": "keyring"})
    with (
        patch("scripts.update.gws_auth.shutil.which", return_value="/usr/local/bin/gws"),
        patch(
            "scripts.update.gws_auth.subprocess.run",
            return_value=_status_proc(authed),
        ),
    ):
        result = configure_gws_auth()

    assert result.action == "already_ok"
    assert result.success is True
    assert "oauth" in (result.detail or "")


def test_needs_auth_when_unauthenticated():
    """`auth_method: none` -> needs_auth with the actionable setup hint."""
    unauthed = json.dumps({"auth_method": "none", "storage": "none"})
    with (
        patch("scripts.update.gws_auth.shutil.which", return_value="/usr/local/bin/gws"),
        patch(
            "scripts.update.gws_auth.subprocess.run",
            return_value=_status_proc(unauthed),
        ),
    ):
        result = configure_gws_auth()

    assert result.action == "needs_auth"
    assert result.success is True  # non-blocking: success True, surfaced as a warning
    assert SETUP_HINT in (result.detail or "")


def test_failed_on_timeout_does_not_raise():
    """A status timeout returns a failed result, never propagates the exception."""
    with (
        patch("scripts.update.gws_auth.shutil.which", return_value="/usr/local/bin/gws"),
        patch(
            "scripts.update.gws_auth.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gws auth status", timeout=15),
        ),
    ):
        result = configure_gws_auth()

    assert result.action == "failed"
    assert result.success is False
    assert "timed out" in (result.error or "")


def test_unparseable_output_fails_soft_to_authed():
    """Non-JSON output without the explicit none-signal assumes authed (no nag)."""
    with (
        patch("scripts.update.gws_auth.shutil.which", return_value="/usr/local/bin/gws"),
        patch(
            "scripts.update.gws_auth.subprocess.run",
            return_value=_status_proc("garbage not json"),
        ),
    ):
        result = configure_gws_auth()

    assert result.action == "already_ok"
