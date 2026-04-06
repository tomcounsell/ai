"""
Agent Session Scheduler - Agent-initiated queue operations.

Allows the agent to programmatically schedule SDLC runs, Teammate sessions,
and manage queue state mid-conversation.

Usage:
    python -m tools.agent_session_scheduler schedule --issue 113
    python -m tools.agent_session_scheduler schedule --issue 113 --priority high \
        --after "2026-03-12T02:00:00Z"
    python -m tools.agent_session_scheduler status
    python -m tools.agent_session_scheduler push --message "What is the architecture?" \\
        --project valor
    python -m tools.agent_session_scheduler bump --agent-session-id <agent_session_id>
    python -m tools.agent_session_scheduler pop --project valor
    python -m tools.agent_session_scheduler cancel --agent-session-id <agent_session_id>
    python -m tools.agent_session_scheduler list --status killed,abandoned
    python -m tools.agent_session_scheduler cleanup --age 30 --dry-run
    python -m tools.agent_session_scheduler cleanup --age 30
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from config.enums import SessionType


def _to_ts(val):
    """Convert datetime or float to Unix timestamp."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    return None


def _to_iso(val):
    """Convert datetime or float to ISO format string."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, int | float):
        return datetime.fromtimestamp(val, tz=UTC).isoformat()
    return None


logger = logging.getLogger(__name__)

# Rate limit: max scheduled sessions per hour per project
MAX_SCHEDULED_PER_HOUR = 30
MAX_SCHEDULING_DEPTH = 3

# Default DM chat_id for headless sessions (Tom's DM)
DEFAULT_DM_CHAT_ID = "179144806"
DEFAULT_PROJECT_KEY = "valor"
DEFAULT_WORKING_DIR = str(Path(__file__).parent.parent)


def _get_env_context() -> dict:
    """Read bridge-injected env vars for routing context."""
    return {
        "chat_id": os.environ.get("CHAT_ID", DEFAULT_DM_CHAT_ID),
        "project_key": os.environ.get("PROJECT_KEY", DEFAULT_PROJECT_KEY),
        "session_id": os.environ.get("VALOR_SESSION_ID", ""),
        "message_id": os.environ.get("MESSAGE_ID", "0"),
    }


def _get_scheduling_depth() -> int:
    """Get current scheduling depth from parent session."""
    session_id = os.environ.get("VALOR_SESSION_ID", "")
    if not session_id:
        return 0
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            return int(sessions[0].scheduling_depth or 0)
    except Exception:
        pass
    return 0


def _check_rate_limit(project_key: str) -> bool:
    """Check if we're under the rate limit for scheduled sessions."""
    try:
        from models.agent_session import AgentSession

        cutoff = time.time() - 3600  # 1 hour ago
        recent_scheduled = 0
        for status in ("pending", "running"):
            sessions = list(AgentSession.query.filter(project_key=project_key, status=status))
            for s in sessions:
                # Check if session has a parent (agent-scheduled, not human-initiated)
                if s.parent_agent_session_id:
                    created = _to_ts(s.created_at) or 0
                    if created > cutoff:
                        recent_scheduled += 1
        return recent_scheduled < MAX_SCHEDULED_PER_HOUR
    except Exception as e:
        logger.warning(f"Rate limit check failed: {e}")
        return True  # Fail open


def _validate_issue(issue_number: int) -> dict | None:
    """Validate GitHub issue exists and return its details."""
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "title,state,body,url"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def _output(data: dict) -> None:
    """Print structured JSON output."""
    print(json.dumps(data, indent=2))


def _get_parent_session(parent_id: str):
    """Look up a parent AgentSession by id for field inheritance.

    Returns the parent session or None if not found.
    """
    try:
        from models.agent_session import AgentSession

        return AgentSession.query.get(parent_id)
    except Exception:
        return None


# --- Persona gate ---

# Persona restrictions: which personas can perform which actions
# teammate cannot schedule SDLC sessions; all other actions are unrestricted
PERSONA_RESTRICTED_ACTIONS = {
    "teammate": {"schedule"},
}


