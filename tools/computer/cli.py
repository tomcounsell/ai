"""CLI wrapper for tools.computer.

Entry point: ``valor-computer`` (declared in pyproject.toml ``[project.scripts]``).

This is the canonical agent-facing surface for desktop automation. The
``computer-use`` skill body invokes this CLI via Bash; ``python -m
tools.computer`` is intentionally not supported (skips the OS gate).

OS gate
-------
bcu (the underlying Swift app) is macOS-only. On non-macOS hosts this
CLI prints a clear stderr message and exits 78 (``EX_CONFIG``) before
performing any work.
"""

from __future__ import annotations

import argparse
import json
import os
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

    p_list_windows = sub.add_parser("list_windows", help="List open windows.")
    p_list_windows.add_argument(
        "--bundle-id", help="Filter by app bundle ID (e.g. com.apple.Notes)."
    )

    p_state = sub.add_parser("get_window_state", help="Dump AX tree for a window.")
    p_state.add_argument("window_id", type=int)

    p_click = sub.add_parser("click", help="Click in a window.")
    p_click.add_argument("window_id", type=int)
    p_click.add_argument("--x", type=float, help="Window-relative x coordinate.")
    p_click.add_argument("--y", type=float, help="Window-relative y coordinate.")
    p_click.add_argument("--ref", help="Raw AX ref JSON (use for non-Electron apps).")
    p_click.add_argument(
        "--selector",
        help=(
            "Selector JSON for Electron targets (re-queries AX tree before "
            'each call). Example: \'{"role":"button","label":"Send"}\'.'
        ),
    )

    p_scroll = sub.add_parser("scroll", help="Scroll a window.")
    p_scroll.add_argument("window_id", type=int)
    p_scroll.add_argument("--dx", type=float, default=0.0)
    p_scroll.add_argument("--dy", type=float, default=0.0)

    p_type = sub.add_parser("type_text", help="Type text into a window.")
    p_type.add_argument("window_id", type=int)
    p_type.add_argument("text")

    p_press = sub.add_parser("press_key", help="Press a single key.")
    p_press.add_argument("window_id", type=int)
    p_press.add_argument("key")
    p_press.add_argument("--mod", action="append", default=[], help="Repeat for multi.")

    p_set = sub.add_parser("set_value", help="Set the value of an AX element.")
    p_set.add_argument("window_id", type=int)
    p_set.add_argument("value")
    p_set.add_argument("--ref")
    p_set.add_argument("--selector")

    p_secondary = sub.add_parser(
        "perform_secondary_action", help="Right-click / show menu on an AX element."
    )
    p_secondary.add_argument("window_id", type=int)
    p_secondary.add_argument("ref", help="AX ref JSON")

    p_drag = sub.add_parser("drag", help="Drag in a window.")
    p_drag.add_argument("window_id", type=int)
    p_drag.add_argument("--from", dest="from_xy", help="x,y window-relative")
    p_drag.add_argument("--to", dest="to_xy", help="x,y window-relative")
    p_drag.add_argument("--ref")
    p_drag.add_argument("--selector")

    p_resize = sub.add_parser("resize", help="Resize a window.")
    p_resize.add_argument("window_id", type=int)
    p_resize.add_argument("width", type=float)
    p_resize.add_argument("height", type=float)

    p_frame = sub.add_parser("set_window_frame", help="Move + resize in one call.")
    p_frame.add_argument("window_id", type=int)
    p_frame.add_argument("x", type=float)
    p_frame.add_argument("y", type=float)
    p_frame.add_argument("width", type=float)
    p_frame.add_argument("height", type=float)

    p_shot = sub.add_parser(
        "screenshot_window", help="Capture a window screenshot (returns base64 PNG)."
    )
    p_shot.add_argument("window_id", type=int)
    p_shot.add_argument(
        "--output", help="Write the decoded PNG to this path instead of printing JSON."
    )

    return parser


def _parse_xy(value: str | None) -> tuple[float, float] | None:
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 2:
        raise SystemExit(f"--from/--to expects 'x,y' format, got {value!r}")
    return float(parts[0]), float(parts[1])


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
        screenshot_window,
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
            return list_windows(bundle_id=args.bundle_id)
        if cmd == "get_window_state":
            return get_window_state(args.window_id)
        if cmd == "click":
            return click(
                args.window_id,
                x=args.x,
                y=args.y,
                ref=_parse_json(args.ref),
                selector=_parse_json(args.selector),
            )
        if cmd == "scroll":
            return scroll(args.window_id, dx=args.dx, dy=args.dy)
        if cmd == "type_text":
            return type_text(args.window_id, args.text)
        if cmd == "press_key":
            return press_key(args.window_id, args.key, modifiers=args.mod or None)
        if cmd == "set_value":
            return set_value(
                args.window_id,
                args.value,
                ref=_parse_json(args.ref),
                selector=_parse_json(args.selector),
            )
        if cmd == "perform_secondary_action":
            return perform_secondary_action(args.window_id, _parse_json(args.ref))
        if cmd == "drag":
            return drag(
                args.window_id,
                from_xy=_parse_xy(args.from_xy),
                to_xy=_parse_xy(args.to_xy),
                ref=_parse_json(args.ref),
                selector=_parse_json(args.selector),
            )
        if cmd == "resize":
            return resize(args.window_id, args.width, args.height)
        if cmd == "set_window_frame":
            return set_window_frame(args.window_id, args.x, args.y, args.width, args.height)
        if cmd == "screenshot_window":
            result = screenshot_window(args.window_id)
            if "error" not in result and args.output:
                # Decode + write the PNG out to disk, replace base64 in the
                # printed payload with the file path so the JSON is small.
                import base64

                data = base64.b64decode(result.get("image_base64", ""))
                with open(args.output, "wb") as f:
                    f.write(data)
                result = {**result, "image_base64": None, "saved_to": args.output}
            return result
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


# os import retained for environment-related extensions (e.g. TMPDIR
# overrides during tests). Currently no direct use here but intentionally
# kept to mirror the pattern of other CLIs in tools/.
_ = os
