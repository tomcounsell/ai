"""CLI for the granite operator interactive TUI PoC (issue #1546).

`valor-granite-loop --user-message "<text>" [--max-turns 10]
[--output ./granite_poc_results.json]` runs the container end-to-
end and writes the per-turn trace JSON to the output path.

The CLI is the operator's standalone kernel-validation tool. It
does not wire to the bridge, does not dispatch child sessions, and
does not invoke /sdlc. The PoC is local to the dev machine; the
operator invokes it directly.

Usage:
    valor-granite-loop --user-message "Build a CLI todo app"
    valor-granite-loop --user-message "Plan a 3-step architecture" \\
        --max-turns 5 --output ./my_results.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from agent.granite_container.container import Container, result_to_json

logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="valor-granite-loop",
        description=(
            "Granite operator PoC: drive a real interactive Claude "
            "Code session via PTY, end-to-end, with zero `claude -p`."
        ),
    )
    p.add_argument(
        "--user-message",
        required=True,
        help="The user message; passed as $ARGUMENTS to the persona-priming slash command.",
    )
    p.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="Safety cap on PM<->Dev cycles. Default: 10.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("./granite_poc_results.json"),
        help="Path to write the results JSON. Default: ./granite_poc_results.json",
    )
    p.add_argument(
        "--cwd",
        type=str,
        default=None,
        help="Working directory for the spawned TUI sessions. Default: a fresh tempdir.",
    )
    p.add_argument(
        "--pm-model",
        type=str,
        default=None,
        help="Override the substrate model for PM. Default: auto-pick (prefer cloud > gemma).",
    )
    p.add_argument(
        "--dev-model",
        type=str,
        default=None,
        help="Override the substrate model for Dev. Default: auto-pick (prefer cloud > gemma).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging to stderr.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    if not args.user_message.strip():
        print("valor-granite-loop: --user-message must be non-empty", file=sys.stderr)
        return 5

    try:
        container = Container(
            user_message=args.user_message,
            cwd=args.cwd,
            max_turns=args.max_turns,
            pm_model=args.pm_model,
            dev_model=args.dev_model,
        )
        result = container.run()
    except ValueError as e:
        print(f"valor-granite-loop: {e}", file=sys.stderr)
        return 5
    except Exception as e:
        logger.exception("container.run failed: %s", e)
        print(f"valor-granite-loop: container.run failed: {e}", file=sys.stderr)
        return 4

    # Write the results JSON.
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result_to_json(result))
    except OSError as e:
        print(f"valor-granite-loop: failed to write {args.output}: {e}", file=sys.stderr)
        return 4

    # Print a one-line summary to stdout for the operator.
    print(
        json.dumps(
            {
                "session_id": result.session_id,
                "exit_reason": result.exit_reason,
                "turns": len(result.turns),
                "classification_compliance_misses": result.classification_compliance_misses,
                "parse_failures": result.parse_failures,
                "total_pm_pty_bytes": result.total_pm_pty_bytes,
                "total_dev_pty_bytes": result.total_dev_pty_bytes,
                "output_path": str(args.output),
            }
        )
    )

    # Map exit_reason to a process exit code.
    exit_code_map = {
        "pm_complete": 0,
        "pm_user": 0,
        "pm_max_turns": 1,
        "dev_hang": 2,
        "pm_hang": 2,
        "startup_unresolved": 3,
        "exception": 4,
    }
    return exit_code_map.get(result.exit_reason, 4)


if __name__ == "__main__":
    sys.exit(main())
