#!/usr/bin/env python3
"""Strip removed pid fields (+expectations) from existing AgentSession records.

The durability-room-job-agentrun plan (Milestone 1) replaced the raw pid
fields with a fenced execution record and deleted these hash fields from the
AgentSession model:

    claude_pid, pm_pid, harness_pid, expectations

Popoto ignores unknown hash fields on load, so pre-cutover records remain
fully readable without this migration -- the stale hash entries are orphaned
data, not a crash hazard. This migration reclaims them via **ORM-safe
operations only** (no raw ``hdel``/``hset``): for each terminal record still
carrying a stale field, it queues ``instance.delete()`` + ``Model.save(
instance)`` on ONE transactional Redis pipeline (MULTI/EXEC), so the record is
atomically rewritten with only the current model fields -- a crash
mid-migration can never lose a record.

Safety properties:

- **Idempotent**: re-running finds zero records with stale fields -> no-op.
- **Terminal-only (never rewrites live rows)**: only records whose ``status``
  is in ``models.session_lifecycle.TERMINAL_STATUSES`` are rewritten. The
  worker never writes terminal rows, so this can never clobber a concurrent
  write. Non-terminal records are skipped and reported -- they hydrate fine
  (Popoto ignores the stale fields on load), and any residual stale field on a
  then-live row ages out via the record's ``Meta.ttl``. This is the
  [DESTRUCTIVE] No-Go boundary from the plan: rewriting a running session's
  hash risks clobbering concurrent writes, so it is out of scope by design.
  The base ``popoto.Model.save`` is used directly so ``updated_at`` is
  preserved as loaded (the AgentSession override would restamp it and falsify
  freshness on old records).
- **TTL note**: the atomic rewrite refreshes the record's ``Meta.ttl``
  (30-day backstop) -- acceptable for the one-time migration; stale terminal
  sessions remain subject to the cleanup CLI.

Usage:
  python scripts/migrate_strip_pid_fields.py            # dry-run (default)
  python scripts/migrate_strip_pid_fields.py --apply    # commit changes
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

#: Hash fields removed from the AgentSession model by the durability plan.
STALE_FIELDS = frozenset(
    {
        "claude_pid",
        "pm_pid",
        "harness_pid",
        "expectations",
    }
)


def _raw_field_names(instance) -> set[str]:
    """Field names present in the record's raw hash.

    Detection-only read of hash FIELD NAMES via ``HKEYS`` against the
    ORM-provided key (``instance._redis_key`` / ``db_key``). This reads no
    values, so the binary-field decode hazard that bans raw value reads
    (``hgetall``/``hget`` -- issue #1038) does not apply; Popoto itself
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
    except Exception as e:  # noqa: BLE001 -- detection failure = treat as clean
        logger.warning("hkeys failed for %s: %s", redis_key, e)
    return names


def migrate(apply: bool = False) -> dict:
    """Strip stale pid hash fields from terminal AgentSession records.

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
                # Live rows are actively written by the worker -- do not
                # rewrite them out from under it (the plan's [DESTRUCTIVE]
                # No-Go). Popoto ignores the stale fields on load, so deferral
                # is safe; the migration runs once per machine, so residual
                # stale fields on then-live rows simply age out via the
                # record's TTL.
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
        except Exception as e:  # noqa: BLE001 -- per-record isolation
            stats["errors"] += 1
            logger.error(
                "Error stripping %s: %s",
                getattr(instance, "agent_session_id", "?"),
                e,
            )

    if apply and stats["stripped"]:
        # Per-record delete()+save() already maintain indexes atomically, so this
        # is a defensive orphan sweep, not a functional requirement. Use the
        # production-safe clean_indexes() (orphan-ref cleanup) rather than the
        # full rebuild_indexes(): the latter tears down and rebuilds every index
        # and chokes ("unpack(b) received extra data") on pre-existing phantom
        # index metadata (#2207 class) that is unrelated to this strip.
        logger.info("Cleaning AgentSession index orphans...")
        try:
            AgentSession.clean_indexes()
            logger.info("Index cleanup complete.")
        except Exception as e:  # noqa: BLE001
            logger.error("Index cleanup failed: %s", e)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strip removed pid fields (+expectations) from AgentSession records"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes (default is dry-run)",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info("migrate_strip_pid_fields: %s", mode)
    stats = migrate(apply=args.apply)
    logger.info("Stats: %s", stats)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
