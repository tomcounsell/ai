"""Drain probe for the update flow's worker restart (issue #2141).

The ~30-min update cron restarts the worker after a worker-relevant code
change. Before #2141 the restart fired unconditionally, killing PM turns
that legitimately run 20+ minutes and orphaning their `claude -p` harness.
This module is the bounded busy-check `scripts/remote-update.sh` consults
before restarting:

    .venv/bin/python -m scripts.update.drain --timeout 300 --poll 10

Exit codes (consumed by the shell):
    0  — idle (no running sessions) → safe to restart NOW
    3  — still busy after the whole window → the shell DEFERS the restart
         to the next update cycle (worker keeps serving on the old code)

Fail-open contract: any import/Redis/ORM error exits 0 with a stderr
warning. A broken probe must degrade to today's behavior (restart), not
wedge fleet updates forever — the warning line lands in update.log so the
degradation is visible. Session counts go through the AgentSession ORM
(never raw Redis — repo convention).

Env knobs (documented in docs/features/config-timeout-catalog.md):
    UPDATE_WORKER_DRAIN_TIMEOUT_S  (default 300) — total drain window
    UPDATE_WORKER_DRAIN_POLL_S     (default 10)  — poll interval
"""

from __future__ import annotations

import argparse
import os
import sys
import time

EXIT_IDLE = 0
EXIT_BUSY = 3

DEFAULT_TIMEOUT_S = int(os.environ.get("UPDATE_WORKER_DRAIN_TIMEOUT_S", 300))
DEFAULT_POLL_S = int(os.environ.get("UPDATE_WORKER_DRAIN_POLL_S", 10))


def count_running_sessions() -> int:
    """Number of AgentSessions currently in status ``running``.

    Non-executable ledger sessions (sdlc-tool run anchors, #2042) hold no
    in-flight turn — a machine whose only "running" rows are ledgers is
    idle for restart purposes, so they are excluded. Raises on any ORM /
    Redis failure; the caller decides the fail-open policy.
    """
    from agent.session_pickup import _truthy
    from models.agent_session import AgentSession

    running = list(AgentSession.query.filter(status="running"))
    # _truthy: Popoto round-trips Field(default=False) as 'False'/'True'
    # strings — mirrors session_health._is_ledger's robustness.
    return sum(1 for s in running if not _truthy(getattr(s, "is_ledger", False)))


def wait_for_idle(timeout_s: float, poll_s: float, *, log=print) -> bool:
    """Poll until no sessions are running or ``timeout_s`` expires.

    Returns True when idle (safe to restart), False when still busy at the
    end of the window. Polls immediately first, so an already-idle machine
    returns without sleeping. Propagates probe exceptions to the caller.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        n = count_running_sessions()
        if n == 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log(f"[update-drain] still busy: {n} running session(s) at window end")
            return False
        log(
            f"[update-drain] {n} running session(s) — waiting "
            f"{min(poll_s, remaining):.0f}s (window {remaining:.0f}s left)"
        )
        time.sleep(min(poll_s, remaining))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_S)
    args = parser.parse_args(argv)

    try:
        idle = wait_for_idle(args.timeout, args.poll)
    except Exception as e:
        # Fail-open: a broken probe must not wedge updates forever.
        print(
            f"[update-drain] WARNING: probe failed ({e!r}) — failing open (restart proceeds)",
            file=sys.stderr,
        )
        return EXIT_IDLE
    return EXIT_IDLE if idle else EXIT_BUSY


if __name__ == "__main__":
    sys.exit(main())
