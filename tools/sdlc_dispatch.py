"""CLI entry point for recording SDLC dispatch events.

This module wraps ``agent.sdlc_router.record_dispatch`` with session resolution
and the safe concurrent write protocol from
``tools.stage_states_helpers.update_stage_states``.

Usage::

    python -m tools.sdlc_dispatch record --skill /do-build --issue-number 1040
    python -m tools.sdlc_dispatch record --skill /do-pr-review --issue-number 1040 --pr-number 42
    python -m tools.sdlc_dispatch get --issue-number 1040

The ``record`` subcommand is called by the SDLC LLM session **after** the
router evaluates guards and selects a dispatch target but **before** invoking
the sub-skill. This ordering preserves the G4 oscillation signal even if the
sub-skill crashes mid-execution.

The ``get`` subcommand prints the current ``_sdlc_dispatches`` list as JSON.
It is useful for debugging G4 state in a live session.

Graceful failure: the module never crashes its caller. All errors are logged
at DEBUG level. The ``record`` subcommand exits with code 0 even if session
resolution or the write fails — a lost dispatch record is observable via
``python -m tools.sdlc_dispatch get`` but is not fatal to the pipeline.

Integration with ``tools.stage_states_helpers.update_stage_states``:
  The write is wrapped in the optimistic-retry helper so that concurrent
  writes by the verdict recorder or ``PipelineStateMachine._save()`` do not
  clobber this module's update, and vice versa.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _find_session(session_id: str | None = None, issue_number: int | None = None):
    """Resolve the PM session. Mirrors sdlc_verdict._find_session logic."""
    resolved_id = (
        session_id or os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")
    )
    if resolved_id:
        try:
            from models.agent_session import AgentSession

            sessions = list(AgentSession.query.filter(session_id=resolved_id))
            if sessions:
                for s in sessions:
                    if getattr(s, "session_type", None) == "pm":
                        return s
                return sessions[0]
        except Exception as e:
            logger.debug(f"sdlc_dispatch: _find_session by id failed: {e}")

    if issue_number is not None:
        try:
            from tools._sdlc_utils import find_session_by_issue

            return find_session_by_issue(issue_number)
        except Exception as e:
            logger.debug(f"sdlc_dispatch: find_session_by_issue failed: {e}")

    return None


def record_dispatch_for_session(
    session,
    skill: str,
    pr_number: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Record a dispatch event on a session's stage_states.

    Wraps ``agent.sdlc_router.record_dispatch`` with the optimistic-retry
    safe write helper from ``tools.stage_states_helpers``.

    Args:
        session: AgentSession to write to.
        skill: The sub-skill being dispatched (e.g. ``"/do-build"``).
        pr_number: Optional PR number — passed into the snapshot so G4
            can include PR state in its equality check.
        now: Optional timestamp override for testability.

    Returns:
        ``True`` if the write succeeded, ``False`` otherwise.
    """
    if session is None:
        logger.debug("sdlc_dispatch: session is None — skipping record")
        return False

    try:
        from agent.sdlc_router import record_dispatch
        from tools.stage_states_helpers import update_stage_states
    except Exception as e:
        logger.debug(f"sdlc_dispatch: import failed: {e}")
        return False

    ts = now or datetime.now(UTC)

    def _apply(states: dict) -> dict:
        return record_dispatch(states, skill=skill, now=ts, pr_number=pr_number)

    try:
        ok = update_stage_states(session, _apply)
    except Exception as e:
        logger.debug(f"sdlc_dispatch: update_stage_states failed: {e}")
        return False

    if not ok:
        logger.debug(f"sdlc_dispatch: write not confirmed for skill={skill!r}")
    return ok


def get_dispatch_history(session) -> list:
    """Read the ``_sdlc_dispatches`` list from a session's stage_states.

    Returns an empty list on any error.
    """
    if session is None:
        return []

    try:
        raw = getattr(session, "stage_states", None)
        if not raw:
            return []
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return []
        history = data.get("_sdlc_dispatches") or []
        return list(history) if isinstance(history, list) else []
    except Exception as e:
        logger.debug(f"sdlc_dispatch: get_dispatch_history failed: {e}")
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_record(args) -> dict:
    session = _find_session(session_id=args.session_id, issue_number=args.issue_number)
    if session is None:
        logger.debug("sdlc_dispatch record: no session resolved — no-op")
        return {}

    ok = record_dispatch_for_session(
        session,
        skill=args.skill,
        pr_number=args.pr_number,
    )
    history = get_dispatch_history(session)
    return {"ok": ok, "history_length": len(history)}


def _cli_get(args) -> list:
    session = _find_session(session_id=args.session_id, issue_number=args.issue_number)
    if session is None:
        return []
    return get_dispatch_history(session)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record or retrieve SDLC dispatch history",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    rec = subparsers.add_parser(
        "record",
        help=(
            "Record a dispatch event. Call AFTER guard evaluation but "
            "BEFORE invoking the sub-skill."
        ),
    )
    rec.add_argument("--skill", required=True, help="Sub-skill being dispatched (e.g. /do-build)")
    rec.add_argument("--pr-number", dest="pr_number", type=int, default=None)
    rec.add_argument("--session-id", dest="session_id", default=None)
    rec.add_argument("--issue-number", dest="issue_number", type=int, default=None)
    rec.set_defaults(func=_cli_record)

    gt = subparsers.add_parser("get", help="Print the dispatch history as JSON")
    gt.add_argument("--session-id", dest="session_id", default=None)
    gt.add_argument("--issue-number", dest="issue_number", type=int, default=None)
    gt.set_defaults(func=_cli_get)

    args = parser.parse_args()

    try:
        result = args.func(args)
    except Exception as e:
        logger.debug(f"sdlc_dispatch: CLI {args.command} failed: {e}")
        result = {}

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
