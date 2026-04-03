#!/usr/bin/env python3
"""Migrate AgentSession: rename parent_chat_session_id → parent_session_id and backfill role.

This script handles hash field renames (no Redis key restructuring needed):
1. SCANs all AgentSession:* keys (excluding index keys)
2. For each record: renames hash field parent_chat_session_id → parent_session_id
3. Backfills role field from session_type on ALL records:
   - session_type="chat" → role="pm"
   - session_type="dev" → role="dev"
4. Calls AgentSession.rebuild_indexes() after all changes

No Redis key RENAME is needed because the key segment position is unchanged
(parent_session_id still sorts to position 4, same as parent_chat_session_id).

Usage:
  python scripts/migrate_parent_session_field.py --dry-run
  python scripts/migrate_parent_session_field.py
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Role backfill mapping from session_type
SESSION_TYPE_TO_ROLE = {
    "chat": "pm",
    "dev": "dev",
}

# Keys to skip (index/sorted-set infrastructure keys)
SKIP_PATTERNS = (b":_sorted_set:", b":_field_index:")


def _is_index_key(key: bytes) -> bool:
    """Check if a Redis key is a Popoto index key (not a data record)."""
    return any(p in key for p in SKIP_PATTERNS)


def migrate(dry_run: bool = True) -> dict:
    """Rename hash field and backfill role on all AgentSession records.

    Args:
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "field_renamed": 0,
        "role_backfilled": 0,
        "skipped_field_already_migrated": 0,
        "skipped_role_already_set": 0,
        "skipped_index_keys": 0,
        "errors": 0,
    }

    # Phase 1: Find all AgentSession hash keys
    cursor = 0
    all_keys = []
    while True:
        cursor, keys = redis_client.scan(cursor, match="AgentSession:*", count=500)
        all_keys.extend(keys)
        if cursor == 0:
            break

    hash_keys = [k for k in all_keys if not _is_index_key(k)]
    stats["skipped_index_keys"] = len(all_keys) - len(hash_keys)
    stats["total_records"] = len(hash_keys)
    logger.info(f"Found {stats['total_records']} AgentSession hash records")

    if not hash_keys:
        logger.info("No records to migrate.")
        return stats

    # Phase 2: Rename hash field and backfill role
    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            # --- Hash field rename: parent_chat_session_id → parent_session_id ---
            has_old = redis_client.hexists(key, "parent_chat_session_id")
            has_new = redis_client.hexists(key, "parent_session_id")

            if has_old and not has_new:
                old_val = redis_client.hget(key, "parent_chat_session_id")
                if old_val is not None:
                    logger.info(
                        f"  Rename field: {key_str} parent_chat_session_id -> parent_session_id"
                    )
                    if not dry_run:
                        pipe = redis_client.pipeline()
                        pipe.hset(key, "parent_session_id", old_val)
                        pipe.hdel(key, "parent_chat_session_id")
                        pipe.execute()
                    stats["field_renamed"] += 1
            elif has_old and has_new:
                # Both exist — clean up old field
                logger.info(f"  Cleanup stale field: {key_str} (both old and new exist)")
                if not dry_run:
                    redis_client.hdel(key, "parent_chat_session_id")
                stats["field_renamed"] += 1
            else:
                stats["skipped_field_already_migrated"] += 1

            # --- Role backfill from session_type ---
            has_role = redis_client.hexists(key, "role")
            if has_role:
                stats["skipped_role_already_set"] += 1
            else:
                session_type_val = redis_client.hget(key, "session_type")
                if session_type_val is not None:
                    if isinstance(session_type_val, bytes):
                        try:
                            st = session_type_val.decode("utf-8")
                        except UnicodeDecodeError:
                            # Popoto may store pickled values; try latin-1 or skip
                            try:
                                st = session_type_val.decode("latin-1")
                            except Exception:
                                st = None
                    else:
                        st = session_type_val
                    role = SESSION_TYPE_TO_ROLE.get(st)
                    # Popoto may prefix values with non-ASCII bytes; strip non-alphanumeric prefix
                    st_clean = st.lstrip("\x00\xa3£").strip() if st else st
                    role = SESSION_TYPE_TO_ROLE.get(st_clean) or SESSION_TYPE_TO_ROLE.get(st)
                    if role:
                        logger.info(
                            f"  Backfill role: {key_str} session_type={st_clean} → role={role}"
                        )
                        if not dry_run:
                            redis_client.hset(key, "role", role)
                        stats["role_backfilled"] += 1
                    else:
                        logger.warning(
                            f"  Unknown session_type={st!r} for {key_str}, skipping role backfill"
                        )
                else:
                    logger.warning(f"  No session_type for {key_str}, skipping role backfill")

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    # Phase 3: Rebuild indexes
    if not dry_run and (stats["field_renamed"] > 0 or stats["role_backfilled"] > 0):
        logger.info("Rebuilding Popoto indexes...")
        try:
            from models.agent_session import AgentSession

            AgentSession.rebuild_indexes()
            logger.info("Index rebuild complete.")
        except Exception as e:
            logger.error(f"Failed to rebuild indexes: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migrate AgentSession: rename parent_chat_session_id and backfill role"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== AgentSession Parent Field Rename + Role Backfill ({mode}) ===")

    stats = migrate(dry_run=args.dry_run)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    changes = stats["field_renamed"] + stats["role_backfilled"]
    if not args.dry_run and changes > 0:
        logger.info(
            f"Successfully migrated {changes} changes across {stats['total_records']} records."
        )
    elif args.dry_run and changes > 0:
        logger.info(
            f"Would make {changes} changes across {stats['total_records']} records."
            " Run without --dry-run to apply."
        )
    else:
        logger.info("No records needed migration.")

    if stats["errors"] > 0:
        logger.warning(f"{stats['errors']} error(s) occurred during migration.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
