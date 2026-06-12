#!/usr/bin/env python3
"""Merge a Dev group's TelegramMessage records onto the Eng chat_id.

Operator-optional migration: run this when consolidating a separate "Dev" Telegram
group that is being retired in favour of the main "Eng" group.

Steps:
1. Guard: worker must be idle (last_worker_connected mtime older than threshold)
2. Guard: email bridge must not be running
3. SCAN all TelegramMessage:* keys partitioned by dev_chat_id
4. For each TelegramMessage record under the Dev chat_id:
   a. EXISTS-check on the new Eng key — skip (never clobber) if collision
   b. RENAME key replacing dev_chat_id segment with eng_chat_id
   c. Update chat_id hash field value to eng_chat_id
5. Chat rename: create Eng Chat first (ORM), verify exists, THEN delete Dev Chat
6. Call TelegramMessage.rebuild_indexes() after all renames
7. Pre/post count assertion via ORM query path (NOT raw key counts)
8. Support --dry-run and --project flags
9. Idempotent: skip keys already bearing the eng_chat_id segment

Usage:
  python scripts/merge_dev_chat_into_eng.py --dev-chat-id -100123 --eng-chat-id -100456 --dry-run
  python scripts/merge_dev_chat_into_eng.py --dev-chat-id -100123 --eng-chat-id -100456

Project-scoped for test isolation:
  python scripts/merge_dev_chat_into_eng.py --dev-chat-id DEV_ID --eng-chat-id ENG_ID \\
      --project test-myproject

IMPORTANT:
  - Stop the bridge and worker before running this script.
  - TelegramMessage has a SortedField partitioned by chat_id; rebuild_indexes()
    is mandatory after renames to repartition the timestamp index.
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Lazy module-level imports — imported here so tests can patch the names on
# this module (e.g. `patch("scripts.merge_dev_chat_into_eng.TelegramMessage")`).
# The try/except prevents import failures when popoto is not yet connected
# (e.g., during static analysis or collection before the test DB fixture runs).
try:
    from models.chat import Chat
    from models.telegram import TelegramMessage
except Exception:  # pragma: no cover
    Chat = None  # type: ignore[assignment]
    TelegramMessage = None  # type: ignore[assignment]

# Keys to skip (index/sorted-set infrastructure keys)
SKIP_PATTERNS = (b":_sorted_set:", b":_field_index:")

# Worker heartbeat threshold: 2× write interval
# AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300 (from agent/session_health.py:206)
WORKER_HEARTBEAT_THRESHOLD = 600  # seconds


def _is_index_key(key: bytes) -> bool:
    """Check if a Redis key is a Popoto index key (not a data record)."""
    return any(p in key for p in SKIP_PATTERNS)


def _check_worker_not_running() -> None:
    """Guard: sys.exit(1) if worker appears to be live.

    Two signals:
    1. last_worker_connected mtime is fresh (< WORKER_HEARTBEAT_THRESHOLD seconds ago)
    2. pgrep -f 'python -m worker' returns a live PID
    """
    heartbeat_file = Path(__file__).parent.parent / "data" / "last_worker_connected"

    if heartbeat_file.exists():
        now = time.time()
        mtime = heartbeat_file.stat().st_mtime
        age = now - mtime
        if age < WORKER_HEARTBEAT_THRESHOLD:
            logger.error(
                f"Worker heartbeat is fresh ({age:.0f}s ago, threshold={WORKER_HEARTBEAT_THRESHOLD}s). "
                "Stop the worker before running this migration.\n"
                f"  Heartbeat file: {heartbeat_file}\n"
                "  Stop command: ./scripts/valor-service.sh worker-stop"
            )
            sys.exit(1)
        else:
            logger.info(f"Worker heartbeat is stale ({age:.0f}s ago). Worker appears stopped.")
    else:
        logger.info("No worker heartbeat file found. Worker appears never started or already stopped.")

    # Secondary check via pgrep
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python -m worker"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split()
            live_pids = []
            for pid_str in pids:
                try:
                    pid = int(pid_str)
                    os.kill(pid, 0)
                    live_pids.append(pid)
                except (ValueError, ProcessLookupError, PermissionError):
                    pass
            if live_pids:
                logger.error(
                    f"Worker process is still running (PID(s): {live_pids}). "
                    "Stop the worker before running this migration.\n"
                    "  Stop command: ./scripts/valor-service.sh worker-stop"
                )
                sys.exit(1)
            else:
                logger.info("pgrep found stale PIDs (already dead). Worker appears stopped.")
        else:
            logger.info("pgrep found no running worker process.")
    except FileNotFoundError:
        logger.warning("pgrep not found; skipping process-based liveness check.")


def _check_email_bridge_not_running() -> None:
    """Guard: sys.exit(1) if email bridge appears to be live.

    Checks pgrep for 'bridge.email_bridge' and fresh email:last_poll_ts Redis key.
    """
    # Check via pgrep
    try:
        result = subprocess.run(
            ["pgrep", "-f", "bridge.email_bridge"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split()
            live_pids = []
            for pid_str in pids:
                try:
                    pid = int(pid_str)
                    os.kill(pid, 0)
                    live_pids.append(pid)
                except (ValueError, ProcessLookupError, PermissionError):
                    pass
            if live_pids:
                logger.error(
                    f"Email bridge process is still running (PID(s): {live_pids}). "
                    "Stop the email bridge before running this migration.\n"
                    "  Stop command: ./scripts/valor-service.sh email-stop"
                )
                sys.exit(1)
            else:
                logger.info("pgrep found stale email bridge PIDs (already dead).")
        else:
            logger.info("No email bridge process found via pgrep.")
    except FileNotFoundError:
        logger.warning("pgrep not found; skipping pgrep-based email bridge check.")

    # Check via email:last_poll_ts Redis key freshness
    try:
        import popoto

        redis_client = popoto.redis_db.get_REDIS_DB()
        poll_ts_raw = redis_client.get("email:last_poll_ts")
        if poll_ts_raw is not None:
            try:
                poll_ts = float(poll_ts_raw)
                age = time.time() - poll_ts
                # Email bridge polls every 30s (IMAP_POLL_INTERVAL); fresh = < 120s
                if age < 120:
                    logger.error(
                        f"email:last_poll_ts is fresh ({age:.0f}s ago). Email bridge appears running. "
                        "Stop the email bridge before running this migration.\n"
                        "  Stop command: ./scripts/valor-service.sh email-stop"
                    )
                    sys.exit(1)
                else:
                    logger.info(f"email:last_poll_ts is stale ({age:.0f}s ago). Email bridge appears stopped.")
            except (ValueError, TypeError):
                logger.warning("Could not parse email:last_poll_ts value; skipping Redis freshness check.")
        else:
            logger.info("email:last_poll_ts not found in Redis. Email bridge appears never started.")
    except Exception as e:
        logger.warning(f"Could not check email:last_poll_ts in Redis: {e}")


def migrate(
    dev_chat_id: str,
    eng_chat_id: str,
    project_key: str | None = None,
    dry_run: bool = True,
) -> dict:
    """Re-key TelegramMessage records from dev_chat_id onto eng_chat_id.

    Args:
        dev_chat_id: The source Dev chat ID (string, as stored in Redis KeyField).
        eng_chat_id: The target Eng chat ID (string, as stored in Redis KeyField).
        project_key: Optional project scope for test isolation.
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    # Use module-level imports (may have been patched by tests)
    _Chat = Chat
    _TelegramMessage = TelegramMessage

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_dev_records": 0,
        "renamed": 0,
        "skipped_already_migrated": 0,
        "skipped_collision": 0,
        "errors": 0,
    }

    # --- Phase 0: Pre-flight counts via ORM query path ---
    pre_dev_count = _TelegramMessage.query.filter(chat_id=dev_chat_id).count()
    pre_eng_count = _TelegramMessage.query.filter(chat_id=eng_chat_id).count()
    logger.info(
        f"Pre-migration counts: dev_chat_id={dev_chat_id!r}: {pre_dev_count} records, "
        f"eng_chat_id={eng_chat_id!r}: {pre_eng_count} records"
    )

    # --- Phase 1: Find all TelegramMessage hash keys for the Dev chat ---
    cursor = 0
    all_keys = []
    while True:
        cursor, keys = redis_client.scan(cursor, match="TelegramMessage:*", count=500)
        all_keys.extend(keys)
        if cursor == 0:
            break

    hash_keys = [k for k in all_keys if not _is_index_key(k)]

    # Filter to only dev_chat_id records
    dev_keys = []
    for k in hash_keys:
        k_str = k.decode() if isinstance(k, bytes) else k
        # TelegramMessage key contains :chat_id: segment; match the value exactly
        if f":{dev_chat_id}:" in k_str:
            dev_keys.append(k)

    stats["total_dev_records"] = len(dev_keys)
    logger.info(f"Found {stats['total_dev_records']} TelegramMessage records for dev_chat_id={dev_chat_id!r}")

    if not dev_keys:
        logger.info("No Dev TelegramMessage records to migrate.")
    else:
        # --- Phase 2: EXISTS-check and rename each key ---
        for key in dev_keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            try:
                # Idempotency: skip keys already bearing eng_chat_id
                if f":{eng_chat_id}:" in key_str:
                    stats["skipped_already_migrated"] += 1
                    logger.debug(f"  SKIP (already migrated): {key_str}")
                    continue

                # Build new key by replacing dev_chat_id segment with eng_chat_id
                new_key_str = key_str.replace(f":{dev_chat_id}:", f":{eng_chat_id}:", 1)
                new_key = new_key_str.encode() if isinstance(key, bytes) else new_key_str

                # EXISTS-check before rename: never clobber an existing key (BLOCKER B5)
                if redis_client.exists(new_key):
                    stats["skipped_collision"] += 1
                    logger.warning(
                        f"  COLLISION: target key already exists — skipping to avoid clobber: "
                        f"{key_str} -> {new_key_str}"
                    )
                    continue

                logger.info(f"  RENAME: {key_str} -> {new_key_str}")

                if not dry_run:
                    redis_client.rename(key, new_key)
                    redis_client.hset(new_key, "chat_id", eng_chat_id)

                stats["renamed"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.error(f"Error migrating {key_str}: {e}")

    # --- Phase 3: Chat record rename — create-then-delete order ---
    dev_chat = _Chat.query.filter(chat_id=dev_chat_id).first()
    eng_chat_existing = _Chat.query.filter(chat_id=eng_chat_id).first()

    if dry_run:
        if dev_chat and not eng_chat_existing:
            logger.info(
                f"  DRY RUN: Would create Eng Chat(chat_id={eng_chat_id!r}) "
                f"from Dev Chat(chat_id={dev_chat_id!r}, name={getattr(dev_chat, 'chat_name', None)!r})"
            )
            logger.info(f"  DRY RUN: Would delete Dev Chat(chat_id={dev_chat_id!r})")
        elif eng_chat_existing:
            logger.info(
                f"  Eng Chat(chat_id={eng_chat_id!r}) already exists — "
                f"would only delete Dev Chat if present."
            )
            if dev_chat:
                logger.info(f"  DRY RUN: Would delete Dev Chat(chat_id={dev_chat_id!r})")
        else:
            logger.info(f"  No Dev Chat(chat_id={dev_chat_id!r}) found to rename.")
    else:
        if dev_chat and not eng_chat_existing:
            # Create Eng Chat FIRST (before deleting Dev Chat)
            eng_chat = _Chat(
                chat_id=eng_chat_id,
                chat_name=getattr(dev_chat, "chat_name", "Eng"),
                chat_type=getattr(dev_chat, "chat_type", None),
                project_key=project_key or getattr(dev_chat, "project_key", None),
                updated_at=time.time(),
            )
            eng_chat.save()

            # Verify Eng Chat exists before deleting Dev Chat
            eng_chat_verify = _Chat.query.filter(chat_id=eng_chat_id).first()
            if not eng_chat_verify:
                logger.error(
                    f"Failed to verify Eng Chat creation (chat_id={eng_chat_id!r}). "
                    "Aborting Dev Chat deletion to preserve data safety."
                )
                stats["errors"] += 1
            else:
                logger.info(f"  Created Eng Chat(chat_id={eng_chat_id!r}). Verified. Deleting Dev Chat.")
                dev_chat.delete()
                logger.info(f"  Deleted Dev Chat(chat_id={dev_chat_id!r}).")

        elif eng_chat_existing:
            logger.info(f"  Eng Chat(chat_id={eng_chat_id!r}) already exists — skipping Chat creation.")
            if dev_chat:
                dev_chat.delete()
                logger.info(f"  Deleted Dev Chat(chat_id={dev_chat_id!r}).")
        else:
            logger.info(f"  No Dev Chat(chat_id={dev_chat_id!r}) found; no Chat rename needed.")

    # --- Phase 4: Rebuild indexes (mandatory — SortedField is partitioned by chat_id) ---
    if not dry_run and (stats["renamed"] > 0):
        logger.info("Rebuilding TelegramMessage Popoto indexes...")
        try:
            _TelegramMessage.rebuild_indexes()
            logger.info("TelegramMessage index rebuild complete.")
        except Exception as e:
            logger.error(f"Failed to rebuild TelegramMessage indexes: {e}")
            stats["errors"] += 1

    # --- Phase 5: Post-migration count assertion via ORM query path ---
    if not dry_run:
        post_dev_count = _TelegramMessage.query.filter(chat_id=dev_chat_id).count()
        post_eng_count = _TelegramMessage.query.filter(chat_id=eng_chat_id).count()
        expected_eng_count = pre_eng_count + stats["renamed"] - 0  # collisions already not renamed
        logger.info(
            f"Post-migration counts: dev_chat_id={dev_chat_id!r}: {post_dev_count} records, "
            f"eng_chat_id={eng_chat_id!r}: {post_eng_count} records"
        )
        logger.info(
            f"Expected eng count: {expected_eng_count} "
            f"({pre_eng_count} existing + {stats['renamed']} migrated - {stats['skipped_collision']} collisions)"
        )
        if post_eng_count != expected_eng_count:
            logger.error(
                f"Count mismatch: expected {expected_eng_count} Eng records, "
                f"got {post_eng_count}. Re-run migration to investigate."
            )
            sys.exit(1)
        else:
            logger.info("Post-migration count assertion passed.")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Merge Dev group TelegramMessage records onto Eng chat_id"
    )
    parser.add_argument(
        "--dev-chat-id",
        required=True,
        help="The Dev chat_id to migrate from (e.g., -100123456)",
    )
    parser.add_argument(
        "--eng-chat-id",
        required=True,
        help="The Eng chat_id to migrate to (e.g., -100654321)",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Project key for scoped test isolation (e.g., test-myproject)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes (also enumerates collisions)",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== TelegramMessage Dev→Eng Chat ID Migration ({mode}) ===")
    logger.info(f"  dev_chat_id: {args.dev_chat_id!r}")
    logger.info(f"  eng_chat_id: {args.eng_chat_id!r}")
    if args.project:
        logger.info(f"  project: {args.project!r}")

    # Pre-flight guards
    _check_worker_not_running()
    _check_email_bridge_not_running()

    stats = migrate(
        dev_chat_id=args.dev_chat_id,
        eng_chat_id=args.eng_chat_id,
        project_key=args.project,
        dry_run=args.dry_run,
    )

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if args.dry_run:
        if stats["renamed"] > 0:
            logger.info(
                f"Would rename {stats['renamed']} records. "
                f"{stats['skipped_collision']} collision(s) detected (would be skipped)."
                " Run without --dry-run to apply."
            )
        else:
            logger.info("No records would be renamed.")
    else:
        if stats["renamed"] > 0:
            logger.info(f"Successfully renamed {stats['renamed']} TelegramMessage records.")
        else:
            logger.info("No records needed migration.")

    if stats["errors"] > 0:
        logger.warning(f"{stats['errors']} error(s) occurred during migration.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
