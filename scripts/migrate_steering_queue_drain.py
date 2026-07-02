#!/usr/bin/env python3
"""Migrate AgentSession: drain queued_steering_messages into the Redis steering list.

Issue #1817 (A1): ``queued_steering_messages`` (a Popoto ListField on
AgentSession that used a read-modify-write instance method) is being removed
in favor of the Redis-list steering inbox in ``agent/steering.py``
(``steering:{session_id}``, atomic RPUSH/LPOP). This script finds any
AgentSession hash records with residual ``queued_steering_messages`` content
-- messages staged before the cutover that the old turn-boundary reader never
drained -- and RPUSHes each entry onto the Redis steering list via
``agent.steering.push_steering_message``, then deletes the stale hash field.

Idempotent: re-running after --apply reports zero remaining records to
migrate. The field is no longer declared on the AgentSession model, so this
script talks to the raw Redis hash directly (the ORM has nothing to bind the
field to) -- the same idiom used by
``scripts/migrate_unify_parent_session_field.py``.

Usage:
  python scripts/migrate_steering_queue_drain.py            # dry-run (default)
  python scripts/migrate_steering_queue_drain.py --apply    # commit changes
"""

import argparse
import json
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
    """Drain residual queued_steering_messages into the Redis steering list.

    Args:
        apply: If False (default), log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    from agent.steering import push_steering_message

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "drained": 0,
        "messages_migrated": 0,
        "already_clean": 0,
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
            if not redis_client.hexists(key, "queued_steering_messages"):
                stats["already_clean"] += 1
                continue

            raw_val = redis_client.hget(key, "queued_steering_messages")
            if raw_val in (None, b"", ""):
                stats["already_clean"] += 1
                if apply:
                    redis_client.hdel(key, "queued_steering_messages")
                continue

            try:
                entries = json.loads(raw_val)
            except (json.JSONDecodeError, TypeError):
                entries = []

            if not isinstance(entries, list) or not entries:
                stats["already_clean"] += 1
                if apply:
                    redis_client.hdel(key, "queued_steering_messages")
                continue

            # The Redis steering list is namespaced by session_id (the Popoto
            # field), not the hash key or agent_session_id -- match
            # agent/steering.py's _queue_key() convention exactly.
            session_id_raw = redis_client.hget(key, "session_id")
            session_id = (
                session_id_raw.decode() if isinstance(session_id_raw, bytes) else session_id_raw
            ) or key_str.split(":", 1)[-1]

            logger.info(
                f"  Draining {len(entries)} residual steering message(s) from {key_str} "
                f"into steering:{session_id}"
            )
            if apply:
                # Deliberate at-least-once: push-all then hdel is not atomic, so a
                # death between the two re-delivers these entries on the next --apply
                # run. Duplicating an ephemeral steer is preferable to losing a human
                # course-correction.
                for text in entries:
                    push_steering_message(session_id, str(text), "migration-drain")
                redis_client.hdel(key, "queued_steering_messages")
            stats["drained"] += 1
            stats["messages_migrated"] += len(entries)
        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drain AgentSession.queued_steering_messages into the Redis steering list"
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
    logger.info(f"=== AgentSession Steering Queue Drain ({mode}) ===")

    stats = migrate(apply=apply)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if not apply and stats["drained"] > 0:
        logger.info(
            f"Would drain {stats['messages_migrated']} message(s) across "
            f"{stats['drained']} record(s). Run with --apply to commit."
        )
    elif apply and stats["drained"] > 0:
        logger.info(
            f"Drained {stats['messages_migrated']} message(s) from {stats['drained']} record(s). "
            "Re-run --dry-run to confirm 0 to migrate."
        )
    else:
        logger.info("0 to migrate.")

    if stats["errors"] > 0:
        logger.warning(f"{stats['errors']} error(s) occurred during migration.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
