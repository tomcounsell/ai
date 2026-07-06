"""Thin CLI wrapper for release verification (issue #1898).

Usage::

    python -m scripts.update.verify_release [--since <epoch>] [--skip-bridge]

Called by ``scripts/remote-update.sh`` as its terminal step on every cron
cycle (including no-op cycles), re-classifying a starved/never-restarted
process. Prints an operator-facing summary line naming any stale process and
its lagging short-SHA (e.g. ``bridge running 659756a4 but HEAD is 6b5b998a``).

Exit codes:

- ``1`` — any in-role process classifies positively ``stale``.
- ``0`` — otherwise. ``unknown`` prints a warning but never fails the run
  (a swallowed best-effort beacon write must never invert into a FAILED).

``--since <epoch>`` is the restart moment captured by ``remote-update.sh``
just before the worker kickstart. When > 0, a bounded 15 x 2s (30s) poll
waits for the worker beacon to freshen past it before classifying (matching
the worker-heartbeat freshness poll at run.py's ``for _ in range(15)``) —
the Race 1 mitigation. A beacon that never freshens past ``--since`` within
the window means the worker failed to come up on new code → stale/fail.

``--skip-bridge`` is passed when a bridge restart is queued this cycle (the
deliberately-about-to-restart bridge must not be escalated as stale).
Independently of the flag, a fresh ``data/update-restart-in-progress``
marker also skips bridge escalation (Decision 27) so a concurrent
invocation inside another process's restart window shares the skip signal.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.update import git, service, verify  # noqa: E402

POLL_ATTEMPTS = 15
POLL_INTERVAL_SECONDS = 2

# Planned-restart marker freshness window: STARTUP_GRACE_SECONDS (5 min) + one
# watchdog cycle (60s) — the shared formula from Decision 26 (#1898), kept in
# lockstep with monitoring/bridge_watchdog.py's UPDATE_RESTART_MARKER_TTL_SECONDS.
UPDATE_RESTART_MARKER_TTL_SECONDS = 5 * 60 + 60


def _restart_marker_fresh(project_dir: Path) -> bool:
    """True when data/update-restart-in-progress exists and is fresh."""
    marker = project_dir / "data" / "update-restart-in-progress"
    try:
        if not marker.exists():
            return False
        return (time.time() - marker.stat().st_mtime) < UPDATE_RESTART_MARKER_TTL_SECONDS
    except Exception:
        return False


def _poll_worker_beacon(project_dir: Path, since: float) -> bool:
    """Bounded 15 x 2s poll for a worker beacon with beacon_ts > since."""
    beacon_path = project_dir / "data" / "worker_boot_sha"
    for attempt in range(POLL_ATTEMPTS):
        beacon = service.read_boot_beacon(beacon_path)
        if beacon is not None and beacon[1] > since:
            return True
        if attempt < POLL_ATTEMPTS - 1:
            time.sleep(POLL_INTERVAL_SECONDS)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.update.verify_release",
        description="Verify running bridge/worker releases against pulled HEAD (#1898).",
    )
    parser.add_argument(
        "--since",
        type=float,
        default=0.0,
        help="Restart moment (unix epoch); >0 polls for a fresher worker beacon first.",
    )
    parser.add_argument(
        "--skip-bridge",
        action="store_true",
        help="Skip bridge escalation (a bridge restart is queued this cycle).",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=PROJECT_ROOT,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    project_dir = args.project_dir

    try:
        head_short = git.get_short_sha(project_dir)
    except Exception as exc:
        # No HEAD to compare against — inconclusive, warn but never fail.
        print(f"WARNING: release verify skipped — could not read HEAD: {exc}")
        return 0

    machine_check = verify.check_machine_identity(project_dir)
    skip_bridge = args.skip_bridge or _restart_marker_fresh(project_dir)

    # Race 1 mitigation: after a worker kickstart, wait (bounded) for the fresh
    # worker beacon before classifying — otherwise we read the pre-restart
    # beacon, classify unknown, and mask a genuine stale/FAILED.
    forced_worker_stale = False
    if args.since > 0 and machine_check.get("projects"):
        forced_worker_stale = not _poll_worker_beacon(project_dir, args.since)

    results = service.verify_running_release(project_dir, head_short, machine_check)
    if skip_bridge:
        results.pop("bridge", None)
    if forced_worker_stale and "worker" in results:
        # The worker never came up on new code within the window.
        results["worker"]["classification"] = "stale"

    stale_lines: list[str] = []
    unknown_names: list[str] = []
    state_parts: list[str] = []
    for name, info in results.items():
        classification = info["classification"]
        boot_sha = info.get("boot_sha") or "unknown"
        if classification == "stale":
            stale_lines.append(f"{name} running {boot_sha} but HEAD is {head_short}")
            state_parts.append(f"{name} STALE {boot_sha}")
        elif classification == "unknown":
            unknown_names.append(name)
            state_parts.append(f"{name} unknown")
        else:
            state_parts.append(f"{name} matches")

    if stale_lines:
        print(f"release verify FAILED @ {head_short}: {'; '.join(stale_lines)}")
        return 1

    for name in unknown_names:
        print(f"WARNING: {name} release could not be confirmed (unknown) — not failing the run")
    detail = ", ".join(state_parts) if state_parts else "no in-role processes"
    print(f"release verify OK @ {head_short} ({detail})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
