"""CLI tool for writing SDLC stage markers to a PM session's PipelineStateMachine.

Invoked by SDLC skills (do-issue, do-plan, do-plan-critique, do-pr-review, do-docs)
to record stage start/completion without depending on the bridge hooks.

Skills use this as a belt-and-suspenders backup — the bridge hooks
(pre_tool_use/subagent_stop) remain the primary marker path for bridge-initiated
sessions. This tool handles local Claude Code sessions where hooks don't fire.

Usage:
    python -m tools.sdlc_stage_marker --stage DOCS --status in_progress
    python -m tools.sdlc_stage_marker --stage DOCS --status completed
    python -m tools.sdlc_stage_marker --stage REVIEW --status in_progress --session-id <ID>
    python -m tools.sdlc_stage_marker --help

Environment variables (checked in order if --session-id not provided):
    VALOR_SESSION_ID   — bridge-injected PM session ID
    AGENT_SESSION_ID   — alternative session ID env var

Exit codes:
    0 — always (errors print {} and exit 0, never crash the calling skill)

Output:
    {} on error (no session found, invalid stage, Redis down, etc.)
    {"stage": "DOCS", "status": "completed"} on success
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Valid stages for marker writes (all pipeline stages including PATCH)
_VALID_STAGES = frozenset(
    ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]
)

# Status values accepted by this tool (maps to state machine calls)
_VALID_STATUSES = frozenset(["in_progress", "completed"])


def _find_session(session_id: str | None):
    """Find a PM AgentSession by explicit ID or env vars.

    Resolution order:
    1. --session-id argument (if provided)
    2. VALOR_SESSION_ID env var
    3. AGENT_SESSION_ID env var

    Returns the session object or None.
    """
    resolved_id = (
        session_id or os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")
    )
    if not resolved_id:
        logger.debug("sdlc_stage_marker: no session ID available (no arg, no env vars)")
        return None

    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=resolved_id))
        if not sessions:
            logger.debug(f"sdlc_stage_marker: no session found for ID {resolved_id!r}")
            return None
        # Prefer PM sessions (they own stage_states)
        for s in sessions:
            if getattr(s, "session_type", None) == "pm":
                return s
        return sessions[0]
    except Exception as e:
        logger.debug(f"sdlc_stage_marker: _find_session failed: {e}")
        return None


def write_marker(stage: str, status: str, session_id: str | None = None) -> dict:
    """Write a stage marker to the PipelineStateMachine.

    Args:
        stage: Pipeline stage name (e.g., "DOCS", "REVIEW").
        status: "in_progress" or "completed".
        session_id: Optional explicit session ID (falls back to env vars).

    Returns:
        Dict with stage/status on success, empty dict on any failure.
    """
    if stage not in _VALID_STAGES:
        logger.debug(f"sdlc_stage_marker: invalid stage {stage!r}")
        return {}

    if status not in _VALID_STATUSES:
        logger.debug(f"sdlc_stage_marker: invalid status {status!r}")
        return {}

    session = _find_session(session_id)
    if not session:
        return {}

    try:
        from bridge.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(session)

        if status == "in_progress":
            try:
                sm.start_stage(stage)
            except ValueError as e:
                # Predecessor not completed — log and continue silently
                # Skills should not crash when pipeline state is inconsistent
                logger.debug(f"sdlc_stage_marker: start_stage({stage}) rejected: {e}")
                return {}
        elif status == "completed":
            # Ensure stage is in_progress before completing
            current = sm.states.get(stage, "pending")
            if current == "completed":
                # Already completed — idempotent no-op
                return {"stage": stage, "status": status}
            if current not in ("in_progress", "ready"):
                # Force to in_progress first so complete_stage() accepts it
                sm.states[stage] = "in_progress"
            sm.complete_stage(stage)

        return {"stage": stage, "status": status}

    except Exception as e:
        logger.debug(f"sdlc_stage_marker: write_marker failed: {e}")
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write SDLC stage markers to PipelineStateMachine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--stage",
        required=True,
        help="Pipeline stage name (e.g., DOCS, REVIEW, PLAN)",
    )
    parser.add_argument(
        "--status",
        required=True,
        choices=["in_progress", "completed"],
        help="Status to write",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="PM session ID (falls back to VALOR_SESSION_ID / AGENT_SESSION_ID env vars)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    result = write_marker(
        stage=args.stage.upper(),
        status=args.status,
        session_id=args.session_id,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
