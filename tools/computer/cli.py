"""CLI wrapper for tools.computer (bcu v0.1.0 contract).

Entry point: ``valor-computer`` (declared in pyproject.toml ``[project.scripts]``).

This is the canonical agent-facing surface for desktop automation. The
``computer-use`` skill body invokes this CLI via Bash; ``python -m
tools.computer`` is intentionally not supported (skips the OS gate).

Window arguments are the string stable IDs returned by ``list_windows``.
Element-level actions take ``--target`` JSON
(``{"kind": "node_id"|"display_index"|"refetch_fingerprint", "value": ...}``)
and an optional ``--state-token`` from a prior ``get_window_state``.

OS gate
-------
bcu (the underlying Swift app) is macOS-only. On non-macOS hosts this
CLI prints a clear stderr message and exits 78 (``EX_CONFIG``) before
performing any work.
"""

from __future__ import annotations

import argparse
import json
import sys

# EX_CONFIG = 78 (sysexits.h). Used here to mark "this machine is not
# configured for this command" -- distinct from generic exit-1 errors
# so callers can branch on it.
EX_CONFIG = 78


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _enforce_os_gate() -> int:
    """Exit 78 with stderr message on non-macOS platforms.

    Returns 0 when the platform is darwin so the caller can proceed; on
    any other platform, prints the canonical message and returns 78
    (caller exits with the returned code).
    """
    if sys.platform != "darwin":
        print(
            f"computer-use is macOS-only. This machine runs {sys.platform}; skipping.",
            file=sys.stderr,
        )
        return EX_CONFIG
    return 0


