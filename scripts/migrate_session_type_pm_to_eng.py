#!/usr/bin/env python3
"""Migrate AgentSession: rename session_type KeyField from "pm" to "eng".

Unlike #652, do NOT read session_mode — deprecated no-op since #1026; read session_type directly.

session_type is a Popoto KeyField, meaning its value is embedded in the Redis hash key
string (e.g., AgentSession:{id}:pm:{project_key}:...). Changing from "pm" to "eng"
requires Redis key RENAME operations.

Steps:
1. Guard: worker must be idle (last_worker_connected mtime older than threshold)
2. Guard: email bridge must not be running
3. Guard: code-version ordering (SessionType.PM must still exist; run BEFORE /update)
4. SCAN all AgentSession:* keys, skip index keys (_sorted_set:, _field_index:)
5. For each key containing exactly one :pm: segment:
   a. Assert exactly one :pm: occurrence in the key (sys.exit(1) if zero or >1)
   b. RENAME key replacing :pm: with :eng:
   c. Update session_type hash field value to "eng"
6. Skip :dev: keys as no-ops (counted in stats["skipped_dev_record"])
7. Call AgentSession.rebuild_indexes() after all renames
8. Support --dry-run flag
9. Idempotent: skip keys that already have :eng: (safe to run twice)

Usage:
  python scripts/migrate_session_type_pm_to_eng.py --dry-run
  python scripts/migrate_session_type_pm_to_eng.py

IMPORTANT:
  - Stop the bridge and worker before running this script.
  - Run BEFORE deploying the new code that removes SessionType.PM.
  - See docs/plans/merge_pm_dev_into_eng_role.md Update System runbook.
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
                f"Worker heartbeat is fresh ({age:.0f}s ago, "
                f"threshold={WORKER_HEARTBEAT_THRESHOLD}s). "
                "Stop the worker before running this migration.\n"
                f"  Heartbeat file: {heartbeat_file}\n"
                "  Stop command: ./scripts/valor-service.sh worker-stop"
            )
            sys.exit(1)
        else:
            logger.info(f"Worker heartbeat is stale ({age:.0f}s ago). Worker appears stopped.")
    else:
        logger.info(
            "No worker heartbeat file found. Worker appears never started or already stopped."
        )

    # Secondary check via pgrep
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python -m worker"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split()
            # Verify each PID is alive
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
                    logger.info(
                        f"email:last_poll_ts is stale ({age:.0f}s ago). Email bridge appears stopped."
                    )
            except (ValueError, TypeError):
                logger.warning(
                    "Could not parse email:last_poll_ts value; skipping Redis freshness check."
                )
        else:
            logger.info(
                "email:last_poll_ts not found in Redis. Email bridge appears never started."
            )
    except Exception as e:
        logger.warning(f"Could not check email:last_poll_ts in Redis: {e}")


def _check_code_version_ordering() -> None:
    """Guard: SessionType.PM must still exist in the installed code.

    Run this migration BEFORE /update. If SessionType.PM is already gone,
    pm records can no longer be matched. This guard prevents running the
    migration against stale code where pm→eng would silently skip all records.
    """
    try:
        from config.enums import SessionType

        if not hasattr(SessionType, "PM"):
            logger.error(
                "Run this migration BEFORE /update — SessionType.PM has already been removed "
                "from the installed code; pm records can no longer be matched. "
                "See Update System runbook step 3."
            )
            sys.exit(1)
        logger.info("Code version check passed: SessionType.PM is present.")
    except ImportError as e:
        logger.error(f"Could not import config.enums: {e}")
        sys.exit(1)


def migrate(dry_run: bool = True) -> dict:
    """Rename Redis keys and update session_type field values from 'pm' to 'eng'.

    Args:
        dry_run: If True, log what would happen without making changes.

    Returns:
        Dict with migration stats.
    """
    import popoto

    redis_client = popoto.redis_db.get_REDIS_DB()

    stats = {
        "total_records": 0,
        "renamed_to_eng": 0,
        "skipped_already_migrated": 0,
        "skipped_no_pm_segment": 0,
        "skipped_dev_record": 0,
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

    # Phase 2: Rename keys containing :pm: segment
    for key in hash_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            # Skip :dev: keys explicitly (no-op)
            if ":dev:" in key_str:
                stats["skipped_dev_record"] += 1
                logger.debug(f"  SKIP (dev record): {key_str}")
                continue

            # Skip already-migrated :eng: or :teammate: keys
            if ":eng:" in key_str or ":teammate:" in key_str:
                stats["skipped_already_migrated"] += 1
                logger.debug(f"  SKIP (already migrated): {key_str}")
                continue

            # Check if key contains :pm: segment
            if ":pm:" not in key_str:
                stats["skipped_no_pm_segment"] += 1
                logger.debug(f"  SKIP (no :pm: segment): {key_str}")
                continue

            # Assert exactly one :pm: occurrence — positional rewrite safety check
            # Split on ':' and find the exact segment, not just substring count
            segments = key_str.split(":")
            pm_indices = [i for i, s in enumerate(segments) if s == "pm"]
            if len(pm_indices) != 1:
                logger.error(
                    f"Expected exactly one :pm: segment in key, found {len(pm_indices)}: {key_str}. "
                    "Cannot safely rewrite. Aborting."
                )
                sys.exit(1)

            # Rewrite only the session_type segment (positional, not unanchored replace)
            pm_idx = pm_indices[0]
            new_segments = segments[:]
            new_segments[pm_idx] = "eng"
            new_key_str = ":".join(new_segments)
            new_key = new_key_str.encode() if isinstance(key, bytes) else new_key_str

            logger.info(f"  RENAME: {key_str} -> {new_key_str}")

            if not dry_run:
                # Atomic rename + field update
                redis_client.rename(key, new_key)
                redis_client.hset(new_key, "session_type", "eng")

            stats["renamed_to_eng"] += 1

        except SystemExit:
            raise
        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Error migrating {key_str}: {e}")

    # Phase 3: Rebuild indexes
    if not dry_run and stats["renamed_to_eng"] > 0:
        logger.info("Rebuilding Popoto indexes...")
        try:
            # Import here to avoid circular import at module load time
            from models.agent_session import AgentSession as _AgentSession

            _AgentSession.rebuild_indexes()
            logger.info("Index rebuild complete.")
        except Exception as e:
            logger.error(f"Failed to rebuild indexes: {e}")
            stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migrate AgentSession session_type KeyField: pm -> eng"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without making changes",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== AgentSession SessionType KeyField Migration: pm -> eng ({mode}) ===")

    # Pre-flight guards
    _check_worker_not_running()
    _check_email_bridge_not_running()
    _check_code_version_ordering()

    stats = migrate(dry_run=args.dry_run)

    logger.info("=== Migration Results ===")
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    if not args.dry_run and stats["renamed_to_eng"] > 0:
        logger.info(
            f"Successfully renamed {stats['renamed_to_eng']} keys across {stats['total_records']} records."
        )
    elif args.dry_run and stats["renamed_to_eng"] > 0:
        logger.info(
            f"Would rename {stats['renamed_to_eng']} keys across {stats['total_records']} records."
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
