"""Unit tests for tools/computer/ -- bcu wrapper, selector resolution, OS gate.

These are pure unit tests with mocked HTTP responses. No live bcu, no
Playwright, no network. Integration tests live in
``tools/computer/tests/test_computer_use_integration.py`` and are marked
``@pytest.mark.integration`` -- they skip when bcu is not running.
"""

from __future__ import annotations

import io
import json
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_manifest(tmp_path, monkeypatch):
    """Plant a runtime-manifest.json under a fake TMPDIR pointing at example.test."""
    bcu_dir = tmp_path / "background-computer-use"
    bcu_dir.mkdir()
    manifest = bcu_dir / "runtime-manifest.json"
    manifest.write_text(json.dumps({"base_url": "http://127.0.0.1:9999"}))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    return manifest


@pytest.fixture
def no_manifest(tmp_path, monkeypatch):
    """Point TMPDIR at a directory with no bcu manifest."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Mocked HTTP helper
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: dict | bytes, status: int = 200):
        self._status = status
        if isinstance(payload, bytes):
            self._body = payload
        else:
            self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(payload: dict | bytes, status: int = 200):
    return patch("urllib.request.urlopen", return_value=_FakeResp(payload, status))


# ---------------------------------------------------------------------------
# Manifest tests
# ---------------------------------------------------------------------------


def test_missing_manifest_raises_unavailable(no_manifest):
    from tools.computer import ComputerUseUnavailableError, list_apps

    with pytest.raises(ComputerUseUnavailableError):
        list_apps()


def test_corrupt_manifest_raises_unavailable(tmp_path, monkeypatch):
    from tools.computer import ComputerUseUnavailableError, list_apps

    bcu_dir = tmp_path / "background-computer-use"
    bcu_dir.mkdir()
    (bcu_dir / "runtime-manifest.json").write_text("not json")
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    with pytest.raises(ComputerUseUnavailableError):
        list_apps()


def test_manifest_without_base_url_raises_unavailable(tmp_path, monkeypatch):
    from tools.computer import ComputerUseUnavailableError, list_apps

    bcu_dir = tmp_path / "background-computer-use"
    bcu_dir.mkdir()
    (bcu_dir / "runtime-manifest.json").write_text(json.dumps({"version": "1"}))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    with pytest.raises(ComputerUseUnavailableError):
        list_apps()


# ---------------------------------------------------------------------------
# Endpoint wrappers
# ---------------------------------------------------------------------------


def test_list_apps_returns_payload(fake_manifest):
    from tools.computer import list_apps

    payload = {"apps": [{"bundle_id": "com.apple.Notes", "name": "Notes", "pid": 100}]}
    with _patch_urlopen(payload):
        result = list_apps()
    assert result == payload


def test_list_windows_filters_by_bundle_id(fake_manifest):
    from tools.computer import list_windows

    captured = {}

    def _fake_urlopen(req, timeout=10.0):
        captured["url"] = req.full_url
        return _FakeResp({"windows": []})

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        list_windows(bundle_id="com.apple.Notes")
    assert "bundle_id=" in captured["url"]
    assert "com.apple.Notes" in captured["url"]


def test_get_window_state_includes_window_id(fake_manifest):
    from tools.computer import get_window_state

    captured = {}

    def _fake_urlopen(req, timeout=10.0):
        captured["url"] = req.full_url
        return _FakeResp({"root": {"role": "AXWindow"}})

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        get_window_state(42)
    assert "window_id=42" in captured["url"]


def test_click_with_coords_sends_xy(fake_manifest):
    from tools.computer import click

    captured = {}

    def _fake_urlopen(req, timeout=10.0):
        captured["body"] = json.loads(req.data.decode("utf-8")) if req.data else None
        return _FakeResp({"ok": True})

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        click(7, x=100.0, y=200.0)
    assert captured["body"]["window_id"] == 7
    assert captured["body"]["x"] == 100.0
    assert captured["body"]["y"] == 200.0


def test_click_requires_one_of_xy_ref_selector(fake_manifest):
    from tools.computer import click

    with pytest.raises(ValueError):
        click(7)


def test_click_rejects_empty_selector(fake_manifest):
    from tools.computer import click

    with pytest.raises(ValueError):
        click(7, selector={})


def test_type_text_empty_string_is_valid(fake_manifest):
    """Empty string is a valid no-op (per plan); should not raise."""
    from tools.computer import type_text

    with _patch_urlopen({"ok": True}):
        result = type_text(7, "")
    assert result == {"ok": True}


def test_press_key_with_modifiers(fake_manifest):
    from tools.computer import press_key

    captured = {}

    def _fake(req, timeout=10.0):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"ok": True})

    with patch("urllib.request.urlopen", side_effect=_fake):
        press_key(7, "a", modifiers=["cmd", "shift"])
    assert captured["body"]["key"] == "a"
    assert captured["body"]["modifiers"] == ["cmd", "shift"]


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


def test_http_404_returns_window_not_found(fake_manifest):
    """When bcu returns 404 for a window-keyed call, get a structured error dict."""
    import urllib.error

    from tools.computer import click

    err = urllib.error.HTTPError(
        url="http://x/", code=404, msg="Not Found", hdrs={}, fp=io.BytesIO(b"")
    )
    with patch("urllib.request.urlopen", side_effect=err):
        result = click(99, x=1.0, y=2.0)
    assert result["error"] == "window_not_found"
    assert result["window_id"] == 99


def test_http_500_returns_structured_error(fake_manifest):
    import urllib.error

    from tools.computer import list_apps

    err = urllib.error.HTTPError(
        url="http://x/",
        code=500,
        msg="Server Error",
        hdrs={},
        fp=io.BytesIO(b"boom"),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        result = list_apps()
    assert result["error"] == "http_500"
    assert "boom" in result.get("message", "")


def test_connection_refused_raises_unavailable(fake_manifest):
    """ECONNREFUSED at the loopback URL means bcu app isn't running."""
    import urllib.error

    from tools.computer import ComputerUseUnavailableError, list_apps

    err = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(ComputerUseUnavailableError):
            list_apps()


