"""Data access for the lock-policy dashboard tile.

Synchronous (``def``, not ``async def``) like every other module here:
FastAPI runs sync handlers in a threadpool, so the blocking Redis read never
touches the event loop.

The tile answers one question a log line cannot: how often has each
coordination lock had to fall back on its declared policy? Every lock is
listed whether or not it has degraded, so a zero is a measurement rather than
an absence.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_lock_policies(project_key: str | None = None) -> list[dict]:
    """One row per lock: name, declared policy, and degradation count.

    Rows are ordered fail-closed first, then by name, so the lock whose
    degradation stalls the queue reads before the two whose degradation only
    duplicates work.
    """
    from agent.lock_policy import LOCK_POLICIES, degradation_counts

    counts = degradation_counts(project_key)
    rows = [
        {
            "name": name,
            "policy": policy,
            "count": counts.get(f"{name}:{policy}", 0),
            "consequence": (
                "pickup stalls until Redis recovers"
                if policy == "closed"
                else "work may be duplicated"
            ),
        }
        for name, policy in LOCK_POLICIES.items()
    ]
    rows.sort(key=lambda row: (row["policy"] != "closed", row["name"]))
    return rows
