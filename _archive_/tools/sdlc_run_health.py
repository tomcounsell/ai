"""Read-only per-run marker-write health report (issue #2451).

``sdlc-tool run-health --issue-number N --run-id X`` reports whether a run's
stage-marker writes have been landing cleanly, or whether a write-failure storm
was silently retried into place (the folded-in #2439 gap: a run reporting
success end-to-end with a ledger that was broadly unwritable throughout).

Disposition taxonomy:
- ``clean`` -- zero fail writes (nothing went wrong), or no counters at all.
- ``transient_recovered`` -- fail writes occurred BUT the trail is complete for
  the stage they targeted (retries landed it; degraded-but-recovered).
- ``never_landed`` -- fail writes occurred AND the trail is MISSING a stage a
  write was attempted for (a genuine hole; rendered LOUD on stderr).

``trail_complete`` is read from ``stage-query`` (read-only): the last-failed
stage is considered landed iff its recorded status is ``completed``.

Exit code is always 0 (read-only, mirrors ``stage-query`` / ``verdict
selfcheck``); callers branch on the JSON ``disposition`` field. A ``never_landed``
result additionally prints a loud stderr line so a supervising ``/do-sdlc`` run
surfaces it in its final report.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logger = logging.getLogger(__name__)

DISPOSITION_CLEAN = "clean"
DISPOSITION_TRANSIENT_RECOVERED = "transient_recovered"
DISPOSITION_NEVER_LANDED = "never_landed"


def run_health(issue_number: int | None, run_id: str | None) -> dict:
    """Compute the health report for a run. Never raises (read-only, fail-soft).

    Returns::

        {
            "ok_writes": int,
            "fail_writes": int,
            "last_failed_stage": str | None,
            "trail_complete": bool,
            "disposition": "clean" | "transient_recovered" | "never_landed",
        }
    """
    from tools._sdlc_marker_telemetry import read_marker_counters

    counters = read_marker_counters(issue_number, run_id)
    ok_writes = counters["ok_writes"]
    fail_writes = counters["fail_writes"]
    last_failed_stage = counters["last_failed_stage"]

    # trail_complete: read the ledger (read-only). The stage a failed write
    # targeted (last_failed_stage) is "landed" iff it now reads completed. With
    # no failed stage recorded, the trail is trivially complete.
    trail_complete = True
    if last_failed_stage:
        try:
            from tools.sdlc_stage_query import query_stage_states

            stages = query_stage_states(issue_number=issue_number)
            trail_complete = stages.get(last_failed_stage) == "completed"
        except Exception as e:
            logger.debug(
                "sdlc_run_health: stage-query read failed for issue #%s (%s: %s) "
                "-- treating trail as incomplete",
                issue_number,
                type(e).__name__,
                e,
            )
            trail_complete = False

    if fail_writes > 0:
        disposition = (
            DISPOSITION_TRANSIENT_RECOVERED if trail_complete else DISPOSITION_NEVER_LANDED
        )
    else:
        disposition = DISPOSITION_CLEAN

    return {
        "ok_writes": ok_writes,
        "fail_writes": fail_writes,
        "last_failed_stage": last_failed_stage,
        "trail_complete": trail_complete,
        "disposition": disposition,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report per-run SDLC marker-write health (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--run-id", dest="run_id", required=True)
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    result = run_health(args.issue_number, args.run_id)

    if result["disposition"] == DISPOSITION_NEVER_LANDED:
        # Render loudly: the supervisor's final report must surface a hole where
        # a write was attempted but never landed in the trail.
        print(
            f"[sdlc-run-health] NEVER_LANDED: issue #{args.issue_number} run_id={args.run_id} "
            f"had {result['fail_writes']} failed marker write(s); the trail is missing "
            f"stage {result['last_failed_stage']!r} (ok_writes={result['ok_writes']}).",
            file=sys.stderr,
        )

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
