"""reflections/housekeeping/disk_reclaim.py — Age out unbounded on-disk state.

What it does: sweeps merged worktree lanes, old Claude CLI transcripts, and
    session snapshots, reporting what it would remove. Removal happens only
    when DISK_RECLAIM_APPLY=true is set in the environment.
Cadence: 86400s (daily) (the categories grow over days, not minutes)
Failure modes:
    - Any sweep raising -> caught per-sweep in tools.disk_reclaim, surfaced as
      a finding; the other sweeps still run
    - gh unavailable -> the worktree sweep skips every lane (fail-closed)
Related reflections:
    - disk_space_check: warns below 10 GB free; this is the reclaimer it names
    - merged_branch_cleanup: deletes merged git branches, not directories
See also: config/reflections.yaml (declaration),
    docs/features/scheduled-disk-reclaim.md
"""

from __future__ import annotations

import logging

from reflections.utilities import PROJECT_ROOT
from tools.disk_reclaim import APPLY_ENV, apply_armed, reclaim

logger = logging.getLogger("reflections.housekeeping")


async def run() -> dict:
    """Sweep the three unbounded on-disk categories.

    ``apply`` is read from the environment inside ``reclaim`` and is
    deliberately not accepted as a reflection parameter. Arming a destructive
    sweep should take a conscious environment change on the host, not a YAML
    edit that rides along with an unrelated config change.
    """
    try:
        report = reclaim(PROJECT_ROOT)
    except Exception as e:
        logger.exception(f"Disk reclaim failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Disk reclaim error: {e}"}

    findings: list[str] = []
    for sweep in report["sweeps"]:
        if sweep["removed"]:
            verb = "removed" if report["applied"] else "reclaimable"
            findings.append(
                f"{sweep['category']}: {len(sweep['removed'])} {verb} "
                f"({', '.join(sweep['removed'][:5])}"
                f"{', ...' if len(sweep['removed']) > 5 else ''})"
            )
        for err in sweep["errors"]:
            findings.append(f"{sweep['category']} error: {err}")

    mb = report["freed_bytes"] / (1024 * 1024)
    mode = "applied" if report["applied"] else "dry-run"
    summary = (
        f"Disk reclaim ({mode}): {report['removed_count']} candidate(s), "
        f"{report['skipped_count']} kept, ~{mb:.0f} MB apparent"
    )
    if not report["applied"] and report["removed_count"]:
        summary += f" — set {APPLY_ENV}=true to arm"

    logger.info(summary)
    if not apply_armed():
        logger.info("disk-reclaim is in dry-run mode; nothing was deleted")

    return {
        "status": "error" if report["errors"] else "ok",
        "findings": findings,
        "summary": summary,
    }
