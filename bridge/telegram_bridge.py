#!/usr/bin/env python3
"""
Telegram-Clawdbot Bridge

Connects a Telegram user account to Clawdbot for AI-powered responses.
Uses Telethon for Telegram and subprocess for Clawdbot agent calls.

Multi-instance support: Set PROJECT_NAME env var to configure which project
this Valor instance serves. Project config loaded from config/projects.json.
"""

import asyncio
import json
import logging
import os
import re
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

# Project identity
PROJECT_NAME = os.getenv("PROJECT_NAME", "valor").lower()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def load_project_config() -> dict:
    """Load project configuration from projects.json."""
    config_path = Path(__file__).parent.parent / "config" / "projects.json"

    if not config_path.exists():
        logger.warning(f"Project config not found at {config_path}, using defaults")
        return {"projects": {}, "defaults": {}}

    with open(config_path) as f:
        return json.load(f)


def get_project_settings(config: dict) -> dict:
    """Get settings for the current project."""
    projects = config.get("projects", {})
    defaults = config.get("defaults", {})

    if PROJECT_NAME in projects:
        project = projects[PROJECT_NAME]
        logger.info(f"Loaded config for project: {project.get('name', PROJECT_NAME)}")
        return project

    logger.warning(f"Project '{PROJECT_NAME}' not found in config, using defaults")
    return {
        "name": PROJECT_NAME,
        "telegram": defaults.get("telegram", {}),
        "context": {}
    }


# Load config at startup
CONFIG = load_project_config()
PROJECT = get_project_settings(CONFIG)
DEFAULTS = CONFIG.get("defaults", {})

# Telegram settings from project config
TELEGRAM_CONFIG = PROJECT.get("telegram", {})
ALLOWED_GROUPS = TELEGRAM_CONFIG.get("groups", [])
RESPOND_TO_ALL = TELEGRAM_CONFIG.get("respond_to_all", False)
RESPOND_TO_MENTIONS = TELEGRAM_CONFIG.get("respond_to_mentions", True)
RESPOND_TO_DMS = TELEGRAM_CONFIG.get("respond_to_dms", DEFAULTS.get("telegram", {}).get("respond_to_dms", True))

# Mention patterns
MENTIONS = TELEGRAM_CONFIG.get(
    "mention_triggers",
    DEFAULTS.get("telegram", {}).get("mention_triggers", ["@valor", "valor", "hey valor"])
)


def should_respond(text: str, is_dm: bool, chat_title: str | None) -> bool:
    """Determine if we should respond to this message."""
    if is_dm:
        return RESPOND_TO_DMS

    # Check if it's an allowed group
    if chat_title:
        group_match = any(
            allowed.lower() in chat_title.lower()
            for allowed in ALLOWED_GROUPS if allowed
        )
        if not group_match:
            return False
    elif ALLOWED_GROUPS:
        # No title and we have allowed groups = don't respond
        return False

    # If respond_to_all is set, respond to everything in allowed groups
    if RESPOND_TO_ALL:
        return True

    # Otherwise, check for mentions
    if RESPOND_TO_MENTIONS:
        text_lower = text.lower()
        return any(mention.lower() in text_lower for mention in MENTIONS)

    return False


def clean_message(text: str) -> str:
    """Remove mention triggers from message for cleaner processing."""
    result = text
    for mention in MENTIONS:
        result = re.sub(re.escape(mention), "", result, flags=re.IGNORECASE)
    return result.strip()


def build_context_prefix() -> str:
    """Build project context to inject into agent prompt."""
    context_parts = [f"PROJECT: {PROJECT.get('name', PROJECT_NAME)}"]

    project_context = PROJECT.get("context", {})
    if project_context.get("description"):
        context_parts.append(f"FOCUS: {project_context['description']}")

    if project_context.get("tech_stack"):
        context_parts.append(f"TECH: {', '.join(project_context['tech_stack'])}")

    github = PROJECT.get("github", {})
    if github.get("repo"):
        context_parts.append(f"REPO: {github.get('org', '')}/{github['repo']}")

    return "\n".join(context_parts)


async def get_agent_response(message: str, session_id: str, sender_name: str, chat_title: str | None) -> str:
    """Call clawdbot agent and get response."""
    try:
        # Build context-enriched message
        context = build_context_prefix()
        enriched_message = f"{context}\n\nFROM: {sender_name}"
        if chat_title:
            enriched_message += f" in {chat_title}"
        enriched_message += f"\nMESSAGE: {message}"

        # Use subprocess to call clawdbot agent
        cmd = [
            "clawdbot",
            "agent",
            "--local",
            "--session-id",
            session_id,
            "--message",
            enriched_message,
            "--thinking",
            "medium",
        ]

        logger.info(f"Calling clawdbot agent for {PROJECT.get('name', PROJECT_NAME)} session {session_id}")

        timeout = DEFAULTS.get("response", {}).get("timeout_seconds", 300)

        # Run with timeout
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "")},
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        if process.returncode != 0:
            logger.error(f"Clawdbot error: {stderr.decode()}")
            return f"Error processing request: {stderr.decode()[:200]}"

        response = stdout.decode().strip()

        # Truncate if needed
        max_length = DEFAULTS.get("response", {}).get("max_response_length", 4000)
        if len(response) > max_length:
            response = response[:max_length - 3] + "..."

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

    logger.info(f"Starting Valor bridge for project: {PROJECT.get('name', PROJECT_NAME)}")
    logger.info(f"Allowed groups: {ALLOWED_GROUPS}")

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

        logger.info(f"[{PROJECT.get('name', PROJECT_NAME)}] Message from {sender_name} in {chat_title or 'DM'}: {text[:50]}...")

        # Clean the message and get session ID
        clean_text = clean_message(text)
        if not clean_text:
            clean_text = "Hello"

        # Use chat ID + project as session identifier for context continuity
        session_id = f"tg_{PROJECT_NAME}_{event.chat_id}"

        # Show typing indicator
        use_typing = DEFAULTS.get("response", {}).get("typing_indicator", True)
        if use_typing:
            async with client.action(event.chat_id, "typing"):
                response = await get_agent_response(clean_text, session_id, sender_name, chat_title)
        else:
            response = await get_agent_response(clean_text, session_id, sender_name, chat_title)

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
