"""Detached lease-heartbeat renewer for the pure-local ``/do-sdlc`` supervisor.

Issue #2446/#2451: a worker-run pipeline keeps its per-issue lease
(``session:issuelock:{N}``) alive via the worker's in-process 60s tick
(``agent/session_executor.py::_tick_issue_lock_renewal``). The pure-local
``/do-sdlc`` supervisor -- a per-turn ``claude -p`` subprocess that is BLOCKED
inside a synchronous stage call and makes no mid-stage sdlc-tool writes -- has
no equivalent renewer, so its lease can lapse mid-run and force a re-mint. This
module is that missing renewer, launched detached by
``tools/sdlc_session_ensure._acquire_run_lock_and_bind`` on a fresh local mint.

**Peek-first, renew-only (BLOCKER 1 -- the load-bearing safety property).**
``touch_issue_lock`` has NO renew-only mode: passing a ``run_id`` against an
*absent* key does ``SET NX EX`` and makes the caller the owner
(``models/session_lifecycle.py`` ~lines 1100-1123). A naive renew loop would
therefore let a zombie heartbeat *re-acquire* a lapsed lease under its stale id
and block a legitimate successor with ``ISSUE_LOCKED`` -- the exact lease-theft
Risk 2 forbids. So every tick PEEKS first (``touch_issue_lock(issue, None,
peek=True)``) and:

- ``peek.owner_run_id is None`` (lease absent/lapsed) -> EXIT 0 immediately.
- ``peek.owner_run_id != run_id`` (a successor owns it) -> EXIT 0 immediately.
- ``peek.orphaned_lock`` (the payload's recorded owner pid is verifiably
  dead, issue #2537) -> EXIT 0 without renewing; the lease lapses at TTL.
- ``peek.owner_run_id == run_id`` and not orphaned -> ONLY THEN call the
  mutating ``touch_issue_lock(issue, run_id, ...)`` to extend the TTL.

This mirrors ``touch_issue_lock``'s own "no run_id supplied: never mutates"
special case at the caller layer: the heartbeat can only ever EXTEND a lease it
already owns, never mint on a free key nor steal one from a successor.

Best-effort throughout: every tick is wrapped in try/except; a Redis hiccup is
swallowed and retried next tick. The loop self-terminates after
``--max-lifetime`` regardless, so a crashed supervisor's heartbeat can never
outlive a bounded window.

Usage::

    python -m tools.sdlc_lease_heartbeat --issue-number 2446 --run-id <hex> \
        --session-id sdlc-local-2446

Exit codes:
    0 -- always (lost ownership, max-lifetime reached, or clean shutdown).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

# Upper bound on how long a single heartbeat process runs before self-exiting
# (issue #2446/#2451). A crashed supervisor's heartbeat must not outlive a
# bounded window even though the peek-first guard already prevents lease-theft.
# GRAIN OF SALT: 4h (14400s) is PROVISIONAL/TUNABLE -- generously above the
# longest observed end-to-end local run, low enough to bound a zombie. Override
# via the SDLC_LEASE_HEARTBEAT_MAX_LIFETIME_SECONDS env var.
MAX_LIFETIME_SECONDS = int(
    os.environ.get("SDLC_LEASE_HEARTBEAT_MAX_LIFETIME_SECONDS", str(4 * 60 * 60))
)


def _default_interval() -> int:
    """Renew cadence: a third of the lease TTL, so ~3 renews per TTL window.

    Read lazily (not at import) so a test/env override of ISSUE_LOCK_TTL_SECONDS
    is honored. Floored at 1s to guarantee forward progress.
    """
    from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS

    return max(1, ISSUE_LOCK_TTL_SECONDS // 3)


def run_heartbeat(
    issue_number: int,
    run_id: str,
    session_id: str = "",
    interval: int | None = None,
    max_lifetime: int = MAX_LIFETIME_SECONDS,
    _sleep=time.sleep,
    _monotonic=time.monotonic,
) -> int:
    """Peek-first renew-only heartbeat loop. Returns an exit code (always 0).

    Args:
        issue_number: The issue whose lease this heartbeat renews.
        run_id: The run identity that must own the lease for a renew to fire.
        session_id: Stored in the lock payload for display only.
        interval: Seconds between ticks (default: ``ISSUE_LOCK_TTL_SECONDS//3``).
        max_lifetime: Hard upper bound on total run time before self-exit.
        _sleep / _monotonic: Injectable clocks for deterministic tests.
    """
    if not issue_number or not run_id:
        # Nothing to renew without both identifiers -- exit cleanly, never mint.
        return 0

    if interval is None:
        interval = _default_interval()
    interval = max(1, interval)

    from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS, touch_issue_lock

    deadline = _monotonic() + max_lifetime
    while _monotonic() < deadline:
        try:
            # PEEK FIRST (run_id=None so the peek itself can never mutate/mint).
            peek = touch_issue_lock(issue_number, None, session_id=session_id, peek=True)
            owner = peek.owner_run_id
            if owner is None or owner != run_id:
                # Lease absent/lapsed, or a successor owns it -> stop. Never
                # re-acquire (that would be lease-theft, Risk 2).
                logger.debug(
                    "sdlc_lease_heartbeat: issue #%s lease no longer owned by run_id=%s "
                    "(current owner=%s) -- exiting",
                    issue_number,
                    run_id,
                    owner,
                )
                return 0
            if peek.orphaned_lock:
                # Issue #2537 (AC 3/7): the peek says the payload's recorded
                # owner pid is verifiably dead (dead pid, or pid recycled to
                # an unrelated process). Renewing would keep a corpse's lease
                # alive forever -- stop renewing and let it lapse at TTL. We
                # still never delete or re-acquire (no lease-theft, Risk 2).
                logger.debug(
                    "sdlc_lease_heartbeat: issue #%s lease owner is verifiably dead "
                    "(orphaned_lock) -- exiting without renewing",
                    issue_number,
                )
                return 0
            # Self-owned: extend the TTL under our own identity only.
            touch_issue_lock(
                issue_number,
                run_id,
                session_id=session_id,
                ttl=ISSUE_LOCK_TTL_SECONDS,
            )
        except Exception as e:  # noqa: BLE001 - best-effort; never crash the loop
            logger.debug(
                "sdlc_lease_heartbeat: tick failed for issue #%s (%s: %s) -- retrying",
                issue_number,
                type(e).__name__,
                e,
            )
        _sleep(interval)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detached peek-first renew-only lease heartbeat for local /do-sdlc",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--run-id", dest="run_id", required=True)
    parser.add_argument("--session-id", dest="session_id", default="")
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Seconds between renews (default: ISSUE_LOCK_TTL_SECONDS//3)",
    )
    parser.add_argument(
        "--max-lifetime",
        dest="max_lifetime",
        type=int,
        default=MAX_LIFETIME_SECONDS,
        help="Hard upper bound on total run time before self-exit",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    exit_code = run_heartbeat(
        issue_number=args.issue_number,
        run_id=args.run_id,
        session_id=args.session_id or "",
        interval=args.interval,
        max_lifetime=args.max_lifetime,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
