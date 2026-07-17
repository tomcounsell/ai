"""macOS desktop control via background-computer-use (bcu).

Wraps the bcu loopback HTTP API so the agent can drive native macOS apps --
click buttons, type text, screenshot windows -- without moving the user's
cursor or stealing focus. The bcu Swift app reads the macOS Accessibility
tree and dispatches AX-API actions against target windows.

Endpoints documented at GET /v1/routes on the running bcu server. This
module covers the most common ones: list_apps, list_windows,
get_window_state, click, scroll, type_text, press_key, set_value,
perform_secondary_action, drag, resize, set_window_frame, screenshot_window.

Runtime discovery
-----------------
bcu writes ``$TMPDIR/background-computer-use/runtime-manifest.json`` when
it starts, containing the loopback ``base_url``. This module reads that
file on every call. If absent, ``ComputerUseUnavailableError`` is raised --
the canonical signal that bcu is not opted-in or not running on this
machine. The CLI entry point (:mod:`tools.computer.cli`) catches that
and exits 78 (``EX_CONFIG``) with a clear message.

Electron staleness
------------------
Electron apps (Slack, VS Code, Telegram Desktop, Discord) lazily build
their accessibility trees, so an AX node ref returned by
``get_window_state`` can become invalid before the next call. For these
apps, callers may pass ``selector={'role': 'button', 'label': 'Send',
'bounds': (x, y, w, h)}`` to ``click``/``set_value``/``drag`` instead of
a raw AX ref. The module re-queries ``get_window_state`` internally and
resolves the selector to a fresh ref before each action. The Electron
bundle-id list lives in :mod:`tools.computer.electron_bundles`.

OS gate
-------
bcu is macOS-only. The OS gate is enforced in
:func:`tools.computer.cli.main` (exit 78 / ``EX_CONFIG``); the SKILL.md
documents the expected behavior. This module itself does not check
``sys.platform`` -- it raises ``ComputerUseUnavailableError`` when the
manifest is absent, which is the same outcome on non-macOS hosts.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from tools.computer.electron_bundles import is_electron_bundle

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

    bcu writes this file on startup containing the loopback ``base_url``.
    Path is fixed by the bcu Swift app and is the same on every macOS host.
    """
    tmpdir = os.environ.get("TMPDIR") or "/tmp"
    return Path(tmpdir) / "background-computer-use" / "runtime-manifest.json"


def _read_base_url() -> str:
    """Read ``base_url`` from the bcu runtime manifest.

    Raises:
        ComputerUseUnavailableError: when the manifest does not exist, is
            unreadable, or omits ``base_url``. Callers must treat this as
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

    # bcu releases ≤/≥ v0.1.0 disagree on the key casing (base_url vs baseURL);
    # accept both so a pin bump can't silently break the CLI.
    base_url = None
    if isinstance(data, dict):
        base_url = data.get("base_url") or data.get("baseURL")
    if not isinstance(base_url, str) or not base_url:
        raise ComputerUseUnavailableError(
            f"bcu runtime manifest at {path} does not contain a base_url"
        )
    return base_url.rstrip("/")


# ---------------------------------------------------------------------------
# HTTP wrapper
# ---------------------------------------------------------------------------


def _http_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict:
    """Make an HTTP request to the bcu loopback API and return the parsed dict.

    Returns:
        - On HTTP 200: parsed JSON dict.
        - On HTTP 404: ``{"error": "window_not_found", "window_id": <int|None>}``
          (for window-keyed endpoints) or ``{"error": "not_found", "path": ...}``.
        - On any other HTTP error or transport error: ``{"error": <str>, ...}``.

    Raises:
        ComputerUseUnavailableError: when bcu is not reachable (manifest
            missing, or connection refused -- i.e. bcu app not running).
    """
    base_url = _read_base_url()
    url = f"{base_url}{path}"

    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"error": "invalid_json_response", "raw": raw[:200]}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            window_id = None
            if isinstance(body, dict):
                window_id = body.get("window_id")
            return {
                "error": "window_not_found" if window_id is not None else "not_found",
                "window_id": window_id,
                "path": path,
            }
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


# ---------------------------------------------------------------------------
# Selector resolution for Electron apps
# ---------------------------------------------------------------------------


def _resolve_selector(window_id: int, selector: dict[str, Any]) -> dict[str, Any] | None:
    """Re-query ``get_window_state`` and resolve the selector to a fresh AX ref.

    Used for Electron apps where AX refs go stale between query and action.
    The selector dict accepts:
      - ``role`` (str): AX role (e.g. "button", "AXButton").
      - ``label`` (str): visible/AX label of the element.
      - ``bounds`` (tuple|list): (x, y, w, h) bounding box for tie-breaking.

    Returns the matched AX ref dict to send to bcu, or None when no match.
    The shape returned must be one bcu's click/set_value/drag handlers
    accept -- typically ``{"window_id": ..., "ref": <ax_ref>}`` or similar
    depending on bcu's selector API. We standardize on ``{"ref": ...}``;
    callers merge with their action body.

    Raises:
        ValueError: when the selector dict is empty (caller mistake).
    """
    if not selector:
        raise ValueError("selector cannot be empty")

    state = get_window_state(window_id)
    if "error" in state:
        return None

    target_role = selector.get("role")
    target_label = selector.get("label")
    target_bounds = selector.get("bounds")

    candidates = _walk_ax_tree(state)
    matches: list[dict[str, Any]] = []
    for node in candidates:
        if target_role and node.get("role") != target_role:
            continue
        if target_label and node.get("label") != target_label:
            continue
        matches.append(node)

    if not matches:
        return None

    # If bounds were provided, prefer the closest match by Manhattan distance
    if target_bounds and len(matches) > 1:

        def _dist(node: dict[str, Any]) -> float:
            nb = node.get("bounds") or (0, 0, 0, 0)
            return sum(abs(a - b) for a, b in zip(nb[:4], target_bounds[:4]))

        matches.sort(key=_dist)

    return matches[0]


def _walk_ax_tree(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten bcu's nested AX-tree response into a list of node dicts.

    bcu returns a nested children structure under ``state["root"]`` with
    each node carrying ``role``, ``label``, ``bounds``, ``ref``. This
    helper depth-first-walks that tree and yields one dict per node so
    selector matching can iterate cheaply.
    """
    out: list[dict[str, Any]] = []
    stack = [state.get("root") or state]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        out.append(node)
        children = node.get("children") or []
        if isinstance(children, list):
            stack.extend(reversed(children))
    return out


