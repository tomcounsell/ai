"""CLI tool for updating AgentSession stage progress and links.

Called by SDLC sub-skills to record stage transitions and set links
on the current AgentSession in Redis.

Usage:
    python -m tools.session_progress --session-id ID --stage BUILD --status completed
    python -m tools.session_progress --session-id ID --pr-url https://github.com/...
    python -m tools.session_progress --session-id ID --issue-url URL --plan-url URL
"""

import argparse
import sys


def _find_session(session_id: str):
    """Look up an AgentSession by session_id, VALOR_SESSION_ID env var, or task_list_id.

    Resolution order:
    1. VALOR_SESSION_ID env var (bridge session_id, most reliable)
    2. Direct session_id match (works when caller has the bridge session_id)
    3. task_list_id match (fallback for hook contexts with Claude Code UUID)
    """
    import os

    from models.agent_session import AgentSession

    try:
        # 1. Check VALOR_SESSION_ID env var first (set by SDK client for hooks)
        valor_session_id = os.environ.get("VALOR_SESSION_ID")
        if valor_session_id:
            sessions = list(AgentSession.query.filter(session_id=valor_session_id))
            if sessions:
                return sessions[0]

        # 2. Try direct session_id match
        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            return sessions[0]

        # 3. Try matching task_list_id (fallback)
        all_sessions = AgentSession.query.all()
        for s in all_sessions:
            if s.task_list_id == session_id:
                return s

        return None
    except Exception as e:
        print(f"Warning: Redis connection error: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Update AgentSession progress")
    parser.add_argument("--session-id", required=True, help="Session or task list ID")
    parser.add_argument(
        "--stage",
        choices=["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS"],
        help="SDLC stage to update",
    )
    parser.add_argument(
        "--status",
        choices=["in_progress", "completed", "failed"],
        default="completed",
        help="Stage status (default: completed)",
    )
    parser.add_argument("--issue-url", help="Set issue URL")
    parser.add_argument("--plan-url", help="Set plan URL")
    parser.add_argument("--pr-url", help="Set PR URL")
    parser.add_argument("--summary", help="Append a summary note to history")

    args = parser.parse_args()

    session = _find_session(args.session_id)
    if session is None:
        print(f"Warning: No session found for {args.session_id}", file=sys.stderr)
        sys.exit(0)  # Exit 0 so agent doesn't treat as failure

    updated = []

    if args.stage:
        status_icon = {"completed": "☑", "in_progress": "▶", "failed": "✗"}.get(
            args.status, "?"
        )
        session.append_history("stage", f"{args.stage} {status_icon}")
        updated.append(f"stage {args.stage}={args.status}")

    if args.issue_url:
        session.set_link("issue", args.issue_url)
        updated.append(f"issue_url={args.issue_url}")

    if args.plan_url:
        session.set_link("plan", args.plan_url)
        updated.append(f"plan_url={args.plan_url}")

    if args.pr_url:
        session.set_link("pr", args.pr_url)
        updated.append(f"pr_url={args.pr_url}")

    if args.summary:
        session.append_history("summary", args.summary)
        updated.append("summary added")

    if updated:
        print(f"Updated session {session.session_id}: {', '.join(updated)}")
    else:
        print("No updates specified", file=sys.stderr)


if __name__ == "__main__":
    main()
