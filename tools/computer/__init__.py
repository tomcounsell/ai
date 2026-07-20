"""macOS desktop control via background-computer-use (bcu), v0.1.0 contract.

Wraps the bcu loopback HTTP API so the agent can drive native macOS apps --
click buttons, type text, screenshot windows -- without moving the user's
cursor or stealing focus. The bcu Swift app reads the macOS Accessibility
tree and dispatches AX/CGEvent actions against target windows.

Contract source: ``RouteRegistry.swift`` in the pinned bcu release
(``config/bcu_pin.json``), the generator for the live ``GET /v1/routes``
catalog. Every discovery/state/action route is ``POST`` with a JSON body;
``window`` is a **string** stable window ID.

Targeting
---------
Element-level actions take a ``target`` dict, passed through verbatim:
``{"kind": "display_index" | "node_id" | "refetch_fingerprint", "value": ...}``.
Staleness is handled server-side: pass the ``stateToken`` from a prior
``get_window_state`` response and bcu rejects actions against a stale tree
(``refetch_fingerprint`` targets re-resolve automatically). No client-side
selector re-resolution exists in this module.

Screenshots
-----------
There is no dedicated screenshot route. ``get_window_state`` carries the
window image under ``screenshot.image`` per its ``imageMode`` request field
(``path`` | ``base64`` | ``omit``). :func:`screenshot` is a convenience
wrapper over that.

Runtime discovery
-----------------
bcu writes ``$TMPDIR/background-computer-use/runtime-manifest.json`` when
it starts, containing the loopback ``baseURL``. This module reads that
file on every call. If absent, ``ComputerUseUnavailableError`` is raised --
the canonical signal that bcu is not opted-in or not running on this
machine. The CLI entry point (:mod:`tools.computer.cli`) catches that
and exits 78 (``EX_CONFIG``) with a clear message.

OS gate
-------
bcu is macOS-only. The OS gate is enforced in
:func:`tools.computer.cli.main` (exit 78 / ``EX_CONFIG``). This module
itself does not check ``sys.platform`` -- it raises
``ComputerUseUnavailableError`` when the manifest is absent, which is the
same outcome on non-macOS hosts.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT_S = 10.0


class ComputerUseUnavailableError(RuntimeError):
    """Raised when bcu is not installed, opted-in, or running on this machine.

    The CLI catches this and exits 78 (``EX_CONFIG``); programmatic callers
    should treat it as a configuration error rather than a transient failure.
    """


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------


def _manifest_path() -> Path:
    """Return the absolute path to bcu's runtime manifest.

    bcu writes this file on startup containing the loopback ``baseURL``.
    Path is fixed by the bcu Swift app and is the same on every macOS host.
    """
    tmpdir = os.environ.get("TMPDIR") or "/tmp"
    return Path(tmpdir) / "background-computer-use" / "runtime-manifest.json"


def _read_base_url() -> str:
    """Read the loopback base URL from the bcu runtime manifest.

    Raises:
        ComputerUseUnavailableError: when the manifest does not exist, is
            unreadable, or omits the base URL. Callers must treat this as
            a hard configuration failure.
    """
    path = _manifest_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise ComputerUseUnavailableError(
            f"bcu runtime manifest not found at {path}. "
            "Install bcu via /setup (computer-use opt-in) and ensure the bcu "
            "app is running."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ComputerUseUnavailableError(
            f"bcu runtime manifest at {path} is unreadable or malformed: {exc}"
        ) from exc

    # v0.1.0 writes ``baseURL``; accept the snake_case spelling too so a
    # future upstream casing change can't silently break the CLI.
    base_url = None
    if isinstance(data, dict):
        base_url = data.get("base_url") or data.get("baseURL")
    if not isinstance(base_url, str) or not base_url:
        raise ComputerUseUnavailableError(
            f"bcu runtime manifest at {path} does not contain a base URL"
        )
    return base_url.rstrip("/")


# ---------------------------------------------------------------------------
# HTTP wrapper
# ---------------------------------------------------------------------------


def _post(path: str, body: dict[str, Any], *, timeout: float = _DEFAULT_TIMEOUT_S) -> dict:
    """POST a JSON body to the bcu loopback API and return the parsed dict.

    Returns:
        - On HTTP 200: parsed JSON dict.
        - On HTTP 404: ``{"error": "not_found", "path": ...}``.
        - On any other HTTP error or transport error: ``{"error": <str>, ...}``.

    Raises:
        ComputerUseUnavailableError: when bcu is not reachable (manifest
            missing, or connection refused -- i.e. bcu app not running).
    """
    base_url = _read_base_url()
    url = f"{base_url}{path}"

    data = json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"error": "invalid_json_response", "raw": raw[:200]}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"error": "not_found", "path": path}
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            err_body = ""
        return {
            "error": f"http_{exc.code}",
            "message": err_body[:500],
            "path": path,
        }
    except urllib.error.URLError as exc:
        # Connection refused == bcu not running; treat as unavailable.
        reason = getattr(exc, "reason", exc)
        msg = str(reason)
        if "Connection refused" in msg or isinstance(reason, ConnectionRefusedError):
            raise ComputerUseUnavailableError(
                f"bcu loopback API at {base_url} not reachable (is the bcu app running?)"
            ) from exc
        return {"error": "transport_error", "message": msg, "path": path}
    except TimeoutError as exc:
        return {"error": "timeout", "message": str(exc), "path": path, "timeout_s": timeout}


def _get(path: str, *, timeout: float = _DEFAULT_TIMEOUT_S) -> dict:
    """GET a bcu loopback route (no request body) and return the parsed dict.

    Mirrors :func:`_post`'s error-dict and ``ComputerUseUnavailableError``
    semantics, but issues an HTTP GET. Only the GET system routes
    (``/health``, ``/v1/bootstrap``, ``/v1/routes``) use this in v0.1.0.
    """
    base_url = _read_base_url()
    url = f"{base_url}{path}"

    headers = {"Accept": "application/json"}
    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"error": "invalid_json_response", "raw": raw[:200]}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"error": "not_found", "path": path}
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            err_body = ""
        return {
            "error": f"http_{exc.code}",
            "message": err_body[:500],
            "path": path,
        }
    except urllib.error.URLError as exc:
        # Connection refused == bcu not running; treat as unavailable.
        reason = getattr(exc, "reason", exc)
        msg = str(reason)
        if "Connection refused" in msg or isinstance(reason, ConnectionRefusedError):
            raise ComputerUseUnavailableError(
                f"bcu loopback API at {base_url} not reachable (is the bcu app running?)"
            ) from exc
        return {"error": "transport_error", "message": msg, "path": path}
    except TimeoutError as exc:
        return {"error": "timeout", "message": str(exc), "path": path, "timeout_s": timeout}


def _merge_optional(body: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Add each field to ``body`` only when its value is not None."""
    for key, value in fields.items():
        if value is not None:
            body[key] = value
    return body


