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

    # Session type: explicit flag > default (chat for issue-based work)
    session_type = getattr(args, "session_type", None) or "chat"

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
            session_type=session_type,
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
        killed = list(AgentSession.query.filter(project_key=project_key, status="killed"))

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
            "killed_count": len(killed),
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

        # Show killed jobs
        if killed:
            result["killed_jobs"] = [_format_job_info(j) for j in killed]

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


def _kill_job(job, *, skip_process_kill: bool = False) -> dict:
    """Kill a single job: terminate its subprocess and set status to killed.

    Args:
        job: AgentSession instance to kill.
        skip_process_kill: If True, skip process termination (for pending jobs).

    Returns a dict with kill result details.
    """
    from agent.job_queue import _extract_job_fields
    from models.agent_session import AgentSession

    result = {
        "job_id": job.job_id,
        "session_id": job.session_id,
        "previous_status": job.status,
    }

    # Kill subprocess if running
    process_result = None
    if not skip_process_kill and job.status == "running":
        pid = _find_process_by_session_id(job.session_id)
        if pid:
            process_result = _kill_process(pid)
            result["process"] = process_result
        else:
            result["process"] = {"pid": None, "action": "no_process_found"}

    # Set status to killed using delete-and-recreate (Popoto pattern)
    fields = _extract_job_fields(job)
    job.delete()
    fields["status"] = "killed"
    fields["completed_at"] = time.time()
    new_job = AgentSession.create(**fields)
    result["new_job_id"] = new_job.job_id
    result["status"] = "killed"

    logger.info(
        f"Killed job {result['job_id']} (session={result['session_id']}, "
        f"previous_status={result['previous_status']})"
    )

    return result


def cmd_kill(args: argparse.Namespace) -> int:
    """Kill running or pending jobs by job_id, session_id, or all."""
    from models.agent_session import AgentSession

    try:
        targets = []

        if getattr(args, "all", False):
            # Kill all running + pending jobs
            for status in ("running", "pending"):
                targets.extend(list(AgentSession.query.filter(status=status)))
            if not targets:
                _output({"status": "ok", "message": "No running or pending jobs to kill."})
                return 0

        elif args.job_id:
            if not args.job_id.strip():
                _output({"status": "error", "message": "--job-id cannot be empty."})
                return 1
            # Search across all statuses
            for status in ("running", "pending", "completed", "failed", "waiting_for_children"):
                for job in AgentSession.query.filter(status=status):
                    if job.job_id == args.job_id:
                        targets.append(job)
                        break
                if targets:
                    break

            if not targets:
                # Retry once after 1s (race condition during job transition)
                time.sleep(1)
                for status in ("running", "pending", "completed", "failed", "waiting_for_children"):
                    for job in AgentSession.query.filter(status=status):
                        if job.job_id == args.job_id:
                            targets.append(job)
                            break
                    if targets:
                        break

            if not targets:
                _output(
                    {"status": "error", "message": f"Job {args.job_id} not found."}
                )
                return 1

        elif args.session_id:
            if not args.session_id.strip():
                _output({"status": "error", "message": "--session-id cannot be empty."})
                return 1
            for status in ("running", "pending", "completed", "failed", "waiting_for_children"):
                for job in AgentSession.query.filter(status=status):
                    if job.session_id == args.session_id:
                        targets.append(job)
                        break
                if targets:
                    break

            if not targets:
                _output(
                    {"status": "error", "message": f"Session {args.session_id} not found."}
                )
                return 1
        else:
            _output(
                {"status": "error", "message": "One of --job-id, --session-id, or --all is required."}
            )
            return 1

        # Kill all targets
        results = []
        for job in targets:
            skip_process = job.status != "running"
            kill_result = _kill_job(job, skip_process_kill=skip_process)
            results.append(kill_result)

        _output({
            "status": "killed",
            "count": len(results),
            "jobs": results,
        })
        return 0

    except Exception as e:
        _output({"status": "error", "message": f"Failed to kill job(s): {e}"})
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
        "--session-type",
        choices=["chat", "dev"],
        help="Session type: chat (PM orchestrates) or dev (direct execution). "
        "Default: chat for issue/PR work, dev for hotfixes.",
    )
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

    # kill
    kill = subparsers.add_parser("kill", help="Kill running or pending jobs")
    kill_group = kill.add_mutually_exclusive_group(required=True)
    kill_group.add_argument("--job-id", help="Kill a specific job by job ID")
    kill_group.add_argument("--session-id", help="Kill a job by session ID")
    kill_group.add_argument("--all", action="store_true", help="Kill all running and pending jobs")

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
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
