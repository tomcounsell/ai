"""
Job Scheduler - Agent-initiated queue operations.

Allows the agent to programmatically schedule SDLC runs, Q&A jobs,
and manage queue state mid-conversation.

Usage:
    python -m tools.job_scheduler schedule --issue 113
    python -m tools.job_scheduler schedule --issue 113 --priority high \
        --after "2026-03-12T02:00:00Z"
    python -m tools.job_scheduler status
    python -m tools.job_scheduler push --message "What is the architecture?" --project valor
    python -m tools.job_scheduler bump --job-id <job_id>
    python -m tools.job_scheduler pop --project valor
    python -m tools.job_scheduler cancel --job-id <job_id>
    python -m tools.job_scheduler playlist --issues 440 445 397
    python -m tools.job_scheduler playlist-status
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Rate limit: max scheduled jobs per hour per project
MAX_SCHEDULED_PER_HOUR = 30
MAX_SCHEDULING_DEPTH = 3

# Default DM chat_id for headless jobs (Tom's DM)
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
    """Check if we're under the rate limit for scheduled jobs."""
    try:
        from models.agent_session import AgentSession

        cutoff = time.time() - 3600  # 1 hour ago
        recent_scheduled = 0
        for status in ("pending", "running"):
            sessions = list(AgentSession.query.filter(project_key=project_key, status=status))
            for s in sessions:
                if s.scheduling_depth and int(s.scheduling_depth) > 0:
                    created = s.created_at or 0
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


def _get_parent_session(parent_job_id: str):
    """Look up a parent AgentSession by job_id for field inheritance.

    Returns the parent session or None if not found.
    """
    try:
        from models.agent_session import AgentSession

        return AgentSession.query.get(parent_job_id)
    except Exception:
        return None


# --- Persona gate ---

# Persona restrictions: which personas can perform which actions
# teammate cannot schedule SDLC jobs; all other actions are unrestricted
PERSONA_RESTRICTED_ACTIONS = {
    "teammate": {"schedule", "playlist"},
}


def _check_persona_permission(action_type: str) -> dict | None:
    """Check if the current persona is allowed to perform the given action.

    Reads persona from PERSONA env var (default: "developer" — permissive).

    Args:
        action_type: The action being attempted (e.g., "schedule", "playlist").

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


# --- Playlist operations (Redis list) ---

PLAYLIST_KEY_PREFIX = "playlist:"
PLAYLIST_RETRIES_KEY_PREFIX = "playlist_retries:"


def _get_redis():
    """Get the popoto Redis connection."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _playlist_key(project_key: str) -> str:
    """Redis key for a project's playlist."""
    return f"{PLAYLIST_KEY_PREFIX}{project_key}"


def _retries_key(project_key: str) -> str:
    """Redis key for tracking playlist retry counts."""
    return f"{PLAYLIST_RETRIES_KEY_PREFIX}{project_key}"


def playlist_push(project_key: str, issue_numbers: list[int]) -> int:
    """Append issue numbers to the end of the playlist.

    Args:
        project_key: The project key for scoping.
        issue_numbers: List of issue numbers to append.

    Returns:
        The new length of the playlist.
    """
    r = _get_redis()
    key = _playlist_key(project_key)
    for num in issue_numbers:
        r.rpush(key, str(num))
    return r.llen(key)


def playlist_pop(project_key: str) -> int | None:
    """Pop the next issue number from the front of the playlist.

    Args:
        project_key: The project key for scoping.

    Returns:
        The issue number, or None if the playlist is empty.
    """
    r = _get_redis()
    key = _playlist_key(project_key)
    value = r.lpop(key)
    if value is None:
        return None
    return int(value)


def playlist_status(project_key: str) -> list[int]:
    """Get all issue numbers in the playlist (in order).

    Args:
        project_key: The project key for scoping.

    Returns:
        List of issue numbers in playlist order.
    """
    r = _get_redis()
    key = _playlist_key(project_key)
    items = r.lrange(key, 0, -1)
    return [int(item) for item in items]


