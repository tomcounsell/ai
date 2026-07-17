"""Contract-level tests for tools/computer/ against the bcu v0.1.0 API.

Every request the module emits is asserted against a real (loopback,
stdlib ``http.server``) fake server: HTTP method, path, and full JSON body
shape per ``RouteRegistry.swift`` in the pinned v0.1.0 release. No live
bcu, no mocks on urllib. Live integration tests are in
``test_computer_use_integration.py`` (auto-skip when the manifest is
absent).
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# ---------------------------------------------------------------------------
# Fake bcu server
# ---------------------------------------------------------------------------


class _RecordingHandler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        self.server.requests.append(  # type: ignore[attr-defined]
            {"method": self.command, "path": self.path, "body": body}
        )
        status, payload = self.server.responses.get(  # type: ignore[attr-defined]
            self.path, (200, {"ok": True})
        )
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def log_message(self, *args) -> None:  # silence test output
        pass


class _FakeBcu:
    """Loopback fake bcu server; records requests, serves canned responses."""

    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
        self.server.requests = []  # type: ignore[attr-defined]
        self.server.responses = {}  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list[dict]:
        return self.server.requests  # type: ignore[attr-defined]

    def respond(self, path: str, payload: dict, status: int = 200) -> None:
        self.server.responses[path] = (status, payload)  # type: ignore[attr-defined]

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def bcu(tmp_path, monkeypatch):
    """Start a fake bcu server and plant a runtime manifest pointing at it."""
    fake = _FakeBcu()
    bcu_dir = tmp_path / "background-computer-use"
    bcu_dir.mkdir()
    (bcu_dir / "runtime-manifest.json").write_text(json.dumps({"baseURL": fake.base_url}))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    yield fake
    fake.shutdown()


@pytest.fixture
def no_manifest(tmp_path, monkeypatch):
    """Point TMPDIR at a directory with no bcu manifest."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    return tmp_path


_TARGET = {"kind": "node_id", "value": "n42"}


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------


def test_missing_manifest_raises_unavailable(no_manifest):
    from tools.computer import ComputerUseUnavailableError, list_apps

    with pytest.raises(ComputerUseUnavailableError):
        list_apps()


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"version": "1"})],
    ids=["corrupt", "no-base-url"],
)
def test_bad_manifest_raises_unavailable(tmp_path, monkeypatch, content):
    from tools.computer import ComputerUseUnavailableError, list_apps

    bcu_dir = tmp_path / "background-computer-use"
    bcu_dir.mkdir()
    (bcu_dir / "runtime-manifest.json").write_text(content)
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    with pytest.raises(ComputerUseUnavailableError):
        list_apps()


def test_manifest_accepts_snake_case_base_url(tmp_path, monkeypatch, bcu):
    """Dual-casing: base_url (snake_case) works alongside v0.1.0's baseURL."""
    from tools.computer import list_apps

    bcu_dir = tmp_path / "background-computer-use"
    (bcu_dir / "runtime-manifest.json").write_text(json.dumps({"base_url": bcu.base_url}))
    assert list_apps() == {"ok": True}


# ---------------------------------------------------------------------------
# Contract: method, path, and full body shape for every route
# ---------------------------------------------------------------------------


def _call(bcu, fn, *args, **kwargs):
    result = fn(*args, **kwargs)
    assert len(bcu.requests) == 1
    return result, bcu.requests[0]


def test_list_apps_contract(bcu):
    from tools.computer import list_apps

    _, req = _call(bcu, list_apps)
    assert req == {"method": "POST", "path": "/v1/list_apps", "body": {}}


def test_list_windows_contract(bcu):
    from tools.computer import list_windows

    _, req = _call(bcu, list_windows, "com.apple.Notes")
    assert req == {
        "method": "POST",
        "path": "/v1/list_windows",
        "body": {"app": "com.apple.Notes"},
    }


def test_get_window_state_contract_defaults(bcu):
    from tools.computer import get_window_state

    _, req = _call(bcu, get_window_state, "w-1")
    assert req == {
        "method": "POST",
        "path": "/v1/get_window_state",
        "body": {"window": "w-1", "imageMode": "path"},
    }


def test_get_window_state_contract_optionals(bcu):
    from tools.computer import get_window_state

    _, req = _call(
        bcu, get_window_state, "w-1", image_mode="omit", include_menu_bar=True, max_nodes=50
    )
    assert req["body"] == {
        "window": "w-1",
        "imageMode": "omit",
        "includeMenuBar": True,
        "maxNodes": 50,
    }


def test_click_with_coordinates_contract(bcu):
    from tools.computer import click

    _, req = _call(bcu, click, "w-1", x=100, y=200)
    assert req == {
        "method": "POST",
        "path": "/v1/click",
        "body": {"window": "w-1", "x": 100.0, "y": 200.0},
    }


