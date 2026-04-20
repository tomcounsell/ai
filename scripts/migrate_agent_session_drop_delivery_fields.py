#!/usr/bin/env python3
"""HDEL dead delivery_* fields from all AgentSession Redis hashes (#1073).

Drops three residual hash fields — ``delivery_text``, ``delivery_action``,
``delivery_emoji`` — from every existing ``AgentSession:*`` hash record.

Why raw Redis instead of Popoto:
  These three fields no longer exist on the Popoto model (their ``Field()``
  declarations were removed as part of #1073). Calling ``instance.save()``
  would NEVER HDEL an undeclared field — it only writes declared fields.
  Raw ``redis_client.hdel`` is the correct primitive for dropping orphaned
  hash keys. The ``PreToolUse:Bash`` validator blocks raw Redis only in
  interactive shell commands; Python scripts run via ``python scripts/…``
  are exempt. This mirrors the pattern in
  ``scripts/migrate_agent_session_fields.py`` (PR #392 lineage).

None of the three fields were ``IndexedField``/``SortedField``, so no
re-save / index rebuild is needed — plain HDEL is sufficient.

``HDEL`` on a missing field is a no-op, so this script is idempotent.

Usage:
  python scripts/migrate_agent_session_drop_delivery_fields.py --dry-run
  python scripts/migrate_agent_session_drop_delivery_fields.py
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEAD_FIELDS = ("delivery_text", "delivery_action", "delivery_emoji")


def drop_delivery_fields(dry_run: bool = True) -> dict:
    """HDEL the three delivery_* fields from all AgentSession:* hashes.

    Args:
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "records_with_dead_fields": 0,
        "records_clean": 0,
        "errors": 0,
    }
    for field in DEAD_FIELDS:
        stats[f"hdel_{field}"] = 0

    # Find all AgentSession hash keys (exclude index/sorted-set keys)
    all_keys = redis_client.keys("AgentSession:*")
    hash_keys = [k for k in all_keys if b":_sorted_set:" not in k and b":_field_index:" not in k]
    stats["total_records"] = len(hash_keys)
    logger.info(f"Found {len(hash_keys)} AgentSession hash records")

    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            present_fields = []
            for field in DEAD_FIELDS:
                val = redis_client.hget(key, field)
                if val is not None:
                    present_fields.append(field)

            if not present_fields:
                stats["records_clean"] += 1
                continue

            stats["records_with_dead_fields"] += 1
            logger.info(f"  {key_str}: dropping {present_fields}")

            if not dry_run:
                for field in present_fields:
                    redis_client.hdel(key, field)
                    stats[f"hdel_{field}"] += 1
            else:
                for field in present_fields:
                    stats[f"hdel_{field}"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error processing {key_str}: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Drop dead delivery_* fields from AgentSession Redis hashes (#1073)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== AgentSession delivery_* Field Drop ({mode}) ===")

    stats = drop_delivery_fields(dry_run=args.dry_run)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if not args.dry_run and stats["records_with_dead_fields"] > 0:
        logger.info(f"Successfully cleaned {stats['records_with_dead_fields']} records.")
    elif args.dry_run and stats["records_with_dead_fields"] > 0:
        logger.info(
            f"Would clean {stats['records_with_dead_fields']} records. "
            "Run without --dry-run to apply."
        )
    else:
        logger.info("No records needed cleaning (all clean or empty).")


if __name__ == "__main__":
    main()
