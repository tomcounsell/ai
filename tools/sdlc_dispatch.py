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
import re
import sys
from datetime import UTC, datetime

from tools._sdlc_utils import find_session as _find_session

logger = logging.getLogger(__name__)


def _parse_issue_number_from_url(issue_url: str | None) -> int | None:
    """Extract the GitHub issue number from an ``issue_url``.

    Mirrors the ``/issues/{N}`` suffix convention used throughout
    ``tools/_sdlc_utils.py::find_session_by_issue`` (its ``target_suffix``
    logic checks ``issue_url.endswith(f"/issues/{issue_number}")``) — this is
    the reverse direction: extracting the number FROM the url. Returns
    ``None`` if ``issue_url`` is falsy or does not contain an ``issues/N``
    segment. Never raises.
    """
    if not issue_url:
        return None
    match = re.search(r"issues/(\d+)", issue_url)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
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

    Issue-lock enforcement (issue #1954): before writing, this calls
    ``touch_issue_lock()`` DIRECTLY -- it must NOT assume ``ensure_session()``
    ran first, since ``tools._sdlc_utils.find_session(ensure=True)``'s Step-2
    short-circuit (matching an existing session via ``find_session_by_issue``)
    skips ``ensure_session()`` entirely for continuing sessions. The issue
    number is derived by parsing ``session.issue_url`` (see
    ``_parse_issue_number_from_url``), NOT from a mirrored ``issue_number``
    field, since a continuing session created before this feature shipped may
    not have one. If the lock is held by a different live session, the write
    is refused and this returns ``False``.

    Args:
        session: AgentSession to write to.
        skill: The sub-skill being dispatched (e.g. ``"/do-build"``).
        pr_number: Optional PR number — passed into the snapshot so G4
            can include PR state in its equality check.
        now: Optional timestamp override for testability.

    Returns:
        ``True`` if the write succeeded, ``False`` otherwise (including when
        the issue lock is held by a different session).
    """
    if session is None:
        logger.debug("sdlc_dispatch: session is None — skipping record")
        return False

    issue_number = _parse_issue_number_from_url(getattr(session, "issue_url", None))
    if issue_number:
        try:
            from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS, touch_issue_lock

            session_id = getattr(session, "session_id", None) or ""
            lock_result = touch_issue_lock(issue_number, session_id, ttl=ISSUE_LOCK_TTL_SECONDS)
            if not lock_result.acquired:
                logger.debug(
                    "sdlc_dispatch: issue #%s lock held by a different session (%s) -- "
                    "refusing to record dispatch",
                    issue_number,
                    lock_result.owner_session_id,
                )
                return False
        except Exception as e:
            logger.debug(f"sdlc_dispatch: touch_issue_lock failed (non-fatal): {e}")

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


def _peek_issue_lock_conflict(session) -> dict | None:
    """Read-only check for whether a dispatch write failure was issue-lock
    contention (issue #1954 gap: the CLI ``ISSUE_LOCKED`` shape).

    ``record_dispatch_for_session()`` intentionally stays a plain ``bool`` --
    other call sites and tests already depend on that return type -- so a
    ``False`` result is ambiguous: lock contention and unrelated write
    failures (e.g. a Redis write conflict in ``update_stage_states``) both
    collapse to the same ``False``. This helper disambiguates AFTER the fact
    with a non-mutating ``peek=True`` lock check, mirroring the
    ``session.issue_url`` -> ``_parse_issue_number_from_url`` derivation
    ``record_dispatch_for_session()`` performs internally. It never acquires,
    renews, or otherwise mutates the lock.

    Returns:
        ``{"reason": "ISSUE_LOCKED", "owner_session_id": "..."}`` if the lock
        is currently held by a different live session. ``None`` if the lock
        is free/owned by this session (failure was unrelated to the lock),
        the session has no parseable issue number, or the peek itself
        errors.
    """
    issue_number = _parse_issue_number_from_url(getattr(session, "issue_url", None))
    if not issue_number:
        return None

    try:
        from models.session_lifecycle import touch_issue_lock

        session_id = getattr(session, "session_id", None) or ""
        lock_result = touch_issue_lock(issue_number, session_id, peek=True)
    except Exception as e:
        logger.debug(f"sdlc_dispatch: issue-lock peek failed (non-fatal): {e}")
        return None

    if lock_result.acquired:
        return None

    return {"reason": "ISSUE_LOCKED", "owner_session_id": lock_result.owner_session_id}


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
    # ensure=True (B1, #1671): the dispatch record path joins the other three
    # write subcommands so a cold-start `dispatch record --issue-number N`
    # creates/uses the issue-scoped sdlc-local-N session rather than env-
    # resolving to an inherited session or silently no-opping. The dispatch
    # trail then has the same issue-scoped home the router reads. `get`/`reset`
    # stay non-ensuring — `get` is read-only and `reset` must not fabricate a
    # session.
    session = _find_session(session_id=args.session_id, issue_number=args.issue_number, ensure=True)
    if session is None:
        logger.debug("sdlc_dispatch record: no session resolved — no-op")
        return {}

    ok = record_dispatch_for_session(
        session,
        skill=args.skill,
        pr_number=args.pr_number,
    )
    history = get_dispatch_history(session)
    result = {"ok": ok, "history_length": len(history)}

    # #1954 gap: record_dispatch_for_session() returning False is ambiguous
    # (issue-lock contention vs. any other write failure). On failure, peek
    # the lock (read-only) to see whether it was specifically lock
    # contention, and if so surface the documented ISSUE_LOCKED shape
    # (SKILL.md: "dispatch record/ensure_session surface the same shape at
    # their own call sites"). Non-lock failures keep the pre-existing
    # {"ok": False, "history_length": N} shape unchanged -- additive only.
    if not ok:
        lock_conflict = _peek_issue_lock_conflict(session)
        if lock_conflict is not None:
            result.update(lock_conflict)

    return result


def _cli_get(args) -> list:
    session = _find_session(session_id=args.session_id, issue_number=args.issue_number)
    if session is None:
        return []
    return get_dispatch_history(session)


def reset_dispatch_history(session) -> bool:
    """Clear ``_sdlc_dispatches`` on a session via the safe write helper.

    The explicit operator escape hatch for G4 (D5): when the oscillation
    guard has latched on a genuinely stale recorded history that the
    self-clearing live-snapshot reset cannot reach, this wipes the streak.

    Returns ``True`` if the write succeeded, ``False`` otherwise.
    """
    if session is None:
        return False

    try:
        from tools.stage_states_helpers import update_stage_states
    except Exception as e:
        logger.debug(f"sdlc_dispatch: reset import failed: {e}")
        return False

    def _apply(states: dict) -> dict:
        states["_sdlc_dispatches"] = []
        return states

    try:
        return update_stage_states(session, _apply)
    except Exception as e:
        logger.debug(f"sdlc_dispatch: reset update_stage_states failed: {e}")
        return False


def _cli_reset(args) -> dict:
    session = _find_session(session_id=args.session_id, issue_number=args.issue_number)
    if session is None:
        logger.debug("sdlc_dispatch reset: no session resolved — no-op")
        return {"ok": False, "history_length": 0}
    ok = reset_dispatch_history(session)
    history = get_dispatch_history(session)
    return {"ok": ok, "history_length": len(history)}


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

    rs = subparsers.add_parser(
        "reset",
        help=(
            "Clear the dispatch history (operator escape hatch for a latched G4 oscillation guard)."
        ),
    )
    rs.add_argument("--session-id", dest="session_id", default=None)
    rs.add_argument("--issue-number", dest="issue_number", type=int, default=None)
    rs.set_defaults(func=_cli_reset)

    args = parser.parse_args()

    failed = False
    try:
        result = args.func(args)
    except Exception as e:
        # Load-bearing tool: failures must be loud so Guard G4 can rely on them.
        # Stdout still emits `[]` / `{}` so existing callers parsing JSON don't
        # break; the non-zero exit is the loud signal.
        logger.debug(f"sdlc_dispatch: CLI {args.command} failed: {e}")
        print(f"sdlc_dispatch: CLI {args.command} failed: {e}", file=sys.stderr)
        result = {}
        failed = True

    print(json.dumps(result))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