def _add_state_token(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-token",
        help="stateToken from a prior get_window_state (stale-target guard).",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="valor-computer",
        description=(
            "Drive native macOS apps via background-computer-use (bcu). "
            "macOS-only. Reads loopback URL from "
            "$TMPDIR/background-computer-use/runtime-manifest.json."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output (default for most commands).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list_apps", help="List visible apps.")

    p_list_windows = sub.add_parser("list_windows", help="List open windows for an app.")
    p_list_windows.add_argument("app", help="App name, bundle ID, or query (e.g. Notes).")

    p_state = sub.add_parser(
        "get_window_state", help="Dump AX tree state (and screenshot) for a window."
    )
    p_state.add_argument("window")
    p_state.add_argument(
        "--image-mode",
        choices=["path", "base64", "omit"],
        default="path",
        help="Screenshot delivery mode (default: path).",
    )

    p_shot = sub.add_parser(
        "screenshot", help="Capture a window screenshot via get_window_state imageMode."
    )
    p_shot.add_argument("window")
    p_shot.add_argument(
        "--output", help="Write the decoded image to this path instead of a server-side path."
    )

    p_click = sub.add_parser("click", help="Click in a window (--target XOR --x/--y).")
    p_click.add_argument("window")
    p_click.add_argument("--x", type=float, help="Window-relative x coordinate.")
    p_click.add_argument("--y", type=float, help="Window-relative y coordinate.")
    p_click.add_argument(
        "--target",
        help='Target JSON, e.g. \'{"kind":"node_id","value":"n42"}\'.',
    )
    p_click.add_argument("--mode", choices=["single", "double"])
    p_click.add_argument("--button", choices=["left", "right", "middle"])
    _add_state_token(p_click)

    p_scroll = sub.add_parser("scroll", help="Scroll within a target element.")
    p_scroll.add_argument("window")
    p_scroll.add_argument("--target", required=True, help="Target JSON.")
    p_scroll.add_argument("--direction", required=True, choices=["up", "down", "left", "right"])
    p_scroll.add_argument("--pages", type=float)
    _add_state_token(p_scroll)

    p_type = sub.add_parser("type_text", help="Type text into a window.")
    p_type.add_argument("window")
    p_type.add_argument("text")
    p_type.add_argument("--target", help="Optional target JSON to focus first.")
    p_type.add_argument(
        "--focus-assist",
        choices=["none", "focus", "focus_and_caret_end"],
        help="focusAssistMode for the request.",
    )
    _add_state_token(p_type)

    p_press = sub.add_parser("press_key", help="Press a key or chord (e.g. cmd+shift+a).")
    p_press.add_argument("window")
    p_press.add_argument("key")
    _add_state_token(p_press)

    p_set = sub.add_parser("set_value", help="Set the value of an AX element.")
    p_set.add_argument("window")
    p_set.add_argument("value")
    p_set.add_argument("--target", required=True, help="Target JSON.")
    _add_state_token(p_set)

    p_secondary = sub.add_parser(
        "perform_secondary_action", help="Perform a secondary AX action on an element."
    )
    p_secondary.add_argument("window")
    p_secondary.add_argument("--target", required=True, help="Target JSON.")
    p_secondary.add_argument("--action", required=True, help="Exact action label.")
    p_secondary.add_argument("--action-id", help="Optional actionID.")
    _add_state_token(p_secondary)

    p_drag = sub.add_parser("drag", help="Drag the window to a new position.")
    p_drag.add_argument("window")
    p_drag.add_argument("--to-x", type=float, required=True)
    p_drag.add_argument("--to-y", type=float, required=True)

    p_resize = sub.add_parser("resize", help="Resize a window by dragging a handle.")
    p_resize.add_argument("window")
    p_resize.add_argument(
        "--handle",
        required=True,
        choices=[
            "left",
            "right",
            "top",
            "bottom",
            "topLeft",
            "topRight",
            "bottomLeft",
            "bottomRight",
        ],
    )
    p_resize.add_argument("--to-x", type=float, required=True)
    p_resize.add_argument("--to-y", type=float, required=True)

    p_frame = sub.add_parser("set_window_frame", help="Move + resize in one call.")
    p_frame.add_argument("window")
    p_frame.add_argument("x", type=float)
    p_frame.add_argument("y", type=float)
    p_frame.add_argument("width", type=float)
    p_frame.add_argument("height", type=float)
    p_frame.add_argument("--no-animate", action="store_true", help="Snap instead of animating.")

    return parser


def _parse_json(value: str | None) -> dict | None:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON: {exc}") from exc


def _dispatch(args: argparse.Namespace) -> dict:
    # Lazy-import so the OS gate can fire before importing the bcu wrapper
    # (which has no platform check itself but also has no side-effects on
    # import).
    from tools.computer import (
        ComputerUseUnavailableError,
        click,
        drag,
        get_window_state,
        list_apps,
        list_windows,
        perform_secondary_action,
        press_key,
        resize,
        screenshot,
        scroll,
        set_value,
        set_window_frame,
        type_text,
    )

    cmd = args.command

    try:
        if cmd == "list_apps":
            return list_apps()
        if cmd == "list_windows":
            return list_windows(args.app)
        if cmd == "get_window_state":
            return get_window_state(args.window, image_mode=args.image_mode)
        if cmd == "screenshot":
            return screenshot(args.window, output=args.output)
        if cmd == "click":
            return click(
                args.window,
                target=_parse_json(args.target),
                x=args.x,
                y=args.y,
                mode=args.mode,
                mouse_button=args.button,
                state_token=args.state_token,
            )
        if cmd == "scroll":
            return scroll(
                args.window,
                _parse_json(args.target),
                args.direction,
                pages=args.pages,
                state_token=args.state_token,
            )
        if cmd == "type_text":
            return type_text(
                args.window,
                args.text,
                target=_parse_json(args.target),
                focus_assist_mode=args.focus_assist,
                state_token=args.state_token,
            )
        if cmd == "press_key":
            return press_key(args.window, args.key, state_token=args.state_token)
        if cmd == "set_value":
            return set_value(
                args.window,
                _parse_json(args.target),
                args.value,
                state_token=args.state_token,
            )
        if cmd == "perform_secondary_action":
            return perform_secondary_action(
                args.window,
                _parse_json(args.target),
                args.action,
                action_id=args.action_id,
                state_token=args.state_token,
            )
        if cmd == "drag":
            return drag(args.window, args.to_x, args.to_y)
        if cmd == "resize":
            return resize(args.window, args.handle, args.to_x, args.to_y)
        if cmd == "set_window_frame":
            return set_window_frame(
                args.window,
                args.x,
                args.y,
                args.width,
                args.height,
                animate=False if args.no_animate else None,
            )
        raise SystemExit(f"unknown command: {cmd}")
    except ComputerUseUnavailableError as exc:
        # Surface to caller as a structured error dict; exit code is 78
        # so the caller can branch on EX_CONFIG.
        return {"error": "computer_use_unavailable", "message": str(exc)}
    except ValueError as exc:
        return {"error": "invalid_argument", "message": str(exc)}


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``valor-computer``."""
    # OS gate: enforce darwin-only at the entry point, before any work.
    gate_rc = _enforce_os_gate()
    if gate_rc:
        return gate_rc

    parser = _build_parser()
    args = parser.parse_args(argv)
    result = _dispatch(args)
    _print_json(result)

    # Surface configuration failures via exit 78 so callers can branch on
    # EX_CONFIG vs. generic command errors.
    if isinstance(result, dict) and result.get("error") == "computer_use_unavailable":
        return EX_CONFIG
    if isinstance(result, dict) and "error" in result:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
