#!/usr/bin/env python3
"""Migrate AgentSession Redis key structure for job_id -> id and parent_job_id -> parent_agent_session_id rename.

This script handles the structural key migration (not just hash field renames):
1. SCANs all AgentSession:* keys (excluding index keys)
2. Parses each key into 6 colon-separated segments
3. Swaps segments 3 and 4 (parent_agent_session_id takes position 3,
   parent_chat_session_id moves to position 4)
4. Renames hash fields inside each record (job_id -> id, parent_job_id -> parent_agent_session_id)
5. Uses pipeline.rename() for atomic key rename
6. Updates $Class:AgentSession set membership
7. Calls AgentSession.rebuild_indexes() after all renames

Key structure change:
  OLD: AgentSession:{chat_id}:{job_id}:{parent_chat_session_id}:{parent_job_id}:{project_key}:{session_type}
  NEW: AgentSession:{chat_id}:{id}:{parent_agent_session_id}:{parent_chat_session_id}:{project_key}:{session_type}

Segments 3 and 4 swap because Popoto orders KeyFields alphabetically:
  - parent_agent_session_id (new name) sorts before parent_chat_session_id
  - parent_job_id (old name) sorted after parent_chat_session_id

Usage:
  python scripts/migrate_agent_session_keyfield_rename.py --dry-run
  python scripts/migrate_agent_session_keyfield_rename.py
  python scripts/migrate_agent_session_keyfield_rename.py --reverse
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Hash field renames
HASH_FIELD_RENAMES = {
    "job_id": "id",
    "parent_job_id": "parent_agent_session_id",
}

# Reverse hash field renames (for --reverse)
HASH_FIELD_RENAMES_REVERSE = {v: k for k, v in HASH_FIELD_RENAMES.items()}

# Keys to skip (index/sorted-set infrastructure keys)
SKIP_PATTERNS = (b":_sorted_set:", b":_field_index:")


def _is_index_key(key: bytes) -> bool:
    """Check if a Redis key is a Popoto index key (not a data record)."""
    return any(p in key for p in SKIP_PATTERNS)


def _is_already_migrated(parts: list[bytes]) -> bool:
    """Check if key is already in new format.

    New format has parent_agent_session_id in position 3 (index 3).
    Old format has parent_chat_session_id in position 3 and parent_job_id in position 4.

    Detection heuristic: In old format, segment 3 is parent_chat_session_id which
    typically looks like 'tg_...' or a non-UUID value. In new format, segment 3 is
    parent_agent_session_id which is a UUID (32 hex chars) or empty.

    However, the most reliable check: if the key was constructed with the new field
    ordering, positions 3 and 4 would already be swapped. We check by looking at
    hash fields inside the record.
    """
    # If we have 7 parts (AgentSession + 6 segments), check segment ordering
    # But this is unreliable without reading the hash. Return False to let
    # the hash field check handle it.
    return False


def migrate_keys(dry_run: bool = True, reverse: bool = False) -> dict:
    """Rename AgentSession Redis keys and hash fields.

    Args:
        dry_run: If True, log what would happen without making changes.
        reverse: If True, undo the migration (swap segments back).

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "migrated": 0,
        "skipped_already_migrated": 0,
        "skipped_index_keys": 0,
        "errors": 0,
    }

    field_renames = HASH_FIELD_RENAMES_REVERSE if reverse else HASH_FIELD_RENAMES
    direction = "reverse" if reverse else "forward"

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
    logger.info(f"Found {len(hash_keys)} AgentSession hash records ({direction} migration)")

    if not hash_keys:
        logger.info("No records to migrate.")
        return stats

    # Phase 2: Rename keys and hash fields
    class_set_key = "$Class:AgentSession"

    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            parts = key.split(b":")
            # Expected: [b'AgentSession', chat_id, job_id/id, parent_chat_session_id/parent_agent_session_id, parent_job_id/parent_chat_session_id, project_key, session_type]
            if len(parts) != 7:
                logger.warning(f"  Skipping key with unexpected segment count ({len(parts)}): {key_str}")
                stats["errors"] += 1
                continue

            # Check hash fields to determine if already migrated
            if not reverse:
                # Forward: check if 'id' hash field already exists (new format)
                has_new_field = redis_client.hexists(key, "id")
                has_old_field = redis_client.hexists(key, "job_id")
                if has_new_field and not has_old_field:
                    stats["skipped_already_migrated"] += 1
                    continue
            else:
                # Reverse: check if 'job_id' hash field already exists (old format)
                has_old_field = redis_client.hexists(key, "job_id")
                has_new_field = redis_client.hexists(key, "id")
                if has_old_field and not has_new_field:
                    stats["skipped_already_migrated"] += 1
                    continue

            # Construct new key by swapping segments 3 and 4
            new_parts = list(parts)
            new_parts[3], new_parts[4] = parts[4], parts[3]
            new_key = b":".join(new_parts)
            new_key_str = new_key.decode() if isinstance(new_key, bytes) else new_key

            logger.info(f"  Migrating: {key_str}")
            logger.info(f"         -> {new_key_str}")

            if not dry_run:
                pipe = redis_client.pipeline()

                # Rename the key (swap segments 3 and 4)
                if new_key != key:
                    pipe.rename(key, new_key)
                    # Update $Class:AgentSession set
                    pipe.srem(class_set_key, key_str)
                    pipe.sadd(class_set_key, new_key_str)

                pipe.execute()

                # Rename hash fields (must use new key now)
                target_key = new_key if new_key != key else key
                for old_field, new_field in field_renames.items():
                    old_val = redis_client.hget(target_key, old_field)
                    if old_val is not None:
                        redis_client.hset(target_key, new_field, old_val)
                        redis_client.hdel(target_key, old_field)
                        logger.info(f"    Hash field: {old_field} -> {new_field}")
            else:
                # Log hash field changes that would happen
                for old_field, new_field in field_renames.items():
                    old_val = redis_client.hget(key, old_field)
                    if old_val is not None:
                        logger.info(f"    Would rename hash field: {old_field} -> {new_field}")

            stats["migrated"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    # Phase 3: Rebuild indexes
    if not dry_run and stats["migrated"] > 0:
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
        description="Migrate AgentSession Redis key structure for KeyField rename"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Reverse the migration (undo key rename)",
    )
    args = parser.parse_args()

    mode_parts = []
    if args.dry_run:
        mode_parts.append("DRY RUN")
    if args.reverse:
        mode_parts.append("REVERSE")
    mode = " + ".join(mode_parts) if mode_parts else "LIVE"

    logger.info(f"=== AgentSession KeyField Rename Migration ({mode}) ===")

    stats = migrate_keys(dry_run=args.dry_run, reverse=args.reverse)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if not args.dry_run and stats["migrated"] > 0:
        logger.info(f"Successfully migrated {stats['migrated']} records.")
    elif args.dry_run and stats["migrated"] > 0:
        logger.info(f"Would migrate {stats['migrated']} records. Run without --dry-run to apply.")
    else:
        logger.info("No records needed migration.")

    if stats["errors"] > 0:
        logger.warning(f"{stats['errors']} error(s) occurred during migration.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
