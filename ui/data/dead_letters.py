"""Data access for the dead-letter dashboard tile.

Synchronous (``def``, not ``async def``) like every other module here:
FastAPI runs sync handlers in a threadpool, so the blocking Redis read never
touches the event loop.

Counts come from the advisory per-stage hash rather than a census of the
rows, so the tile costs two Redis reads no matter how many dead letters
exist. The hash drifts when TTL expiry removes a row; the ``dead-letter-replay``
reflection reconciles it from the eviction index on its own cadence.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_dead_letter_counts(project_key: str | None = None) -> dict:
    """Per-stage dead-letter counts plus the total.

    Every stage appears, including the ones at zero: "no dead letters on the
    steering wire" is a fact worth rendering, and a stage that silently
    disappears from the tile reads as a stage that stopped being watched.
    """
    from bridge import dead_letters

    counts = dead_letters.counts_by_stage(project_key)
    rows = [
        {"stage": stage, "count": counts.get(stage, 0)} for stage in sorted(dead_letters.STAGES)
    ]
    rows.sort(key=lambda row: (-row["count"], row["stage"]))
    return {
        "rows": rows,
        "total": sum(row["count"] for row in rows),
        "cap": dead_letters.DEAD_LETTER_STAGE_CAP,
    }
