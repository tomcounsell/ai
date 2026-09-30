"""Redis counters for the Memory write-gate (issue #2201).

Small, isolated module so `models/memory.py` doesn't need to inline Redis
plumbing directly. NOT Popoto-managed keys -- these are plain `INCR`/`GET`
counters shaped `{project_key}:memory-gate:{reason}`, one per rejection
reason (`ack`, `fragment`, `short`, `fallback_dropped`). The raw-Redis ban
enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`
targets `delete`/`srem`/`sadd`/`zrem` on Popoto *model* keys only --
`INCR`/`GET` on this counter namespace is exactly the pattern
`_sum_project_counter` (`ui/app.py:434`) already uses for other
non-Popoto-managed operational counters.

Uses the same Redis handle the rest of the repo already uses for this
purpose (`monitoring/worker_watchdog.py:409`) -- there is no
`tools.redis_client` module.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _increment_gate_counter(project_key: str | None, reason: str) -> None:
    """Best-effort atomic increment of `{project_key}:memory-gate:{reason}`.

    Never raises -- a Redis hiccup must never crash a write. `INCR` is
    atomic, so concurrent increments from the bridge/worker/hooks racing on
    the same `project_key` are safe by construction (no read-modify-write).
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.incr(f"{project_key}:memory-gate:{reason}")
    except Exception as e:
        logger.debug(
            "[memory_gate] Could not increment gate counter project_key=%r reason=%r: %s",
            project_key,
            reason,
            e,
        )
