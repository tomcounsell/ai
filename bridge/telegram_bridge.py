#!/usr/bin/env python3
"""
Telegram-Clawdbot Bridge

Connects a Telegram user account to Clawdbot for AI-powered responses.
Uses Telethon for Telegram and subprocess for Clawdbot agent calls.

Multi-project support: Set ACTIVE_PROJECTS env var to configure which projects
this machine monitors. When a message comes in, the bridge identifies which
project's group it belongs to and injects appropriate context.
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

# Active projects on this machine (comma-separated)
# Example: ACTIVE_PROJECTS=valor,popoto,django-project-template
ACTIVE_PROJECTS = [p.strip().lower() for p in os.getenv("ACTIVE_PROJECTS", "valor").split(",") if p.strip()]

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load project configuration from projects.json."""
    config_path = Path(__file__).parent.parent / "config" / "projects.json"

    if not config_path.exists():
        logger.warning(f"Project config not found at {config_path}, using defaults")
        return {"projects": {}, "defaults": {}}

    with open(config_path) as f:
        return json.load(f)


def build_group_to_project_map(config: dict) -> dict:
    """Build a mapping from group names (lowercase) to project configs."""
    group_map = {}
    projects = config.get("projects", {})

    for project_key in ACTIVE_PROJECTS:
        if project_key not in projects:
            logger.warning(f"Project '{project_key}' not found in config, skipping")
            continue

        project = projects[project_key]
        project["_key"] = project_key  # Store the key for reference

        telegram_config = project.get("telegram", {})
        groups = telegram_config.get("groups", [])

        for group in groups:
            group_lower = group.lower()
            if group_lower in group_map:
                logger.warning(f"Group '{group}' is mapped to multiple projects, using first")
                continue
            group_map[group_lower] = project
            logger.info(f"Mapping group '{group}' -> project '{project.get('name', project_key)}'")

    return group_map


# Load config at startup
CONFIG = load_config()
DEFAULTS = CONFIG.get("defaults", {})
GROUP_TO_PROJECT = build_group_to_project_map(CONFIG)

# Collect all monitored groups
ALL_MONITORED_GROUPS = list(GROUP_TO_PROJECT.keys())

# DM settings - respond to DMs if any active project allows it
RESPOND_TO_DMS = any(
    CONFIG.get("projects", {}).get(p, {}).get("telegram", {}).get("respond_to_dms", True)
    for p in ACTIVE_PROJECTS
)

# Default mention triggers
DEFAULT_MENTIONS = DEFAULTS.get("telegram", {}).get("mention_triggers", ["@valor", "valor", "hey valor"])


def find_project_for_chat(chat_title: str | None) -> dict | None:
    """Find which project a chat belongs to."""
    if not chat_title:
        return None

    chat_lower = chat_title.lower()
    for group_name, project in GROUP_TO_PROJECT.items():
        if group_name in chat_lower:
            return project

    return None


def should_respond(text: str, is_dm: bool, chat_title: str | None, project: dict | None) -> bool:
    """Determine if we should respond to this message."""
    if is_dm:
        return RESPOND_TO_DMS

    # Must be in a monitored group
    if not project:
        return False

    telegram_config = project.get("telegram", {})

    # If respond_to_all is set, respond to everything in this group
    if telegram_config.get("respond_to_all", False):
        return True

    # Check for mentions
    if telegram_config.get("respond_to_mentions", True):
        mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)
        text_lower = text.lower()
        return any(mention.lower() in text_lower for mention in mentions)

    return False


def clean_message(text: str, project: dict | None) -> str:
    """Remove mention triggers from message for cleaner processing."""
    mentions = DEFAULT_MENTIONS
    if project:
        telegram_config = project.get("telegram", {})
        mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)

    result = text
    for mention in mentions:
        result = re.sub(re.escape(mention), "", result, flags=re.IGNORECASE)
    return result.strip()


def build_context_prefix(project: dict | None, is_dm: bool) -> str:
    """Build project context to inject into agent prompt."""
    if not project:
        if is_dm:
            return "CONTEXT: Direct message to Valor (no specific project context)"
        return ""

    context_parts = [f"PROJECT: {project.get('name', project.get('_key', 'Unknown'))}"]

    project_context = project.get("context", {})
    if project_context.get("description"):
        context_parts.append(f"FOCUS: {project_context['description']}")

    if project_context.get("tech_stack"):
        context_parts.append(f"TECH: {', '.join(project_context['tech_stack'])}")

    github = project.get("github", {})
    if github.get("repo"):
        context_parts.append(f"REPO: {github.get('org', '')}/{github['repo']}")

    return "\n".join(context_parts)


async def get_agent_response(message: str, session_id: str, sender_name: str, chat_title: str | None, project: dict | None) -> str:
    """Call clawdbot agent and get response."""
    try:
        # Build context-enriched message
        context = build_context_prefix(project, chat_title is None)
        enriched_message = f"{context}\n\nFROM: {sender_name}"
        if chat_title:
            enriched_message += f" in {chat_title}"
        enriched_message += f"\nMESSAGE: {message}"

        project_name = project.get("name", "Valor") if project else "Valor"

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

        logger.info(f"Calling clawdbot agent for {project_name}, session {session_id}")

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

    logger.info(f"Starting Valor bridge")
    logger.info(f"Active projects: {ACTIVE_PROJECTS}")
    logger.info(f"Monitored groups: {ALL_MONITORED_GROUPS}")
    logger.info(f"Respond to DMs: {RESPOND_TO_DMS}")

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

        # Find which project this chat belongs to
        project = find_project_for_chat(chat_title) if chat_title else None

        # Check if we should respond
        if not should_respond(text, is_dm, chat_title, project):
            return

        project_name = project.get("name", "DM") if project else "DM"
        logger.info(f"[{project_name}] Message from {sender_name} in {chat_title or 'DM'}: {text[:50]}...")

        # Clean the message
        clean_text = clean_message(text, project)
        if not clean_text:
            clean_text = "Hello"

        # Build session ID: include project key for context isolation
        project_key = project.get("_key", "dm") if project else "dm"
        session_id = f"tg_{project_key}_{event.chat_id}"

        # Show typing indicator
        use_typing = DEFAULTS.get("response", {}).get("typing_indicator", True)
        if use_typing:
            async with client.action(event.chat_id, "typing"):
                response = await get_agent_response(clean_text, session_id, sender_name, chat_title, project)
        else:
            response = await get_agent_response(clean_text, session_id, sender_name, chat_title, project)

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
