"""CLI tool for ensuring a local SDLC session exists for an issue.

Creates or finds an AgentSession keyed by issue number for local Claude Code
sessions where no bridge-injected session ID is available.

Usage:
    python -m tools.sdlc_session_ensure --issue-number 941
    python -m tools.sdlc_session_ensure --issue-number 941 --issue-url https://github.com/tomcounsell/ai/issues/941
    python -m tools.sdlc_session_ensure --help

Exit codes:
    0 -- always (errors print {} and exit 0, never crash the calling skill)

Output:
    {"session_id": "<id>", "created": true}  -- new session created
    {"session_id": "<id>", "created": false} -- existing session found
    {} on error
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)


def ensure_session(issue_number: int, issue_url: str | None = None) -> dict:
    """Ensure a local AgentSession exists for the given issue number.

    If a PM session already tracks this issue, returns it.
    Otherwise, creates a new local session with session_id="sdlc-local-{issue_number}".

    Args:
        issue_number: GitHub issue number.
        issue_url: Optional full issue URL (e.g., https://github.com/owner/repo/issues/N).

    Returns:
        Dict with session_id and created flag, or empty dict on error.
    """
    if not issue_number or issue_number < 1:
        logger.debug(f"sdlc_session_ensure: invalid issue_number {issue_number}")
        return {}

    try:
        from tools._sdlc_utils import find_session_by_issue

        existing = find_session_by_issue(issue_number)
        if existing:
            session_id = getattr(existing, "session_id", None)
            if session_id:
                return {"session_id": session_id, "created": False}

        # No existing session — create one
        from models.agent_session import AgentSession

        local_session_id = f"sdlc-local-{issue_number}"

        # Check if a session with this exact ID already exists (idempotent)
        try:
            existing_by_id = list(AgentSession.query.filter(session_id=local_session_id))
            if existing_by_id:
                return {"session_id": local_session_id, "created": False}
        except Exception:
            pass

        # Build kwargs for create_local
        kwargs = {}
        if issue_url:
            kwargs["issue_url"] = issue_url

        session = AgentSession.create_local(
            session_id=local_session_id,
            project_key="ai",
            working_dir=os.getcwd(),
            session_type="pm",
            **kwargs,
        )

        # Transition from default pending to running via lifecycle module
        try:
            from models.session_lifecycle import transition_status

            transition_status(session, "running", "local SDLC session started")
        except Exception as e:
            logger.debug(f"sdlc_session_ensure: transition_status failed: {e}")
            # Session is created but in pending state — still usable

        return {"session_id": local_session_id, "created": True}

    except Exception as e:
        logger.debug(f"sdlc_session_ensure: ensure_session failed: {e}")
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure a local SDLC session exists for an issue",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        required=True,
        help="GitHub issue number",
    )
    parser.add_argument(
        "--issue-url",
        default=None,
        help="Full GitHub issue URL (optional, used for issue_url field)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    result = ensure_session(
        issue_number=args.issue_number,
        issue_url=args.issue_url,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
