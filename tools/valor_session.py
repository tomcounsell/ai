#!/usr/bin/env python3
"""
Session management CLI for AgentSession — create, steer, monitor, and kill sessions.

Usage:
    valor-session create --role pm --chat-id 123 --message "Plan issue #735"
    valor-session create --role dev --message "Fix the bug" --parent abc123
    valor-session create --role dev --model sonnet --message "Build feature X" --parent abc123
    valor-session create --role pm --message "..." --project-key valor
    valor-session resume --id abc123 --message "Fix: add missing validation"
    valor-session release --pr 900
    valor-session steer --id abc123 --message "Stop after critique stage"
    valor-session status --id abc123
    valor-session status --id abc123 --full-message
    valor-session inspect --id abc123
    valor-session children --id abc123
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
import time
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
        load_dotenv(Path.home() / "Desktop" / "Valor" / ".env")  # symlink target — no-op
    except Exception:
        pass


_WORKER_HEARTBEAT_FILE = _repo_root / "data" / "last_worker_connected"

from agent.constants import HEARTBEAT_STALENESS_THRESHOLD_S  # noqa: E402


def _check_worker_health() -> tuple[bool, int | None]:
    """Check worker health by reading the heartbeat file modification time.

    Returns (healthy, age_s) where:
      - healthy is True if the heartbeat was updated within the last 360 seconds
      - age_s is the integer age in seconds, or None if the file is missing or unreadable

    Never raises — all OSError and unexpected exceptions are caught silently.
    Missing file == unhealthy (worker has never run on this machine).
    """
    try:
        mtime = _WORKER_HEARTBEAT_FILE.stat().st_mtime
        age_s = int(time.time() - mtime)
        return (age_s < HEARTBEAT_STALENESS_THRESHOLD_S, age_s)
    except Exception:
        return (False, None)


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
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
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

        _ROLE_TO_SESSION_TYPE = {"pm": "pm", "dev": "dev", "teammate": "teammate"}
        role = args.role or "pm"
        if role not in _ROLE_TO_SESSION_TYPE:
            raise ValueError(
                f"Unknown --role value: {role!r}. Allowed values: {sorted(_ROLE_TO_SESSION_TYPE)}"
            )
        session_type = _ROLE_TO_SESSION_TYPE[role]
        message = args.message
        chat_id = args.chat_id or "0"
        parent_id = getattr(args, "parent", None)
        model = getattr(args, "model", None)

        # Derive a session_id from timestamp + role
        ts_suffix = str(int(utc_now().timestamp() * 1000))
        session_id = f"{chat_id}_{ts_suffix}"

        working_dir = args.working_dir or str(_repo_root)

        # If --slug is provided, validate and provision worktree (issue #887)
        slug = getattr(args, "slug", None)
        if slug:
            from agent.worktree_manager import _validate_slug, get_or_create_worktree

            _validate_slug(slug)  # Raises ValueError for invalid slugs
            wt_path = get_or_create_worktree(Path(working_dir), slug)
            working_dir = str(wt_path)
            print(f"  Worktree:    {working_dir}", file=sys.stderr)

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
                slug=slug,
                model=model,
            )
            return session_id

        result = asyncio.run(_create())

        # Check worker health after enqueue — warn if no active worker
        worker_healthy, worker_age_s = _check_worker_health()

        if args.json:
            print(
                json.dumps(
                    {
                        "session_id": result,
                        "status": "created",
                        "project_key": project_key,
                        "model": model,
                        "worker_healthy": worker_healthy,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Created session: {result}")
            print(f"  Role:        {role}")
            print(f"  Project key: {project_key}")
            if model:
                print(f"  Model:       {model}")
            print(f"  Message: {message[:80]}")
            print(f"  Chat ID: {chat_id}")
            if not worker_healthy:
                print(
                    "WARNING: no active worker detected — session will stay pending until a "
                    "worker is started (run: ./scripts/valor-service.sh worker-start)",
                    file=sys.stderr,
                )
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume a completed BUILD session by re-enqueuing it with a new message.

    Validates the session is in 'completed' status, transitions it back to
    'pending', appends the new message to the steering queue, so the worker
    delivers it as the first message in the resumed conversation.

    This enables hard-PATCH resume: the worker picks up the session and calls
    `claude -p --resume <uuid>` to continue the original BUILD transcript.
    """
    _load_env()
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import transition_status

        session_id = args.id
        new_message = args.message

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            print(f"Error: Session not found: {session_id}", file=sys.stderr)
            return 1

        # Pick the most recent record for this session_id
        sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
        session = sessions[0]

        current_status = getattr(session, "status", None)
        if current_status == "pending":
            print(
                f"Error: Session {session_id} is already pending — cannot resume.",
                file=sys.stderr,
            )
            return 1
        if current_status == "running":
            print(
                f"Error: Session {session_id} is currently running — cannot resume.",
                file=sys.stderr,
            )
            return 1
        if current_status != "completed":
            print(
                f"Error: Session {session_id} has status '{current_status}'. "
                "Only completed sessions can be resumed.",
                file=sys.stderr,
            )
            return 1

        # Stage steering message BEFORE transitioning to pending so the worker
        # always sees it — eliminates the two-write race (transition then save).
        existing_steering = list(session.queued_steering_messages or [])
        existing_steering.append(new_message)
        session.queued_steering_messages = existing_steering
        session.save()

        # Transition to pending (atomic — fails if another process raced us).
        # Steering message is already persisted above, so no race window.
        try:
            transition_status(
                session, "pending", reason="valor-session resume", reject_from_terminal=False
            )
        except Exception as e:
            print(
                f"Error: Could not transition session {session_id} to pending: {e}",
                file=sys.stderr,
            )
            return 1

        model = getattr(session, "model", None)
        uuid = getattr(session, "claude_session_uuid", None)

        if args.json:
            print(
                json.dumps(
                    {
                        "session_id": session_id,
                        "status": "resumed",
                        "model": model,
                        "claude_session_uuid": uuid,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Resumed session: {session_id}")
            if model:
                print(f"  Model:               {model}")
            if uuid:
                print(f"  Claude session UUID: {uuid}")
            print(f"  Message: {new_message[:80]}")
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
        full_message = getattr(args, "full_message", False)

        # Check worker health when session is pending
        worker_healthy: bool | None = None
        if session.status == "pending":
            worker_healthy, _ = _check_worker_health()

        if args.json:
            data = {
                "agent_session_id": session.agent_session_id,
                "session_id": session.session_id,
                "status": session.status,
                "session_type": getattr(session, "session_type", None),
                "auto_continue_count": session.auto_continue_count,
                "created_at": str(session.created_at) if session.created_at else None,
                "started_at": str(session.started_at) if session.started_at else None,
                "updated_at": str(session.updated_at) if session.updated_at else None,
                "message": session.message_text
                if full_message
                else (session.message_text or "")[:100],
                "message_preview": (session.message_text or "")[:100],  # backward-compat alias
                "queued_steering_messages": session.queued_steering_messages or [],
                "slug": getattr(session, "slug", None),
                "branch_name": getattr(session, "branch_name", None),
                "issue_url": getattr(session, "issue_url", None),
                "pr_url": getattr(session, "pr_url", None),
                "parent_agent_session_id": getattr(session, "parent_agent_session_id", None),
            }
            if worker_healthy is not None:
                data["worker_healthy"] = worker_healthy
            print(json.dumps(data, indent=2, default=str))
            return 0

        print(f"Session: {session.session_id}")
        print(f"  Status:        {session.status}")
        if worker_healthy is False:
            print("  WARNING: No active worker — session may wait indefinitely.", file=sys.stderr)
        stype = getattr(session, "session_type", "—")
        print(f"  Type:          {stype}")
        print(f"  Auto-continue: {session.auto_continue_count}")
        print(f"  Created:       {_format_ts(session.created_at)}")
        print(f"  Started:       {_format_ts(session.started_at)}")
        print(f"  Updated:       {_format_ts(session.updated_at)}")
        parent = getattr(session, "parent_agent_session_id", None)
        if parent:
            print(f"  Parent:        {parent}")

        if full_message:
            print(f"  Message:\n{session.message_text or ''}")
        else:
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


def cmd_inspect(args: argparse.Namespace) -> int:
    """Dump all raw fields of a session for debugging."""
    _load_env()
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=args.id))
        if not sessions:
            print(f"Session not found: {args.id}", file=sys.stderr)
            return 1

        session = sessions[0]

        # Gather all accessible fields
        data: dict = {}
        for field_name in dir(session):
            if field_name.startswith("_"):
                continue
            try:
                val = getattr(session, field_name)
                if callable(val):
                    continue
                data[field_name] = val
            except Exception:
                pass

        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            for k, v in sorted(data.items()):
                v_str = str(v) if not isinstance(v, str) else v
                if len(v_str) > 200:
                    v_str = v_str[:200] + "…"
                print(f"  {k:<35} {v_str}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_children(args: argparse.Namespace) -> int:
    """List child sessions spawned by a parent session."""
    _load_env()
    try:
        from models.agent_session import AgentSession

        # Resolve the parent's agent_session_id from its session_id
        parent_id = args.id
        parent_sessions = list(AgentSession.query.filter(session_id=parent_id))
        if parent_sessions:
            parent_agent_id = parent_sessions[0].agent_session_id
        else:
            # Maybe they passed the agent_session_id directly
            parent_agent_id = parent_id

        # Scan all sessions for matching parent
        all_children: list[AgentSession] = []
        from models.session_lifecycle import ALL_STATUSES

        for st in ALL_STATUSES:
            try:
                for s in AgentSession.query.filter(status=st):
                    pid = getattr(s, "parent_agent_session_id", None)
                    # Dual-match: caller may pass either session_id or agent_session_id; check both
                    if pid and (pid == parent_agent_id or pid == parent_id):
                        all_children.append(s)
            except Exception:
                pass

        all_children.sort(key=lambda s: s.created_at or 0)

        if args.json:
            data = [
                {
                    "session_id": s.session_id,
                    "agent_session_id": s.agent_session_id,
                    "status": s.status,
                    "session_type": getattr(s, "session_type", None),
                    "created_at": str(s.created_at) if s.created_at else None,
                    "message_preview": (s.message_text or "")[:120],
                }
                for s in all_children
            ]
            print(json.dumps(data, indent=2, default=str))
            return 0

        if not all_children:
            print(f"No child sessions found for: {parent_id}")
            return 0

        print(f"Children of {parent_id} ({len(all_children)}):")
        print()
        for s in all_children:
            sid = s.session_id or "—"
            status = s.status or "—"
            stype = getattr(s, "session_type", None) or "—"
            created = _format_ts(s.created_at)
            msg = (s.message_text or "")[:60]
            print(f"  {sid:<38} {status:<12} {stype:<8} {created:<22} {msg}")

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
            # All known statuses — use ALL_STATUSES to avoid silently missing statuses
            from models.session_lifecycle import ALL_STATUSES

            for st in ALL_STATUSES:
                try:
                    all_sessions.extend(list(AgentSession.query.filter(status=st)))
                except Exception:
                    pass

        # Client-side role filter — matches on session_type only
        if role_filter:
            all_sessions = [
                s
                for s in all_sessions
                if getattr(s, "session_type", None) == role_filter
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
            stype = getattr(s, "session_type", None) or "—"
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


def cmd_wait_for_children(args: argparse.Namespace) -> int:
    """Transition the calling session to waiting_for_children status.

    Used by PM sessions after spawning child PM sessions via fan-out.
    The parent session will auto-transition to completed when all children
    finish via _finalize_parent_sync() in models.session_lifecycle.
    """
    _load_env()
    try:
        import os

        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES, transition_status

        session_id = getattr(args, "session_id", None) or os.environ.get("AGENT_SESSION_ID")
        if not session_id:
            print(
                "Error: No session ID provided. Use --session-id or set $AGENT_SESSION_ID.",
                file=sys.stderr,
            )
            return 1

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            print(f"Error: Session not found: {session_id}", file=sys.stderr)
            return 1

        session = sessions[0]
        current_status = getattr(session, "status", None)
        if current_status in TERMINAL_STATUSES:
            print(
                f"Error: Session {session_id} is already in terminal status {current_status!r}.",
                file=sys.stderr,
            )
            return 1

        transition_status(session, "waiting_for_children")
        print(f"Session {session_id} transitioned to waiting_for_children.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_release(args: argparse.Namespace) -> int:
    """Clear retain_for_resume on the BUILD session associated with a PR.

    Called by the PM session after a PR merges or closes to release the BUILD
    session from retention. Without this, the session lingers until the 30-day
    Meta.ttl backstop expires.

    Lookup strategy: match by slug (PR branch `session/{slug}` → slug on AgentSession).
    If no match is found, logs a warning and exits cleanly (no crash — the TTL
    backstop will handle it).
    """
    _load_env()
    try:
        from models.agent_session import AgentSession

        pr_number = str(args.pr)

        # Strategy 1: match by pr_url containing the PR number
        released = []
        all_completed: list[AgentSession] = []
        try:
            all_completed = list(AgentSession.query.filter(status="completed"))
        except Exception:
            pass

        # Also check superseded (may have been superseded after completion)
        try:
            all_completed.extend(list(AgentSession.query.filter(status="superseded")))
        except Exception:
            pass

        # Strategy 2: match by slug via PR branch name (session/{slug})
        # Fetched once up-front so we don't shell out per-session.
        pr_branch = ""
        try:
            import subprocess as _subprocess

            _gh_result = _subprocess.run(
                ["gh", "pr", "view", pr_number, "--json", "headRefName", "--jq", ".headRefName"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if _gh_result.returncode == 0:
                pr_branch = _gh_result.stdout.strip()
        except Exception:
            pass  # gh unavailable — fall back to pr_url matching only

        for s in all_completed:
            pr_url = getattr(s, "pr_url", None) or ""
            slug = getattr(s, "slug", None) or ""
            retain = getattr(s, "retain_for_resume", False)
            if not retain:
                continue
            # Match by pr_url containing PR number
            pr_match = pr_number in pr_url
            # Or match by slug appearing in the PR's branch name (e.g. session/{slug})
            branch_match = bool(slug) and bool(pr_branch) and slug in pr_branch
            if pr_match or branch_match:
                s.retain_for_resume = False
                s.save()
                released.append(s.session_id)

        if args.json:
            print(
                json.dumps(
                    {
                        "pr": pr_number,
                        "released": released,
                        "count": len(released),
                    },
                    indent=2,
                )
            )
        else:
            if released:
                print(f"Released {len(released)} BUILD session(s) for PR #{pr_number}:")
                for sid in released:
                    print(f"  {sid}")
            else:
                print(
                    f"No retained BUILD sessions found for PR #{pr_number}. "
                    "(TTL backstop will handle cleanup.)"
                )
        return 0

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
    create_parser.add_argument(
        "--slug",
        help=(
            "Work item slug for worktree isolation. When provided, a worktree "
            "is provisioned at .worktrees/{slug}/ and working_dir is set to it. "
            "This ensures the session runs in an isolated directory (issue #887)."
        ),
    )
    create_parser.add_argument(
        "--model",
        help=(
            "Claude model to use for this session (e.g. 'sonnet', 'opus'). "
            "When set, overrides the environment/CLI default. "
            "Enables per-SDLC-stage model selection."
        ),
    )
    create_parser.add_argument("--json", action="store_true", help="Output JSON")

    # resume subcommand
    resume_parser = subparsers.add_parser(
        "resume",
        help="Resume a completed BUILD session with a new message (hard-PATCH resume)",
    )
    resume_parser.add_argument(
        "--id", required=True, help="Session ID of the completed BUILD session to resume"
    )
    resume_parser.add_argument(
        "--message",
        "-m",
        required=True,
        help="New message to inject into the resumed session",
    )
    resume_parser.add_argument("--json", action="store_true", help="Output JSON")

    # steer subcommand
    steer_parser = subparsers.add_parser("steer", help="Inject a steering message into a session")
    steer_parser.add_argument("--id", required=True, help="Session ID to steer")
    steer_parser.add_argument("--message", "-m", required=True, help="Steering message to inject")
    steer_parser.add_argument("--json", action="store_true", help="Output JSON")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show session status")
    status_parser.add_argument("--id", required=True, help="Session ID")
    status_parser.add_argument(
        "--full-message",
        dest="full_message",
        action="store_true",
        help="Print full initial message without truncation",
    )
    status_parser.add_argument("--json", action="store_true", help="Output JSON")

    # list subcommand
    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("--status", help="Filter by status (comma-separated)")
    list_parser.add_argument("--role", help="Filter by role/session_type")
    list_parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    list_parser.add_argument("--json", action="store_true", help="Output JSON")

    # inspect subcommand
    inspect_parser = subparsers.add_parser(
        "inspect", help="Dump all raw fields of a session (for debugging)"
    )
    inspect_parser.add_argument("--id", required=True, help="Session ID")
    inspect_parser.add_argument("--json", action="store_true", help="Output JSON")

    # children subcommand
    children_parser = subparsers.add_parser(
        "children", help="List child sessions spawned by a parent session"
    )
    children_parser.add_argument("--id", required=True, help="Parent session ID")
    children_parser.add_argument("--json", action="store_true", help="Output JSON")

    # kill subcommand
    kill_parser = subparsers.add_parser("kill", help="Kill a session")
    kill_parser.add_argument("--id", help="Session ID to kill")
    kill_parser.add_argument("--all", action="store_true", help="Kill all running sessions")
    kill_parser.add_argument("--json", action="store_true", help="Output JSON")

    # wait-for-children subcommand
    wfc_parser = subparsers.add_parser(
        "wait-for-children",
        help="Transition session to waiting_for_children (called by PM after fan-out)",
    )
    wfc_parser.add_argument(
        "--session-id",
        dest="session_id",
        help="Session ID to transition (defaults to $AGENT_SESSION_ID env var)",
    )

    # release subcommand
    release_parser = subparsers.add_parser(
        "release",
        help="Clear retain_for_resume on BUILD session(s) associated with a merged/closed PR",
    )
    release_parser.add_argument(
        "--pr",
        required=True,
        type=int,
        help="PR number whose BUILD session(s) should be released from retention",
    )
    release_parser.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    dispatch = {
        "create": cmd_create,
        "resume": cmd_resume,
        "steer": cmd_steer,
        "status": cmd_status,
        "list": cmd_list,
        "kill": cmd_kill,
        "wait-for-children": cmd_wait_for_children,
        "release": cmd_release,
        "inspect": cmd_inspect,
        "children": cmd_children,
    }

    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
