"""CLI entry point for recording SDLC dispatch events against the
issue-keyed PipelineLedger.

This module wraps ``agent.sdlc_router.record_dispatch`` with the safe
concurrent write protocol from ``tools.stage_states_helpers.update_stage_states``.
Issue #2012 task 2 re-points ``record`` at ``PipelineLedger`` instead of an
``AgentSession`` -- there is no session left to resolve; the run_id-keyed
issue lease is the sole authorization for a write.

Usage::

    python -m tools.sdlc_dispatch record --skill /do-build --issue-number 1040 --run-id <hex>
    python -m tools.sdlc_dispatch record --skill /do-pr-review --issue-number 1040 \
        --pr-number 42 --run-id <hex>
    python -m tools.sdlc_dispatch get --issue-number 1040

Run identity (issue #2003): ``record`` is state-mutating and therefore
REQUIRES ``--run-id`` (the run identity emitted by ``sdlc-tool
session-ensure``). A missing flag is a named non-zero error
(``RUN_ID_REQUIRED``) — no mint, no adopt. A missing/foreign/repo-less lease
yields ``{"ok": False, "history_length": 0, "reason": ...}`` (issue #2012:
this is now LOUD -- there is no session to fail to resolve to anymore).
``get``/``reset`` are read paths and take no run-id; they read the ledger
first with a retained session fallback for pre-cutover records.

The ``record`` subcommand is called by the SDLC LLM session **after** the
router evaluates guards and selects a dispatch target but **before** invoking
the sub-skill. This ordering preserves the G4 oscillation signal even if the
sub-skill crashes mid-execution.

The ``get`` subcommand prints the current ``_sdlc_dispatches`` list as JSON.
It is useful for debugging G4 state in a live session.

Integration with ``tools.stage_states_helpers.update_stage_states``:
  The write is wrapped in the optimistic-retry helper so that concurrent
  writes by the verdict recorder or ``PipelineStateMachine._save()`` do not
  clobber this module's update, and vice versa.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime

from tools._sdlc_run_identity import heal_missing_run_id, maybe_heal_after_write
from tools._sdlc_utils import (
    find_session as _find_session,
)
from tools._sdlc_utils import (
    is_pipeline_ledger,
    resolve_ledger_lease,
    revalidate_ledger_lease,
)

logger = logging.getLogger(__name__)


def record_dispatch_for_ledger(
    ledger,
    skill: str,
    pr_number: int | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> bool:
    """Record a dispatch event on a ``PipelineLedger``'s stage states.

    Wraps ``agent.sdlc_router.record_dispatch`` with the optimistic-retry
    safe write helper from ``tools.stage_states_helpers``. The caller
    (``_cli_record``) MUST have already resolved and re-validated the issue
    lease before calling this -- this function performs no lock check
    itself, since ownership is decided once, atomically, right before the
    write (see ``tools._sdlc_utils.revalidate_ledger_lease``).

    Args:
        ledger: The ``PipelineLedger`` to write to.
        skill: The sub-skill being dispatched (e.g. ``"/do-build"``).
        pr_number: Optional PR number — passed into the snapshot so G4
            can include PR state in its equality check.
        now: Optional timestamp override for testability.
        run_id: The caller's run identity, annotated onto the dispatch
            record for observability.

    Returns:
        ``True`` if the write succeeded, ``False`` otherwise.
    """
    if ledger is None:
        logger.debug("sdlc_dispatch: ledger is None — skipping record")
        return False

    try:
        from agent.sdlc_router import record_dispatch
        from tools.stage_states_helpers import update_stage_states
    except Exception as e:
        logger.debug(f"sdlc_dispatch: import failed: {e}")
        return False

    ts = now or datetime.now(UTC)

    def _apply(states: dict) -> dict:
        states = record_dispatch(states, skill=skill, now=ts, pr_number=pr_number)
        # Dispatch records carry the run identity (issue #2003) — annotated
        # here so ``agent.sdlc_router.record_dispatch`` stays run-id-agnostic.
        try:
            history = states.get("_sdlc_dispatches") or []
            if history and isinstance(history[-1], dict):
                history[-1]["run_id"] = run_id
        except Exception:  # pragma: no cover - annotation must never block the write
            pass
        return states

    try:
        ok = update_stage_states(ledger, _apply, field="stage_states_json")
    except Exception as e:
        logger.debug(f"sdlc_dispatch: update_stage_states failed: {e}")
        return False

    if not ok:
        logger.debug(f"sdlc_dispatch: write not confirmed for skill={skill!r}")
    return ok


def get_dispatch_history(record) -> list:
    """Read the ``_sdlc_dispatches`` list from a record's stage states.

    ``record`` may be an ``AgentSession`` (field ``stage_states``) or a
    ``PipelineLedger`` (field ``stage_states_json`` -- issue #2012 task 2).

    Returns an empty list on any error.
    """
    if record is None:
        return []

    try:
        field = "stage_states_json" if is_pipeline_ledger(record) else "stage_states"
        raw = getattr(record, field, None)
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


def reset_dispatch_history(record) -> bool:
    """Clear ``_sdlc_dispatches`` on a record via the safe write helper.

    ``record`` may be an ``AgentSession`` or a ``PipelineLedger`` (issue
    #2012 task 2) -- detected the same way as :func:`get_dispatch_history`.
    The explicit operator escape hatch for G4 (D5): when the oscillation
    guard has latched on a genuinely stale recorded history that the
    self-clearing live-snapshot reset cannot reach, this wipes the streak.

    Returns ``True`` if the write succeeded, ``False`` otherwise.
    """
    if record is None:
        return False

    try:
        from tools.stage_states_helpers import update_stage_states
    except Exception as e:
        logger.debug(f"sdlc_dispatch: reset import failed: {e}")
        return False

    def _apply(states: dict) -> dict:
        states["_sdlc_dispatches"] = []
        return states

    field = "stage_states_json" if is_pipeline_ledger(record) else "stage_states"
    try:
        return update_stage_states(record, _apply, field=field)
    except Exception as e:
        logger.debug(f"sdlc_dispatch: reset update_stage_states failed: {e}")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_record(args) -> dict:
    """Record a dispatch event against the issue-keyed PipelineLedger.

    This is a WRITER (issue #2012 task 2): there is no session to resolve.
    The run_id-keyed issue lease for ``args.issue_number`` is the sole
    authorization. A missing/foreign/repo-less lease surfaces as
    ``{"ok": False, "history_length": 0, "reason": ...}`` -- LOUD, matching
    the shape SKILL.md already documents for ``ISSUE_LOCKED``.
    """
    target_repo, lease_error = resolve_ledger_lease(args.issue_number, args.run_id)
    if lease_error is not None:
        logger.debug(
            "sdlc_dispatch: lease invalid for issue #%s (reason=%s) -- refusing dispatch record",
            args.issue_number,
            lease_error.get("reason"),
        )
        return {"ok": False, "history_length": 0, **lease_error}
    if not target_repo:
        logger.debug(
            "sdlc_dispatch: issue #%s lease has no pinned target_repo -- refusing dispatch record",
            args.issue_number,
        )
        return {"ok": False, "history_length": 0, "reason": "TARGET_REPO_MISSING"}

    # TOCTOU close (Risk 5): re-validate the lease non-peek immediately
    # before the actual write.
    if not revalidate_ledger_lease(args.issue_number, args.run_id, target_repo):
        logger.debug(
            "sdlc_dispatch: lease for issue #%s was taken by a foreign run between "
            "resolve and write -- refusing dispatch record",
            args.issue_number,
        )
        return {"ok": False, "history_length": 0, "reason": "ISSUE_LOCKED"}

    from agent.pipeline_ledger import PipelineLedger

    ledger = PipelineLedger.get_or_create(target_repo, args.issue_number)
    ok = record_dispatch_for_ledger(
        ledger,
        skill=args.skill,
        pr_number=args.pr_number,
        run_id=args.run_id,
    )
    history = get_dispatch_history(ledger)
    return {"ok": ok, "history_length": len(history)}


def _cli_get(args) -> list:
    """Read the dispatch history — issue-keyed ledger first, with a
    retained session fallback for pre-cutover records (issue #2012 task 2).

    When ``--issue-number`` is given, delegates the resolution to
    ``tools.sdlc_stage_query._resolve_issue_record`` -- the SOLE place that
    performs the ledger-first/env-fallback/session-fallback dance (Risk 5,
    reader side), rather than duplicating it here. That function returns
    ``None`` when ``target_repo`` cannot be resolved at all -- the defined
    empty outcome ``[]``, never a phantom ``PipelineLedger[(None, issue)]``
    read.

    Without ``--issue-number``, this stays the plain session lookup
    (``--session-id`` / env-var resolution).
    """
    if args.issue_number is not None:
        from tools.sdlc_stage_query import _resolve_issue_record

        record = _resolve_issue_record(args.issue_number)
        if record is None:
            return []
        return get_dispatch_history(record)

    session = _find_session(session_id=args.session_id, issue_number=args.issue_number)
    if session is None:
        return []
    return get_dispatch_history(session)


def _cli_reset(args) -> dict:
    """Clear the dispatch history — same ledger-first/session-fallback
    resolution as :func:`_cli_get` (issue #2012 task 2)."""
    if args.issue_number is not None:
        from tools.sdlc_stage_query import _resolve_issue_record

        record = _resolve_issue_record(args.issue_number)
        if record is None:
            logger.debug("sdlc_dispatch reset: no target_repo resolved — no-op")
            return {"ok": False, "history_length": 0}
        ok = reset_dispatch_history(record)
        history = get_dispatch_history(record)
        return {"ok": ok, "history_length": len(history)}

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
    rec.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help=(
            "Run identity emitted by `sdlc-tool session-ensure` (issue #2003). "
            "REQUIRED for this state-mutating subcommand; missing -> RUN_ID_REQUIRED."
        ),
    )
    rec.set_defaults(func=_cli_record, requires_run_id=True)

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

    # Run-identity self-heal (issue #2144): a resumed pipeline turn loses the
    # run_id from context. Re-establish identity from the environment instead of
    # silently refusing; only a genuinely unhealable state (foreign live lease,
    # no issue-number) keeps the RUN_ID_REQUIRED refusal.
    requires_run_id = getattr(args, "requires_run_id", False)
    healed_at_gate = False
    if requires_run_id and not getattr(args, "run_id", None):
        healed = heal_missing_run_id(getattr(args, "issue_number", None), "dispatch")
        if not healed:
            print(
                "sdlc_dispatch: RUN_ID_REQUIRED — state-mutating calls must pass "
                "--run-id (emitted by `sdlc-tool session-ensure`).",
                file=sys.stderr,
            )
            print(json.dumps({"error": "RUN_ID_REQUIRED"}))
            sys.exit(2)
        args.run_id = healed
        healed_at_gate = True

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

    # A stale run_id whose lease lapsed refuses with LEASE_ABSENT; heal once and
    # retry the record under the re-established id (at-most-once).
    if (
        requires_run_id
        and not failed
        and not healed_at_gate
        and isinstance(result, dict)
        and result.get("reason") in ("LEASE_ABSENT", "ISSUE_LOCKED")
    ):
        healed = maybe_heal_after_write(
            result, getattr(args, "run_id", None), getattr(args, "issue_number", None), "dispatch"
        )
        if healed:
            args.run_id = healed
            try:
                result = args.func(args)
            except Exception as e:
                logger.debug(f"sdlc_dispatch: CLI {args.command} failed after heal: {e}")
                print(f"sdlc_dispatch: CLI {args.command} failed: {e}", file=sys.stderr)
                result = {}
                failed = True

    print(json.dumps(result))
    if (
        not failed
        and isinstance(result, dict)
        and result.get("reason")
        in (
            "ISSUE_LOCKED",
            "LEASE_ABSENT",
            "TARGET_REPO_MISSING",
        )
    ):
        # Issue #2012 task 2: a lease problem is LOUD -- there is no session
        # to fail to resolve to anymore, so silence is never correct.
        print(
            f"sdlc_dispatch: {result.get('reason')} — dispatch record refused "
            f"(owner_run_id={result.get('owner_run_id')}, "
            f"owner_session_id={result.get('owner_session_id')}).",
            file=sys.stderr,
        )
        failed = True

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
