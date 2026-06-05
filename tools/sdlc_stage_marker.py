"""CLI tool for writing SDLC stage markers to a PM session's PipelineStateMachine.

Invoked by SDLC skills (do-issue, do-plan, do-plan-critique, do-pr-review, do-docs)
to record stage start/completion without depending on the bridge hooks.

Skills use this as a belt-and-suspenders backup — the bridge pre_tool_use hook
remains the primary marker path for bridge-initiated sessions. This tool handles
local Claude Code sessions where hooks don't fire.

Usage:
    python -m tools.sdlc_stage_marker --stage DOCS --status in_progress
    python -m tools.sdlc_stage_marker --stage DOCS --status completed
    python -m tools.sdlc_stage_marker --stage REVIEW --status in_progress --session-id <ID>
    python -m tools.sdlc_stage_marker --stage PLAN --status completed --issue-number 941
    python -m tools.sdlc_stage_marker --help

Environment variables (checked in order if --session-id not provided):
    VALOR_SESSION_ID   — bridge-injected PM session ID
    AGENT_SESSION_ID   — alternative session ID env var

When no session ID is available (local Claude Code sessions), use --issue-number
to resolve the session by GitHub issue number.

Degradation contract (D7 — loud failure, quiet absence):
    A tri-state probe replaces the old binary present/absent check so a missing
    orchestration substrate degrades to a *visible* marker instead of silently
    lagging, while a session-less local invocation stays quiet:

    - ABSENT — cannot import models.agent_session / Redis unreachable
      (ImportError / redis.ConnectionError): emit a degraded marker
      ({"status": "degraded", ...}) and exit 0. This is the non-`ai`-repo case.
    - PRESENT_NO_SESSION — substrate imports and Redis is reachable, but no PM
      session resolves: emit a degraded marker and exit 0 (QUIET). The marker
      cannot tell a legitimate non-`ai` repo apart from an `ai` wiring bug, so
      it must not be noisy.
    - PRESENT_WRITE_FAILED — session resolved but start_stage/complete_stage
      rejects or raises: print a clear stderr diagnostic and exit NON-ZERO.
      Loud is reserved ONLY for this case. The idempotent already-completed
      path stays exit 0.

Exit codes:
    0 — success, degraded (substrate absent / no session), or idempotent no-op
    1 — substrate present, session resolved, but the marker write genuinely
        failed (the only loud case)

Output:
    {"status": "degraded", "stage": ..., "reason": ...} when the substrate is
        absent or no session resolves (exit 0)
    {"stage": "DOCS", "status": "completed"} on success (exit 0)
    {} + stderr diagnostic on genuine write failure (exit 1)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from tools._sdlc_utils import find_session

logger = logging.getLogger(__name__)

# Valid stages for marker writes (all pipeline stages including PATCH)
_VALID_STAGES = frozenset(
    ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]
)

# Status values accepted by this tool (maps to state machine calls)
_VALID_STATUSES = frozenset(["in_progress", "completed"])

# Tri-state substrate probe outcomes (D7).
SUBSTRATE_ABSENT = "ABSENT"
SUBSTRATE_PRESENT = "PRESENT"


def probe_substrate() -> str:
    """Probe whether the orchestration substrate (models + Redis) is reachable.

    Returns ``SUBSTRATE_PRESENT`` when ``models.agent_session`` imports AND a
    trivial Redis-backed query succeeds; ``SUBSTRATE_ABSENT`` on ImportError or
    any connection error. Never raises.

    This distinguishes the genuinely-absent substrate (a non-`ai` repo, where a
    degraded marker is correct) from a present substrate that merely has no
    matching PM session (handled separately by the caller).
    """
    try:
        from models.agent_session import AgentSession
    except Exception as e:
        logger.debug(f"sdlc_stage_marker: substrate import failed: {e}")
        return SUBSTRATE_ABSENT

    try:
        # A cheap reachability check that forces a Redis round-trip. ``count``
        # is evaluated eagerly (unlike the lazy ``filter`` QueryBuilder), so a
        # Redis connection error surfaces here rather than masquerading as
        # "no session".
        AgentSession.query.count(session_type="pm")
    except Exception as e:
        logger.debug(f"sdlc_stage_marker: substrate query failed (Redis unreachable?): {e}")
        return SUBSTRATE_ABSENT

    return SUBSTRATE_PRESENT


def _degraded(stage: str, reason: str) -> dict:
    """Build a visible degraded-mode marker payload (D7)."""
    return {"status": "degraded", "stage": stage, "reason": reason}


def write_marker(
    stage: str, status: str, session_id: str | None = None, issue_number: int | None = None
) -> tuple[dict, int]:
    """Write a stage marker to the PipelineStateMachine.

    Args:
        stage: Pipeline stage name (e.g., "DOCS", "REVIEW").
        status: "in_progress" or "completed".
        session_id: Optional explicit session ID (falls back to env vars).
        issue_number: Optional issue number for local session resolution.

    Returns:
        A ``(result, exit_code)`` tuple (D7 tri-state contract):
        - success / degraded / idempotent no-op → exit_code 0
        - genuine write failure (substrate present, session resolved) →
          exit_code 1 (the only loud case)
    """
    if stage not in _VALID_STAGES:
        logger.debug(f"sdlc_stage_marker: invalid stage {stage!r}")
        return {}, 0

    if status not in _VALID_STATUSES:
        logger.debug(f"sdlc_stage_marker: invalid status {status!r}")
        return {}, 0

    # Tri-state probe. ABSENT → degraded marker, exit 0 (the non-`ai`-repo case).
    if probe_substrate() == SUBSTRATE_ABSENT:
        return _degraded(stage, "state not persisted — substrate absent"), 0

    # Substrate is present. A missing session is QUIET (a session-less local
    # invocation, or a non-`ai` repo with no PM session) — degraded, exit 0.
    # Writes opt into auto-ensure so a sessionless local invocation with issue
    # context still gets a PM session to persist into (#1558).
    session = find_session(session_id, issue_number=issue_number, ensure=True)
    if not session:
        return _degraded(stage, "state not persisted — no PM session resolved"), 0

    # PRESENT_WRITE_FAILED is the ONLY loud case: the session resolved but the
    # state-machine write rejects or raises.
    try:
        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(session)

        if status == "in_progress":
            try:
                sm.start_stage(stage)
            except ValueError as e:
                # Predecessor not completed — inconsistent pipeline state, not a
                # substrate failure. Loud so the operator notices the misorder.
                logger.debug(f"sdlc_stage_marker: start_stage({stage}) rejected: {e}")
                return {}, 1
        elif status == "completed":
            # Ensure stage is in_progress before completing
            current = sm.states.get(stage, "pending")
            if current == "completed":
                # Already completed — idempotent no-op (exit 0)
                return {"stage": stage, "status": status}, 0
            if current not in ("in_progress", "ready"):
                # Force to in_progress first so complete_stage() accepts it
                sm.states[stage] = "in_progress"
            sm.complete_stage(stage)

        return {"stage": stage, "status": status}, 0

    except Exception as e:
        logger.debug(f"sdlc_stage_marker: write_marker failed: {e}")
        return {}, 1


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
        "--issue-number",
        type=int,
        default=None,
        help="GitHub issue number (for local sessions without VALOR_SESSION_ID)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    stage = args.stage.upper()
    result, exit_code = write_marker(
        stage=stage,
        status=args.status,
        session_id=args.session_id,
        issue_number=args.issue_number,
    )
    print(json.dumps(result))

    if exit_code != 0:
        # PRESENT_WRITE_FAILED — the only loud case. A clear stderr diagnostic
        # so a forked sub-skill / operator sees the genuine writeback failure
        # instead of a silent no-op (mirrors sdlc_dispatch's loud-failure path).
        print(
            f"sdlc_stage_marker: FAILED to write {stage}={args.status} "
            "(substrate present, session resolved, but the state-machine write "
            "was rejected or raised). State NOT persisted.",
            file=sys.stderr,
        )
    elif result.get("status") == "degraded":
        # Visible degraded-mode marker (quiet on stderr, but the stdout JSON
        # carries status: degraded so the PM/operator can see it).
        logger.debug(f"sdlc_stage_marker: degraded — {result.get('reason')}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
