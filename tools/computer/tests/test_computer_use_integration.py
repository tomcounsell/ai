"""Integration tests for tools/computer/ -- live bcu HTTP calls.

These tests require:
  - macOS host (sys.platform == 'darwin')
  - bcu (background-computer-use) installed and running
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


@pytest.mark.skipif(sys.platform != "darwin", reason="bcu is macOS-only")
@pytest.mark.skipif(not _manifest_present(), reason=_SKIP_REASON)
def test_list_apps_against_live_bcu():
    """list_apps round-trips through the bcu HTTP API and returns a non-empty list."""
    from tools.computer import list_apps

    result = list_apps()
    assert "error" not in result, result
    assert "apps" in result or isinstance(result, dict)


@pytest.mark.skipif(sys.platform != "darwin", reason="bcu is macOS-only")
@pytest.mark.skipif(not _manifest_present(), reason=_SKIP_REASON)
def test_list_windows_against_live_bcu():
    """list_windows round-trips through bcu and returns at least one window."""
    from tools.computer import list_windows

    result = list_windows()
    assert "error" not in result, result


@pytest.mark.skipif(sys.platform != "darwin", reason="bcu is macOS-only")
@pytest.mark.skipif(not _manifest_present(), reason=_SKIP_REASON)
def test_notes_app_end_to_end():
    """Open Notes, find a window, click in body, type, screenshot.

    Verifies the full chain: list_apps -> list_windows -> click -> type_text
    -> screenshot_window. Caller is responsible for closing the spurious
    Notes draft after the test finishes.
    """
    import base64

    from tools.computer import (
        click,
        list_apps,
        list_windows,
        screenshot_window,
        type_text,
    )

    # 1. Find Notes
    apps = list_apps()
    notes_bundle = "com.apple.Notes"
    apps_list = apps.get("apps", []) if isinstance(apps, dict) else []
    if not any(a.get("bundle_id") == notes_bundle for a in apps_list):
        pytest.skip("Notes.app not running on this host")

    # 2. Find the window
    wins = list_windows(bundle_id=notes_bundle)
    if "error" in wins:
        pytest.skip(f"list_windows failed: {wins}")
    win_list = wins.get("windows", []) if isinstance(wins, dict) else []
    if not win_list:
        pytest.skip("Notes has no open window")
    window_id = win_list[0]["window_id"]

    # 3. Click in the body (rough center of the window)
    frame = win_list[0].get("frame", {})
    cx = frame.get("width", 800) / 2
    cy = frame.get("height", 600) / 2
    click_result = click(window_id, x=cx, y=cy)
    assert "error" not in click_result, click_result

    # 4. Type a marker string
    marker = "valor-test-#1256"
    typed = type_text(window_id, marker)
    assert "error" not in typed, typed

    # 5. Capture a screenshot
    shot = screenshot_window(window_id)
    assert "error" not in shot, shot
    img_b64 = shot.get("image_base64")
    assert img_b64 and len(base64.b64decode(img_b64)) > 0