def playlist_requeue(project_key: str, issue_number: int) -> bool:
    """Requeue a failed issue to the end of the playlist (max 1 retry).

    Args:
        project_key: The project key for scoping.
        issue_number: The issue number to requeue.

    Returns:
        True if requeued, False if max retries exceeded.
    """
    r = _get_redis()
    retries_key = _retries_key(project_key)

    # Check retry count
    current_retries = r.hget(retries_key, str(issue_number))
    if current_retries is not None and int(current_retries) >= 1:
        return False

    # Increment retry count and requeue
    r.hincrby(retries_key, str(issue_number), 1)
    r.rpush(_playlist_key(project_key), str(issue_number))
    return True


def playlist_clear(project_key: str) -> None:
    """Clear the playlist and retry counts for a project."""
    r = _get_redis()
    r.delete(_playlist_key(project_key))
    r.delete(_retries_key(project_key))


def cmd_playlist(args: argparse.Namespace) -> int:
    """Enqueue multiple issues for sequential SDLC processing."""
    # Persona gate
    perm = _check_persona_permission("playlist")
    if perm:
        _output(perm)
        return 1

    ctx = _get_env_context()
    project_key = args.project or ctx["project_key"]

    if not args.issues:
        _output({"status": "error", "message": "No issues provided. Use --issues 440 445 397"})
        return 1

    # Validate all issue numbers
    valid_issues = []
    skipped_issues = []
    for issue_num in args.issues:
        if issue_num <= 0:
            skipped_issues.append({"issue": issue_num, "reason": "invalid issue number"})
            continue

        issue = _validate_issue(issue_num)
        if issue is None:
            skipped_issues.append({"issue": issue_num, "reason": "not found or not accessible"})
            continue
        if issue.get("state") == "closed":
            skipped_issues.append({"issue": issue_num, "reason": "issue is closed"})
            continue

        valid_issues.append({"number": issue_num, "title": issue.get("title", f"#{issue_num}")})

    if not valid_issues:
        _output(
            {
                "status": "error",
                "message": "No valid issues to enqueue.",
                "skipped": skipped_issues,
            }
        )
        return 1

    # Add valid issues to playlist
    issue_numbers = [v["number"] for v in valid_issues]
    new_length = playlist_push(project_key, issue_numbers)

    # Schedule the first issue immediately (if nothing is currently running)
    first_issue = issue_numbers[0]

    # Build a synthetic args namespace for cmd_schedule
    schedule_args = argparse.Namespace(
        issue=first_issue,
        priority=args.priority or "normal",
        project=project_key,
        after=None,
        parent_job=None,
    )

    # Pop the first issue from playlist since we're scheduling it now
    playlist_pop(project_key)

    # Schedule the first issue
    schedule_result = cmd_schedule(schedule_args)

    result = {
        "status": "playlist_created",
        "project": project_key,
        "enqueued": valid_issues,
        "playlist_remaining": playlist_status(project_key),
        "playlist_length": new_length - 1,  # minus the one we just scheduled
        "first_scheduled": first_issue,
        "schedule_exit_code": schedule_result,
    }

    if skipped_issues:
        result["skipped"] = skipped_issues

    _output(result)
    return 0


