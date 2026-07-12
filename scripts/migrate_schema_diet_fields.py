#!/usr/bin/env python3
"""Strip schema-diet fields (#1927) from existing AgentSession records.

Plan #1927 (AgentSession schema diet) pruned an accreted telemetry surface
and applied one precision rename. This migration reclaims the stale hash
fields on Redis records exactly the way `scripts/migrate_strip_pty_fields.py`
(#1924) did — the pattern this script clones.

Field-by-field disposition (audited starting point — see the plan's
"Field-by-Field Disposition" table for full rationale):

DELETE — no live reader AND no live writer:
    self_report_sent_at        — retired 2026-05-06 self-report frequency cap
    sdk_connection_torn_down_at — idle-sweeper deleted by plan #2000
    session_mode                — deprecated no-op, superseded by session_type
    pm_transcript_path           — no live writer, dashboard-only read (dropped)
    dev_transcript_path          — no live writer, dashboard-only read (dropped)
    startup_failure_kind          — historical PTY-era diagnostic; the ENTIRE
                                     plumbing chain (crash_signature.py reader,
                                     pass-through, "ceiling" branches, keyword
                                     param, docstring refs) was removed too
    startup_captured_frame        — historical PTY-era diagnostic pointer

CUT — write-only observability counters with no production reader:
    compaction_count
    compaction_skipped_count
    nudge_deferred_count

  (tool_timeout_count_{internal,mcp,default} is NOT in this set — it is
  written via a dynamic `f"tool_timeout_count_{tier}"` setattr in
  agent/session_health.py and reads as dead to a literal grep only; the
  plan explicitly flags it as a delete-trap and keeps it.)

COLLAPSE — metered/total accounting split, redirected to `total_*`:
    metered_input_tokens
    metered_output_tokens
    metered_cache_read_tokens
    metered_cost_usd

  `accumulate_session_tokens` no longer branches on a `metered=` flag —
  every caller (including the former `metered=True` session-runner leg)
  now accumulates onto the SAME `total_*` fields. The per-turn "metered-leg
  cost" ledger-metric series that the metered branch used to emit ended at
  this migration; there is no `total_*` replacement (deliberate, matches
  the plan's accepted loss of longitudinal comparability).

RENAME — the one frozen rename (no open-ended survivor audit):
    watchdog_unhealthy -> unhealthy_reason
        Held a reason string, not a bool; the old name implied a flag.
        `AgentSession._normalize_kwargs` carries a back-alias so
        archive-restore payloads (which route through `__init__`) still
        map the old key -- this migration strips the orphaned old-name
        hash key from live Redis records, where Popoto's lazy-load reads
        the raw hash and bypasses `_normalize_kwargs` (so the pre-rename
        value is NOT copied forward; see the plan's "Rename value-
        preservation stance").

KEPT — explicitly NOT renamed or deleted (frozen scope, do not touch):
    user_facing_routed — persisted delivery-confirmation boolean; renaming
        it is unsafe because Popoto's lazy-load bypasses _normalize_kwargs,
        so an in-flight session crossing the deploy boundary would read the
        renamed field as its False default and mis-fire the delivery
        emoji. See the plan's Critique Results concern 2.
    total_input_tokens / total_output_tokens / total_cache_read_tokens /
        total_cost_usd — high read fan-out (analytics, watchdog,
        tool_budget, pm_briefings); renaming is pure churn.

Popoto ignores unknown hash fields on load, so pre-cutover records remain
fully readable without this migration — the stale hash entries are orphaned
data, not a crash hazard. This migration reclaims them via **ORM-safe
operations only** (no raw ``hdel``/``hset``): for each terminal record still
carrying a stale field, it queues ``instance.delete()`` + ``Model.save()``
on ONE transactional Redis pipeline, so the record is atomically rewritten
with only the current model fields — a crash mid-migration can never lose a
record.

Safety properties:

- **Idempotent**: re-running finds zero records with stale fields → no-op.
- **Concurrent-safe**: only TERMINAL-status records are rewritten (the
  worker never writes terminal rows); non-terminal records are skipped and
  reported (they hydrate fine; the migration runs once per machine, so any
  residual stale fields on then-live rows age out via the record's TTL).
  The base ``popoto.Model.save`` is used directly so ``updated_at`` is
  preserved as loaded (the AgentSession override would restamp it and
  falsify freshness on old records).
- **TTL note**: the atomic rewrite refreshes the record's ``Meta.ttl``
  (30-day backstop) — acceptable for the one-time migration; stale terminal
  sessions remain subject to the cleanup CLI.

Usage:
  python scripts/migrate_schema_diet_fields.py            # dry-run (default)
  python scripts/migrate_schema_diet_fields.py --apply    # commit changes
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

#: Hash fields removed (or renamed away from) the AgentSession model by
#: plan #1927 (AgentSession schema diet).
STALE_FIELDS = frozenset(
    {
        # DELETE — no live reader and no live writer.
        "self_report_sent_at",
        "sdk_connection_torn_down_at",
        "session_mode",
        "pm_transcript_path",
        "dev_transcript_path",
        "startup_failure_kind",
        "startup_captured_frame",
        # CUT — write-only observability counters.
        "compaction_count",
        "compaction_skipped_count",
        "nudge_deferred_count",
        # COLLAPSE — metered/total accounting split.
        "metered_input_tokens",
        "metered_output_tokens",
        "metered_cache_read_tokens",
        "metered_cost_usd",
        # RENAME — old field name (survivor now lives at unhealthy_reason).
        "watchdog_unhealthy",
    }
)


def _raw_field_names(instance) -> set[str]:
    """Field names present in the record's raw hash.

    Detection-only read of hash FIELD NAMES via ``HKEYS`` against the
    ORM-provided key (``instance._redis_key`` / ``db_key``). This reads no
    values, so the binary-field decode hazard that bans raw value reads
    (``hgetall``/``hget`` — issue #1038) does not apply; Popoto itself
    exposes no ORM API for orphaned-hash-field discovery (its migration
    cookbook prescribes raw access for exactly this). All WRITES in this
    script remain ORM-only (``instance.delete()`` + ``Model.save()``).
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    redis_key = getattr(instance, "_redis_key", None) or instance.db_key.redis_key
    names: set[str] = set()
    try:
        for key in POPOTO_REDIS_DB.hkeys(redis_key):
            names.add(key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key))
    except Exception as e:  # noqa: BLE001 — detection failure = treat as clean
        logger.warning("hkeys failed for %s: %s", redis_key, e)
    return names


