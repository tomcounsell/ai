"""Declared policy and a degradation counter for the pipeline's coordination locks.

Three short-lived Redis locks gate the pipeline, and each one had the same
unstated behaviour: on a Redis error, log a warning and return success. That
is a defensible choice for two of them and a wrong one for the third, and
nothing counted how often it happened, so a Redis degradation was invisible
until its consequences showed up somewhere else.

Every lock now states its policy in its own docstring and calls
:func:`record_lock_degradation` on the branch where Redis failed it:

===================  =======  =====================================================
Lock                 Policy   Why
===================  =======  =====================================================
``pop_lock``         open     Duplicate work beats a stalled queue.
``claim_message``    open     A Redis hiccup must not silently drop a message; the
                              durable dedup set is the fallback.
``claim_pending_run``closed   Two ``claude -p`` processes on one worktree corrupt
                              git state. There is no break-glass.
===================  =======  =====================================================

The counter measures *degradation*, not fail-open specifically — a lock that
fails closed is degraded too — so the policy is the second half of the field
name and the dashboard tile renders the count beside the policy it was taken
under.

The counter hash is a plain (non-Popoto) key, so the client comes from
``utils.redis_client.text_redis()``. This function swallows its own errors:
a counter write must never be able to change a lock's answer.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

# Field name -> "{lock name}:{policy}". Kept in the hash below, per project.
LOCKS_DEGRADED_KEY_SUFFIX = "locks:degraded"

# The declared policy of each lock, by name. The dashboard reads this so a
# lock with no degradation yet still renders with its policy.
LOCK_POLICIES: dict[str, str] = {
    "pop_lock": "open",
    "claim_message": "open",
    "claim_pending_run": "closed",
}


def degraded_key(project_key: str | None = None) -> str:
    """Redis key of the per-project degradation hash."""
    return f"{project_key or 'valor'}:{LOCKS_DEGRADED_KEY_SUFFIX}"


def record_lock_degradation(
    name: str,
    policy: Literal["open", "closed"],
    project_key: str | None = None,
) -> None:
    """Count one degraded acquisition of ``name`` under its declared ``policy``.

    Never raises and never blocks: the caller has already decided what to
    return, and this is only the record that it had to decide.
    """
    try:
        from utils.redis_client import text_redis

        text_redis().hincrby(degraded_key(project_key), f"{name}:{policy}", 1)
    except Exception as e:  # noqa: BLE001 -- observability never breaks a lock
        logger.debug("lock degradation counter write failed for %s: %s", name, e)


def degradation_counts(project_key: str | None = None) -> dict[str, int]:
    """Every ``{name}:{policy}`` field and its count. Never raises."""
    try:
        from utils.redis_client import text_redis

        raw = text_redis().hgetall(degraded_key(project_key)) or {}
        return {field: int(value) for field, value in raw.items()}
    except Exception as e:  # noqa: BLE001 -- dashboards degrade, they do not crash
        logger.debug("lock degradation counter read failed: %s", e)
        return {}