# ---------------------------------------------------------------------------
# Public API -- bcu v0.1.0 endpoint wrappers
# ---------------------------------------------------------------------------


def bootstrap() -> dict:
    """Return bcu's connection/permission/route-discovery payload.

    Maps to ``GET /v1/bootstrap``. The response (``BootstrapResponse`` in the
    v0.1.0 contract) carries ``contractVersion``, ``baseURL``, ``startedAt``,
    ``permissions`` (accessibility + screenRecording grant status),
    ``instructions`` (``{ready, summary, agent, user}``), ``guide``, and
    ``routes``.

    ``instructions.ready`` is the readiness gate: when ``true`` the action
    routes (click/type/screenshot/...) are available; when ``false`` the user
    must grant macOS Accessibility / Screen Recording permission (relay
    ``instructions.user``) before any action will succeed. Callers should check
    this once per session before the first action -- see :func:`is_ready`.

    Returns the parsed dict, an error dict, or raises
    ``ComputerUseUnavailableError`` exactly like the other wrappers.
    """
    return _get("/v1/bootstrap")


def is_ready(bootstrap_result: dict) -> bool:
    """Return ``True`` only when a :func:`bootstrap` payload signals readiness.

    The gate is truthy only when the payload carries no ``error`` key and
    ``instructions.ready`` is truthy. Error dicts, missing ``instructions``, and
    ``ready == false`` all return ``False``. This is the single predicate the
    CLI and any future caller share so the gating decision stays consistent.
    """
    if not isinstance(bootstrap_result, dict) or "error" in bootstrap_result:
        return False
    instructions = bootstrap_result.get("instructions")
    if not isinstance(instructions, dict):
        return False
    return bool(instructions.get("ready"))