def _check_persona_permission(action_type: str) -> dict | None:
    """Check if the current persona is allowed to perform the given action.

    Reads persona from PERSONA env var (default: "developer" — permissive).

    Args:
        action_type: The action being attempted (e.g., "schedule").

    Returns:
        None if allowed, or a dict with error details if blocked.
    """
    persona = os.environ.get("PERSONA", "developer").lower()
    restricted = PERSONA_RESTRICTED_ACTIONS.get(persona, set())

    if action_type in restricted:
        return {
            "status": "error",
            "message": (
                f"Permission denied: the '{persona}' persona cannot perform "
                f"'{action_type}' operations. SDLC scheduling is restricted to "
                f"developer and project-manager personas."
            ),
            "persona": persona,
            "action": action_type,
        }
    return None


def cmd_schedule(args: argparse.Namespace) -> int:
    """Schedule an SDLC session for a GitHub issue."""
    # Persona gate
    perm = _check_persona_permission("schedule")
    if perm:
        _output(perm)
        return 1

    from models.agent_session import AgentSession

    ctx = _get_env_context()
    depth = _get_scheduling_depth()

    # Check depth cap
    if depth >= MAX_SCHEDULING_DEPTH:
        _output(
            {
                "status": "error",
                "message": f"Scheduling depth cap reached ({MAX_SCHEDULING_DEPTH}). "
                "Cannot schedule further sessions from a self-scheduled session.",
            }
        )
        return 1

    project_key = args.project or ctx["project_key"]

    # Check rate limit
    if not _check_rate_limit(project_key):
        _output(
            {
                "status": "error",
                "message": (
                    f"Rate limit exceeded: max {MAX_SCHEDULED_PER_HOUR} "
                    "scheduled sessions per hour per project."
                ),
            }
        )
        return 1

    # Validate issue
    issue = _validate_issue(args.issue)
    if issue is None:
        _output(
            {
                "status": "error",
                "message": f"GitHub issue #{args.issue} not found or not accessible.",
            }
        )
        return 1

    if issue.get("state") == "closed":
        _output(
            {
                "status": "error",
                "message": f"GitHub issue #{args.issue} is closed.",
            }
        )
        return 1

    # Parse scheduled_at
    scheduled_at = None
    if args.after:
        try:
            dt = datetime.fromisoformat(args.after.replace("Z", "+00:00"))
            scheduled_at = dt.timestamp()
            if scheduled_at < time.time():
                scheduled_at = None  # Past = immediate
        except ValueError:
            _output(
                {
                    "status": "error",
                    "message": (
                        f"Invalid datetime format: {args.after}. "
                        "Use ISO 8601 (e.g., 2026-03-12T02:00:00Z)."
                    ),
                }
            )
            return 1

    # Build message text for SDLC dispatch
    issue_title = issue.get("title", f"Issue #{args.issue}")
    issue_url = issue.get("url", f"https://github.com/tomcounsell/ai/issues/{args.issue}")
    message_text = f"/sdlc {issue_url}\n\nIssue: {issue_title}"

    # Create session
    session_id = f"scheduled-{args.issue}-{uuid.uuid4().hex[:8]}"
    priority = args.priority or "normal"

    # Session type: explicit flag > default (pm for issue-based work)
    session_type = getattr(args, "session_type", None) or SessionType.PM

    # Parent session inheritance
    parent_id = getattr(args, "parent_session", None)
    parent_session = None
    if parent_id:
        if not parent_id.strip():
            _output({"status": "error", "message": "--parent-session cannot be empty."})
            return 1
        parent_session = _get_parent_session(parent_id)
        if parent_session is None:
            _output(
                {
                    "status": "error",
                    "message": f"Parent session {parent_id} not found.",
                }
            )
            return 1

    try:
        # Get working dir from project config (loaded from projects.json)
        working_dir = DEFAULT_WORKING_DIR
        try:
            from bridge.routing import load_config as _load_projects_config

            _all_projects = _load_projects_config().get("projects", {})
            config = _all_projects.get(project_key, {})
            if config:
                working_dir = config.get("working_directory", working_dir)
        except Exception:
            pass

        # Inherit fields from parent if this is a child session
        inherited_chat_id = ctx["chat_id"]
        inherited_correlation_id = f"sched-{uuid.uuid4().hex[:12]}"
        inherited_classification_type = "sdlc"
        if parent_session:
            if parent_session.chat_id:
                inherited_chat_id = parent_session.chat_id
            if parent_session.correlation_id:
                inherited_correlation_id = parent_session.correlation_id
            if parent_session.classification_type:
                inherited_classification_type = parent_session.classification_type
            if parent_session.working_dir:
                working_dir = parent_session.working_dir
            # Inherit priority from parent unless explicitly overridden
            if not args.priority and parent_session.priority:
                priority = parent_session.priority

        session = AgentSession.create(
            project_key=project_key,
            status="pending",
            priority=priority,
            created_at=time.time(),
            session_id=session_id,
            working_dir=working_dir,
            message_text=message_text,
            sender_name="System (Scheduled)",
            chat_id=inherited_chat_id,
            telegram_message_id=int(ctx["message_id"]) if ctx["message_id"] else 0,
            classification_type=inherited_classification_type,
            session_type=session_type,
            scheduled_at=scheduled_at,
            issue_url=issue_url,
            correlation_id=inherited_correlation_id,
            parent_agent_session_id=parent_id,
        )

        # Transition parent to waiting_for_children if not already
        if parent_session and parent_session.status != "waiting_for_children":
            try:
                from agent.agent_session_queue import _transition_parent

                _transition_parent(parent_session, "waiting_for_children")
                logger.info(f"Parent {parent_id} transitioned to waiting_for_children")
            except Exception as e:
                logger.warning(
                    f"Failed to transition parent {parent_id} to waiting_for_children: {e}"
                )

        # Count queue position
        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))
        queue_position = len(pending)

        scheduled_iso = None
        if scheduled_at:
            scheduled_iso = datetime.fromtimestamp(scheduled_at, tz=UTC).isoformat()

        result = {
            "status": "queued",
            "agent_session_id": session.agent_session_id,
            "session_id": session_id,
            "issue": args.issue,
            "issue_title": issue_title,
            "priority": priority,
            "queue_position": queue_position,
            "scheduled_at": scheduled_iso,
        }
        if parent_id:
            result["parent_agent_session_id"] = parent_id

        _output(result)
        return 0

    except Exception as e:
        _output(
            {
                "status": "error",
                "message": f"Failed to enqueue session: {e}",
            }
        )
        return 1


