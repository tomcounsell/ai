#!/usr/bin/env python3
"""Migrate AgentSession: rename session_type KeyField from "chat" to "pm" or "teammate".

session_type is a Popoto KeyField, meaning its value is embedded in the Redis hash key
string (e.g., AgentSession:{id}:chat:{project_key}:...). Changing from "chat" to "pm"
or "teammate" requires Redis key RENAME operations.

Steps:
1. SCAN all AgentSession:* keys, skip index keys (_sorted_set:, _field_index:)
2. For each key containing :chat: segment:
   a. Read the session_mode hash field
   b. If session_mode == "teammate" -> RENAME key replacing :chat: with :teammate:
   c. Otherwise -> RENAME key replacing :chat: with :pm:
   d. Update the session_type hash field value accordingly
3. Call AgentSession.rebuild_indexes() after all renames
4. Support --dry-run flag
5. Idempotent: skip keys that already have :pm: or :teammate:

Usage:
  python scripts/migrate_session_type_chat_to_pm.py --dry-run
  python scripts/migrate_session_type_chat_to_pm.py

IMPORTANT: Stop the bridge before running this script.
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


def _decode_value(val) -> str | None:
    """Decode a Redis value to string, handling Popoto byte encoding."""
    if val is None:
        return None
    if isinstance(val, bytes):
        try:
            decoded = val.decode("utf-8")
        except UnicodeDecodeError:
            try:
                decoded = val.decode("latin-1")
            except Exception:
                return None
        # Popoto may prefix values with non-ASCII bytes; strip non-alphanumeric prefix
        return decoded.lstrip("\x00\xa3\xc2").strip()
    return str(val)


def migrate(dry_run: bool = True) -> dict:
    """Rename Redis keys and update session_type field values.

    Args:
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "renamed_to_pm": 0,
        "renamed_to_teammate": 0,
        "skipped_already_migrated": 0,
        "skipped_no_chat_segment": 0,
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

    # Phase 2: Rename keys containing :chat: segment
    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            # Check if key contains :chat: segment
            if b":chat:" not in key and ":chat:" not in key_str:
                # Check if already migrated
                if (
                    b":pm:" in key
                    or b":teammate:" in key
                    or ":pm:" in key_str
                    or ":teammate:" in key_str
                ):
                    stats["skipped_already_migrated"] += 1
                else:
                    stats["skipped_no_chat_segment"] += 1
                continue

            # Determine target: teammate or pm based on session_mode
            session_mode = _decode_value(redis_client.hget(key, "session_mode"))
            if session_mode == "teammate":
                new_type = "teammate"
            else:
                new_type = "pm"

            # Build new key by replacing :chat: with :pm: or :teammate:
            new_key_str = key_str.replace(":chat:", f":{new_type}:")
            new_key = new_key_str.encode() if isinstance(key, bytes) else new_key_str

            logger.info(f"  RENAME: {key_str} -> {new_key_str} (session_mode={session_mode})")

            if not dry_run:
                # Atomic rename + field update
                redis_client.rename(key, new_key)
                redis_client.hset(new_key, "session_type", new_type)

            if new_type == "teammate":
                stats["renamed_to_teammate"] += 1
            else:
                stats["renamed_to_pm"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    # Phase 3: Rebuild indexes
    total_renamed = stats["renamed_to_pm"] + stats["renamed_to_teammate"]
    if not dry_run and total_renamed > 0:
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
        description="Migrate AgentSession session_type KeyField: chat -> pm/teammate"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== AgentSession SessionType KeyField Migration ({mode}) ===")
    logger.info("Migrating session_type: 'chat' -> 'pm' or 'teammate'")

    stats = migrate(dry_run=args.dry_run)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    total_renamed = stats["renamed_to_pm"] + stats["renamed_to_teammate"]
    if not args.dry_run and total_renamed > 0:
        logger.info(
            f"Successfully renamed {total_renamed} keys across {stats['total_records']} records."
        )
    elif args.dry_run and total_renamed > 0:
        logger.info(
            f"Would rename {total_renamed} keys across {stats['total_records']} records."
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