# ---------------------------------------------------------------------------
# Public API -- bcu endpoint wrappers
# ---------------------------------------------------------------------------


def list_apps() -> dict:
    """Return the list of visible apps known to bcu.

    Maps to ``GET /v1/list_apps``. Each app entry includes ``bundle_id``,
    ``name``, ``pid``.
    """
    return _http_request("GET", "/v1/list_apps")


def list_windows(bundle_id: str | None = None) -> dict:
    """Return the list of open windows, optionally filtered by app bundle_id.

    Maps to ``GET /v1/list_windows`` (optional ``?bundle_id=...``). Each
    window entry includes ``window_id``, ``bundle_id``, ``title``, ``frame``.
    """
    path = "/v1/list_windows"
    if bundle_id:
        path += f"?bundle_id={urllib.request.quote(bundle_id)}"
    return _http_request("GET", path)


def get_window_state(window_id: int) -> dict:
    """Return the AX tree state for the given window.

    Maps to ``GET /v1/get_window_state?window_id=N``.
    """
    return _http_request("GET", f"/v1/get_window_state?window_id={int(window_id)}")


def click(
    window_id: int,
    *,
    x: float | None = None,
    y: float | None = None,
    ref: Any = None,
    selector: dict[str, Any] | None = None,
) -> dict:
    """Click in the target window.

    Three positioning modes:
      - ``x``/``y``: literal window-relative coordinates.
      - ``ref``: a raw AX ref returned by ``get_window_state``. Used for
        non-Electron apps where AX refs are stable.
      - ``selector``: a dict of ``role``/``label``/``bounds`` for Electron
        apps. The module re-queries window state and resolves the selector
        to a fresh ref before each call (Race 3 mitigation).

    Raises:
        ValueError: when neither x/y, ref, nor selector is provided, or
            when selector is provided as an empty dict.
    """
    if selector is not None:
        if not selector:
            raise ValueError("selector cannot be empty")
        # For Electron targets, always re-query for a fresh ref. For
        # non-Electron, the same path still works -- it costs one extra
        # round-trip but keeps the call site uniform.
        bundle_id = selector.get("bundle_id")
        # is_electron_bundle is informational; the re-query happens
        # regardless. We expose it to the caller via a logger note.
        if bundle_id and is_electron_bundle(bundle_id):
            logger.debug("click: re-querying AX tree for Electron bundle %s", bundle_id)
        resolved = _resolve_selector(window_id, selector)
        if resolved is None:
            return {"error": "selector_no_match", "selector": selector}
        ref = resolved.get("ref", resolved)

    if ref is None and (x is None or y is None):
        raise ValueError("click requires one of: (x, y), ref, or selector")

    body: dict[str, Any] = {"window_id": int(window_id)}
    if ref is not None:
        body["ref"] = ref
    if x is not None and y is not None:
        body["x"] = float(x)
        body["y"] = float(y)
    return _http_request("POST", "/v1/click", body=body)


