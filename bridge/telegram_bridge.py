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

# =============================================================================
# File Detection and Sending
# =============================================================================

# Explicit file marker: <<FILE:/path/to/file>>
FILE_MARKER_PATTERN = re.compile(r'<<FILE:([^>]+)>>')

# Fallback: detect absolute paths to common file types
# Matches paths like /Users/foo/bar.png or /tmp/output.pdf
ABSOLUTE_PATH_PATTERN = re.compile(
    r'(/(?:Users|home|tmp|var)[^\s\'"<>|]*\.(?:png|jpg|jpeg|gif|webp|bmp|pdf|mp3|mp4|wav|ogg))',
    re.IGNORECASE
)

# Image extensions (for choosing send method)
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}


def extract_files_from_response(response: str) -> tuple[str, list[Path]]:
    """
    Extract files to send from response text.

    Returns (cleaned_text, list_of_file_paths).

    Detection methods:
    1. Explicit markers: <<FILE:/path/to/file>>
    2. Fallback: Absolute paths to existing media files
    """
    files_to_send: list[Path] = []
    seen_paths: set[str] = set()  # Use resolved paths to avoid duplicates from symlinks

    # Method 1: Explicit file markers (highest priority)
    for match in FILE_MARKER_PATTERN.finditer(response):
        path_str = match.group(1).strip()
        path = Path(path_str)
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in seen_paths:
                files_to_send.append(path)
                seen_paths.add(resolved)

    # Method 2: Fallback - detect absolute paths to media files
    for match in ABSOLUTE_PATH_PATTERN.finditer(response):
        path_str = match.group(1).strip()
        path = Path(path_str)
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in seen_paths:
                files_to_send.append(path)
                seen_paths.add(resolved)

    # Clean response: remove file markers
    cleaned = FILE_MARKER_PATTERN.sub('', response)

    # Optionally clean up lines that are just file paths (cosmetic)
    lines = cleaned.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just a detected file path
        if stripped and any(stripped == str(f) or stripped.endswith(str(f)) for f in files_to_send):
            continue
        cleaned_lines.append(line)

    cleaned = '\n'.join(cleaned_lines).strip()

    return cleaned, files_to_send

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

# DM whitelist - only respond to DMs from these users (by username or first name)
# If empty, responds to all DMs (when RESPOND_TO_DMS is True)
DM_WHITELIST = [name.strip().lower() for name in
    os.getenv("TELEGRAM_DM_WHITELIST", "").split(",") if name.strip()]

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


def should_respond(text: str, is_dm: bool, chat_title: str | None, project: dict | None, sender_name: str | None = None, sender_username: str | None = None) -> bool:
    """Determine if we should respond to this message."""
    if is_dm:
        if not RESPOND_TO_DMS:
            return False
        # Check whitelist if configured
        if DM_WHITELIST:
            sender_lower = (sender_name or "").lower()
            username_lower = (sender_username or "").lower()
            # Check if sender matches any whitelisted name/username
            if not any(
                allowed in sender_lower or allowed in username_lower or
                sender_lower == allowed or username_lower == allowed
                for allowed in DM_WHITELIST
            ):
                return False
        return True

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


async def send_response_with_files(client: TelegramClient, event, response: str) -> None:
    """
    Send response to Telegram, handling both files and text.

    1. Extract any files from the response
    2. Send files first (as separate messages)
    3. Send remaining text (if any)
    """
    text, files = extract_files_from_response(response)

    # Send files first
    for file_path in files:
        try:
            is_image = file_path.suffix.lower() in IMAGE_EXTENSIONS
            await client.send_file(
                event.chat_id,
                file_path,
                reply_to=event.message.id,
                # Images get no caption, other files get filename
                caption=None if is_image else f"📎 {file_path.name}",
            )
            logger.info(f"Sent file: {file_path}")
        except Exception as e:
            logger.error(f"Failed to send file {file_path}: {e}")
            # Notify user of failure
            await event.reply(f"Failed to send file: {file_path.name}")

    # Send text if there's meaningful content
    if text and not text.isspace():
        # Truncate if needed
        max_length = DEFAULTS.get("response", {}).get("max_response_length", 4000)
        if len(text) > max_length:
            text = text[: max_length - 3] + "..."
        await event.reply(text)
    elif not files:
        # No files and no text - send a minimal acknowledgment
        await event.reply("Done.")


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

        # Log a preview (truncation happens in send_response_with_files)
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
    if DM_WHITELIST:
        logger.info(f"DM whitelist: {DM_WHITELIST}")
    else:
        logger.info("DM whitelist: (none - responding to all DMs)")

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

        # Get sender username for whitelist check
        sender_username = getattr(sender, "username", None)

        # Check if we should respond
        if not should_respond(text, is_dm, chat_title, project, sender_name, sender_username):
            if is_dm and DM_WHITELIST:
                logger.debug(f"Ignoring DM from {sender_name} (@{sender_username}) - not in whitelist")
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

        # Show typing indicator while getting response
        use_typing = DEFAULTS.get("response", {}).get("typing_indicator", True)
        if use_typing:
            async with client.action(event.chat_id, "typing"):
                response = await get_agent_response(clean_text, session_id, sender_name, chat_title, project)
        else:
            response = await get_agent_response(clean_text, session_id, sender_name, chat_title, project)

        # Send response (handles both files and text)
        await send_response_with_files(client, event, response)
        logger.info(f"Replied to {sender_name}")

    # Start the client
    logger.info("Starting Telegram bridge...")
    await client.start(phone=PHONE, password=PASSWORD)
    logger.info("Connected to Telegram")

    # Keep running
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
