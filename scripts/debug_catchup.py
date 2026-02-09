#!/usr/bin/env python3
"""Debug script to check what messages exist and why catchup missed them."""

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from telethon import TelegramClient

# Load environment
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_FILE = Path(__file__).parent.parent / "data" / "telegram_session"


async def main():
    """Check messages in Dev: Yudame Research group."""
    client = TelegramClient(str(SESSION_FILE), API_ID, API_HASH)
    await client.connect()

    # Get all dialogs to find the group
    dialogs = await client.get_dialogs()
    target_group = None

    for dialog in dialogs:
        title = getattr(dialog.entity, "title", None)
        if title == "Dev: Yudame Research":
            target_group = dialog.entity
            break

    if not target_group:
        logger.error("Could not find 'Dev: Yudame Research' group")
        await client.disconnect()
        return

    logger.info(f"Found group: {target_group.title}")

    # Get messages from the last 24 hours
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    messages = await client.get_messages(target_group, limit=100)

    logger.info("\nMessages in last 24 hours (showing last 100):")
    logger.info("=" * 80)

    me = await client.get_me()
    my_id = me.id

    count = 0
    for msg in messages:
        if msg.date < cutoff:
            break

        sender = await msg.get_sender()
        sender_name = getattr(sender, "first_name", "Unknown")
        is_me = msg.out or (sender and sender.id == my_id)
        text_preview = (msg.text or "")[:60].replace("\n", " ")

        # Check if I replied to this message
        replied = await check_if_replied(client, target_group, msg, my_id)

        status = "✓ REPLIED" if replied else ("  (me)" if is_me else "✗ NO REPLY")

        logger.info(
            f"[{msg.id}] {msg.date.isoformat()} | {sender_name:15} | {status:12} | {text_preview}"
        )
        count += 1

    logger.info("=" * 80)
    logger.info(f"Total messages in last 24h: {count}")

    await client.disconnect()


async def check_if_replied(client, entity, message, my_id):
    """Check if I replied to this message."""
    try:
        replies = await client.get_messages(entity, limit=20, min_id=message.id)

        for reply in replies:
            if reply.out and reply.reply_to_msg_id == message.id:
                return True
        return False
    except Exception as e:
        logger.debug(f"Error checking reply: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(main())