def list_apps() -> dict:
    """Return the list of visible apps known to bcu.

    Maps to ``POST /v1/list_apps`` with an empty body.
    """
    return _post("/v1/list_apps", {})


def list_windows(app: str) -> dict:
    """Return the list of open windows for the given app.

    Maps to ``POST /v1/list_windows``. ``app`` (required) accepts an app
    name, bundle ID, or query string.
    """
    return _post("/v1/list_windows", {"app": str(app)})


def get_window_state(
    window: str,
    *,
    image_mode: str = "path",
    include_menu_bar: bool | None = None,
    max_nodes: int | None = None,
) -> dict:
    """Return the AX tree state (and screenshot) for the given window.

    Maps to ``POST /v1/get_window_state``. ``image_mode`` is one of
    ``path`` | ``base64`` | ``omit``; the response carries ``stateToken``,
    ``screenshot.image`` (``imagePath`` or ``imageBase64`` per mode),
    ``tree``, ``focusedElement``, ``notes``.
    """
    body: dict[str, Any] = {"window": str(window), "imageMode": image_mode}
    return _post(
        "/v1/get_window_state",
        _merge_optional(body, includeMenuBar=include_menu_bar, maxNodes=max_nodes),
    )


def screenshot(window: str, output: str | None = None) -> dict:
    """Capture a window screenshot via ``get_window_state`` imageMode.

    With ``output`` given, requests ``imageMode: "base64"``, decodes the
    image, and writes it to ``output`` (result carries ``saved_to``).
    Without ``output``, requests ``imageMode: "path"`` and returns the
    server-side ``imagePath``.

    Error dicts from ``get_window_state`` are returned unchanged.
    """
    state = get_window_state(window, image_mode="base64" if output else "path")
    if "error" in state:
        return state

    image = (state.get("screenshot") or {}).get("image") or {}
    meta = {k: image.get(k) for k in ("pixelWidth", "pixelHeight", "mimeType")}

    if output:
        image_b64 = image.get("imageBase64")
        if not image_b64:
            return {"error": "missing_image", "window": str(window)}
        Path(output).write_bytes(base64.b64decode(image_b64))
        return {"window": str(window), "saved_to": output, **meta}

    image_path = image.get("imagePath")
    if not image_path:
        return {"error": "missing_image", "window": str(window)}
    return {"window": str(window), "imagePath": image_path, **meta}


def click(
    window: str,
    *,
    target: dict[str, Any] | None = None,
    x: float | None = None,
    y: float | None = None,
    mode: str | None = None,
    click_count: int | None = None,
    mouse_button: str | None = None,
    state_token: str | None = None,
) -> dict:
    """Click in the target window.

    Maps to ``POST /v1/click``. Positioning is ``target`` XOR literal
    window-relative ``x``/``y`` coordinates -- exactly one form required.
    Optional: ``mode`` (``single`` | ``double``), ``click_count``,
    ``mouse_button`` (``left`` | ``right`` | ``middle``), ``state_token``.

    Raises:
        ValueError: when both ``target`` and ``x``/``y`` are given, or neither.
    """
    has_xy = x is not None and y is not None
    if target is not None and has_xy:
        raise ValueError("click takes target XOR x/y, not both")
    if target is None and not has_xy:
        raise ValueError("click requires one of: target, or x and y")

    body: dict[str, Any] = {"window": str(window)}
    if target is not None:
        body["target"] = target
    else:
        body["x"] = float(x)  # type: ignore[arg-type]
        body["y"] = float(y)  # type: ignore[arg-type]
    return _post(
        "/v1/click",
        _merge_optional(
            body,
            mode=mode,
            clickCount=click_count,
            mouseButton=mouse_button,
            stateToken=state_token,
        ),
    )