def cmd_playlist_status(args: argparse.Namespace) -> int:
    """Show the current playlist status for a project."""
    ctx = _get_env_context()
    project_key = args.project or ctx["project_key"]

    issues = playlist_status(project_key)

    r = _get_redis()
    retries_key = _retries_key(project_key)
    retries = {}
    if r.exists(retries_key):
        raw = r.hgetall(retries_key)
        retries = {k.decode() if isinstance(k, bytes) else k: int(v) for k, v in raw.items()}

    _output(
        {
            "status": "ok",
            "project": project_key,
            "playlist": issues,
            "playlist_length": len(issues),
            "retry_counts": retries,
        }
    )
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    """Schedule an SDLC job for a GitHub issue."""
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
                "Cannot schedule further jobs from a self-scheduled job.",
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
                    "scheduled jobs per hour per project."
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

    # Parse scheduled_after
    scheduled_after = None
    if args.after:
        try:
            dt = datetime.fromisoformat(args.after.replace("Z", "+00:00"))
            scheduled_after = dt.timestamp()
            if scheduled_after < time.time():
                scheduled_after = None  # Past = immediate
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

    # Parent job inheritance
    parent_job_id = getattr(args, "parent_job", None)
    parent_session = None
    if parent_job_id:
        if not parent_job_id.strip():
            _output({"status": "error", "message": "--parent-job cannot be empty."})
            return 1
        parent_session = _get_parent_session(parent_job_id)
        if parent_session is None:
            _output(
                {
                    "status": "error",
                    "message": f"Parent job {parent_job_id} not found.",
                }
            )
            return 1

    try:
        # Get working dir from project config
        working_dir = DEFAULT_WORKING_DIR
        try:
            from agent.job_queue import get_project_config

            config = get_project_config(project_key)
            if config:
                working_dir = config.get("working_directory", working_dir)
        except Exception:
            pass

        # Inherit fields from parent if this is a child job
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
            message_id=int(ctx["message_id"]) if ctx["message_id"] else 0,
            classification_type=inherited_classification_type,
            scheduled_after=scheduled_after,
            scheduling_depth=depth + 1,
            issue_url=issue_url,
            correlation_id=inherited_correlation_id,
            parent_job_id=parent_job_id,
        )

        # Transition parent to waiting_for_children if not already
        if parent_session and parent_session.status != "waiting_for_children":
            try:
                from agent.job_queue import _transition_parent

                _transition_parent(parent_session, "waiting_for_children")
                logger.info(f"Parent {parent_job_id} transitioned to waiting_for_children")
            except Exception as e:
                logger.warning(
                    f"Failed to transition parent {parent_job_id} to waiting_for_children: {e}"
                )

        # Count queue position
        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))
        queue_position = len(pending)

        scheduled_iso = None
        if scheduled_after:
            scheduled_iso = datetime.fromtimestamp(scheduled_after, tz=UTC).isoformat()

        result = {
            "status": "queued",
            "job_id": session.job_id,
            "session_id": session_id,
            "issue": args.issue,
            "issue_title": issue_title,
            "priority": priority,
            "queue_position": queue_position,
            "scheduling_depth": depth + 1,
            "scheduled_after": scheduled_iso,
        }
        if parent_job_id:
            result["parent_job_id"] = parent_job_id

        _output(result)
        return 0

    except Exception as e:
        _output(
            {
                "status": "error",
                "message": f"Failed to enqueue job: {e}",
            }
        )
        return 1


def _format_job_info(j, include_children: bool = False) -> dict:
    """Format a single job's info for status output."""
    job_info = {
        "job_id": j.job_id,
        "session_id": j.session_id,
        "status": j.status,
        "priority": j.priority,
        "message_preview": (j.message_text or "")[:100],
    }
    if j.created_at:
        job_info["created_at"] = datetime.fromtimestamp(j.created_at, tz=UTC).isoformat()
    if j.started_at:
        job_info["started_at"] = datetime.fromtimestamp(j.started_at, tz=UTC).isoformat()
    if j.scheduled_after:
        job_info["scheduled_after"] = datetime.fromtimestamp(j.scheduled_after, tz=UTC).isoformat()
    if j.issue_url:
        job_info["issue_url"] = j.issue_url
    if j.parent_job_id:
        job_info["parent_job_id"] = j.parent_job_id

    if include_children and j.status == "waiting_for_children":
        completed, total, failed = j.get_completion_progress()
        job_info["children_progress"] = {
            "completed": completed,
            "failed": failed,
            "total": total,
        }
        children = j.get_children()
        job_info["children"] = [
            {
                "job_id": c.job_id,
                "session_id": c.session_id,
                "status": c.status,
                "message_preview": (c.message_text or "")[:80],
            }
            for c in children
        ]

    return job_info