def _format_agent_session_info(j, include_children: bool = False) -> dict:
    """Format a single session's info for status output."""
    session_info = {
        "agent_session_id": j.agent_session_id,
        "session_id": j.session_id,
        "status": j.status,
        "priority": j.priority,
        "message_preview": (j.message_text or "")[:100],
    }
    if j.created_at:
        session_info["created_at"] = _to_iso(j.created_at)
    if j.started_at:
        session_info["started_at"] = _to_iso(j.started_at)
    if j.scheduled_at:
        session_info["scheduled_at"] = _to_iso(j.scheduled_at)
    if j.issue_url:
        session_info["issue_url"] = j.issue_url
    if j.parent_agent_session_id:
        session_info["parent_agent_session_id"] = j.parent_agent_session_id

    if include_children and j.status == "waiting_for_children":
        completed, total, failed = j.get_completion_progress()
        session_info["children_progress"] = {
            "completed": completed,
            "failed": failed,
            "total": total,
        }
        children = j.get_children()
        session_info["children"] = [
            {
                "agent_session_id": c.agent_session_id,
                "session_id": c.session_id,
                "status": c.status,
                "message_preview": (c.message_text or "")[:80],
            }
            for c in children
        ]

    return session_info


def cmd_status(args: argparse.Namespace) -> int:
    """Show queue status with session tree display."""
    from models.agent_session import AgentSession

    project_key = args.project or _get_env_context()["project_key"]

    try:
        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))
        running = list(AgentSession.query.filter(project_key=project_key, status="running"))
        completed = list(AgentSession.query.filter(project_key=project_key, status="completed"))
        waiting = list(
            AgentSession.query.filter(project_key=project_key, status="waiting_for_children")
        )
        killed = list(AgentSession.query.filter(project_key=project_key, status="killed"))

        # Sort pending by priority then FIFO
        from agent.agent_session_queue import PRIORITY_RANK

        pending.sort(key=lambda j: (PRIORITY_RANK.get(j.priority, 2), _to_ts(j.created_at) or 0))

        # Separate root sessions from child sessions for tree display
        root_pending = [j for j in pending if not j.parent_agent_session_id]
        child_pending = [j for j in pending if j.parent_agent_session_id]

        result = {
            "project": project_key,
            "pending_count": len(pending),
            "running_count": len(running),
            "waiting_for_children_count": len(waiting),
            "killed_count": len(killed),
            "recent_completed_count": len(completed),
            "pending_sessions": [_format_agent_session_info(j) for j in root_pending],
            "running_sessions": [_format_agent_session_info(j) for j in running],
        }

        # Show waiting-for-children sessions with their child trees
        if waiting:
            result["waiting_sessions"] = [
                _format_agent_session_info(j, include_children=True) for j in waiting
            ]

        # Show child sessions separately if any are pending
        if child_pending:
            result["child_pending_sessions"] = [
                _format_agent_session_info(j) for j in child_pending
            ]

        # Show killed sessions
        if killed:
            result["killed_sessions"] = [_format_agent_session_info(j) for j in killed]

        _output(result)
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to get status: {e}"})
        return 1


