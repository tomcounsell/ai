#!/usr/bin/env python3
"""Migrate AgentSession Redis field names for issue #473.

Renames:
  message_id -> telegram_message_id
  trigger_message_id -> telegram_message_key

For each AgentSession record in Redis:
1. Copy old field value to new field name
2. HDEL old field name from the Redis hash
3. Save the updated record

Usage:
  python scripts/migrate_agent_session_fields.py --dry-run
  python scripts/migrate_agent_session_fields.py
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FIELD_RENAMES = {
    "message_id": "telegram_message_id",
    "trigger_message_id": "telegram_message_key",
}


def migrate_field_names(dry_run: bool = True) -> dict:
    """Rename fields on all AgentSession records in Redis.

    Args:
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    from models.agent_session import AgentSession

    stats = {
        "total_sessions": 0,
        "migrated": 0,
        "skipped_no_data": 0,
        "errors": 0,
    }
    # Per-field stats
    for old_name in FIELD_RENAMES:
        stats[f"copied_{old_name}"] = 0

    all_sessions = AgentSession.query.all()
    stats["total_sessions"] = len(all_sessions)
    logger.info(f"Found {len(all_sessions)} AgentSession records")

    for session in all_sessions:
        try:
            changed = False
            for old_name, new_name in FIELD_RENAMES.items():
                # Read old field value directly from Redis hash
                old_val = None
                try:
                    old_val = getattr(session, old_name, None)
                except Exception:
                    pass

                # Also try reading from the Redis hash directly
                if old_val is None:
                    try:
                        import popoto

                        redis_client = popoto.redis_db.REDIS
                        key = session._key
                        raw = redis_client.hget(key, old_name)
                        if raw is not None:
                            old_val = raw.decode() if isinstance(raw, bytes) else raw
                    except Exception:
                        pass

                if old_val is None:
                    continue

                # Check if new field already has a value
                new_val = getattr(session, new_name, None)
                if new_val is not None:
                    continue

                stats[f"copied_{old_name}"] += 1
                changed = True

                if not dry_run:
                    setattr(session, new_name, old_val)

            if changed:
                stats["migrated"] += 1
                if not dry_run:
                    session.save()

                    # HDEL old field names from Redis hash
                    try:
                        import popoto

                        redis_client = popoto.redis_db.REDIS
                        key = session._key
                        for old_name in FIELD_RENAMES:
                            redis_client.hdel(key, old_name)
                    except Exception as e:
                        logger.warning(f"Failed to HDEL old fields from {session.job_id}: {e}")
            else:
                stats["skipped_no_data"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating session {session.job_id}: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate AgentSession field names in Redis")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== AgentSession Field Migration ({mode}) ===")

    stats = migrate_field_names(dry_run=args.dry_run)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if not args.dry_run and stats["migrated"] > 0:
        logger.info(
            f"Successfully migrated {stats['migrated']} sessions. "
            f"Old field names have been removed from Redis hashes."
        )
    elif args.dry_run and stats["migrated"] > 0:
        logger.info(
            f"Would migrate {stats['migrated']} sessions. Run without --dry-run to apply changes."
        )
    else:
        logger.info("No sessions needed migration.")


if __name__ == "__main__":
    main()