def cmd_status(args: argparse.Namespace) -> int:
    """Show queue status with job tree display."""
    from models.agent_session import AgentSession

    project_key = args.project or _get_env_context()["project_key"]

    try:
        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))
        running = list(AgentSession.query.filter(project_key=project_key, status="running"))
        completed = list(AgentSession.query.filter(project_key=project_key, status="completed"))
        waiting = list(
            AgentSession.query.filter(project_key=project_key, status="waiting_for_children")
        )

        # Sort pending by priority then FIFO
        from agent.job_queue import PRIORITY_RANK

        pending.sort(key=lambda j: (PRIORITY_RANK.get(j.priority, 2), j.created_at or 0))

        # Separate root jobs from child jobs for tree display
        root_pending = [j for j in pending if not j.parent_job_id]
        child_pending = [j for j in pending if j.parent_job_id]

        result = {
            "project": project_key,
            "pending_count": len(pending),
            "running_count": len(running),
            "waiting_for_children_count": len(waiting),
            "recent_completed_count": len(completed),
            "pending_jobs": [_format_job_info(j) for j in root_pending],
            "running_jobs": [_format_job_info(j) for j in running],
        }

        # Show waiting-for-children jobs with their child trees
        if waiting:
            result["waiting_jobs"] = [_format_job_info(j, include_children=True) for j in waiting]

        # Show child jobs separately if any are pending
        if child_pending:
            result["child_pending_jobs"] = [_format_job_info(j) for j in child_pending]

        _output(result)
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to get status: {e}"})
        return 1


def cmd_push(args: argparse.Namespace) -> int:
    """Push an arbitrary message as a job (not issue-bound)."""
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
        from agent.job_queue import get_project_config

        config = get_project_config(project_key)
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
            message_id=int(ctx["message_id"]) if ctx["message_id"] else 0,
            scheduling_depth=depth + 1,
            correlation_id=f"push-{uuid.uuid4().hex[:12]}",
        )

        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))

        _output(
            {
                "status": "queued",
                "job_id": session.job_id,
                "session_id": session_id,
                "priority": priority,
                "queue_position": len(pending),
                "scheduling_depth": depth + 1,
            }
        )
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to push job: {e}"})
        return 1


def cmd_bump(args: argparse.Namespace) -> int:
    """Bump a pending job to top of queue (set priority=urgent, reset created_at)."""
    from models.agent_session import AgentSession

    try:
        # Find by job_id across all projects
        all_pending = list(AgentSession.query.filter(status="pending"))
        target = None
        for j in all_pending:
            if j.job_id == args.job_id:
                target = j
                break

        if not target:
            _output(
                {"status": "error", "message": f"Job {args.job_id} not found in pending queue."}
            )
            return 1

        # Use delete-and-recreate pattern for KeyField safety
        from agent.job_queue import _extract_job_fields

        fields = _extract_job_fields(target)
        target.delete()
        fields["priority"] = "urgent"
        fields["created_at"] = time.time()
        new_job = AgentSession.create(**fields)

        _output(
            {
                "status": "bumped",
                "job_id": new_job.job_id,
                "session_id": new_job.session_id,
                "new_priority": "urgent",
            }
        )
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to bump job: {e}"})
        return 1


