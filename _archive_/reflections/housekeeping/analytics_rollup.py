"""reflections/housekeeping/analytics_rollup.py — Aggregate analytics metrics, purge old rows.

What it does: Calls analytics.rollup.rollup_daily to aggregate daily metrics and
    purge stale rows (writes to the analytics store).
Cadence: 86400s (daily) (rolls up the prior day's metrics)
Failure modes:
    - rollup_daily raises -> caught, status="error" with the exception in summary
Related reflections:
    - redis_ttl_cleanup: complementary data-retention housekeeping
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger("reflections.maintenance")


async def run() -> dict:
    """Run analytics daily rollup: aggregate metrics and purge old data."""
    try:
        from analytics.rollup import rollup_daily

        result = rollup_daily()
        summary = (
            f"Analytics rollup: aggregated {result['aggregated_days']} days, "
            f"purged {result['purged_rows']} rows"
        )
        logger.info(summary)
        return {"status": "ok", "findings": [summary], "summary": summary}
    except Exception as e:
        logger.warning(f"Analytics rollup failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Analytics rollup error: {e}"}
