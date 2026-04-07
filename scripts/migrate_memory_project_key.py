#!/usr/bin/env python3
"""Migrate Memory records: re-key project_key from "dm" to "valor" for agent/hook sources.

All Memory records created by Claude Code hooks between 2026-03-24 and 2026-04-07
were saved with project_key="dm" instead of the correct "valor" partition. This
happened because the hooks called _get_project_key() with no cwd argument, falling
through to DEFAULT_PROJECT_KEY="dm" which is semantically reserved for Telegram DMs.

This script:
1. Scans all Memory:* Redis keys with project_key segment "dm"
2. Separates Telegram DM records (source="human" AND agent_id="dm") from
   mislabeled agent/hook records
3. For mislabeled records: RENAME key replacing :dm: with :valor:, update
   project_key and agent_id hash fields, rebuild indexes
4. Supports --dry-run flag (default behavior)
5. Idempotent: skips keys already under "valor" partition

Key structure: Memory:{memory_id}:{agent_id}:{project_key}
Both agent_id and project_key are KeyFields embedded in the Redis key.

Usage:
  python scripts/migrate_memory_project_key.py --dry-run   # preview
  python scripts/migrate_memory_project_key.py              # apply

IMPORTANT: The bridge/worker does not need to be stopped for this migration,
but avoid running it during high-traffic periods to minimize RENAME conflicts.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Keys to skip (index/sorted-set infrastructure keys)
SKIP_PATTERNS = (b":_sorted_set:", b":_field_index:", b":bloom:", b":bm25:")

# Source values that indicate Telegram DM origin (keep as "dm")
# A record is a genuine DM if source="human" AND agent_id="dm"
# Agent/hook records have agent_id = a session identifier or project key

SOURCE_HUMAN = "human"

TARGET_FROM = "dm"
TARGET_TO = "valor"


def _is_index_key(key: bytes) -> bool:
    """Check if a Redis key is a Popoto index/bloom/bm25 key (not a data record)."""
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


def _is_telegram_dm(redis_client, key: bytes) -> bool:
    """Return True if this Memory record is a genuine Telegram DM.

    A genuine DM record has source="human" AND agent_id="dm".
    This distinguishes real DMs from mislabeled hook-created records.
    """
    source = _decode_value(redis_client.hget(key, "source"))
    agent_id = _decode_value(redis_client.hget(key, "agent_id"))
    return source == SOURCE_HUMAN and agent_id == TARGET_FROM


def migrate(dry_run: bool = True) -> dict:
    """Re-key Memory records from project_key="dm" to project_key="valor".

    Args:
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_scanned": 0,
        "skipped_index_keys": 0,
        "skipped_already_migrated": 0,
        "skipped_no_dm_segment": 0,
        "kept_as_dm_telegram": 0,
        "migrated_to_valor": 0,
        "errors": 0,
    }

    # Phase 1: Find all Memory hash keys with :dm: segment
    cursor = 0
    all_keys = []
    while True:
        cursor, keys = redis_client.scan(cursor, match="Memory:*:dm:*", count=500)
        all_keys.extend(keys)
        if cursor == 0:
            break

    # Filter out Popoto infrastructure keys
    hash_keys = [k for k in all_keys if not _is_index_key(k)]
    stats["skipped_index_keys"] = len(all_keys) - len(hash_keys)
    stats["total_scanned"] = len(hash_keys)

    logger.info(f"Found {stats['total_scanned']} Memory records with project_key segment 'dm'")

    if not hash_keys:
        logger.info("No records to migrate.")
        return stats

    # Phase 2: Classify and rename
    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            # Check if already migrated (should not happen given scan pattern, but be safe)
            if f":{TARGET_TO}:" in key_str:
                stats["skipped_already_migrated"] += 1
                continue

            # Check if dm segment is present as a project_key segment
            # Key structure: Memory:{memory_id}:{agent_id}:{project_key}
            # The dm segment should be at position 3 (0-indexed) when split by ':'
            parts = key_str.split(":")
            if len(parts) < 4:
                stats["skipped_no_dm_segment"] += 1
                continue

            # The project_key is the LAST segment
            project_key_segment = parts[-1]
            if project_key_segment != TARGET_FROM:
                stats["skipped_no_dm_segment"] += 1
                continue

            # Check if this is a genuine Telegram DM record
            if _is_telegram_dm(redis_client, key):
                logger.debug(f"  KEEP (genuine DM): {key_str}")
                stats["kept_as_dm_telegram"] += 1
                continue

            # Build new key: replace last :dm with :valor
            # agent_id (second-to-last segment) may also be "dm" for mislabeled records
            agent_id_segment = parts[-2]
            new_parts = parts[:-1]  # everything except project_key
            new_parts.append(TARGET_TO)  # new project_key

            # Also update agent_id if it was also "dm" (mislabeled)
            if agent_id_segment == TARGET_FROM:
                new_parts[-2] = TARGET_TO  # update agent_id segment too

            new_key_str = ":".join(new_parts)
            new_key = new_key_str.encode() if isinstance(key, bytes) else new_key_str

            source = _decode_value(redis_client.hget(key, "source"))
            logger.info(
                f"  RENAME: {key_str} -> {new_key_str} "
                f"(source={source}, agent_id={agent_id_segment})"
            )

            if not dry_run:
                # Atomic rename
                redis_client.rename(key, new_key)
                # Update hash fields to match new key values
                redis_client.hset(new_key, "project_key", TARGET_TO)
                if agent_id_segment == TARGET_FROM:
                    redis_client.hset(new_key, "agent_id", TARGET_TO)

            stats["migrated_to_valor"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    # Phase 3: Rebuild indexes
    if not dry_run and stats["migrated_to_valor"] > 0:
        logger.info("Rebuilding Popoto indexes for Memory model...")
        try:
            from models.memory import Memory

            Memory.rebuild_indexes()
            logger.info("Index rebuild complete.")
        except Exception as e:
            logger.error(f"Failed to rebuild indexes: {e}")
            stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Migrate Memory records: re-key project_key from 'dm' to 'valor' "
            "for agent/hook-sourced records. Genuine Telegram DM records are preserved."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Log what would happen without making changes (default: True)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply the migration (overrides --dry-run)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info(f"=== Memory project_key Migration: 'dm' -> 'valor' ({mode}) ===")
    if dry_run:
        logger.info("Run with --apply to execute the migration.")

    stats = migrate(dry_run=dry_run)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if not dry_run and stats["migrated_to_valor"] > 0:
        logger.info(
            f"Successfully migrated {stats['migrated_to_valor']} records to 'valor' partition."
        )
    elif dry_run and stats["migrated_to_valor"] > 0:
        logger.info(
            f"Would migrate {stats['migrated_to_valor']} records to 'valor' partition. "
            "Run with --apply to execute."
        )
    else:
        logger.info("No records needed migration.")

    if stats["kept_as_dm_telegram"] > 0:
        logger.info(
            f"Preserved {stats['kept_as_dm_telegram']} genuine Telegram DM records as 'dm'."
        )

    if stats["errors"] > 0:
        logger.warning(f"{stats['errors']} error(s) occurred during migration.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
