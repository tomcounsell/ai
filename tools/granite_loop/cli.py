"""CLI for the granite interactive-TUI session runner.

`valor-granite-loop --user-message "<text>" [--max-turns 10]
[--output ./granite_poc_results.json]` runs the container end-to-
end and writes the per-turn trace JSON to the output path.

The CLI is the operator's standalone driver for the container. It
does not wire to the bridge, does not dispatch child sessions, and
does not invoke /sdlc. It runs locally on the dev machine; the
operator invokes it directly.

AgentSession lifecycle
----------------------
Before the container starts, ``main()`` mints a ``local-``-prefixed
session ID and creates a ``running`` AgentSession record in Redis
(session_type=``granite``, project_key=``valor``). On clean exit the
session is finalized as ``completed`` (exit_reason in
{pm_complete, pm_user}) or ``failed`` (all other exit reasons). On
unexpected exception the session is also finalized as ``failed``
with ``reject_from_terminal=False`` to prevent a double-finalize
raise if the post-run path already set the status.

Session persistence is best-effort: a Redis failure prints exactly
one ``granite session not recorded: <reason>`` line to stderr and
the CLI proceeds normally. Exit codes and results JSON are never
affected by a persistence failure.

The ``agent_session_id`` field in the stdout summary JSON is the
operational ID for ``valor-session`` operations (steer/kill). The
``session_id`` field is the container's internal trace artifact.

Usage:
    valor-granite-loop --user-message "Build a CLI todo app"
    valor-granite-loop --user-message "Plan a 3-step architecture" \\
        --max-turns 5 --output ./my_results.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from agent.granite_container.container import Container, result_to_json
from config.enums import SessionType
from models.agent_session import AgentSession
from models.session_lifecycle import finalize_session

logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="valor-granite-loop",
        description=(
            "Granite interactive-TUI session runner: drive a real "
            "interactive Claude Code session via PTY, end-to-end, with "
            "zero `claude -p`."
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

    # Mint a local- prefixed session ID. The local- prefix is REQUIRED:
    # worker startup recovery (agent/session_health.py:538) discriminates by
    # session_id.startswith("local") to avoid re-executing CLI sessions as
    # bridge sessions. A bare-hex id would fall through to the bridge recovery
    # path and could re-execute this run.
    session_id = "local-" + uuid.uuid4().hex[:12]
    working_dir = args.cwd or os.getcwd()

    session: AgentSession | None = None
    try:
        session = AgentSession.create_local(
            session_id=session_id,
            session_type=SessionType.GRANITE,
            project_key="valor",
            working_dir=working_dir,
            status="running",
        )
    except Exception as e:
        print(f"granite session not recorded: {e}", file=sys.stderr)

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
        if session is not None:
            try:
                finalize_session(session, "failed", reason=repr(e), reject_from_terminal=False)
            except Exception as fe:
                logger.warning("granite session not recorded: %s", fe)
        return 5
    except Exception as e:
        logger.exception("container.run failed: %s", e)
        print(f"valor-granite-loop: container.run failed: {e}", file=sys.stderr)
        if session is not None:
            try:
                finalize_session(session, "failed", reason=repr(e), reject_from_terminal=False)
            except Exception as fe:
                logger.warning("granite session not recorded: %s", fe)
        return 4

    # Finalize the session based on exit_reason.
    if session is not None:
        try:
            status = "completed" if result.exit_reason in ("pm_complete", "pm_user") else "failed"
            finalize_session(session, status, reason=result.exit_reason)
        except Exception as e:
            logger.warning("granite session not recorded: %s", e)

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
                "agent_session_id": session.agent_session_id if session is not None else None,
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