# ---------------------------------------------------------------------------
# Electron selector resolution (Race 3 mitigation)
# ---------------------------------------------------------------------------


def test_click_with_selector_re_queries_window_state(fake_manifest):
    """click(selector=...) must re-query get_window_state and resolve to a fresh ref."""
    from tools.computer import click

    state_payload = {
        "root": {
            "role": "AXWindow",
            "label": "Slack",
            "ref": "win-ref",
            "children": [
                {
                    "role": "AXButton",
                    "label": "Send",
                    "ref": "fresh-ref-after-rebuild",
                    "bounds": [10, 20, 30, 40],
                },
                {
                    "role": "AXButton",
                    "label": "Cancel",
                    "ref": "other-ref",
                    "bounds": [100, 200, 30, 40],
                },
            ],
        }
    }
    click_payload = {"ok": True}

    call_log = []

    def _fake(req, timeout=10.0):
        url = req.full_url
        call_log.append(url)
        if "/v1/get_window_state" in url:
            return _FakeResp(state_payload)
        if "/v1/click" in url:
            captured = json.loads(req.data.decode("utf-8"))
            call_log.append(("click_body", captured))
            return _FakeResp(click_payload)
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("urllib.request.urlopen", side_effect=_fake):
        result = click(
            7,
            selector={
                "role": "AXButton",
                "label": "Send",
                "bundle_id": "com.tinyspeck.slackmacgap",
            },
        )

    # get_window_state must have been called (the re-query)
    assert any("/v1/get_window_state" in c for c in call_log if isinstance(c, str))
    # click must have been called with the freshly-resolved ref
    click_call = next(c for c in call_log if isinstance(c, tuple) and c[0] == "click_body")
    assert click_call[1]["ref"] == "fresh-ref-after-rebuild"
    assert result == click_payload


