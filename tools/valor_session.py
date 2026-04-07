#!/usr/bin/env python3
"""
Session management CLI for AgentSession — create, steer, monitor, and kill sessions.

Usage:
    valor-session create --role pm --chat-id 123 --message "Plan issue #735"
    valor-session create --role dev --message "Fix the bug" --parent abc123
    valor-session create --role pm --message "..." --project-key valor
    valor-session steer --id abc123 --message "Stop after critique stage"
    valor-session status --id abc123
    valor-session list
    valor-session list --status running
    valor-session list --role pm
    valor-session kill --id abc123
    valor-session kill --all

Project Key Resolution (for `create` subcommand):
    The project_key is derived automatically from the current working directory by
    matching against the working_directory field of each project in projects.json.
    The most-specific match (longest path prefix) wins.

    If no match is found, "valor" is used as the fallback and a warning is printed
    to stderr so it doesn't pollute --json output.

    Use --project-key to override resolution explicitly (useful in scripts/CI).

This tool is the external interface for session steering. It writes to
AgentSession.queued_steering_messages (via steer_session()) and manages
session lifecycle without requiring bridge access.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Bootstrap path so this runs as a standalone script from any directory
_repo_root = Path(__file__).parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def _load_env() -> None:
    """Load environment variables from .env files."""
    try:
        from dotenv import load_dotenv

        load_dotenv(_repo_root / ".env")
        load_dotenv(Path.home() / "Desktop" / "Valor" / ".env")
    except Exception:
        pass


def resolve_project_key(cwd: str) -> str:
    """Derive the project_key from cwd by matching against projects.json.

    Loads projects.json via bridge.routing.load_config(), iterates the projects
    dict, and returns the key whose working_directory equals or is a parent of
    cwd. When multiple projects match (overlapping paths), the most specific
    match (longest working_directory path) wins.

    Falls back to "valor" and prints a warning to stderr if no match is found
    or if projects.json is unavailable.

    Args:
        cwd: The current working directory to match against project paths.

    Returns:
        The matching project key, or "valor" if no match is found.
    """
    try:
        from bridge.routing import load_config

        config = load_config()
    except Exception as e:
        print(
            f"Warning: could not load projects.json ({e}), using project_key='valor'",
            file=sys.stderr,
        )
        return "valor"

    cwd_path = Path(cwd).resolve()
    best_key: str | None = None
    best_len: int = -1

    projects = config.get("projects", {})
    for key, project in projects.items():
        wd = project.get("working_directory", "")
        if not wd:
            continue
        try:
            wd_path = Path(wd).resolve()
            if cwd_path == wd_path or cwd_path.is_relative_to(wd_path):
                wd_len = len(str(wd_path))
                if wd_len > best_len:
                    best_len = wd_len
                    best_key = key
        except Exception:
            continue

    if best_key is not None:
        return best_key

    print(
        f"Warning: current directory {cwd!r} does not match any project in projects.json, "
        "using project_key='valor'",
        file=sys.stderr,
    )
    return "valor"


def _format_ts(ts: str | float | None) -> str:
    """Format a timestamp for display."""
    if ts is None:
        return "—"
    try:
        if isinstance(ts, float | int):
            dt = datetime.fromtimestamp(ts, tz=UTC)
        else:
            dt = datetime.fromisoformat(str(ts))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)[:19]


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new AgentSession and enqueue it.

    project_key is resolved from the current working directory via projects.json
    unless --project-key is provided explicitly.
    """
    _load_env()
    try:
        import asyncio
        import os

        from agent.agent_session_queue import _push_agent_session
        from bridge.utc import utc_now

        role = args.role or "pm"
        message = args.message
        chat_id = args.chat_id or "0"
        parent_id = getattr(args, "parent", None)

        # Derive a session_id from timestamp + role
        ts_suffix = str(int(utc_now().timestamp()))
        session_id = f"{chat_id}_{ts_suffix}"

        working_dir = args.working_dir or str(_repo_root)

        session_type = role  # pm, dev, teammate

        # Resolve project_key: explicit flag takes priority, else derive from cwd
        explicit_key = getattr(args, "project_key", None)
        if explicit_key:
            project_key = explicit_key
        else:
            project_key = resolve_project_key(os.getcwd())

        async def _create():
            await _push_agent_session(
                project_key=project_key,
                session_id=session_id,
                working_dir=working_dir,
                message_text=message,
                sender_name=f"valor-session ({role})",
                chat_id=chat_id,
                telegram_message_id=0,
                session_type=session_type,
                parent_agent_session_id=parent_id,
            )
            return session_id

        result = asyncio.run(_create())

        if args.json:
            print(
                json.dumps(
                    {"session_id": result, "status": "created", "project_key": project_key},
                    indent=2,
                )
            )
        else:
            print(f"Created session: {result}")
            print(f"  Role:        {role}")
            print(f"  Project key: {project_key}")
            print(f"  Message: {message[:80]}")
            print(f"  Chat ID: {chat_id}")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_steer(args: argparse.Namespace) -> int:
    """Write a steering message to a session's queued_steering_messages."""
    _load_env()
    try:
        from agent.agent_session_queue import steer_session

        result = steer_session(args.id, args.message)

        if args.json:
            print(json.dumps(result, indent=2))
            return 0 if result["success"] else 1

        if result["success"]:
            print(f"Steered session {args.id}: {args.message[:80]!r}")
            return 0
        else:
            print(f"Error: {result['error']}", file=sys.stderr)
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show status of a session."""
    _load_env()
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=args.id))
        if not sessions:
            print(f"Session not found: {args.id}", file=sys.stderr)
            return 1

        session = sessions[0]

        if args.json:
            data = {
                "agent_session_id": session.agent_session_id,
                "session_id": session.session_id,
                "status": session.status,
                "session_type": getattr(session, "session_type", None),
                "role": getattr(session, "role", None),
                "auto_continue_count": session.auto_continue_count,
                "created_at": str(session.created_at) if session.created_at else None,
                "started_at": str(session.started_at) if session.started_at else None,
                "updated_at": str(session.updated_at) if session.updated_at else None,
                "message_preview": (session.message_text or "")[:100],
                "queued_steering_messages": session.queued_steering_messages or [],
                "slug": getattr(session, "slug", None),
                "branch_name": getattr(session, "branch_name", None),
                "issue_url": getattr(session, "issue_url", None),
                "pr_url": getattr(session, "pr_url", None),
            }
            print(json.dumps(data, indent=2, default=str))
            return 0

        print(f"Session: {session.session_id}")
        print(f"  Status:        {session.status}")
        stype = getattr(session, "session_type", "—")
        srole = getattr(session, "role", "—")
        print(f"  Type/Role:     {stype} / {srole}")
        print(f"  Auto-continue: {session.auto_continue_count}")
        print(f"  Created:       {_format_ts(session.created_at)}")
        print(f"  Started:       {_format_ts(session.started_at)}")
        print(f"  Updated:       {_format_ts(session.updated_at)}")
        print(f"  Message:       {(session.message_text or '')[:80]}")

        steering = session.queued_steering_messages
        if steering:
            print(f"  Pending steering messages ({len(steering)}):")
            for i, msg in enumerate(steering, 1):
                print(f"    {i}. {str(msg)[:80]}")
        else:
            print("  Pending steering messages: none")

        slug = getattr(session, "slug", None)
        if slug:
            print(f"  Slug:          {slug}")
        branch = getattr(session, "branch_name", None)
        if branch:
            print(f"  Branch:        {branch}")
        pr = getattr(session, "pr_url", None)
        if pr:
            print(f"  PR:            {pr}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """List sessions filtered by status and/or role."""
    _load_env()
    try:
        from models.agent_session import AgentSession

        # Collect all sessions — filter client-side since Popoto filter is limited
        all_sessions: list[AgentSession] = []

        status_filter = getattr(args, "status", None)
        role_filter = getattr(args, "role", None)

        if status_filter:
            for st in status_filter.split(","):
                st = st.strip()
                try:
                    all_sessions.extend(list(AgentSession.query.filter(status=st)))
                except Exception:
                    pass
        else:
            # Common statuses
            for st in ("pending", "running", "active", "completed", "failed", "killed"):
                try:
                    all_sessions.extend(list(AgentSession.query.filter(status=st)))
                except Exception:
                    pass

        # Client-side role filter
        if role_filter:
            all_sessions = [
                s
                for s in all_sessions
                if getattr(s, "role", None) == role_filter
                or getattr(s, "session_type", None) == role_filter
            ]

        # Sort by created_at descending
        all_sessions.sort(key=lambda s: s.created_at or 0, reverse=True)

        # Deduplicate by session_id
        seen = set()
        unique = []
        for s in all_sessions:
            if s.session_id not in seen:
                seen.add(s.session_id)
                unique.append(s)

        # Limit
        limit = getattr(args, "limit", 20) or 20
        unique = unique[:limit]

        if args.json:
            data = [
                {
                    "session_id": s.session_id,
                    "status": s.status,
                    "priority": getattr(s, "priority", None) or "normal",
                    "session_type": getattr(s, "session_type", None),
                    "role": getattr(s, "role", None),
                    "auto_continue_count": s.auto_continue_count,
                    "created_at": str(s.created_at) if s.created_at else None,
                    "message_preview": (s.message_text or "")[:60],
                }
                for s in unique
            ]
            print(json.dumps(data, indent=2, default=str))
            return 0

        if not unique:
            print("No sessions found.")
            return 0

        print(f"Sessions ({len(unique)}):")
        print()
        hdr = f"{'Session ID':<36} {'Status':<12} {'Priority':<8} {'Type':<10} {'Nudges':>6}"
        hdr += f" {'Created':<20} {'Message':<40}"
        print(hdr)
        print("-" * 136)

        for s in unique:
            sid = s.session_id or "—"
            if len(sid) > 34:
                sid = sid[:31] + "..."
            status = s.status or "—"
            priority = getattr(s, "priority", None) or "normal"
            stype = getattr(s, "session_type", None) or getattr(s, "role", None) or "—"
            nudges = s.auto_continue_count or 0
            created = _format_ts(s.created_at)
            msg = (s.message_text or "")[:38]
            row = f"{sid:<36} {status:<12} {priority:<8} {stype:<10} {nudges:>6}"
            row += f" {created:<20} {msg:<40}"
            print(row)

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_kill(args: argparse.Namespace) -> int:
    """Kill a session or all running sessions."""
    _load_env()
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES, finalize_session

        killed = []
        errors = []

        if getattr(args, "all", False):
            # Kill all non-terminal sessions
            for st in ("pending", "running", "active"):
                try:
                    sessions = list(AgentSession.query.filter(status=st))
                    for s in sessions:
                        try:
                            finalize_session(s, "killed", reason="valor-session kill --all")
                            killed.append(s.session_id)
                        except Exception as e:
                            errors.append(f"{s.session_id}: {e}")
                except Exception:
                    pass
        else:
            session_id = args.id
            sessions = list(AgentSession.query.filter(session_id=session_id))
            if not sessions:
                print(f"Session not found: {session_id}", file=sys.stderr)
                return 1

            session = sessions[0]
            current_status = getattr(session, "status", None)
            if current_status in TERMINAL_STATUSES:
                msg = f"Session {session_id} is already in terminal status {current_status!r}"
                if args.json:
                    print(json.dumps({"success": False, "error": msg}))
                else:
                    print(f"Warning: {msg}")
                return 0

            finalize_session(session, "killed", reason="valor-session kill")
            killed.append(session_id)

        if args.json:
            print(json.dumps({"killed": killed, "errors": errors}, indent=2))
            return 0 if not errors else 1

        if killed:
            print(f"Killed {len(killed)} session(s):")
            for sid in killed:
                print(f"  {sid}")
        if errors:
            print(f"Errors ({len(errors)}):")
            for err in errors:
                print(f"  {err}", file=sys.stderr)

        return 0 if not errors else 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="valor-session",
        description="Manage AgentSessions — create, steer, monitor, and kill",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # create subcommand
    create_parser = subparsers.add_parser("create", help="Create and enqueue a new session")
    create_parser.add_argument(
        "--role",
        "-r",
        default="pm",
        choices=["pm", "dev", "teammate"],
        help="Session role/type (default: pm)",
    )
    create_parser.add_argument(
        "--message", "-m", required=True, help="Initial message for the session"
    )
    create_parser.add_argument("--chat-id", help="Telegram chat ID (default: 0)")
    create_parser.add_argument("--parent", help="Parent AgentSession ID (for child sessions)")
    create_parser.add_argument("--working-dir", help="Working directory for the session")
    create_parser.add_argument(
        "--project-key",
        help=(
            "Explicit project key (overrides automatic cwd-based resolution). "
            "If omitted, the key is derived from the current working directory "
            "by matching against projects.json."
        ),
    )
    create_parser.add_argument("--json", action="store_true", help="Output JSON")

    # steer subcommand
    steer_parser = subparsers.add_parser("steer", help="Inject a steering message into a session")
    steer_parser.add_argument("--id", required=True, help="Session ID to steer")
    steer_parser.add_argument("--message", "-m", required=True, help="Steering message to inject")
    steer_parser.add_argument("--json", action="store_true", help="Output JSON")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show session status")
    status_parser.add_argument("--id", required=True, help="Session ID")
    status_parser.add_argument("--json", action="store_true", help="Output JSON")

    # list subcommand
    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("--status", help="Filter by status (comma-separated)")
    list_parser.add_argument("--role", help="Filter by role/session_type")
    list_parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    list_parser.add_argument("--json", action="store_true", help="Output JSON")

    # kill subcommand
    kill_parser = subparsers.add_parser("kill", help="Kill a session")
    kill_parser.add_argument("--id", help="Session ID to kill")
    kill_parser.add_argument("--all", action="store_true", help="Kill all running sessions")
    kill_parser.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    dispatch = {
        "create": cmd_create,
        "steer": cmd_steer,
        "status": cmd_status,
        "list": cmd_list,
        "kill": cmd_kill,
    }

    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
