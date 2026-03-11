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
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Rate limit: max scheduled jobs per hour per project
MAX_SCHEDULED_PER_HOUR = 30
MAX_SCHEDULING_DEPTH = 3

# Default DM chat_id for headless jobs (Tom's DM)
DEFAULT_DM_CHAT_ID = "179144806"
DEFAULT_PROJECT_KEY = "valor"
DEFAULT_WORKING_DIR = "/Users/valorengels/src/ai"


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
        all_sessions = list(AgentSession.query.filter(project_key=project_key))
        recent_scheduled = 0
        for s in all_sessions:
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


def cmd_schedule(args: argparse.Namespace) -> int:
    """Schedule an SDLC job for a GitHub issue."""
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

        session = AgentSession.create(
            project_key=project_key,
            status="pending",
            priority=priority,
            created_at=time.time(),
            session_id=session_id,
            working_dir=working_dir,
            message_text=message_text,
            sender_name="System (Scheduled)",
            chat_id=ctx["chat_id"],
            message_id=int(ctx["message_id"]) if ctx["message_id"] else 0,
            classification_type="sdlc",
            scheduled_after=scheduled_after,
            scheduling_depth=depth + 1,
            issue_url=issue_url,
            correlation_id=f"sched-{uuid.uuid4().hex[:12]}",
        )

        # Count queue position
        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))
        queue_position = len(pending)

        scheduled_info = ""
        if scheduled_after:
            dt = datetime.fromtimestamp(scheduled_after, tz=UTC)
            scheduled_info = f", scheduled_after={dt.isoformat()}"

        _output(
            {
                "status": "queued",
                "job_id": session.job_id,
                "session_id": session_id,
                "issue": args.issue,
                "issue_title": issue_title,
                "priority": priority,
                "queue_position": queue_position,
                "scheduling_depth": depth + 1,
                "scheduled_after": scheduled_info or None,
            }
        )
        return 0

    except Exception as e:
        _output(
            {
                "status": "error",
                "message": f"Failed to enqueue job: {e}",
            }
        )
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show queue status."""
    from models.agent_session import AgentSession

    project_key = args.project or _get_env_context()["project_key"]

    try:
        pending = list(AgentSession.query.filter(project_key=project_key, status="pending"))
        running = list(AgentSession.query.filter(project_key=project_key, status="running"))
        completed = list(AgentSession.query.filter(project_key=project_key, status="completed"))

        # Sort pending by priority then FIFO
        from agent.job_queue import PRIORITY_RANK

        pending.sort(key=lambda j: (PRIORITY_RANK.get(j.priority, 2), j.created_at or 0))

        result = {
            "project": project_key,
            "pending_count": len(pending),
            "running_count": len(running),
            "recent_completed_count": len(completed),
            "pending_jobs": [],
            "running_jobs": [],
        }

        for j in pending:
            job_info = {
                "job_id": j.job_id,
                "session_id": j.session_id,
                "priority": j.priority,
                "message_preview": (j.message_text or "")[:100],
                "created_at": datetime.fromtimestamp(j.created_at, tz=UTC).isoformat()
                if j.created_at
                else None,
            }
            if j.scheduled_after:
                job_info["scheduled_after"] = datetime.fromtimestamp(
                    j.scheduled_after, tz=UTC
                ).isoformat()
            if j.issue_url:
                job_info["issue_url"] = j.issue_url
            result["pending_jobs"].append(job_info)

        for j in running:
            job_info = {
                "job_id": j.job_id,
                "session_id": j.session_id,
                "priority": j.priority,
                "message_preview": (j.message_text or "")[:100],
                "started_at": datetime.fromtimestamp(j.started_at, tz=UTC).isoformat()
                if j.started_at
                else None,
            }
            if j.issue_url:
                job_info["issue_url"] = j.issue_url
            result["running_jobs"].append(job_info)

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
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
