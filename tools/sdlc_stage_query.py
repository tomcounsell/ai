"""CLI tool for querying SDLC stage_states from a PM session.

Invoked by the SDLC router skill (SKILL.md) to read the current pipeline
state from Redis. Returns JSON mapping stage names to their statuses.

Usage:
    python -m tools.sdlc_stage_query --session-id <SESSION_ID>
    python -m tools.sdlc_stage_query --issue-number <ISSUE_NUMBER>
    python -m tools.sdlc_stage_query --help

Exit codes:
    0 — always (errors return empty JSON {})

Output:
    JSON dict, e.g.: {"ISSUE": "completed", "PLAN": "completed", ...}
    Empty dict {} when no session found or stage_states unavailable.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logger = logging.getLogger(__name__)


def _find_session_by_id(session_id: str):
    """Find an AgentSession by session_id.

    Returns the session object or None.
    """
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return None
        # Prefer PM sessions (they own stage_states)
        for s in sessions:
            if getattr(s, "session_type", None) == "pm":
                return s
        return sessions[0]
    except Exception as e:
        logger.debug(f"_find_session_by_id failed: {e}")
        return None


def _find_session_by_issue(issue_number: int):
    """Find a PM session tracking the given issue number.

    Scans recent PM sessions for an issue_url containing the issue number.
    Returns the session object or None.
    """
    try:
        from models.agent_session import AgentSession

        # NOTE: Linear scan of PM sessions — acceptable for current scale (typically
        # <100 PM sessions). If PM session count grows significantly, consider adding
        # an indexed lookup by issue_url or caching issue->session mappings.
        pm_sessions = list(AgentSession.query.filter(session_type="pm"))
        target_suffix = f"/issues/{issue_number}"
        for s in pm_sessions:
            issue_url = getattr(s, "issue_url", None) or ""
            if issue_url.endswith(target_suffix):
                return s
        return None
    except Exception as e:
        logger.debug(f"_find_session_by_issue failed: {e}")
        return None


def _get_stage_states(session) -> dict[str, str]:
    """Extract stage_states from a session, returning a dict.

    Returns an empty dict if stage_states is unavailable or malformed.
    """
    try:
        raw = session.stage_states
        if not raw:
            return {}
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return {}

        if not isinstance(data, dict):
            return {}

        # Filter to known stages only, exclude internal metadata keys
        from bridge.pipeline_state import ALL_STAGES

        return {k: v for k, v in data.items() if k in ALL_STAGES}
    except Exception as e:
        logger.debug(f"_get_stage_states failed: {e}")
        return {}


def query_stage_states(
    session_id: str | None = None,
    issue_number: int | None = None,
) -> dict[str, str]:
    """Query stage_states for a session.

    Args:
        session_id: Session ID to look up directly.
        issue_number: Issue number to find the PM session for.

    Returns:
        Dict mapping stage names to status strings, or empty dict.
    """
    session = None

    if session_id:
        session = _find_session_by_id(session_id)

    if session is None and issue_number is not None:
        session = _find_session_by_issue(issue_number)

    if session is None:
        return {}

    return _get_stage_states(session)


def main():
    parser = argparse.ArgumentParser(
        description="Query SDLC stage_states from a PM session",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m tools.sdlc_stage_query --session-id tg_project_123_456
  python -m tools.sdlc_stage_query --issue-number 704

Output:
  {"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress", ...}
  {} (when no session found or stage_states unavailable)
""",
    )
    parser.add_argument(
        "--session-id",
        help="Session ID to look up (e.g., VALOR_SESSION_ID)",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        help="GitHub issue number to find the PM session for",
    )

    args = parser.parse_args()

    if not args.session_id and args.issue_number is None:
        # Check environment variables as fallback
        import os

        session_id = os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")
        if session_id:
            args.session_id = session_id
        else:
            # No args and no env vars — return empty JSON gracefully
            print("{}")
            sys.exit(0)

    try:
        result = query_stage_states(
            session_id=args.session_id,
            issue_number=args.issue_number,
        )
        print(json.dumps(result))
    except Exception:
        # Never crash — always return empty JSON
        print("{}")

    sys.exit(0)


if __name__ == "__main__":
    main()