def test_click_with_target_and_options_contract(bcu):
    from tools.computer import click

    _, req = _call(
        bcu,
        click,
        "w-1",
        target=_TARGET,
        mode="double",
        click_count=2,
        mouse_button="right",
        state_token="tok-1",
    )
    assert req["path"] == "/v1/click"
    assert req["body"] == {
        "window": "w-1",
        "target": _TARGET,
        "mode": "double",
        "clickCount": 2,
        "mouseButton": "right",
        "stateToken": "tok-1",
    }


def test_scroll_contract(bcu):
    from tools.computer import scroll

    _, req = _call(bcu, scroll, "w-1", _TARGET, "down", pages=2.5, state_token="tok-1")
    assert req == {
        "method": "POST",
        "path": "/v1/scroll",
        "body": {
            "window": "w-1",
            "target": _TARGET,
            "direction": "down",
            "pages": 2.5,
            "stateToken": "tok-1",
        },
    }


def test_drag_contract(bcu):
    from tools.computer import drag

    _, req = _call(bcu, drag, "w-1", 300, 400)
    assert req == {
        "method": "POST",
        "path": "/v1/drag",
        "body": {"window": "w-1", "toX": 300.0, "toY": 400.0},
    }


def test_resize_contract(bcu):
    from tools.computer import resize

    _, req = _call(bcu, resize, "w-1", "bottomRight", 800, 600)
    assert req == {
        "method": "POST",
        "path": "/v1/resize",
        "body": {"window": "w-1", "handle": "bottomRight", "toX": 800.0, "toY": 600.0},
    }


def test_set_window_frame_contract(bcu):
    from tools.computer import set_window_frame

    _, req = _call(bcu, set_window_frame, "w-1", 0, 0, 1280, 720)
    assert req == {
        "method": "POST",
        "path": "/v1/set_window_frame",
        "body": {"window": "w-1", "x": 0.0, "y": 0.0, "width": 1280.0, "height": 720.0},
    }


def test_set_window_frame_animate_false_contract(bcu):
    from tools.computer import set_window_frame

    _, req = _call(bcu, set_window_frame, "w-1", 0, 0, 100, 100, animate=False)
    assert req["body"]["animate"] is False


def test_type_text_contract(bcu):
    from tools.computer import type_text

    _, req = _call(
        bcu,
        type_text,
        "w-1",
        "hello",
        target=_TARGET,
        focus_assist_mode="focus",
        state_token="tok-1",
    )
    assert req == {
        "method": "POST",
        "path": "/v1/type_text",
        "body": {
            "window": "w-1",
            "text": "hello",
            "target": _TARGET,
            "focusAssistMode": "focus",
            "stateToken": "tok-1",
        },
    }


def test_type_text_empty_string_is_valid(bcu):
    """Empty string is a valid no-op; body carries text: ''."""
    from tools.computer import type_text

    result, req = _call(bcu, type_text, "w-1", "")
    assert req["body"] == {"window": "w-1", "text": ""}
    assert result == {"ok": True}


def test_press_key_chord_contract(bcu):
    """v0.1.0 has NO modifiers array -- chords go in the key string."""
    from tools.computer import press_key

    _, req = _call(bcu, press_key, "w-1", "cmd+shift+a")
    assert req == {
        "method": "POST",
        "path": "/v1/press_key",
        "body": {"window": "w-1", "key": "cmd+shift+a"},
    }


def test_set_value_contract(bcu):
    from tools.computer import set_value

    _, req = _call(bcu, set_value, "w-1", _TARGET, "new text", state_token="tok-1")
    assert req == {
        "method": "POST",
        "path": "/v1/set_value",
        "body": {
            "window": "w-1",
            "target": _TARGET,
            "value": "new text",
            "stateToken": "tok-1",
        },
    }


def test_perform_secondary_action_contract(bcu):
    from tools.computer import perform_secondary_action

    _, req = _call(bcu, perform_secondary_action, "w-1", _TARGET, "Open Link", action_id="AXOpen")
    assert req == {
        "method": "POST",
        "path": "/v1/perform_secondary_action",
        "body": {
            "window": "w-1",
            "target": _TARGET,
            "action": "Open Link",
            "actionID": "AXOpen",
        },
    }


# ---------------------------------------------------------------------------
# click target XOR x/y
# ---------------------------------------------------------------------------


def test_click_rejects_both_target_and_xy(bcu):
    from tools.computer import click

    with pytest.raises(ValueError):
        click("w-1", target=_TARGET, x=1.0, y=2.0)
    assert bcu.requests == []


def test_click_rejects_neither_target_nor_xy(bcu):
    from tools.computer import click

    with pytest.raises(ValueError):
        click("w-1")
    assert bcu.requests == []


# ---------------------------------------------------------------------------
# screenshot convenience (get_window_state imageMode)
# ---------------------------------------------------------------------------

_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_screenshot_path_mode(bcu):
    from tools.computer import screenshot

    bcu.respond(
        "/v1/get_window_state",
        {
            "stateToken": "tok-1",
            "screenshot": {
                "image": {
                    "imagePath": "/tmp/bcu/shot.png",
                    "pixelWidth": 100,
                    "pixelHeight": 50,
                    "mimeType": "image/png",
                }
            },
        },
    )
    result, req = _call(bcu, screenshot, "w-1")
    assert req["body"] == {"window": "w-1", "imageMode": "path"}
    assert result["imagePath"] == "/tmp/bcu/shot.png"
    assert result["pixelWidth"] == 100


