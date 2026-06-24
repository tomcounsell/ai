"""reflections/audits/principal_staleness.py — PRINCIPAL.md freshness check.

What it does: Reads the mtime of `config/PRINCIPAL.md` and flags it as a
    finding if it has not been modified in over 90 days, since stale
    supervisor priorities may no longer reflect reality.
Cadence: 86400s (a document age threshold of 90 days only needs daily polling)
Failure modes:
    - config/PRINCIPAL.md missing -> returns an "unavailable" finding (status ok)
Related reflections:
    - task-backlog-check: sibling task-management reflection
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from reflections.utilities import PROJECT_ROOT

logger = logging.getLogger("reflections.task_management")


async def run() -> dict:
    """Check if PRINCIPAL.md is stale (>90 days since last modification).

    PRINCIPAL.md encodes the supervisor's strategic context. If it hasn't
    been updated in 90+ days, flag it for review since priorities may
    have shifted.
    """
    principal_path = PROJECT_ROOT / "config" / "PRINCIPAL.md"

    if not principal_path.exists():
        finding = "config/PRINCIPAL.md does not exist — principal context is unavailable"
        logger.warning(finding)
        return {"status": "ok", "findings": [finding], "summary": finding}

    mod_time = datetime.fromtimestamp(principal_path.stat().st_mtime, tz=UTC)
    from bridge.utc import utc_now

    age_days = (utc_now() - mod_time).days
    staleness_threshold = 90

    if age_days > staleness_threshold:
        finding = (
            f"config/PRINCIPAL.md is {age_days} days old (threshold: {staleness_threshold}). "
            "Consider reviewing and updating supervisor priorities."
        )
        logger.warning(
            f"PRINCIPAL.md is stale: last modified {age_days} days ago "
            f"(threshold: {staleness_threshold} days)"
        )
        return {"status": "ok", "findings": [finding], "summary": finding}
    else:
        msg = (
            f"PRINCIPAL.md is fresh: last modified {age_days} days ago "
            f"(threshold: {staleness_threshold} days)"
        )
        logger.info(msg)
        return {"status": "ok", "findings": [], "summary": msg}