def cmd_push(args: argparse.Namespace) -> int:
    """Push an arbitrary message as a session (not issue-bound)."""
    from models.agent_session import AgentSession

    ctx = _get_env_context()
    depth = _get_scheduling_depth()

    if depth >= MAX_SCHEDULING_DEPTH:
        _output(
            {
                "status": "error",
                "message": f"Scheduling depth cap reached ({MAX_SCHEDULING_DEPTH}).",
            }
        )
        return 1

    project_key = args.project or ctx["project_key"]

    if not _check_rate_limit(project_key):
        _output(
            {
                "status": "error",
                "message": f"Rate limit exceeded: max {MAX_SCHEDULED_PER_HOUR}/hr/project.",
            }
        )
        return 1

    session_id = f"push-{uuid.uuid4().hex[:8]}"
    priority = args.priority or "normal"

    working_dir = DEFAULT_WORKING_DIR
    try:
        from bridge.routing import load_config as _load_projects_config

        _all_projects = _load_projects_config().get("projects", {})
        config = _all_projects.get(project_key, {})
        if config:
            working_dir = config.get("working_directory", working_dir)
    except Exception:
        pass

    try:
        session = AgentSession.create(
            project_key=project_key,
            status="pending",
            priority=priority,
            created_at=time.time(),
            session_id=session_id,
            working_dir=working_dir,
            message_text=args.message,
            sender_name="System (Push)",
            chat_id=ctx["chat_id"],
            telegram_message_id=int(ctx["message_id"]) if ctx["message_id"] else 0,
            correlation_id=f"push-{uuid.uuid4().hex[:12]}",
        )

        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))

        _output(
            {
                "status": "queued",
                "agent_session_id": session.agent_session_id,
                "session_id": session_id,
                "priority": priority,
                "queue_position": len(pending),
            }
        )
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to push session: {e}"})
        return 1


def cmd_bump(args: argparse.Namespace) -> int:
    """Bump a pending session's priority and reset created_at for FIFO ordering."""
    from models.agent_session import AgentSession

    new_priority = getattr(args, "priority", None) or "urgent"

    try:
        # Find by agent_session_id across all projects
        all_pending = list(AgentSession.query.filter(status="pending"))
        target = None
        for j in all_pending:
            if j.agent_session_id == args.agent_session_id:
                target = j
                break

        if not target:
            _output(
                {
                    "status": "error",
                    "message": f"Session {args.agent_session_id} not found in pending queue.",
                }
            )
            return 1

        # Use delete-and-recreate pattern for KeyField safety
        from agent.agent_session_queue import _extract_agent_session_fields

        fields = _extract_agent_session_fields(target)
        target.delete()
        fields["priority"] = new_priority
        fields["created_at"] = datetime.now(tz=UTC)
        new_session = AgentSession.create(**fields)

        _output(
            {
                "status": "bumped",
                "agent_session_id": new_session.agent_session_id,
                "session_id": new_session.session_id,
                "new_priority": new_priority,
            }
        )
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to bump session: {e}"})
        return 1


