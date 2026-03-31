#!/usr/bin/env python3
"""Migrate AgentSession persona values for issue #599.

Migrates session_mode values from ChatMode to PersonaType:
  "qa"  -> "teammate"
  "pm"  -> "project-manager"
  "dev" -> "developer"

Also cleans up legacy fields:
  - HDEL _qa_mode_legacy from all session hashes
  - HDEL qa_mode from all session hashes
  - Delete old qa_metrics:* Redis keys

Usage:
  python scripts/migrate_persona_values.py --dry-run
  python scripts/migrate_persona_values.py
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

VALUE_MIGRATIONS = {
    "qa": "teammate",
    "pm": "project-manager",
    "dev": "developer",
}

LEGACY_FIELDS_TO_DELETE = ["_qa_mode_legacy", "qa_mode"]


def migrate_persona_values(dry_run: bool = True) -> dict:
    """Migrate session_mode values and clean up legacy fields.

    Args:
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    from models.agent_session import AgentSession

    stats = {
        "total_sessions": 0,
        "session_mode_migrated": 0,
        "legacy_fields_cleaned": 0,
        "already_migrated": 0,
        "no_session_mode": 0,
        "qa_metrics_keys_deleted": 0,
        "errors": 0,
    }

    all_sessions = AgentSession.query.all()
    stats["total_sessions"] = len(all_sessions)
    logger.info(f"Found {len(all_sessions)} AgentSession records")

    for session in all_sessions:
        try:
            changed = False

            # Migrate session_mode value
            current_mode = getattr(session, "session_mode", None)
            if current_mode is None:
                stats["no_session_mode"] += 1
            elif current_mode in VALUE_MIGRATIONS:
                new_value = VALUE_MIGRATIONS[current_mode]
                logger.info(
                    f"  Session {session.job_id}: session_mode {current_mode!r} -> {new_value!r}"
                )
                if not dry_run:
                    session.session_mode = new_value
                changed = True
                stats["session_mode_migrated"] += 1
            elif current_mode in VALUE_MIGRATIONS.values():
                stats["already_migrated"] += 1
            else:
                logger.warning(
                    f"  Session {session.job_id}: unknown session_mode {current_mode!r}, skipping"
                )

            # Clean up legacy fields from Redis hash
            try:
                import popoto

                redis_client = popoto.redis_db.REDIS
                key = session._key
                for field_name in LEGACY_FIELDS_TO_DELETE:
                    if not dry_run:
                        removed = redis_client.hdel(key, field_name)
                        if removed:
                            stats["legacy_fields_cleaned"] += 1
                            logger.info(
                                f"  Session {session.job_id}: removed legacy field {field_name!r}"
                            )
                    else:
                        exists = redis_client.hexists(key, field_name)
                        if exists:
                            stats["legacy_fields_cleaned"] += 1
                            logger.info(
                                f"  Session {session.job_id}: would remove legacy field "
                                f"{field_name!r}"
                            )
            except Exception as e:
                logger.warning(f"  Session {session.job_id}: legacy field cleanup failed: {e}")

            if changed and not dry_run:
                session.save()

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating session {session.job_id}: {e}")

    # Delete old qa_metrics:* Redis keys
    try:
        import popoto

        redis_client = popoto.redis_db.REDIS
        qa_keys = list(redis_client.scan_iter("qa_metrics:*"))
        if qa_keys:
            logger.info(f"Found {len(qa_keys)} old qa_metrics:* keys to delete")
            if not dry_run:
                redis_client.delete(*qa_keys)
            stats["qa_metrics_keys_deleted"] = len(qa_keys)
        else:
            logger.info("No old qa_metrics:* keys found")
    except Exception as e:
        logger.warning(f"Failed to clean up qa_metrics keys: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate AgentSession persona values in Redis")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== Persona Value Migration ({mode}) ===")

    stats = migrate_persona_values(dry_run=args.dry_run)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if not args.dry_run and stats["session_mode_migrated"] > 0:
        logger.info(
            f"Successfully migrated {stats['session_mode_migrated']} sessions. "
            f"Legacy fields cleaned: {stats['legacy_fields_cleaned']}."
        )
    elif args.dry_run and stats["session_mode_migrated"] > 0:
        logger.info(
            f"Would migrate {stats['session_mode_migrated']} sessions. "
            f"Run without --dry-run to apply changes."
        )
    else:
        logger.info("No sessions needed migration.")


if __name__ == "__main__":
    main()