def cmd_pop(args: argparse.Namespace) -> int:
    """Remove the next pending job from queue without executing it."""
    from models.agent_session import AgentSession

    project_key = args.project or _get_env_context()["project_key"]

    try:
        from agent.job_queue import PRIORITY_RANK

        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))
        if not pending:
            _output({"status": "empty", "message": "No pending jobs."})
            return 0

        # Sort same as _pop_job: priority then FIFO
        pending.sort(key=lambda j: (PRIORITY_RANK.get(j.priority, 2), j.created_at or 0))
        chosen = pending[0]

        info = {
            "job_id": chosen.job_id,
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
        _output({"status": "error", "message": f"Failed to pop job: {e}"})
        return 1


def cmd_children(args: argparse.Namespace) -> int:
    """List children of a given parent job ID with their statuses."""
    from models.agent_session import AgentSession

    try:
        parent = AgentSession.query.get(args.job_id)
        if parent is None:
            _output({"status": "error", "message": f"Job {args.job_id} not found."})
            return 1

        children = parent.get_children()
        completed, total, failed = parent.get_completion_progress()

        result = {
            "parent_job_id": args.job_id,
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
                "job_id": c.job_id,
                "session_id": c.session_id,
                "status": c.status,
                "priority": c.priority,
                "message_preview": (c.message_text or "")[:100],
            }
            if c.created_at:
                child_info["created_at"] = datetime.fromtimestamp(c.created_at, tz=UTC).isoformat()
            if c.issue_url:
                child_info["issue_url"] = c.issue_url
            result["children"].append(child_info)

        _output(result)
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to list children: {e}"})
        return 1


def cmd_cancel(args: argparse.Namespace) -> int:
    """Cancel a specific pending job by job_id."""
    from models.agent_session import AgentSession

    try:
        all_pending = list(AgentSession.query.filter(status="pending"))
        target = None
        for j in all_pending:
            if j.job_id == args.job_id:
                target = j
                break

        if not target:
            _output(
                {"status": "error", "message": f"Job {args.job_id} not found in pending queue."}
            )
            return 1

        info = {
            "job_id": target.job_id,
            "session_id": target.session_id,
            "message_preview": (target.message_text or "")[:100],
        }
        target.delete()

        _output({"status": "cancelled", **info})
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to cancel job: {e}"})
        return 1


def main():
    parser = argparse.ArgumentParser(
        prog="job_scheduler",
        description="Agent-initiated job queue operations",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # schedule
    sched = subparsers.add_parser("schedule", help="Schedule SDLC job for a GitHub issue")
    sched.add_argument("--issue", type=int, required=True, help="GitHub issue number")
    sched.add_argument("--priority", choices=["urgent", "high", "normal", "low"], default="normal")
    sched.add_argument("--project", help="Project key (default: from env or 'valor')")
    sched.add_argument("--after", help="Defer execution until this ISO 8601 datetime")
    sched.add_argument(
        "--parent-job",
        help="Parent job ID — creates this as a child job inheriting parent fields",
    )

    # children
    ch = subparsers.add_parser("children", help="List children of a parent job")
    ch.add_argument("--job-id", required=True, help="Parent job ID")

    # status
    st = subparsers.add_parser("status", help="Show queue status")
    st.add_argument("--project", help="Project key")

    # push
    push = subparsers.add_parser("push", help="Push arbitrary message as a job")
    push.add_argument("--message", required=True, help="Message text for the job")
    push.add_argument("--priority", choices=["urgent", "high", "normal", "low"], default="normal")
    push.add_argument("--project", help="Project key")

    # bump
    bump = subparsers.add_parser("bump", help="Bump pending job to top of queue")
    bump.add_argument("--job-id", required=True, help="Job ID to bump")

    # pop
    pop = subparsers.add_parser("pop", help="Remove next pending job without executing")
    pop.add_argument("--project", help="Project key")

    # cancel
    cancel = subparsers.add_parser("cancel", help="Cancel a specific pending job")
    cancel.add_argument("--job-id", required=True, help="Job ID to cancel")

    # playlist
    pl = subparsers.add_parser(
        "playlist", help="Enqueue multiple issues for sequential SDLC processing"
    )
    pl.add_argument(
        "--issues", type=int, nargs="+", required=True, help="Issue numbers to enqueue"
    )
    pl.add_argument(
        "--priority", choices=["urgent", "high", "normal", "low"], default="normal"
    )
    pl.add_argument("--project", help="Project key")

    # playlist-status
    pls = subparsers.add_parser("playlist-status", help="Show current playlist status")
    pls.add_argument("--project", help="Project key")

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
        "playlist": cmd_playlist,
        "playlist-status": cmd_playlist_status,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