def scroll(
    window: str,
    target: dict[str, Any],
    direction: str,
    pages: float | None = None,
    *,
    state_token: str | None = None,
) -> dict:
    """Scroll within the target element.

    Maps to ``POST /v1/scroll``. ``direction`` is one of
    ``up`` | ``down`` | ``left`` | ``right``; ``pages`` scales the distance.
    """
    body: dict[str, Any] = {"window": str(window), "target": target, "direction": direction}
    return _post("/v1/scroll", _merge_optional(body, pages=pages, stateToken=state_token))


def drag(window: str, to_x: float, to_y: float) -> dict:
    """Drag the window to ``(to_x, to_y)`` (window motion).

    Maps to ``POST /v1/drag``.
    """
    body = {"window": str(window), "toX": float(to_x), "toY": float(to_y)}
    return _post("/v1/drag", body)


def resize(window: str, handle: str, to_x: float, to_y: float) -> dict:
    """Resize the window by dragging the given edge/corner handle.

    Maps to ``POST /v1/resize``. ``handle`` is one of left/right/top/bottom/
    topLeft/topRight/bottomLeft/bottomRight.
    """
    body = {
        "window": str(window),
        "handle": str(handle),
        "toX": float(to_x),
        "toY": float(to_y),
    }
    return _post("/v1/resize", body)


def set_window_frame(
    window: str,
    x: float,
    y: float,
    width: float,
    height: float,
    animate: bool | None = None,
) -> dict:
    """Move + resize the window in one call.

    Maps to ``POST /v1/set_window_frame``. ``animate`` defaults server-side
    to true; pass ``False`` to snap.
    """
    body: dict[str, Any] = {
        "window": str(window),
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
    }
    return _post("/v1/set_window_frame", _merge_optional(body, animate=animate))


def type_text(
    window: str,
    text: str,
    target: dict[str, Any] | None = None,
    focus_assist_mode: str | None = None,
    *,
    state_token: str | None = None,
) -> dict:
    """Type ``text`` into the window (optionally into a specific target).

    Maps to ``POST /v1/type_text``. ``focus_assist_mode`` is one of
    ``none`` | ``focus`` | ``focus_and_caret_end``. Empty string is a
    valid no-op.
    """
    body: dict[str, Any] = {"window": str(window), "text": str(text)}
    return _post(
        "/v1/type_text",
        _merge_optional(
            body, target=target, focusAssistMode=focus_assist_mode, stateToken=state_token
        ),
    )


def press_key(window: str, key: str, *, state_token: str | None = None) -> dict:
    """Press a key or chord.

    Maps to ``POST /v1/press_key``. ``key`` is a key name or chord string
    (e.g. ``"return"``, ``"cmd+shift+a"``) -- there is no modifiers array
    in v0.1.0.
    """
    body: dict[str, Any] = {"window": str(window), "key": str(key)}
    return _post("/v1/press_key", _merge_optional(body, stateToken=state_token))


def set_value(
    window: str,
    target: dict[str, Any],
    value: str,
    *,
    state_token: str | None = None,
) -> dict:
    """Set the value of an AX element (text fields, checkboxes, sliders).

    Maps to ``POST /v1/set_value``. ``value`` is a string per the contract.
    """
    body: dict[str, Any] = {"window": str(window), "target": target, "value": str(value)}
    return _post("/v1/set_value", _merge_optional(body, stateToken=state_token))


def perform_secondary_action(
    window: str,
    target: dict[str, Any],
    action: str,
    action_id: str | None = None,
    *,
    state_token: str | None = None,
) -> dict:
    """Perform a secondary AX action (exact label) on an element.

    Maps to ``POST /v1/perform_secondary_action``.
    """
    body: dict[str, Any] = {"window": str(window), "target": target, "action": str(action)}
    return _post(
        "/v1/perform_secondary_action",
        _merge_optional(body, actionID=action_id, stateToken=state_token),
    )
