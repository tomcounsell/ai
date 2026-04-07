#!/usr/bin/env python3
"""Migrate Memory records: rename project_key KeyField from "dm" to "valor".

project_key is a Popoto KeyField, meaning its value is embedded in the Redis hash key
string (e.g., Memory:{id}:dm:{...}). Changing from "dm" to "valor" requires Redis key
RENAME operations.

Background:
  On machines where ~/Desktop/Valor/projects.json was missing (or where recall/ingest
  were called without a cwd argument), all Memory records accumulated under
  project_key="dm" -- the old DEFAULT_PROJECT_KEY value. This migration renames those
  records to project_key="valor" so that Claude Code session memory recall works
  correctly.

  On this machine (no Telegram bridge running), ALL "dm" records were created by
  Claude Code hooks, not by Telegram DM conversations. It is safe to migrate all of
  them unconditionally. The optional --filter-source flag is wired for future cross-
  machine use but is not required here.

Steps:
1. SCAN all Memory:* keys, skip index keys (_sorted_set:, _field_index:)
2. For each key containing :dm: segment:
   a. Optionally filter by source field if --filter-source is given
   b. RENAME key replacing :dm: with :valor:
   c. Update the project_key hash field value to "valor"
3. Call Memory.rebuild_indexes() after all renames
4. Support --dry-run flag
5. Idempotent: skip keys already having :valor:

Usage:
  python scripts/migrate_memory_project_key.py --dry-run
  python scripts/migrate_memory_project_key.py
  python scripts/migrate_memory_project_key.py --filter-source claude_code  # cross-machine use

IMPORTANT: Stop the bridge/worker before running this script.
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


def migrate(dry_run: bool = True, filter_source: str | None = None) -> dict:
    """Rename Redis keys and update project_key field values from "dm" to "valor".

    Args:
        dry_run: If True, log what would happen without making changes.
        filter_source: If set, only migrate records with this source field value.
                       Use for cross-machine runs where Telegram DM records exist.

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "renamed_to_valor": 0,
        "skipped_already_migrated": 0,
        "skipped_no_dm_segment": 0,
        "skipped_source_filter": 0,
        "skipped_index_keys": 0,
        "errors": 0,
    }

    # Phase 1: Find all Memory hash keys
    cursor = 0
    all_keys = []
    while True:
        cursor, keys = redis_client.scan(cursor, match="Memory:*", count=500)
        all_keys.extend(keys)
        if cursor == 0:
            break

    hash_keys = [k for k in all_keys if not _is_index_key(k)]
    stats["skipped_index_keys"] = len(all_keys) - len(hash_keys)
    stats["total_records"] = len(hash_keys)
    logger.info(f"Found {stats['total_records']} Memory hash records")

    if not hash_keys:
        logger.info("No records to migrate.")
        return stats

    # Phase 2: Rename keys containing :dm: segment
    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            # Skip already-migrated keys
            if b":valor:" in key or ":valor:" in key_str:
                stats["skipped_already_migrated"] += 1
                continue

            # Check if key contains :dm: segment
            if b":dm:" not in key and ":dm:" not in key_str:
                stats["skipped_no_dm_segment"] += 1
                continue

            # Optional source filter for cross-machine use
            if filter_source:
                source = _decode_value(redis_client.hget(key, "source"))
                if source != filter_source:
                    stats["skipped_source_filter"] += 1
                    continue

            # Build new key by replacing :dm: with :valor:
            new_key_str = key_str.replace(":dm:", ":valor:")
            new_key = new_key_str.encode() if isinstance(key, bytes) else new_key_str

            logger.info(f"  RENAME: {key_str} -> {new_key_str}")

            if not dry_run:
                # Atomic rename + field update
                redis_client.rename(key, new_key)
                redis_client.hset(new_key, "project_key", "valor")

            stats["renamed_to_valor"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    # Phase 3: Rebuild indexes
    if not dry_run and stats["renamed_to_valor"] > 0:
        logger.info("Rebuilding Popoto indexes...")
        try:
            from models.memory import Memory

            Memory.rebuild_indexes()
            logger.info("Index rebuild complete.")
        except Exception as e:
            logger.error(f"Failed to rebuild indexes: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Migrate Memory project_key KeyField: "dm" -> "valor"'
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes",
    )
    parser.add_argument(
        "--filter-source",
        metavar="SOURCE",
        help=(
            "Only migrate records with this source field value. "
            "Not required on machines without Telegram bridge "
            "(all dm records were created by Claude Code hooks)."
        ),
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== Memory project_key Migration ({mode}) ===")
    logger.info('Migrating project_key: "dm" -> "valor"')
    if args.filter_source:
        logger.info(f"  Source filter: {args.filter_source}")
    else:
        logger.info("  Source filter: none (migrating ALL dm records unconditionally)")
        logger.info(
            "  Note: Safe on this machine -- no Telegram bridge means all "
            '"dm" records came from Claude Code hooks, not Telegram DMs.'
        )

    stats = migrate(dry_run=args.dry_run, filter_source=args.filter_source)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if not args.dry_run and stats["renamed_to_valor"] > 0:
        logger.info(
            f"Successfully renamed {stats['renamed_to_valor']} keys "
            f"across {stats['total_records']} records."
        )
    elif args.dry_run and stats["renamed_to_valor"] > 0:
        logger.info(
            f"Would rename {stats['renamed_to_valor']} keys "
            f"across {stats['total_records']} records."
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
