#!/usr/bin/env python3
"""Strip removed PTY fields (+resume_handles) from existing AgentSession records.

Plan #1924 (granite PTY teardown, task 5) removed these fields from the
AgentSession model:

    dev_pid, pty_slot, last_pty_read_loop_at, last_pty_activity_at,
    mid_run_quiescent_since, mid_run_pty_snapshot, role_transports,
    resume_handles

Popoto ignores unknown hash fields on load, so pre-cutover records remain
fully readable without this migration — the stale hash entries are orphaned
data, not a crash hazard (Risk 5). This migration reclaims them via
**ORM-safe operations only** (no raw ``hdel``/``hset``): for each terminal
record still carrying a stale field, it queues ``instance.delete()`` +
``Model.save(instance)`` on ONE transactional Redis pipeline (MULTI/EXEC),
so the record is atomically rewritten with only the current model fields —
a crash mid-migration can never lose a record.

Safety properties:

- **Idempotent**: re-running finds zero records with stale fields → no-op.
- **Concurrent-safe**: only TERMINAL-status records are rewritten (the
  worker never writes terminal rows); non-terminal records are skipped and
  reported (they hydrate fine; a later run strips them once terminal).
  The base ``popoto.Model.save`` is used directly so ``updated_at`` is
  preserved as loaded (the AgentSession override would restamp it and
  falsify freshness on old records).
- **TTL note**: the atomic rewrite refreshes the record's ``Meta.ttl``
  (30-day backstop) — acceptable for the one-time migration; stale terminal
  sessions remain subject to the cleanup CLI.

Usage:
  python scripts/migrate_strip_pty_fields.py            # dry-run (default)
  python scripts/migrate_strip_pty_fields.py --apply    # commit changes
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

#: Hash fields removed from the AgentSession model by plan #1924 task 5.
STALE_FIELDS = frozenset(
    {
        "dev_pid",
        "pty_slot",
        "last_pty_read_loop_at",
        "last_pty_activity_at",
        "mid_run_quiescent_since",
        "mid_run_pty_snapshot",
        "role_transports",
        "resume_handles",
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
    """Strip stale PTY hash fields from terminal AgentSession records.

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
                # fields on load, so deferral is safe; a later run strips
                # the record once it is terminal.
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
        description="Strip removed PTY fields (+resume_handles) from AgentSession records"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes (default is dry-run)",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info("migrate_strip_pty_fields: %s", mode)
    stats = migrate(apply=args.apply)
    logger.info("Stats: %s", stats)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
