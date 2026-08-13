"""CLI tool for releasing an SDLC run's issue lease and supervised-run signal.

The counterpart to ``sdlc-tool session-ensure``: where that tool mints a
``run_id`` and takes the per-issue lease, this one hands both back. It exists
because a ``/do-sdlc`` supervisor exiting (HALT, blocked, cap reached) never
goes through ``finalize_session``, so before this the lease sat until its TTL
or the heartbeat's max-lifetime ceiling lapsed, and the next run on the same
issue saw ``ISSUE_LOCKED`` behind a dead run (issue #2714).

Usage:
    sdlc-tool session-release --issue-number 2714 --run-id <hex>

Exit codes:
    0 -- always (best-effort; errors report a named reason and never crash the
         calling skill)

Output (typed JSON, always all four keys):
    {"released": true,  "reason": "released",     "issue_number": N, "run_id": "..."}
    {"released": false, "reason": "not_owner",    ...}  -- a live lease is held
        by a DIFFERENT run; nothing was touched
    {"released": false, "reason": "no_lease",     ...}  -- no lease to release
    {"released": false, "reason": "missing_args", ...}  -- absent/blank input;
        no Redis primitive was called at all
    {"released": false, "reason": "error",        ...}  -- the substrate raised;
        swallowed, but always with a printable reason (never an empty object)

Ownership: this module deliberately contains NO ownership logic and no key
mutation of its own. Both primitives it calls -- ``release_issue_lock`` and
``clear_supervised_run_signal`` -- are already Lua compare-and-drop operations
keyed on ``run_id``, so passing a wrong ``run_id`` is a safe no-op and a
delayed release can never free a *successor* run's lease. The only read this
module performs itself is a non-mutating ``peek``, used solely to tell
``not_owner`` apart from ``no_lease`` for the operator-facing reason string.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logger = logging.getLogger(__name__)


def release_run(issue_number: int | None, run_id: str | None) -> dict:
    """Release the issue lease and supervised-run signal owned by ``run_id``.

    A thin, ownership-checked wrapper: it calls
    :func:`models.session_lifecycle.release_issue_lock` followed by
    :func:`agent.supervised_run.clear_supervised_run_signal`, both of which
    compare against the stored ``run_id`` before dropping anything. The signal
    clear runs even when the lease release reports ``False`` (the two keys can
    legitimately expire independently), and is itself a no-op for a foreign or
    absent signal.

    Args:
        issue_number: The GitHub issue this run owns. Falsy input is refused.
        run_id: The run identity minted by ``session-ensure``. Absent, empty,
            or whitespace-only input is refused -- the primitives are never
            called with ``None``, which would be an unkeyed release attempt.

    Returns:
        A dict with exactly ``released`` (bool), ``reason`` (one of
        ``released`` / ``not_owner`` / ``no_lease`` / ``missing_args`` /
        ``error``), ``issue_number``, and ``run_id``. Never raises.
    """
    normalized_run_id = run_id.strip() if isinstance(run_id, str) else run_id
    result = {
        "released": False,
        "reason": "missing_args",
        "issue_number": issue_number,
        "run_id": normalized_run_id,
    }

    if not issue_number or not normalized_run_id:
        return result

    try:
        from agent.supervised_run import clear_supervised_run_signal
        from models.session_lifecycle import release_issue_lock, touch_issue_lock

        released = bool(release_issue_lock(issue_number, normalized_run_id))
        clear_supervised_run_signal(issue_number, normalized_run_id)

        if released:
            reason = "released"
        else:
            # Not a second ownership decision -- the drop already happened (or
            # did not) above. This peek only labels WHY for the operator: a
            # lease still standing means someone else holds it.
            peek = touch_issue_lock(issue_number, None, peek=True)
            reason = "not_owner" if getattr(peek, "owner_run_id", None) else "no_lease"

        result["released"] = released
        result["reason"] = reason
    except Exception as e:
        logger.warning(
            "[session-release] release failed for issue #%s run_id=%s (%s: %s)",
            issue_number,
            normalized_run_id,
            type(e).__name__,
            e,
        )
        result["released"] = False
        result["reason"] = "error"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Release the SDLC issue lease and supervised-run signal for a run."
    )
    parser.add_argument("--issue-number", type=int, default=None, help="GitHub issue number")
    parser.add_argument("--run-id", type=str, default=None, help="Run identity to release")
    args = parser.parse_args()

    result = release_run(args.issue_number, args.run_id)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