def migrate(apply: bool = False) -> dict:
    """Strip stale schema-diet hash fields from terminal AgentSession records.

    Args:
        apply: If False (default), report what would happen without writing.

    Returns:
        Dict with migration stats.
    """
    import popoto
    from popoto.redis_db import POPOTO_REDIS_DB

    from models.agent_session import AgentSession
    from models.session_lifecycle import TERMINAL_STATUSES

    stats = {
        "total_records": 0,
        "clean": 0,
        "stripped": 0,
        "deferred_non_terminal": 0,
        "errors": 0,
    }

    for instance in AgentSession.query.all():
        stats["total_records"] += 1
        try:
            stale_present = _raw_field_names(instance) & STALE_FIELDS
            if not stale_present:
                stats["clean"] += 1
                continue

            status = getattr(instance, "status", None)
            if status not in TERMINAL_STATUSES:
                # Live rows are actively written by the worker — do not
                # rewrite them out from under it. Popoto ignores the stale
                # fields on load, so deferral is safe; the migration runs
                # once per machine, so residual stale fields on then-live
                # rows simply age out via the record's TTL.
                stats["deferred_non_terminal"] += 1
                logger.info(
                    "  DEFER %s (status=%s): stale fields %s left in place",
                    getattr(instance, "agent_session_id", "?"),
                    status,
                    sorted(stale_present),
                )
                continue

            logger.info(
                "  %s %s: stripping %s",
                "STRIP" if apply else "WOULD strip",
                getattr(instance, "agent_session_id", "?"),
                sorted(stale_present),
            )
            if apply:
                # Atomic delete + recreate on one transactional pipeline:
                # the hash is rewritten with only the current model fields.
                # Base-class save preserves the loaded updated_at (the
                # AgentSession override would restamp it to now).
                pipe = POPOTO_REDIS_DB.pipeline()
                pipe = instance.delete(pipeline=pipe)
                pipe = popoto.Model.save(instance, pipeline=pipe)
                pipe.execute()
            stats["stripped"] += 1
        except Exception as e:  # noqa: BLE001 — per-record isolation
            stats["errors"] += 1
            logger.error(
                "Error stripping %s: %s",
                getattr(instance, "agent_session_id", "?"),
                e,
            )

    if apply and stats["stripped"]:
        logger.info("Rebuilding AgentSession indexes...")
        try:
            AgentSession.rebuild_indexes()
            logger.info("Index rebuild complete.")
        except Exception as e:  # noqa: BLE001
            logger.error("Index rebuild failed: %s", e)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strip schema-diet (#1927) fields from AgentSession records"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes (default is dry-run)",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info("migrate_schema_diet_fields: %s", mode)
    stats = migrate(apply=args.apply)
    logger.info("Stats: %s", stats)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
