#!/usr/bin/env python3
"""
Telegram-Clawdbot Bridge

Connects a Telegram user account to Clawdbot for AI-powered responses.
Uses Telethon for Telegram and subprocess for Clawdbot agent calls.
"""

import asyncio
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events

# Load environment
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Configuration
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
PASSWORD = os.getenv("TELEGRAM_PASSWORD", "")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "valor_bridge")
ALLOWED_GROUPS = os.getenv("TELEGRAM_ALLOWED_GROUPS", "").split(",")
ALLOW_DMS = os.getenv("TELEGRAM_ALLOW_DMS", "true").lower() == "true"

# Mention patterns that trigger the bot
MENTIONS = ["@valor", "@valorengels", "valor", "hey valor"]

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def should_respond(text: str, is_dm: bool, chat_title: str | None) -> bool:
    """Determine if we should respond to this message."""
    if is_dm:
        return ALLOW_DMS

    # Check if it's an allowed group
    if chat_title and not any(g.strip() in chat_title for g in ALLOWED_GROUPS if g.strip()):
        return False

    # Check for mentions
    text_lower = text.lower()
    return any(mention.lower() in text_lower for mention in MENTIONS)


def clean_message(text: str) -> str:
    """Remove mention triggers from message for cleaner processing."""
    result = text
    for mention in MENTIONS:
        result = re.sub(re.escape(mention), "", result, flags=re.IGNORECASE)
    return result.strip()


async def get_agent_response(message: str, session_id: str) -> str:
    """Call clawdbot agent and get response."""
    try:
        # Use subprocess to call clawdbot agent
        cmd = [
            "clawdbot",
            "agent",
            "--local",
            "--session-id",
            session_id,
            "--message",
            message,
            "--thinking",
            "medium",
        ]

        logger.info(f"Calling clawdbot agent with session {session_id}")

        # Run with timeout
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "")},
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=300,  # 5 minute timeout
        )

        if process.returncode != 0:
            logger.error(f"Clawdbot error: {stderr.decode()}")
            return f"Error processing request: {stderr.decode()[:200]}"

        response = stdout.decode().strip()
        logger.info(f"Agent response: {response[:100]}...")
        return response

    except asyncio.TimeoutError:
        logger.error("Agent request timed out")
        return "Request timed out. Please try again."
    except Exception as e:
        logger.error(f"Error calling agent: {e}")
        return f"Error: {str(e)}"


async def main():
    """Main entry point."""
    if not API_ID or not API_HASH:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set")
        sys.exit(1)

    # Create client
    session_path = Path(__file__).parent.parent / "data" / SESSION_NAME
    client = TelegramClient(str(session_path), API_ID, API_HASH)

    @client.on(events.NewMessage)
    async def handler(event):
        """Handle incoming messages."""
        # Skip outgoing messages
        if event.out:
            return

        # Get message details
        message = event.message
        text = message.text or ""
        is_dm = event.is_private
        chat = await event.get_chat()
        chat_title = getattr(chat, "title", None)
        sender = await event.get_sender()
        sender_name = getattr(sender, "first_name", "Unknown")

        # Check if we should respond
        if not should_respond(text, is_dm, chat_title):
            return

        logger.info(f"Message from {sender_name} in {chat_title or 'DM'}: {text[:50]}...")

        # Clean the message and get session ID
        clean_text = clean_message(text)
        if not clean_text:
            clean_text = "Hello"

        # Use chat ID as session identifier for context continuity
        session_id = f"tg_{event.chat_id}"

        # Show typing indicator
        async with client.action(event.chat_id, "typing"):
            response = await get_agent_response(clean_text, session_id)

        # Send response
        await event.reply(response)
        logger.info(f"Replied to {sender_name}")

    # Start the client
    logger.info("Starting Telegram bridge...")
    await client.start(phone=PHONE, password=PASSWORD)
    logger.info("Connected to Telegram")

    # Keep running
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
