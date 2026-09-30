"""Unit tests for the gws (Google Workspace CLI) update prereq + verify check.

Covers:
- `@googleworkspace/cli` is a managed npm package (so /update installs the
  `gws` binary on every machine).
- The optional `gws` verify check in `check_system_tools` stays silent and
  non-raising when `gws` is absent (the pre-install / Workspace-less reality),
  and surfaces a status line when present.
"""

from __future__ import annotations

from unittest.mock import patch

from scripts.update.npm_tools import MANAGED_PACKAGES
from scripts.update.verify import check_system_tools


def test_googleworkspace_cli_in_managed_packages():
    """`@googleworkspace/cli` must be a managed npm package (float on latest)."""
    names = {pkg for pkg, _version in MANAGED_PACKAGES}
    assert "@googleworkspace/cli" in names

    pinned = dict(MANAGED_PACKAGES)["@googleworkspace/cli"]
    assert pinned is None, "should float on latest (None), not pin a version"


def _which_stub(present: set[str]):
    """Return a shutil.which replacement that only resolves names in `present`."""

    def _which(name: str):
        return f"/usr/local/bin/{name}" if name in present else None

    return _which


def test_gws_check_silent_when_absent():
    """When `gws` is not on PATH, check_system_tools surfaces no gws line and never raises."""
    # Always-present base tools resolve; gws and sentry-cli are absent.
    base = {"claude", "gh", "git", "uv", "python", "python3"}
    with (
        patch("scripts.update.verify.shutil.which", side_effect=_which_stub(base)),
        patch(
            "scripts.update.verify.run_cmd",
            side_effect=lambda *a, **k: type(
                "R", (), {"stdout": "1.0", "stderr": "", "returncode": 0}
            )(),
        ),
    ):
        results = check_system_tools()

    names = {r.name for r in results}
    assert "gws" not in names, "gws check must stay silent when the binary is absent"


def test_gws_check_present_surfaces_status():
    """When `gws` is on PATH, check_system_tools includes a gws status line without raising."""
    base = {"claude", "gh", "git", "uv", "python", "python3", "gws"}
    with (
        patch("scripts.update.verify.shutil.which", side_effect=_which_stub(base)),
        patch(
            "scripts.update.verify.run_cmd",
            side_effect=lambda *a, **k: type(
                "R", (), {"stdout": "gws 0.22.5", "stderr": "", "returncode": 0}
            )(),
        ),
    ):
        results = check_system_tools()

    gws_checks = [r for r in results if r.name == "gws"]
    assert len(gws_checks) == 1
    assert gws_checks[0].available is True
