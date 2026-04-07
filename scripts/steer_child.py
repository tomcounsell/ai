#!/usr/bin/env python3
"""Push steering messages from a ChatSession to its child DevSessions.

Usage:
    python scripts/steer_child.py --session-id ID --message "focus on tests" --parent-id PID
    python scripts/steer_child.py --session-id ID --message "stop" --parent-id PID --abort
    python scripts/steer_child.py --list --parent-id PID

The --parent-id can also be read from the VALOR_SESSION_ID environment variable.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure the project root is on sys.path so imports work when called from any cwd
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _resolve_parent_id(args_parent_id: str | None) -> str | None:
    """Resolve parent session ID from args or environment."""
    return args_parent_id or os.environ.get("VALOR_SESSION_ID")


def _list_children(parent_id: str) -> int:
    """List active child DevSessions for a parent ChatSession.

    Returns exit code (0 on success, 1 on error).
    """
    from models.agent_session import AgentSession

    parent = AgentSession.get_by_id(parent_id)
    if parent is None:
        print(f"Error: parent session '{parent_id}' not found", file=sys.stderr)
        return 1

    children = parent.get_child_sessions()
    running = [c for c in children if c.status == "running"]

    if not running:
        print("No active child DevSessions found.")
        return 0

    print(f"Active child DevSessions for {parent_id}:")
    for child in running:
        slug_info = f" slug={child.slug}" if child.slug else ""
        stage_info = f" stage={child.current_stage}" if child.current_stage else ""
        print(f"  {child.agent_session_id}{slug_info}{stage_info} status={child.status}")

    return 0


def _steer_child(session_id: str, message: str, parent_id: str, abort: bool) -> int:
    """Push a steering message to a child DevSession.

    Returns exit code (0 on success, 1 on error).
    """
    from agent.steering import push_steering_message
    from models.agent_session import AgentSession

    # Validate message is non-empty
    message = message.strip()
    if not message:
        print("Error: message cannot be empty", file=sys.stderr)
        return 1

    # Look up the target child session
    child = AgentSession.get_by_id(session_id)
    if child is None:
        print(f"Error: session '{session_id}' not found", file=sys.stderr)
        return 1

    # Validate it is a DevSession
    if not child.is_dev:
        print(f"Error: session '{session_id}' is not a DevSession", file=sys.stderr)
        return 1

    # Validate parent-child relationship
    if child.parent_session_id != parent_id:
        print(
            f"Error: session '{session_id}' is not a child of '{parent_id}'",
            file=sys.stderr,
        )
        return 1

    # Validate child is still running
    if child.status != "running":
        print(
            f"Error: session '{session_id}' is not active (status={child.status})",
            file=sys.stderr,
        )
        return 1

    # Push the steering message
    push_steering_message(
        session_id=session_id,
        text=message,
        sender="ChatSession",
        is_abort=abort,
    )

    preview = message[:60] + "..." if len(message) > 60 else message
    action = "Aborted" if abort else "Steered"
    print(f"{action} {session_id}: {preview}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the steer_child CLI."""
    parser = argparse.ArgumentParser(
        description="Push steering messages from ChatSession to child DevSessions."
    )
    parser.add_argument(
        "--session-id",
        help="Target child DevSession ID to steer.",
    )
    parser.add_argument(
        "--message",
        help="Steering message text to inject.",
    )
    parser.add_argument(
        "--parent-id",
        help="Parent ChatSession ID (defaults to VALOR_SESSION_ID env var).",
    )
    parser.add_argument(
        "--abort",
        action="store_true",
        help="Send an abort signal to the child session.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_children",
        help="List active child DevSessions instead of steering.",
    )

    args = parser.parse_args(argv)

    # Resolve parent ID
    parent_id = _resolve_parent_id(args.parent_id)
    if not parent_id:
        print(
            "Error: --parent-id is required (or set VALOR_SESSION_ID env var)",
            file=sys.stderr,
        )
        return 1

    # List mode
    if args.list_children:
        return _list_children(parent_id)

    # Steer mode: validate required args
    if not args.session_id:
        print("Error: --session-id is required", file=sys.stderr)
        return 1

    if args.message is None:
        print("Error: --message is required", file=sys.stderr)
        return 1

    return _steer_child(args.session_id, args.message, parent_id, args.abort)


if __name__ == "__main__":
    sys.exit(main())
