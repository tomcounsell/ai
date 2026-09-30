"""Integration tests for tools/computer/ -- live bcu HTTP calls (v0.1.0).

These tests require:
  - macOS host (sys.platform == 'darwin')
  - bcu (background-computer-use) v0.1.0 installed and running
  - $TMPDIR/background-computer-use/runtime-manifest.json present

Marked with ``@pytest.mark.integration`` so the default ``pytest tests/unit``
run skips them. They auto-skip when the manifest is absent so the regression
suite passes on machines without bcu installed.

To run locally:
    pytest tools/computer/tests/test_computer_use_integration.py -v -m integration
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _manifest_present() -> bool:
    tmpdir = os.environ.get("TMPDIR") or "/tmp"
    return (Path(tmpdir) / "background-computer-use" / "runtime-manifest.json").exists()


_SKIP_REASON = "bcu runtime manifest absent or platform != darwin"

_live = pytest.mark.skipif(sys.platform != "darwin" or not _manifest_present(), reason=_SKIP_REASON)


def _first_window_id(windows: dict) -> str | None:
    """Extract the string stable window ID from a live list_windows response."""
    win_list = windows.get("windows", []) if isinstance(windows, dict) else []
    if not win_list:
        return None
    win = win_list[0]
    for key in ("window", "id", "windowID"):
        value = win.get(key)
        if value:
            return str(value)
    return None


@_live
def test_list_apps_against_live_bcu():
    """list_apps round-trips through POST /v1/list_apps."""
    from tools.computer import list_apps

    result = list_apps()
    assert "error" not in result, result


@_live
def test_list_windows_against_live_bcu():
    """list_windows requires an app query and round-trips through POST."""
    from tools.computer import list_windows

    result = list_windows("Finder")
    assert "error" not in result, result


@_live
def test_notes_app_end_to_end():
    """Notes: list_windows -> get_window_state -> click -> type_text -> screenshot.

    Verifies the full v0.1.0 chain including imageMode screenshots. Caller
    is responsible for closing the spurious Notes draft after the test.
    """
    from tools.computer import click, get_window_state, list_windows, screenshot, type_text

    wins = list_windows("com.apple.Notes")
    if "error" in wins:
        pytest.skip(f"list_windows failed (Notes not running?): {wins}")
    window = _first_window_id(wins)
    if window is None:
        pytest.skip("Notes has no open window")

    # State + screenshot path mode
    state = get_window_state(window, image_mode="path")
    assert "error" not in state, state
    assert state.get("stateToken")

    # Click roughly in the body, type a marker
    click_result = click(window, x=400.0, y=300.0)
    assert "error" not in click_result, click_result

    typed = type_text(window, "valor-test-#2114")
    assert "error" not in typed, typed

    # Screenshot via get_window_state imageMode base64 -> file on disk
    out = Path("/tmp/valor-computer-integration.png")
    shot = screenshot(window, output=str(out))
    assert "error" not in shot, shot
    assert out.exists() and out.stat().st_size > 0
    out.unlink()
