#!/usr/bin/env python3
"""Fetch recent DMs from a specific user."""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

# Load environment
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "valor_bridge")


async def fetch_messages(username: str, limit: int = 5):
    """Fetch recent messages from a user."""
    session_path = Path(__file__).parent.parent / "data" / SESSION_NAME
    client = TelegramClient(str(session_path), API_ID, API_HASH)

    await client.connect()

    if not await client.is_user_authorized():
        print("Not authorized - session may be invalid")
        return

    try:
        entity = await client.get_entity(username)
        print(f"\n=== Recent messages with {username} ===\n")

        messages = await client.get_messages(entity, limit=limit)

        for msg in reversed(messages):
            direction = ">>> SENT" if msg.out else "<<< RECEIVED"
            timestamp = msg.date.strftime("%Y-%m-%d %H:%M:%S")
            text = msg.text or "[media/no text]"
            print(f"{timestamp} {direction}")
            print(f"{text[:500]}{'...' if len(text) > 500 else ''}")
            print()

    finally:
        await client.disconnect()


if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else "tomcounsell"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    asyncio.run(fetch_messages(username, limit))