def cmd_pop(args: argparse.Namespace) -> int:
    """Remove the next pending session from queue without executing it."""
    from models.agent_session import AgentSession

    project_key = args.project or _get_env_context()["project_key"]

    try:
        from agent.agent_session_queue import PRIORITY_RANK

        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))
        if not pending:
            _output({"status": "empty", "message": "No pending sessions."})
            return 0

        # Sort same as _pop_agent_session: priority then FIFO
        pending.sort(key=lambda j: (PRIORITY_RANK.get(j.priority, 2), _to_ts(j.created_at) or 0))
        chosen = pending[0]

        info = {
            "agent_session_id": chosen.agent_session_id,
            "session_id": chosen.session_id,
            "priority": chosen.priority,
            "message_preview": (chosen.message_text or "")[:100],
        }
        chosen.delete()

        _output(
            {
                "status": "popped",
                **info,
            }
        )
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to pop session: {e}"})
        return 1


def cmd_children(args: argparse.Namespace) -> int:
    """List children of a given parent session ID with their statuses."""
    from models.agent_session import AgentSession

    try:
        parent = AgentSession.query.get(args.agent_session_id)
        if parent is None:
            _output({"status": "error", "message": f"Session {args.agent_session_id} not found."})
            return 1

        children = parent.get_children()
        completed, total, failed = parent.get_completion_progress()

        result = {
            "parent_agent_session_id": args.agent_session_id,
            "parent_status": parent.status,
            "progress": {
                "completed": completed,
                "failed": failed,
                "total": total,
            },
            "children": [],
        }

        for c in children:
            child_info = {
                "agent_session_id": c.agent_session_id,
                "session_id": c.session_id,
                "status": c.status,
                "priority": c.priority,
                "message_preview": (c.message_text or "")[:100],
            }
            if c.created_at:
                child_info["created_at"] = _to_iso(c.created_at)
            if c.issue_url:
                child_info["issue_url"] = c.issue_url
            result["children"].append(child_info)

        _output(result)
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to list children: {e}"})
        return 1


def cmd_cancel(args: argparse.Namespace) -> int:
    """Cancel a specific pending session by agent_session_id."""
    from models.agent_session import AgentSession

    try:
        all_pending = list(AgentSession.query.filter(status="pending"))
        target = None
        for j in all_pending:
            if j.agent_session_id == args.agent_session_id:
                target = j
                break

        if not target:
            _output(
                {
                    "status": "error",
                    "message": f"Session {args.agent_session_id} not found in pending queue.",
                }
            )
            return 1

        info = {
            "agent_session_id": target.agent_session_id,
            "session_id": target.session_id,
            "message_preview": (target.message_text or "")[:100],
        }
        target.delete()

        _output({"status": "cancelled", **info})
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to cancel session: {e}"})
        return 1


def _find_process_by_session_id(session_id: str) -> int | None:
    """Find a running process by matching session_id in its command-line args.

    Uses pgrep -f to find processes whose arguments contain the session_id.
    Returns the PID if found, None otherwise.
    """
    if not session_id:
        return None
    try:
        result = subprocess.run(
            ["pgrep", "-f", session_id],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        # pgrep may return multiple PIDs; take the first non-self PID
        my_pid = os.getpid()
        for line in result.stdout.strip().split("\n"):
            pid = int(line.strip())
            if pid != my_pid:
                return pid
    except Exception:
        pass
    return None


def _kill_process(pid: int) -> dict:
    """Kill a process with SIGTERM -> wait 3s -> SIGKILL sequence.

    Returns a dict with kill result details.
    """
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info(f"Sent SIGTERM to PID {pid}")
    except ProcessLookupError:
        return {"pid": pid, "action": "already_dead"}
    except PermissionError:
        return {"pid": pid, "action": "permission_denied"}

    # Wait up to 3s for graceful termination
    for _ in range(6):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)  # Check if still alive
        except ProcessLookupError:
            return {"pid": pid, "action": "terminated_sigterm"}

    # Still alive after 3s -- SIGKILL
    try:
        os.kill(pid, signal.SIGKILL)
        logger.info(f"Sent SIGKILL to PID {pid}")
        return {"pid": pid, "action": "terminated_sigkill"}
    except ProcessLookupError:
        return {"pid": pid, "action": "terminated_sigterm"}
    except PermissionError:
        return {"pid": pid, "action": "permission_denied"}