def test_screenshot_output_writes_file(bcu, tmp_path):
    from tools.computer import screenshot

    bcu.respond(
        "/v1/get_window_state",
        {
            "stateToken": "tok-1",
            "screenshot": {
                "image": {
                    "imageBase64": base64.b64encode(_FAKE_PNG).decode(),
                    "pixelWidth": 1,
                    "pixelHeight": 1,
                    "mimeType": "image/png",
                }
            },
        },
    )
    out_path = tmp_path / "out.png"
    result, req = _call(bcu, screenshot, "w-1", output=str(out_path))
    assert req["body"] == {"window": "w-1", "imageMode": "base64"}
    assert result["saved_to"] == str(out_path)
    assert out_path.read_bytes() == _FAKE_PNG


def test_screenshot_returns_error_dict_verbatim(bcu):
    """An error from get_window_state passes through untouched -- no KeyError,
    no base64 decode attempt on the error payload."""
    from tools.computer import screenshot

    bcu.respond("/v1/get_window_state", {}, status=404)
    result = screenshot("w-gone", output="/nonexistent/never-written.png")
    assert result == {"error": "not_found", "path": "/v1/get_window_state"}


# ---------------------------------------------------------------------------
# HTTP error mapping
# ---------------------------------------------------------------------------


def test_http_404_returns_not_found(bcu):
    from tools.computer import list_apps

    bcu.respond("/v1/list_apps", {}, status=404)
    result = list_apps()
    assert result == {"error": "not_found", "path": "/v1/list_apps"}


def test_http_500_returns_structured_error(bcu):
    from tools.computer import list_apps

    bcu.respond("/v1/list_apps", {"detail": "boom"}, status=500)
    result = list_apps()
    assert result["error"] == "http_500"
    assert "boom" in result["message"]


def test_connection_refused_raises_unavailable(tmp_path, monkeypatch):
    """ECONNREFUSED at the loopback URL means the bcu app isn't running."""
    from tools.computer import ComputerUseUnavailableError, list_apps

    # Grab a port that is (momentarily) free, then leave it closed.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    bcu_dir = tmp_path / "background-computer-use"
    bcu_dir.mkdir()
    (bcu_dir / "runtime-manifest.json").write_text(
        json.dumps({"baseURL": f"http://127.0.0.1:{port}"})
    )
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    with pytest.raises(ComputerUseUnavailableError):
        list_apps()


# ---------------------------------------------------------------------------
# CLI: OS gate, exit codes, dispatch
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
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "computer_use_unavailable"


def test_cli_error_dict_exits_1(bcu, monkeypatch, capsys):
    """A structured error dict (here: 404 not_found) exits 1, not 78."""
    from tools.computer.cli import main

    monkeypatch.setattr(sys, "platform", "darwin")
    bcu.respond("/v1/list_apps", {}, status=404)
    rc = main(["list_apps"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "not_found"


def test_cli_click_both_target_and_xy_exits_1(bcu, monkeypatch, capsys):
    """target XOR x/y violation surfaces as invalid_argument, exit 1."""
    from tools.computer.cli import main

    monkeypatch.setattr(sys, "platform", "darwin")
    rc = main(["click", "w-1", "--x", "1", "--y", "2", "--target", json.dumps(_TARGET)])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_argument"
    assert bcu.requests == []


def test_cli_invalid_target_json_raises_system_exit(bcu, monkeypatch):
    from tools.computer.cli import main

    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(SystemExit, match="invalid JSON"):
        main(["click", "w-1", "--target", "{not json"])


def test_cli_list_windows_dispatch(bcu, monkeypatch, capsys):
    """Window/app args are strings and route through the module wrappers."""
    from tools.computer.cli import main

    monkeypatch.setattr(sys, "platform", "darwin")
    bcu.respond("/v1/list_windows", {"windows": [{"id": "w-1", "title": "Notes"}]})
    rc = main(["list_windows", "Notes"])
    assert rc == 0
    assert bcu.requests[0]["body"] == {"app": "Notes"}
    payload = json.loads(capsys.readouterr().out)
    assert payload["windows"][0]["id"] == "w-1"


def test_cli_screenshot_output_writes_file(bcu, monkeypatch, tmp_path, capsys):
    """CLI screenshot --output writes the decoded image and emits saved_to."""
    from tools.computer.cli import main

    monkeypatch.setattr(sys, "platform", "darwin")
    bcu.respond(
        "/v1/get_window_state",
        {"screenshot": {"image": {"imageBase64": base64.b64encode(_FAKE_PNG).decode()}}},
    )
    out_path = tmp_path / "out.png"
    rc = main(["screenshot", "w-1", "--output", str(out_path)])
    assert rc == 0
    assert out_path.read_bytes() == _FAKE_PNG
    payload = json.loads(capsys.readouterr().out)
    assert payload["saved_to"] == str(out_path)