def scroll(window_id: int, dx: float = 0.0, dy: float = 0.0) -> dict:
    """Scroll the window by ``(dx, dy)`` pixels.

    Maps to ``POST /v1/scroll``.
    """
    body = {"window_id": int(window_id), "dx": float(dx), "dy": float(dy)}
    return _http_request("POST", "/v1/scroll", body=body)


def type_text(window_id: int, text: str) -> dict:
    """Type ``text`` into the window's currently-focused element.

    Empty string is a valid no-op (returns success). Maps to
    ``POST /v1/type_text``.
    """
    body = {"window_id": int(window_id), "text": str(text)}
    return _http_request("POST", "/v1/type_text", body=body)


def press_key(window_id: int, key: str, modifiers: list[str] | None = None) -> dict:
    """Press a single key with optional modifiers.

    Maps to ``POST /v1/press_key``. ``key`` is bcu's key name (e.g. "return",
    "tab", "a"); ``modifiers`` is a list like ``["cmd", "shift"]``.
    """
    body: dict[str, Any] = {"window_id": int(window_id), "key": str(key)}
    if modifiers:
        body["modifiers"] = list(modifiers)
    return _http_request("POST", "/v1/press_key", body=body)


def set_value(
    window_id: int,
    value: Any,
    *,
    ref: Any = None,
    selector: dict[str, Any] | None = None,
) -> dict:
    """Set the value of an AX element (text fields, checkboxes, sliders).

    Same selector semantics as :func:`click`: pass ``ref`` for stable AX
    targets, ``selector`` for Electron targets that need fresh-ref
    re-resolution before each action.
    """
    if selector is not None:
        if not selector:
            raise ValueError("selector cannot be empty")
        resolved = _resolve_selector(window_id, selector)
        if resolved is None:
            return {"error": "selector_no_match", "selector": selector}
        ref = resolved.get("ref", resolved)

    if ref is None:
        raise ValueError("set_value requires one of: ref, selector")

    body = {"window_id": int(window_id), "ref": ref, "value": value}
    return _http_request("POST", "/v1/set_value", body=body)


def perform_secondary_action(window_id: int, ref: Any) -> dict:
    """Perform the AX 'show menu' / right-click equivalent on an element.

    Maps to ``POST /v1/perform_secondary_action``.
    """
    body = {"window_id": int(window_id), "ref": ref}
    return _http_request("POST", "/v1/perform_secondary_action", body=body)


def drag(
    window_id: int,
    *,
    from_xy: tuple[float, float] | None = None,
    to_xy: tuple[float, float] | None = None,
    ref: Any = None,
    selector: dict[str, Any] | None = None,
) -> dict:
    """Drag from one point to another.

    Either pass explicit ``from_xy`` and ``to_xy`` window-relative coords,
    or pass ``ref`` / ``selector`` to resolve a target element. When
    ``selector`` is used, the module re-queries the AX tree for a fresh
    ref (Electron-staleness mitigation).
    """
    if selector is not None:
        if not selector:
            raise ValueError("selector cannot be empty")
        resolved = _resolve_selector(window_id, selector)
        if resolved is None:
            return {"error": "selector_no_match", "selector": selector}
        ref = resolved.get("ref", resolved)

    if ref is None and (from_xy is None or to_xy is None):
        raise ValueError("drag requires one of: (from_xy, to_xy), ref, or selector")

    body: dict[str, Any] = {"window_id": int(window_id)}
    if ref is not None:
        body["ref"] = ref
    if from_xy is not None:
        body["from_x"], body["from_y"] = float(from_xy[0]), float(from_xy[1])
    if to_xy is not None:
        body["to_x"], body["to_y"] = float(to_xy[0]), float(to_xy[1])
    return _http_request("POST", "/v1/drag", body=body)


def resize(window_id: int, width: float, height: float) -> dict:
    """Resize the window to ``(width, height)`` in pixels."""
    body = {"window_id": int(window_id), "width": float(width), "height": float(height)}
    return _http_request("POST", "/v1/resize", body=body)


def set_window_frame(window_id: int, x: float, y: float, width: float, height: float) -> dict:
    """Move + resize the window in one call.

    Maps to ``POST /v1/set_window_frame``.
    """
    body = {
        "window_id": int(window_id),
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
    }
    return _http_request("POST", "/v1/set_window_frame", body=body)


def screenshot_window(window_id: int) -> dict:
    """Capture a screenshot of the target window.

    Maps to ``GET /v1/screenshot_window?window_id=N``. The bcu response
    typically includes ``image_base64`` (PNG) and ``width``/``height``.
    Callers that need to fit downstream agent-context budgets can pipe
    the bytes through :func:`tools.browser._downscale_if_needed`.
    """
    return _http_request("GET", f"/v1/screenshot_window?window_id={int(window_id)}")
