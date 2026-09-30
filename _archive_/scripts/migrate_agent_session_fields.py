#!/usr/bin/env python3
"""Migrate AgentSession Redis hash field names for the job->agent_session rename.

Renames hash fields in existing Redis records:
  job_id -> agent_session_id
  parent_job_id -> parent_job_id
  stable_job_id -> stable_agent_session_id

Works at the raw Redis level because Popoto can't load records whose
AutoKeyField hash name doesn't match the model definition.

After renaming fields, re-saves each record to rebuild sorted sets
and field indices (IndexedField, SortedField).

Usage:
  python scripts/migrate_agent_session_fields.py --dry-run
  python scripts/migrate_agent_session_fields.py
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FIELD_RENAMES = {
    "job_id": "agent_session_id",
    "parent_job_id": "parent_job_id",
    "stable_job_id": "stable_agent_session_id",
}


def migrate_field_names(dry_run: bool = True) -> dict:
    """Rename hash fields on all AgentSession records in Redis.

    Operates at the raw Redis level:
    1. Scan for all AgentSession:* hash keys
    2. For each key, rename old hash fields to new names via HSET + HDEL
    3. Re-load and save via Popoto to rebuild indices

    Args:
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "migrated": 0,
        "skipped_already_migrated": 0,
        "re_indexed": 0,
        "errors": 0,
    }
    for old_name in FIELD_RENAMES:
        stats[f"renamed_{old_name}"] = 0

    # Find all AgentSession hash keys (exclude index/sorted-set keys)
    all_keys = redis_client.keys("AgentSession:*")
    hash_keys = [k for k in all_keys if b":_sorted_set:" not in k and b":_field_index:" not in k]
    stats["total_records"] = len(hash_keys)
    logger.info(f"Found {len(hash_keys)} AgentSession hash records")

    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            needs_rename = False

            for old_name, new_name in FIELD_RENAMES.items():
                old_val = redis_client.hget(key, old_name)
                if old_val is None:
                    continue

                # Check if new field already exists
                new_val = redis_client.hget(key, new_name)
                if new_val is not None:
                    continue

                needs_rename = True
                stats[f"renamed_{old_name}"] += 1
                logger.info(f"  {key_str}: {old_name} -> {new_name}")

                if not dry_run:
                    redis_client.hset(key, new_name, old_val)
                    redis_client.hdel(key, old_name)

            if needs_rename:
                stats["migrated"] += 1
            else:
                stats["skipped_already_migrated"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    # Phase 2: Re-save via Popoto to rebuild indices (creates new-format keys)
    if not dry_run and stats["migrated"] > 0:
        logger.info("Rebuilding Popoto indices by re-saving all records...")
        from models.agent_session import AgentSession

        try:
            sessions = AgentSession.query.all()
            for session in sessions:
                try:
                    session.save()
                    stats["re_indexed"] += 1
                except Exception as e:
                    logger.warning(f"Failed to re-index session {session.agent_session_id}: {e}")
        except Exception as e:
            logger.error(f"Failed to rebuild indices: {e}")

        # Phase 3: Delete old-format keys
        # Popoto orders KeyFields alphabetically by name. Renaming job_id
        # to agent_session_id changed the key order (agent_session_id < chat_id
        # but job_id > chat_id). The re-save created new keys; delete the old ones.
        import re

        uuid_re = re.compile(rb"^[0-9a-f]{32}$")
        refreshed = redis_client.keys("AgentSession:*")
        old_deleted = 0
        for key in refreshed:
            if b":_sorted_set:" in key or b":_field_index:" in key:
                continue
            parts = key.split(b":")
            # New-format keys have UUID (agent_session_id) as first value
            if len(parts) > 1 and not uuid_re.match(parts[1]):
                redis_client.delete(key)
                old_deleted += 1
        stats["old_keys_deleted"] = old_deleted
        if old_deleted:
            logger.info(f"Deleted {old_deleted} old-format Redis keys")

        # Clean stale Popoto index references
        try:
            AgentSession.query.keys(clean=True)
        except Exception:
            pass

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migrate AgentSession Redis field names (job -> agent_session)"
    )
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
            f"Successfully migrated {stats['migrated']} records. "
            f"Re-indexed {stats['re_indexed']} records."
        )
    elif args.dry_run and stats["migrated"] > 0:
        logger.info(f"Would migrate {stats['migrated']} records. Run without --dry-run to apply.")
    else:
        logger.info("No records needed migration.")


if __name__ == "__main__":
    main()
