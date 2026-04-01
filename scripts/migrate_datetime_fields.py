#!/usr/bin/env python3
"""Migrate AgentSession float timestamps to datetime objects.

Run this script once after deploying the field cleanup changes.
It converts existing float timestamps (from time.time()) to proper
datetime objects, and migrates flat history strings to SessionEvent dicts.

Usage:
    python scripts/migrate_datetime_fields.py [--dry-run]
"""

import argparse
import logging
import sys
from datetime import UTC, datetime

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def migrate_timestamps(dry_run: bool = False) -> dict:
    """Convert float timestamps to datetime on all AgentSession records."""
    from models.agent_session import AgentSession

    stats = {"total": 0, "converted": 0, "errors": 0, "skipped": 0}

    all_sessions = AgentSession.query.all()
    stats["total"] = len(all_sessions)
    logger.info("Found %d sessions to check", stats["total"])

    timestamp_fields = ["created_at", "started_at", "updated_at", "completed_at", "scheduled_at"]

    for session in all_sessions:
        changed = False
        for field in timestamp_fields:
            val = getattr(session, field, None)
            if isinstance(val, (int, float)):
                new_val = datetime.fromtimestamp(val, tz=UTC)
                if not dry_run:
                    setattr(session, field, new_val)
                changed = True
                logger.debug(
                    "  Session %s: %s %.1f -> %s",
                    session.session_id,
                    field,
                    val,
                    new_val.isoformat(),
                )

        # Migrate flat history strings to session_events
        events = session.session_events
        if isinstance(events, list) and events:
            migrated_events = []
            any_migrated = False
            for event in events:
                if isinstance(event, str):
                    # Old format: "[role] text"
                    if event.startswith("[") and "] " in event:
                        bracket_end = event.index("] ")
                        role = event[1:bracket_end]
                        text = event[bracket_end + 2 :]
                    else:
                        role = "system"
                        text = event
                    migrated_events.append(
                        {
                            "event_type": role,
                            "timestamp": datetime.now(tz=UTC).timestamp(),
                            "text": text,
                            "data": None,
                        }
                    )
                    any_migrated = True
                elif isinstance(event, dict):
                    migrated_events.append(event)
                else:
                    migrated_events.append(event)

            if any_migrated:
                if not dry_run:
                    session.session_events = migrated_events
                changed = True

        if changed:
            if not dry_run:
                try:
                    session.save()
                    stats["converted"] += 1
                except Exception as e:
                    logger.error("Failed to save session %s: %s", session.session_id, e)
                    stats["errors"] += 1
            else:
                stats["converted"] += 1
        else:
            stats["skipped"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate AgentSession timestamps")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN -- no changes will be saved")

    stats = migrate_timestamps(dry_run=args.dry_run)

    logger.info(
        "Migration complete: %d total, %d converted, %d skipped, %d errors",
        stats["total"],
        stats["converted"],
        stats["skipped"],
        stats["errors"],
    )

    if stats["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