def test_selector_no_match_returns_error(fake_manifest):
    from tools.computer import click

    state_payload = {
        "root": {"role": "AXWindow", "ref": "w", "children": []},
    }

    def _fake(req, timeout=10.0):
        return _FakeResp(state_payload)

    with patch("urllib.request.urlopen", side_effect=_fake):
        result = click(7, selector={"role": "AXButton", "label": "Missing"})
    assert result["error"] == "selector_no_match"


def test_set_value_with_selector_re_queries(fake_manifest):
    """set_value also routes through the selector-resolution path."""
    from tools.computer import set_value

    state_payload = {
        "root": {
            "ref": "w",
            "children": [
                {
                    "role": "AXTextField",
                    "label": "Message",
                    "ref": "field-ref",
                    "bounds": [0, 0, 100, 30],
                }
            ],
        }
    }

    captured = {}

    def _fake(req, timeout=10.0):
        url = req.full_url
        if "/v1/get_window_state" in url:
            return _FakeResp(state_payload)
        if "/v1/set_value" in url:
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp({"ok": True})
        raise AssertionError(f"Unexpected URL: {url}")

    with patch("urllib.request.urlopen", side_effect=_fake):
        set_value(7, "hello", selector={"role": "AXTextField", "label": "Message"})
    assert captured["body"]["ref"] == "field-ref"
    assert captured["body"]["value"] == "hello"


# ---------------------------------------------------------------------------
# Electron bundle helper
# ---------------------------------------------------------------------------


def test_is_electron_bundle_known_apps():
    from tools.computer.electron_bundles import is_electron_bundle

    assert is_electron_bundle("com.tinyspeck.slackmacgap")
    assert is_electron_bundle("com.microsoft.VSCode")
    assert is_electron_bundle("org.telegram.desktop")
    assert is_electron_bundle("com.hnc.Discord")


def test_is_electron_bundle_unknown_apps():
    from tools.computer.electron_bundles import is_electron_bundle

    assert not is_electron_bundle("com.apple.Notes")
    assert not is_electron_bundle("com.apple.Safari")
    assert not is_electron_bundle("")
    assert not is_electron_bundle(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# OS gate (CLI entry point)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["linux", "win32", "freebsd"])
def test_cli_os_gate_exits_78_on_non_darwin(platform, capsys, monkeypatch):
    """The CLI must exit 78 with the documented stderr message on non-darwin."""
    from tools.computer.cli import EX_CONFIG, main

    monkeypatch.setattr(sys, "platform", platform)
    rc = main(["list_apps"])
    assert rc == EX_CONFIG
    captured = capsys.readouterr()
    assert "computer-use is macOS-only" in captured.err
    assert platform in captured.err


def test_cli_os_gate_passes_on_darwin(monkeypatch):
    """On darwin, OS gate returns 0 so the command can proceed."""
    from tools.computer.cli import _enforce_os_gate

    monkeypatch.setattr(sys, "platform", "darwin")
    assert _enforce_os_gate() == 0


def test_cli_unavailable_exits_78(monkeypatch, no_manifest, capsys):
    """When bcu isn't running, the CLI prints the structured error and exits 78."""
    from tools.computer.cli import EX_CONFIG, main

    monkeypatch.setattr(sys, "platform", "darwin")
    rc = main(["list_apps"])
    assert rc == EX_CONFIG
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["error"] == "computer_use_unavailable"


def test_cli_dispatches_screenshot_to_output_path(fake_manifest, monkeypatch, tmp_path, capsys):
    """CLI screenshot_window --output writes the PNG to disk and emits saved_to."""
    import base64

    from tools.computer.cli import main

    monkeypatch.setattr(sys, "platform", "darwin")
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    payload = {"image_base64": base64.b64encode(fake_png).decode(), "width": 1, "height": 1}

    out_path = tmp_path / "out.png"
    with _patch_urlopen(payload):
        rc = main(["screenshot_window", "5", "--output", str(out_path)])
    assert rc == 0
    assert out_path.exists()
    assert out_path.read_bytes() == fake_png
    out_text = capsys.readouterr().out
    parsed = json.loads(out_text)
    assert parsed["saved_to"] == str(out_path)
