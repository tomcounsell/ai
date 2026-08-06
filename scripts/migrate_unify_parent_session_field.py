#!/usr/bin/env python3
"""Migrate AgentSession: unify parent_session_id into parent_agent_session_id.

After issue #757, parent_agent_session_id is the single canonical KeyField for the
parent link. parent_session_id (and parent_chat_session_id) are deprecated @property
aliases. This script copies any leftover Redis hash field parent_session_id values
into parent_agent_session_id where the latter is empty, then deletes the stale field.

Idempotent: re-running after --apply reports zero remaining records to migrate.

Usage:
  python scripts/migrate_unify_parent_session_field.py            # dry-run (default)
  python scripts/migrate_unify_parent_session_field.py --apply    # commit changes
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Keys to skip (index/sorted-set infrastructure keys)
SKIP_PATTERNS = (b":_sorted_set:", b":_field_index:")


def _is_index_key(key: bytes) -> bool:
    """Check if a Redis key is a Popoto index key (not a data record)."""
    return any(p in key for p in SKIP_PATTERNS)


def migrate(apply: bool = False) -> dict:
    """Copy parent_session_id -> parent_agent_session_id where the latter is empty.

    Args:
        apply: If False (default), log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "copied": 0,
        "stale_field_cleared": 0,
        "already_consistent": 0,
        "no_parent_at_all": 0,
        "errors": 0,
    }

    cursor = 0
    all_keys: list[bytes] = []
    while True:
        cursor, keys = redis_client.scan(cursor, match="AgentSession:*", count=500)
        all_keys.extend(keys)
        if cursor == 0:
            break

    hash_keys = [k for k in all_keys if not _is_index_key(k)]
    stats["total_records"] = len(hash_keys)
    logger.info(f"Found {stats['total_records']} AgentSession hash records")

    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            has_old = redis_client.hexists(key, "parent_session_id")
            has_new = redis_client.hexists(key, "parent_agent_session_id")
            old_val = redis_client.hget(key, "parent_session_id") if has_old else None
            new_val = redis_client.hget(key, "parent_agent_session_id") if has_new else None

            old_set = old_val not in (None, b"", "")
            new_set = new_val not in (None, b"", "")

            if old_set and not new_set:
                logger.info(f"  Copy parent_session_id -> parent_agent_session_id on {key_str}")
                if apply:
                    pipe = redis_client.pipeline()
                    pipe.hset(key, "parent_agent_session_id", old_val)
                    pipe.hdel(key, "parent_session_id")
                    pipe.execute()
                stats["copied"] += 1
            elif old_set and new_set:
                # Canonical already populated; clean up the stale alias field.
                logger.info(
                    f"  Clear stale parent_session_id on {key_str} "
                    "(parent_agent_session_id already set)"
                )
                if apply:
                    redis_client.hdel(key, "parent_session_id")
                stats["stale_field_cleared"] += 1
            elif new_set:
                stats["already_consistent"] += 1
            else:
                stats["no_parent_at_all"] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    # Index reconstruction is LOAD-BEARING here, unlike the strip migrations'
    # trailing sweep (#2524). `parent_agent_session_id` is a KeyField, and the
    # hset above writes it via raw Redis, so no index entry is created for the
    # copied value and `query.filter(parent_agent_session_id=...)` cannot see
    # it. `clean_indexes()` is removal-only and cannot create the missing
    # entry, so it is NOT a substitute. See #2544 and
    # docs/features/popoto-index-hygiene.md "Migration Guards".
    #
    # Use the GUARDED repair path, not popoto's raw rebuild. Both reconstruct
    # the indexes, but repair_indexes() additionally:
    #   - asserts the popoto version floor FIRST (#2536), so a below-floor
    #     popoto cannot tear down every index and then fail to rebuild it --
    #     the 2026-07-14 silent-empty incident;
    #   - clears the stale $IndexF pointers the raw rebuild never enumerates;
    #   - installs the A1 identity-less shim so phantom hashes (#2101, #2207)
    #     are not re-inflated into the indexes on the way through.
    #
    # It still opens the #1720 class-set window (~22s on a 4006-row keyspace,
    # #2549) against a live worker, because /update runs migrations at Step 3.6
    # before the service restart. That window is inherent to reconstructing the
    # index at all; the guards above are what make paying it survivable.
    if apply and (stats["copied"] > 0 or stats["stale_field_cleared"] > 0):
        logger.info("Repairing Popoto indexes...")
        try:
            from models.agent_session import AgentSession

            _stale, rebuilt = AgentSession.repair_indexes()
            if rebuilt:
                logger.info(f"Index repair complete ({rebuilt} records reindexed).")
            else:
                # repair_indexes() returns (0, 0) without rebuilding when its
                # non-reentrant lock is already held. We just wrote KeyField
                # values raw, so skipping reconstruction leaves those records
                # unqueryable -- never report that as success.
                #
                # Self-healing, but say so out loud: the in-flight repair that
                # holds the lock rebuilds from the same hashes, and the worker
                # runs repair_indexes() at startup and on its hourly tick, so
                # the index converges. The non-zero exit keeps /update from
                # recording this migration complete on the strength of a
                # reconstruction that did not run.
                stats["errors"] += 1
                logger.error(
                    "Index repair was SKIPPED (another repair_indexes() holds the lock). "
                    "Records were rewritten raw, so their KeyField indexes are not yet "
                    "reconstructed; not reporting success."
                )
        except Exception as e:
            # Includes the popoto floor assertion. Failing closed matters more
            # here than anywhere else in this script: the renames already
            # landed, so a swallowed error would record the migration complete
            # with the index still describing the pre-rename state.
            stats["errors"] += 1
            logger.error(f"Failed to repair indexes: {e}")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unify parent_session_id into parent_agent_session_id"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes (default is dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run flag (default behavior; for symmetry with siblings)",
    )
    args = parser.parse_args()

    apply = args.apply and not args.dry_run
    mode = "LIVE" if apply else "DRY RUN"
    logger.info(f"=== AgentSession Parent Field Unification ({mode}) ===")

    stats = migrate(apply=apply)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    pending = stats["copied"] + stats["stale_field_cleared"]
    if not apply and pending > 0:
        logger.info(
            f"Would make {pending} changes across {stats['total_records']} records. "
            "Run with --apply to commit."
        )
    elif apply and pending > 0:
        logger.info(f"Migrated {pending} records. Re-run --dry-run to confirm 0 to migrate.")
    else:
        logger.info("0 to migrate.")

    if stats["errors"] > 0:
        logger.warning(f"{stats['errors']} error(s) occurred during migration.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