def _kill_agent_session(target, *, skip_process_kill: bool = False) -> dict:
    """Kill a single session: terminate its subprocess and set status to killed.

    Args:
        target: AgentSession instance to kill.
        skip_process_kill: If True, skip process termination (for pending sessions).

    Returns a dict with kill result details.
    """
    from agent.agent_session_queue import _extract_agent_session_fields
    from models.agent_session import AgentSession

    result = {
        "agent_session_id": target.agent_session_id,
        "session_id": target.session_id,
        "previous_status": target.status,
    }

    # Kill subprocess if running
    process_result = None
    if not skip_process_kill and target.status == "running":
        pid = _find_process_by_session_id(target.session_id)
        if pid:
            process_result = _kill_process(pid)
            result["process"] = process_result
        else:
            result["process"] = {"pid": None, "action": "no_process_found"}

    # Set status to killed using delete-and-recreate (Popoto pattern)
    fields = _extract_agent_session_fields(target)
    target.delete()
    fields["status"] = "killed"
    fields["completed_at"] = datetime.now(tz=UTC)
    new_session = AgentSession.create(**fields)
    result["new_agent_session_id"] = new_session.agent_session_id
    result["status"] = "killed"

    logger.info(
        f"Killed session {result['agent_session_id']} (session={result['session_id']}, "
        f"previous_status={result['previous_status']})"
    )

    return result


