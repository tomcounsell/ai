"""Redis counters for the Memory distillation backfill (memory-distilled-ingest, Phase 3).

Small, isolated module mirroring `models/memory_gate.py`'s counter pattern
(issue #2201) but for a distinct key namespace: `{project_key}:memory-distill:
{reason}`, one per distillation outcome (`distilled`, `distill_failed`,
`distill_refused`, `distill_abandoned` -- see
`reflections/memory/memory_distill_backfill.py`).

Kept as a separate module from `models/memory_gate.py` rather than folding in:
that module is scoped to the INSERT-time content gate inside `Memory.save()`
(`gate_reason` rejections -- `ack`/`fragment`/`short`/`fallback_dropped`); this
module counts UPDATE-time distillation outcomes from a different reflection
subsystem. Conflating the two namespaces under one `{reason}` suffix space
would risk an accidental collision and make `/memories/metrics.json` harder to
reason about (issue #2200's committed baseline already keys on the
`gate_rejected_*` names).

NOT Popoto-managed keys -- plain `INCR`/`GET` counters, same shape and same
raw-Redis-ban exemption as `models/memory_gate.py` (`INCR`/`GET` only, never
`delete`/`srem`/`sadd`/`zrem` on Popoto *model* keys -- enforced by
`.claude/hooks/validators/validate_no_raw_redis_delete.py`).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _increment_distill_counter(project_key: str | None, reason: str) -> None:
    """Best-effort atomic increment of `{project_key}:memory-distill:{reason}`.

    Never raises -- a Redis hiccup must never crash the reflection. `INCR` is
    atomic, so concurrent increments from overlapping reflection runs racing
    on the same `project_key` are safe by construction (no read-modify-write).
    Mirrors `models.memory_gate._increment_gate_counter`.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.incr(f"{project_key}:memory-distill:{reason}")
    except Exception as e:
        logger.debug(
            "[memory_distill_gate] Could not increment distill counter "
            "project_key=%r reason=%r: %s",
            project_key,
            reason,
            e,
        )
