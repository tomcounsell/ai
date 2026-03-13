#!/usr/bin/env python3
"""One-time migration script: backfill project_key and copy enrichment metadata.

Backfills the new model relationship fields introduced by the popoto model
relationships refactor (issue #295):

1. Populates project_key on TelegramMessage, Link, DeadLetter, Chat,
   ReflectionRun by deriving it from chat_id using config/projects.json.
2. Copies media/URL/classification fields from AgentSession to the
   corresponding TelegramMessage (matched by chat_id + message_id).
3. Sets trigger_message_id on AgentSession and agent_session_id on
   TelegramMessage for cross-referencing.

Usage:
    python scripts/migrate_model_relationships.py              # Run migration
    python scripts/migrate_model_relationships.py --dry-run    # Preview changes
    python scripts/migrate_model_relationships.py --max-age 30 # Only last 30 days

Only processes records from the last 90 days by default (matching cleanup TTL).
Older records will be cleaned up naturally by the existing cleanup_expired methods.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def load_chat_to_project_map() -> dict[str, str]:
    """Build a chat_id -> project_key mapping from config/projects.json."""
    config_path = PROJECT_DIR / "config" / "projects.json"
    if not config_path.exists():
        logger.warning(f"Projects config not found at {config_path}")
        return {}

    with open(config_path) as f:
        projects = json.load(f)

    chat_map: dict[str, str] = {}
    for project in projects:
        project_key = project.get("_key", "")
        for chat in project.get("telegram_chats", []):
            chat_id = str(chat.get("id", ""))
            if chat_id:
                chat_map[chat_id] = project_key
    return chat_map


def backfill_project_key(dry_run: bool, max_age_days: int) -> dict[str, int]:
    """Backfill project_key on models that lack it."""
    from models.chat import Chat
    from models.dead_letter import DeadLetter
    from models.link import Link
    from models.telegram import TelegramMessage

    chat_map = load_chat_to_project_map()
    if not chat_map:
        logger.warning("No chat-to-project mapping found. Skipping project_key backfill.")
        return {"skipped": True}

    cutoff = time.time() - (max_age_days * 86400)
    stats = {
        "telegram_messages": 0,
        "links": 0,
        "dead_letters": 0,
        "chats": 0,
    }

    # TelegramMessage
    for msg in TelegramMessage.query.all():
        if msg.project_key:
            continue
        if msg.timestamp and msg.timestamp < cutoff:
            continue
        project_key = chat_map.get(str(msg.chat_id))
        if project_key:
            if not dry_run:
                msg.project_key = project_key
                msg.save()
            stats["telegram_messages"] += 1

    # Link
    for link in Link.query.all():
        if link.project_key:
            continue
        if link.timestamp and link.timestamp < cutoff:
            continue
        project_key = chat_map.get(str(link.chat_id))
        if project_key:
            if not dry_run:
                link.project_key = project_key
                link.save()
            stats["links"] += 1

    # DeadLetter
    for dl in DeadLetter.query.all():
        if dl.project_key:
            continue
        if dl.created_at and dl.created_at < cutoff:
            continue
        project_key = chat_map.get(str(dl.chat_id))
        if project_key:
            if not dry_run:
                dl.project_key = project_key
                dl.save()
            stats["dead_letters"] += 1

    # Chat
    for chat in Chat.query.all():
        if chat.project_key:
            continue
        if chat.updated_at and chat.updated_at < cutoff:
            continue
        project_key = chat_map.get(str(chat.chat_id))
        if project_key:
            if not dry_run:
                chat.project_key = project_key
                chat.save()
            stats["chats"] += 1

    return stats


def backfill_enrichment_metadata(dry_run: bool, max_age_days: int) -> dict[str, int]:
    """Copy enrichment fields from AgentSession to TelegramMessage and set cross-references."""
    from models.agent_session import AgentSession
    from models.telegram import TelegramMessage

    cutoff = time.time() - (max_age_days * 86400)
    stats = {
        "enrichment_copied": 0,
        "cross_refs_set": 0,
        "sessions_processed": 0,
        "no_match": 0,
    }

    all_sessions = list(AgentSession.query.all())
    for session in all_sessions:
        created = session.started_at or session.created_at
        if created and created < cutoff:
            continue

        stats["sessions_processed"] += 1

        # Skip if no enrichment data to copy
        has_enrichment = session.has_media or session.youtube_urls or session.non_youtube_urls
        if not has_enrichment and not session.classification_type:
            continue

        # Skip if already has trigger_message_id
        if session.trigger_message_id:
            continue

        # Find matching TelegramMessage by chat_id + message_id
        if not session.chat_id or not session.message_id:
            stats["no_match"] += 1
            continue

        matching = list(
            TelegramMessage.query.filter(
                chat_id=str(session.chat_id),
                message_id=session.message_id,
            )
        )
        if not matching:
            stats["no_match"] += 1
            continue

        tm = matching[0]

        if not dry_run:
            # Copy enrichment metadata
            if session.has_media and not tm.has_media:
                tm.has_media = session.has_media
            if session.media_type and not tm.media_type:
                tm.media_type = session.media_type
            if session.youtube_urls and not tm.youtube_urls:
                tm.youtube_urls = session.youtube_urls
            if session.non_youtube_urls and not tm.non_youtube_urls:
                tm.non_youtube_urls = session.non_youtube_urls
            if session.reply_to_msg_id and not tm.reply_to_msg_id:
                tm.reply_to_msg_id = session.reply_to_msg_id
            if session.classification_type and not tm.classification_type:
                tm.classification_type = session.classification_type
            if session.classification_confidence and not tm.classification_confidence:
                tm.classification_confidence = session.classification_confidence

            # Set cross-references
            tm.agent_session_id = session.job_id
            tm.save()

            session.trigger_message_id = tm.msg_id
            session.save()

        stats["enrichment_copied"] += 1
        stats["cross_refs_set"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill model relationship fields")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument(
        "--max-age",
        type=int,
        default=90,
        help="Only process records from last N days (default: 90)",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== Model Relationships Migration ({mode}) ===")
    logger.info(f"Processing records from last {args.max_age} days")

    logger.info("\n--- Phase 1: Backfill project_key ---")
    pk_stats = backfill_project_key(args.dry_run, args.max_age)
    for model, count in pk_stats.items():
        logger.info(f"  {model}: {count} records {'would be ' if args.dry_run else ''}updated")

    logger.info("\n--- Phase 2: Backfill enrichment metadata + cross-references ---")
    enrich_stats = backfill_enrichment_metadata(args.dry_run, args.max_age)
    for key, count in enrich_stats.items():
        logger.info(f"  {key}: {count}")

    logger.info(f"\n=== Migration {'preview' if args.dry_run else 'complete'} ===")


if __name__ == "__main__":
    main()