def cmd_kill(args: argparse.Namespace) -> int:
    """Kill running or pending sessions by agent_session_id, session_id, or all."""
    from models.agent_session import AgentSession

    try:
        targets = []

        if getattr(args, "all", False):
            # Kill all running + pending sessions
            for status in ("running", "pending"):
                targets.extend(list(AgentSession.query.filter(status=status)))
            if not targets:
                _output({"status": "ok", "message": "No running or pending sessions to kill."})
                return 0

        elif args.agent_session_id:
            if not args.agent_session_id.strip():
                _output({"status": "error", "message": "--agent-session-id cannot be empty."})
                return 1
            # Search across all statuses
            for status in ("running", "pending", "completed", "failed", "waiting_for_children"):
                for entry in AgentSession.query.filter(status=status):
                    if entry.agent_session_id == args.agent_session_id:
                        targets.append(entry)
                        break
                if targets:
                    break

            if not targets:
                # Retry once after 1s (race condition during session transition)
                time.sleep(1)
                for status in ("running", "pending", "completed", "failed", "waiting_for_children"):
                    for entry in AgentSession.query.filter(status=status):
                        if entry.agent_session_id == args.agent_session_id:
                            targets.append(entry)
                            break
                    if targets:
                        break

            if not targets:
                _output(
                    {"status": "error", "message": f"Session {args.agent_session_id} not found."}
                )
                return 1

        elif args.session_id:
            if not args.session_id.strip():
                _output({"status": "error", "message": "--session-id cannot be empty."})
                return 1
            for status in ("running", "pending", "completed", "failed", "waiting_for_children"):
                for entry in AgentSession.query.filter(status=status):
                    if entry.session_id == args.session_id:
                        targets.append(entry)
                        break
                if targets:
                    break

            if not targets:
                _output({"status": "error", "message": f"Session {args.session_id} not found."})
                return 1
        else:
            _output(
                {
                    "status": "error",
                    "message": "One of --agent-session-id, --session-id, or --all is required.",
                }
            )
            return 1

        # Kill all targets
        results = []
        for entry in targets:
            skip_process = entry.status != "running"
            kill_result = _kill_agent_session(entry, skip_process_kill=skip_process)
            results.append(kill_result)

        _output(
            {
                "status": "killed",
                "count": len(results),
                "sessions": results,
            }
        )
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to kill session(s): {e}"})
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """List sessions filtered by status, with optional sort and FIFO position."""
    from models.agent_session import AgentSession

    project_key = args.project or _get_env_context()["project_key"]
    statuses = [s.strip() for s in args.status.split(",")]
    sort_by = getattr(args, "sort", "fifo")

    try:
        sessions = []
        for status in statuses:
            sessions.extend(list(AgentSession.query.filter(project_key=project_key, status=status)))

        # Sort based on --sort flag
        if sort_by == "priority":
            from agent.agent_session_queue import PRIORITY_RANK

            sessions.sort(
                key=lambda s: (PRIORITY_RANK.get(s.priority, 2), _to_ts(s.created_at) or 0)
            )
        elif sort_by == "status":
            sessions.sort(key=lambda s: (s.status, _to_ts(s.created_at) or 0))
        else:
            # Default: fifo (priority-then-created_at, newest-first for display)
            sessions.sort(key=lambda s: _to_ts(s.created_at) or 0, reverse=True)

        # Apply limit
        if args.limit:
            sessions = sessions[: args.limit]

        # Build FIFO position index (rank within priority band for pending sessions)
        # Only computed when sort=priority or sort=fifo
        fifo_positions: dict[str, int] = {}
        if sort_by in ("priority", "fifo"):
            from agent.agent_session_queue import PRIORITY_RANK

            priority_counters: dict[str, int] = {}
            pending_sorted = sorted(
                [s for s in sessions if s.status == "pending"],
                key=lambda s: (PRIORITY_RANK.get(s.priority, 2), _to_ts(s.created_at) or 0),
            )
            for s in pending_sorted:
                band = s.priority or "normal"
                priority_counters[band] = priority_counters.get(band, 0) + 1
                fifo_positions[s.agent_session_id] = priority_counters[band]

        items = []
        for s in sessions:
            item = {
                "session_id": s.session_id,
                "status": s.status,
                "priority": s.priority or "normal",
                "project_key": s.project_key,
            }
            if s.agent_session_id in fifo_positions:
                item["fifo_position"] = fifo_positions[s.agent_session_id]
            if s.created_at:
                age_min = int((time.time() - _to_ts(s.created_at)) / 60)
                item["created_at"] = _to_iso(s.created_at)
                item["age_minutes"] = age_min
            if s.message_text:
                item["message_preview"] = (s.message_text or "")[:80]
            items.append(item)

        _output({"status": "ok", "count": len(items), "sessions": items})
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to list sessions: {e}"})
        return 1


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Delete stale sessions older than --age minutes in terminal statuses."""
    from models.agent_session import AgentSession

    project_key = args.project
    terminal_statuses = ["killed", "abandoned", "failed"]
    age_threshold = args.age * 60  # convert to seconds
    now = time.time()

    try:
        targets = []
        for status in terminal_statuses:
            if project_key:
                sessions = list(AgentSession.query.filter(project_key=project_key, status=status))
            else:
                sessions = list(AgentSession.query.filter(status=status))
            for s in sessions:
                age_sec = (now - _to_ts(s.created_at)) if s.created_at else 0
                if age_sec > age_threshold:
                    targets.append(s)

        if not targets:
            _output({"status": "ok", "message": "No stale sessions to clean up.", "deleted": 0})
            return 0

        if args.dry_run:
            items = []
            for s in targets:
                age_min = int((now - float(_to_ts(s.created_at) or 0)) / 60)
                items.append(
                    {
                        "session_id": s.session_id,
                        "status": s.status,
                        "project_key": s.project_key,
                        "age_minutes": age_min,
                    }
                )
            _output({"status": "dry_run", "would_delete": len(items), "sessions": items})
            return 0

        for s in targets:
            s.delete()

        _output(
            {
                "status": "ok",
                "deleted": len(targets),
                "message": f"Deleted {len(targets)} stale session(s).",
            }
        )
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to clean up sessions: {e}"})
        return 1


def main():
    parser = argparse.ArgumentParser(
        prog="agent_session_scheduler",
        description="Agent-initiated session queue operations",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # schedule
    sched = subparsers.add_parser("schedule", help="Schedule SDLC session for a GitHub issue")
    sched.add_argument("--issue", type=int, required=True, help="GitHub issue number")
    sched.add_argument("--priority", choices=["urgent", "high", "normal", "low"], default="normal")
    sched.add_argument("--project", help="Project key (default: from env or 'valor')")
    sched.add_argument("--after", help="Defer execution until this ISO 8601 datetime")
    sched.add_argument(
        "--session-type",
        choices=[SessionType.PM, SessionType.TEAMMATE, SessionType.DEV],
        help="Session type: pm (PM orchestrates), teammate "
        "(conversational), or dev (direct execution). "
        "Default: pm for issue/PR work, dev for hotfixes.",
    )
    sched.add_argument(
        "--parent-session",
        help="Parent session ID — creates this as a child session inheriting parent fields",
    )

    # children
    ch = subparsers.add_parser("children", help="List children of a parent session")
    ch.add_argument("--agent-session-id", required=True, help="Parent session ID")

    # status
    st = subparsers.add_parser("status", help="Show queue status")
    st.add_argument("--project", help="Project key")

    # push
    push = subparsers.add_parser("push", help="Push arbitrary message as a session")
    push.add_argument("--message", required=True, help="Message text for the session")
    push.add_argument("--priority", choices=["urgent", "high", "normal", "low"], default="normal")
    push.add_argument("--project", help="Project key")

    # bump
    bump = subparsers.add_parser("bump", help="Bump pending session priority and reset FIFO order")
    bump.add_argument("--agent-session-id", required=True, help="Session ID to bump")
    bump.add_argument(
        "--priority",
        choices=["urgent", "high", "normal", "low"],
        default="urgent",
        help="Priority to set (default: urgent)",
    )

    # pop
    pop = subparsers.add_parser("pop", help="Remove next pending session without executing")
    pop.add_argument("--project", help="Project key")

    # cancel
    cancel = subparsers.add_parser("cancel", help="Cancel a specific pending session")
    cancel.add_argument("--agent-session-id", required=True, help="Session ID to cancel")

    # kill
    kill = subparsers.add_parser("kill", help="Kill running or pending sessions")
    kill_group = kill.add_mutually_exclusive_group(required=True)
    kill_group.add_argument("--agent-session-id", help="Kill a specific session by ID")
    kill_group.add_argument("--session-id", help="Kill a session by session ID")
    kill_group.add_argument(
        "--all", action="store_true", help="Kill all running and pending sessions"
    )

    # list
    lst = subparsers.add_parser("list", help="List sessions filtered by status")
    lst.add_argument(
        "--status",
        required=True,
        help="Comma-separated statuses to filter (e.g. killed,abandoned,failed)",
    )
    lst.add_argument("--project", help="Project key (default: from env or 'valor')")
    lst.add_argument("--limit", type=int, help="Max number of sessions to return")
    lst.add_argument(
        "--sort",
        choices=["priority", "fifo", "status"],
        default="fifo",
        help="Sort order: priority (by tier+FIFO), fifo (creation order), status (default: fifo)",
    )

    # cleanup
    clean = subparsers.add_parser("cleanup", help="Delete stale sessions in terminal statuses")
    clean.add_argument(
        "--age",
        type=int,
        default=30,
        help="Delete sessions older than this many minutes (default: 30)",
    )
    clean.add_argument("--project", help="Scope to a specific project (default: all projects)")
    clean.add_argument(
        "--dry-run", action="store_true", help="Show what would be deleted without deleting"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "schedule": cmd_schedule,
        "status": cmd_status,
        "push": cmd_push,
        "bump": cmd_bump,
        "pop": cmd_pop,
        "cancel": cmd_cancel,
        "children": cmd_children,
        "kill": cmd_kill,
        "list": cmd_list,
        "cleanup": cmd_cleanup,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
