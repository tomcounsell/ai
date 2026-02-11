#!/usr/bin/env python3
"""
Telegram Bridge - Main Entry Point and Coordinator

This module is the main entry point for the Telegram bridge. It initializes the
Telegram client, registers event handlers, and coordinates message processing.

Domain-specific logic has been extracted into sub-modules:
  - bridge.media: Media detection, download, transcription, image description
  - bridge.routing: Message routing, project config, mention/response classification
  - bridge.context: Context building, conversation history, reply chains
  - bridge.response: Message formatting, reactions, file extraction, sending
  - bridge.agents: Agent invocation, retry logic, self-healing

Backward-compatible imports are maintained here so existing code that imports
from bridge.telegram_bridge continues to work, but new code should import
directly from the appropriate sub-module.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure user site-packages is available for claude_agent_sdk
# Add user site-packages as fallback (after venv packages take priority)
user_site = Path.home() / "Library/Python/3.12/lib/python/site-packages"
if user_site.exists() and str(user_site) not in sys.path:
    sys.path.append(str(user_site))

from dotenv import load_dotenv  # noqa: E402

# Load environment variables FIRST before any env checks
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Feature flag for Claude Agent SDK migration
# Set USE_CLAUDE_SDK=true in .env to use the new SDK instead of clawdbot
USE_CLAUDE_SDK = os.getenv("USE_CLAUDE_SDK", "false").lower() == "true"

# Import SDK client and messenger if enabled (lazy import to avoid loading if not used)
if USE_CLAUDE_SDK:
    from agent import get_agent_response_sdk  # noqa: F401

# Local tool imports for message and link storage
from telethon import TelegramClient, events  # noqa: E402

from bridge.context import (  # noqa: E402
    build_activity_context,  # noqa: F401
    build_context_prefix,  # noqa: F401
    build_conversation_history,  # noqa: F401
    is_status_question,  # noqa: F401
)
from bridge.media import (  # noqa: E402
    MEDIA_DIR,  # noqa: F401
    VISION_EXTENSIONS,  # noqa: F401
    VOICE_EXTENSIONS,  # noqa: F401
    describe_image,  # noqa: F401
    download_media,  # noqa: F401
    extract_document_text,  # noqa: F401
    get_media_type,
    transcribe_voice,  # noqa: F401
    validate_media_file,  # noqa: F401
)
from bridge.response import (  # noqa: E402
    FILE_MARKER_PATTERN,  # noqa: F401
    REACTION_ERROR,
    REACTION_RECEIVED,
    REACTION_SUCCESS,
    clean_message,
    extract_files_from_response,  # noqa: F401
    filter_tool_logs,
    get_processing_emoji,  # noqa: F401
    get_processing_emoji_async,
    send_response_with_files,
    set_reaction,
)
from bridge.routing import (  # noqa: E402
    build_group_to_project_map,
    classify_needs_response,  # noqa: F401
    classify_needs_response_async,  # noqa: F401
    extract_at_mentions,  # noqa: F401
    find_project_for_chat,
    get_user_permissions,  # noqa: F401
    get_valor_usernames,  # noqa: F401
    is_message_for_others,  # noqa: F401
    is_message_for_valor,  # noqa: F401
    load_config,
    should_respond_async,
    should_respond_sync,  # noqa: F401
)
from tools.link_analysis import (  # noqa: E402
    extract_urls,
    extract_youtube_urls,
)
from tools.telegram_history import (  # noqa: E402
    register_chat,
    store_link,
    store_message,
)

# =============================================================================
# Message Queue for Graceful Restart
# =============================================================================

# Shutdown flag - set by signal handlers to stop accepting new messages
SHUTTING_DOWN = False

# Project directory (for running scripts, checking flags, etc.)
_BRIDGE_PROJECT_DIR = Path(__file__).parent.parent


def _cleanup_session_locks() -> int:
    """Kill stale processes holding the Telegram session file.

    Uses lsof to find processes holding *.session files in the data directory,
    then kills any that are older than 60 seconds (to avoid killing active bridge).

    Returns the number of processes killed.
    """
    import random

    logger = logging.getLogger(__name__)
    data_dir = _BRIDGE_PROJECT_DIR / "data"
    killed = 0

    # Find session files
    session_files = list(data_dir.glob("*.session"))
    if not session_files:
        return 0

    for session_file in session_files:
        try:
            # Use lsof to find processes holding this file
            result = subprocess.run(
                ["/usr/sbin/lsof", "-t", str(session_file)],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0 or not result.stdout.strip():
                continue

            pids = result.stdout.strip().split("\n")
            current_pid = os.getpid()

            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())

                    # Don't kill ourselves
                    if pid == current_pid:
                        continue

                    # Check process age (only kill if > 60s old)
                    try:
                        stat_result = subprocess.run(
                            ["ps", "-o", "etime=", "-p", str(pid)],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if stat_result.returncode == 0:
                            etime = stat_result.stdout.strip()
                            # Parse etime format: [[DD-]HH:]MM:SS
                            # If it has a dash or colon-colon, it's old enough
                            if "-" in etime or etime.count(":") >= 2:
                                # Definitely old (days or hours)
                                pass
                            else:
                                # MM:SS format - check if > 1 minute
                                parts = etime.split(":")
                                if len(parts) == 2:
                                    minutes = int(parts[0])
                                    if minutes < 1:
                                        logger.debug(
                                            f"Skipping young process {pid} (age: {etime})"
                                        )
                                        continue
                    except (subprocess.TimeoutExpired, ValueError):
                        # Can't determine age, skip to be safe
                        continue

                    # Graceful shutdown: SIGTERM first, then SIGKILL if still alive
                    logger.warning(
                        f"Sending SIGTERM to stale process {pid} holding {session_file}"
                    )
                    os.kill(pid, signal.SIGTERM)
                    # Wait up to 5 seconds for graceful exit
                    for _ in range(10):
                        time.sleep(0.5)
                        try:
                            os.kill(pid, 0)  # Check if still alive
                        except ProcessLookupError:
                            break  # Process exited
                    else:
                        # Still alive after 5s, force kill
                        logger.warning(
                            f"Process {pid} did not exit after SIGTERM, sending SIGKILL"
                        )
                        os.kill(pid, signal.SIGKILL)
                    killed += 1

                except (ValueError, ProcessLookupError, PermissionError) as e:
                    logger.debug(f"Could not kill PID {pid_str}: {e}")

        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout checking locks on {session_file}")
        except Exception as e:
            logger.debug(f"Error checking {session_file}: {e}")

    # Also clean up journal/wal files that indicate incomplete transactions
    for suffix in ["-journal", "-wal", "-shm"]:
        for leftover in data_dir.glob(f"*{suffix}"):
            try:
                leftover.unlink()
                logger.info(f"Removed stale {leftover.name}")
            except Exception as e:
                logger.debug(f"Could not remove {leftover}: {e}")

    # Add jitter to prevent thundering herd on restart
    if killed > 0:
        jitter = random.uniform(0.5, 2.0)
        time.sleep(jitter)

    return killed


# Configuration (environment already loaded at top of file)
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
PASSWORD = os.getenv("TELEGRAM_PASSWORD", "")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "valor_bridge")

# Active projects on this machine (comma-separated)
# Example: ACTIVE_PROJECTS=valor,popoto,django-project-template
ACTIVE_PROJECTS = [
    p.strip().lower()
    for p in os.getenv("ACTIVE_PROJECTS", "valor").split(",")
    if p.strip()
]

# =============================================================================
# Logging Configuration
# =============================================================================

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Create formatters
CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s"

# Packages whose loggers should get full DEBUG output in the file handler.
# External packages (telethon, httpx, etc.) are restricted to INFO+ to
# avoid debug spam while keeping their warnings/errors visible.
INTERNAL_PACKAGES = ("bridge", "agent", "tools", "monitoring", "models")


class InternalDebugFilter(logging.Filter):
    """Level-based filter: internal packages at DEBUG, external at INFO+.

    Internal packages (bridge, agent, tools, monitoring, models) get full
    DEBUG logging to the file handler. External packages (telethon, httpx,
    etc.) only pass INFO+ to avoid debug spam while keeping their
    warnings/errors visible.
    """

    def filter(self, record):
        if record.name.split(".")[0] in INTERNAL_PACKAGES:
            return True  # All levels for internal packages
        return record.levelno >= logging.INFO  # INFO+ for external


# Setup root logger with console handler (INFO level for terminal output)
logging.basicConfig(
    level=logging.INFO,
    format=CONSOLE_FORMAT,
    handlers=[logging.StreamHandler()],
)

# Add file handler to the ROOT logger so all child loggers (agent.job_queue,
# bridge.*, tools.*, etc.) inherit it automatically. Without this, only
# the bridge.telegram_bridge module logger would write to bridge.log.
root_logger = logging.getLogger()
file_handler = logging.FileHandler(LOG_DIR / "bridge.log")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
file_handler.addFilter(InternalDebugFilter())
root_logger.addHandler(file_handler)

# Module logger for this file. It inherits the root logger's file handler,
# so we only need to set its level to DEBUG for verbose local output.
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def log_event(event_type: str, **kwargs) -> None:
    """Log a structured event to Redis via BridgeEvent model."""
    try:
        from models.bridge_event import BridgeEvent

        BridgeEvent.log(event_type, **kwargs)
    except Exception:
        # Fallback: don't let event logging break the bridge
        pass


# Load config at startup (propagate ACTIVE_PROJECTS to routing module first)
import bridge.routing as _routing_module  # noqa: E402

_routing_module.ACTIVE_PROJECTS = ACTIVE_PROJECTS
CONFIG = load_config()
DEFAULTS = CONFIG.get("defaults", {})
GROUP_TO_PROJECT = build_group_to_project_map(CONFIG)

# Collect all monitored groups
ALL_MONITORED_GROUPS = list(GROUP_TO_PROJECT.keys())


# DM settings - respond to DMs if any active project allows it
RESPOND_TO_DMS = any(
    CONFIG.get("projects", {})
    .get(p, {})
    .get("telegram", {})
    .get("respond_to_dms", True)
    for p in ACTIVE_PROJECTS
)

# DM whitelist - only respond to DMs from these Telegram user IDs
# Loaded from ~/Desktop/claude_code/dm_whitelist.json, falls back to TELEGRAM_DM_WHITELIST env var
# Format: {"users": {"123456": {"name": "Name", "permissions": "full|qa_only"}}}
DM_WHITELIST: set[int] = set()
DM_WHITELIST_CONFIG: dict[int, dict] = {}  # Full config per user for permissions lookup
_dm_whitelist_path = Path.home() / "Desktop" / "claude_code" / "dm_whitelist.json"
if _dm_whitelist_path.exists():
    try:
        _wl_config = json.loads(_dm_whitelist_path.read_text())
        _users = _wl_config.get("users", {})
        for uid, user_info in _users.items():
            uid_int = int(uid)
            DM_WHITELIST.add(uid_int)
            # Handle both old format (string name) and new format (dict with permissions)
            if isinstance(user_info, str):
                DM_WHITELIST_CONFIG[uid_int] = {
                    "name": user_info,
                    "permissions": "full",
                }
            else:
                DM_WHITELIST_CONFIG[uid_int] = user_info
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning(f"Failed to load DM whitelist from {_dm_whitelist_path}: {e}")
if not DM_WHITELIST:
    for _id in os.getenv("TELEGRAM_DM_WHITELIST", "").split(","):
        _id = _id.strip()
        if _id.isdigit():
            DM_WHITELIST.add(int(_id))
            DM_WHITELIST_CONFIG[int(_id)] = {"permissions": "full"}

# Propagate config to routing module so imported functions work correctly
_routing_module.CONFIG = CONFIG
_routing_module.DEFAULTS = DEFAULTS
_routing_module.GROUP_TO_PROJECT = GROUP_TO_PROJECT
_routing_module.ALL_MONITORED_GROUPS = ALL_MONITORED_GROUPS
_routing_module.RESPOND_TO_DMS = RESPOND_TO_DMS
_routing_module.DM_WHITELIST = DM_WHITELIST
_routing_module.DM_WHITELIST_CONFIG = DM_WHITELIST_CONFIG
_routing_module.DEFAULT_MENTIONS = DEFAULTS.get("telegram", {}).get(
    "mention_triggers", ["@valor", "valor", "hey valor"]
)
# Re-export DEFAULT_MENTIONS for backward compat and use by other functions still in this module
DEFAULT_MENTIONS = _routing_module.DEFAULT_MENTIONS

# Propagate config to context module so imported functions work correctly
import bridge.context as _context_module  # noqa: E402

_context_module.CONFIG = CONFIG
_context_module.DEFAULTS = DEFAULTS
_context_module.DM_WHITELIST_CONFIG = DM_WHITELIST_CONFIG
_context_module._BRIDGE_PROJECT_DIR = _BRIDGE_PROJECT_DIR

# Re-export LINK_COLLECTORS from context module (used by handler)
LINK_COLLECTORS = _context_module.LINK_COLLECTORS

# Propagate config to agents module so imported functions work correctly
import bridge.agents as _agents_module  # noqa: E402

_agents_module.CONFIG = CONFIG
_agents_module.DEFAULTS = DEFAULTS
_agents_module._BRIDGE_PROJECT_DIR = _BRIDGE_PROJECT_DIR

from bridge.agents import (  # noqa: E402
    ACKNOWLEDGMENT_MESSAGE,  # noqa: F401
    ACKNOWLEDGMENT_TIMEOUT_SECONDS,  # noqa: F401
    MAX_RETRIES,  # noqa: F401
    RETRY_DELAYS,  # noqa: F401
    _detect_issue_number,  # noqa: F401
    _get_github_repo_url,  # noqa: F401
    _get_running_jobs_info,  # noqa: F401
    _handle_update_command,
    _match_plan_by_name,  # noqa: F401
    attempt_self_healing,  # noqa: F401
    create_failure_plan,  # noqa: F401
    create_workflow_for_tracked_work,
    detect_tracked_work,  # noqa: F401
    get_agent_response,  # noqa: F401
    get_agent_response_clawdbot,  # noqa: F401
    get_agent_response_with_retry,
)


async def check_message_query_request(client: TelegramClient) -> None:
    """Check for and process message query requests via file-based IPC.

    Monitors data/message_query_request.json for CLI requests to fetch messages.
    When found, executes the query using Telegram client and writes results to
    data/message_query_result.json.

    Args:
        client: Active TelegramClient instance
    """
    request_file = _BRIDGE_PROJECT_DIR / "data" / "message_query_request.json"
    result_file = _BRIDGE_PROJECT_DIR / "data" / "message_query_result.json"

    # Check if request file exists
    if not request_file.exists():
        return

    try:
        # Read request
        with open(request_file) as f:
            request = json.load(f)

        user_id = request.get("user_id")
        username = request.get("username")
        limit = request.get("limit", 100)

        logger.info(
            f"Processing message query request: user_id={user_id}, "
            f"username={username}, limit={limit}"
        )

        # Execute query using Telegram client
        messages = await client.get_messages(user_id, limit=limit)

        # Format messages for response
        formatted_messages = []
        for msg in messages:
            sender = await msg.get_sender()
            sender_name = (
                getattr(sender, "first_name", "Unknown") if sender else "Unknown"
            )

            formatted_messages.append(
                {
                    "date": msg.date.isoformat() if msg.date else None,
                    "sender": sender_name,
                    "text": msg.text or "",
                }
            )

        # Build result
        result = {
            "success": True,
            "user_id": user_id,
            "username": username,
            "messages": formatted_messages,
            "completed_at": datetime.now().isoformat(),
        }

        # Write result
        with open(result_file, "w") as f:
            json.dump(result, f, indent=2)

        logger.info(
            f"Message query completed: fetched {len(formatted_messages)} messages"
        )

    except Exception as e:
        # Write error result
        error_result = {
            "success": False,
            "error": str(e),
            "completed_at": datetime.now().isoformat(),
        }
        with open(result_file, "w") as f:
            json.dump(error_result, f, indent=2)

        logger.error(f"Message query failed: {e}", exc_info=True)

    finally:
        # Always delete request file after processing
        try:
            request_file.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete request file: {e}")


async def main():
    """Main entry point."""
    if not API_ID or not API_HASH:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set")
        sys.exit(1)

    logger.info("Starting Valor bridge")
    logger.info(
        f"Agent backend: {'Claude Agent SDK' if USE_CLAUDE_SDK else 'Clawdbot (legacy)'}"
    )
    logger.info(f"Active projects: {ACTIVE_PROJECTS}")
    logger.info(f"Monitored groups: {ALL_MONITORED_GROUPS}")
    logger.info(f"Respond to DMs: {RESPOND_TO_DMS}")
    if DM_WHITELIST:
        logger.info(f"DM whitelist (user IDs): {sorted(DM_WHITELIST)}")
    else:
        logger.info("DM whitelist: (none - responding to all DMs)")
    if LINK_COLLECTORS:
        logger.info(f"Link collectors: {LINK_COLLECTORS}")
    else:
        logger.info("Link collectors: (none - not storing links)")

    # Create client
    session_path = Path(__file__).parent.parent / "data" / SESSION_NAME
    client = TelegramClient(
        str(session_path),
        API_ID,
        API_HASH,
        sequential_updates=False,
        flood_sleep_threshold=10,
        catch_up=True,
    )

    # Register client for deferred enrichment (media, reply chains)
    from bridge.enrichment import set_telegram_client

    set_telegram_client(client)

    @client.on(events.NewMessage)
    async def handler(event):
        """Handle incoming messages."""
        # Skip outgoing messages
        if event.out:
            return

        # Reject new messages during shutdown
        if SHUTTING_DOWN:
            logger.info("Ignoring message during shutdown")
            return

        # Dedup: skip if we've already processed this message (catch_up replay)
        from bridge.dedup import is_duplicate_message

        if await is_duplicate_message(event.chat_id, event.message.id):
            logger.debug(
                f"Skipping duplicate message {event.message.id} (catch_up replay)"
            )
            return

        # === BRIDGE COMMANDS (bypass agent entirely) ===
        _raw_text = (event.message.text or "").strip().lower()
        if _raw_text == "/update":
            # Only respond to /update if DMs are enabled or sender is whitelisted
            if event.is_private:
                sender = await event.get_sender()
                sender_id = getattr(sender, "id", None)
                if not RESPOND_TO_DMS and sender_id not in DM_WHITELIST:
                    logger.debug(
                        "Ignoring /update from DM - DMs disabled on this instance"
                    )
                    return
            await _handle_update_command(client, event)
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

        # Get sender username and ID for whitelist check
        sender_username = getattr(sender, "username", None)
        sender_id = getattr(sender, "id", None)

        # Store ALL incoming messages for history (regardless of whether we respond)
        try:
            store_result = store_message(
                chat_id=str(event.chat_id),
                content=text,
                sender=sender_name,
                message_id=message.id,
                timestamp=message.date,
                message_type=(
                    "text" if not message.media else get_media_type(message) or "media"
                ),
            )
            if store_result.get("stored"):
                logger.debug(f"Stored message {message.id} from {sender_name}")
                # Register chat mapping for CLI lookup
                if chat_title:
                    chat_type = "private" if is_dm else "group"
                    register_chat(
                        chat_id=str(event.chat_id),
                        chat_name=chat_title,
                        chat_type=chat_type,
                    )
            elif store_result.get("error"):
                logger.warning(f"Failed to store message: {store_result['error']}")
        except Exception as e:
            logger.error(f"Error storing message: {e}")

        # Extract and store links from whitelisted senders
        if sender_username and sender_username.lower() in LINK_COLLECTORS:
            try:
                urls_result = extract_urls(text)
                for url in urls_result.get("urls", []):
                    link_result = store_link(
                        url=url,
                        sender=sender_name,
                        chat_id=str(event.chat_id),
                        message_id=message.id,
                        timestamp=message.date,
                    )
                    if link_result.get("stored"):
                        logger.info(f"Stored link from {sender_name}: {url[:50]}...")
                    elif link_result.get("error"):
                        logger.warning(f"Failed to store link: {link_result['error']}")
            except Exception as e:
                logger.error(f"Error extracting/storing links: {e}")

        # Check if we should respond (async for Ollama classification on unaddressed messages)
        should_reply, is_reply_to_valor = await should_respond_async(
            client,
            event,
            text,
            is_dm,
            chat_title,
            project,
            sender_name,
            sender_username,
            sender_id,
        )
        if not should_reply:
            if is_dm and DM_WHITELIST:
                logger.debug(
                    f"Ignoring DM from {sender_name} (id={sender_id}) - not in whitelist"
                )
            return

        project_name = project.get("name", "DM") if project else "DM"
        message_id = message.id
        logger.info(
            f"[{project_name}] Message {message_id} from "
            f"{sender_name} in {chat_title or 'DM'}: {text[:50]}..."
        )
        logger.debug(f"[{project_name}] Full message text: {text}")

        # Log incoming message event
        log_event(
            "message_received",
            message_id=message_id,
            project=project_name,
            sender=sender_name,
            sender_username=sender_username,
            chat=chat_title,
            is_dm=is_dm,
            text_length=len(text),
            has_media=bool(message.media),
        )

        # --- Lightweight metadata extraction (enrichment deferred to worker) ---
        has_media = bool(message.media)
        media_type = get_media_type(message) if has_media else None

        # Clean the message text (no media/YouTube/link enrichment here)
        clean_text = clean_message(text, project)
        if not clean_text:
            clean_text = "Hello"

        # Extract YouTube URLs (lightweight -- no transcription yet)
        youtube_urls = extract_youtube_urls(text)

        # Extract non-YouTube URLs for deferred link summarization
        all_urls = extract_urls(text).get("urls", [])
        youtube_url_set = {u for u, _vid in youtube_urls} if youtube_urls else set()
        non_youtube_urls = [u for u in all_urls if u not in youtube_url_set]

        # Build session ID with reply-based continuity
        # - Reply to Valor's message → continue that session
        # - New message (no reply) → fresh session using message ID
        project_key = project.get("_key", "dm") if project else "dm"
        telegram_chat_id = str(event.chat_id)  # For history lookup

        # Use the is_reply_to_valor flag from should_respond_async
        # (already checked there, no need to query Telegram again)
        if is_reply_to_valor and message.reply_to_msg_id:
            # Continue the session from the replied message
            session_id = f"tg_{project_key}_{event.chat_id}_{message.reply_to_msg_id}"
            logger.debug(f"Session ID: {session_id} (continuation: True)")
        else:
            # Fresh session - use this message's ID as unique identifier
            session_id = f"tg_{project_key}_{event.chat_id}_{message.id}"
            logger.debug(f"Session ID: {session_id} (continuation: False)")

        # === REACTION WORKFLOW ===
        # 1. 👀 Eyes = Message received/acknowledged
        await set_reaction(client, event.chat_id, message.id, REACTION_RECEIVED)

        # Classify intent with Ollama (fast, for reaction emoji)
        async def classify_and_update_reaction():
            """Classify intent with Ollama and update reaction emoji."""
            emoji = await get_processing_emoji_async(clean_text)
            await set_reaction(client, event.chat_id, message.id, emoji)
            logger.debug(f"Intent classified, reaction set to {emoji}")

        # Start intent classification (don't await)
        asyncio.create_task(classify_and_update_reaction())

        # === SDK MODE: Job queue with per-session branching ===
        if USE_CLAUDE_SDK:
            import re as _re

            from agent.job_queue import (
                check_revival,
                enqueue_job,
                queue_revival_job,
                record_revival_cooldown,
            )
            from agent.steering import push_steering_message

            # Check if this is a reply to a revival notification
            # (stateless: read the replied-to message)
            if message.reply_to_msg_id:
                try:
                    replied_msg = await client.get_messages(
                        event.chat_id, ids=message.reply_to_msg_id
                    )
                    if (
                        replied_msg
                        and replied_msg.text
                        and replied_msg.text.startswith("Unfinished work detected")
                    ):
                        branch_match = _re.search(r"`([^`]+)`", replied_msg.text)
                        if branch_match:
                            revival_branch = branch_match.group(1)
                            working_dir_str = ""
                            if project:
                                working_dir_str = project.get(
                                    "working_directory",
                                    DEFAULTS.get("working_directory", ""),
                                )
                            if not working_dir_str:
                                working_dir_str = str(Path(__file__).parent.parent)
                            revival_info = {
                                "branch": revival_branch,
                                "project_key": project_key,
                                "session_id": session_id,
                                "working_dir": working_dir_str,
                            }
                            logger.info(
                                f"[{project_name}] Reply to revival "
                                "notification, queuing revival with context"
                            )
                            await queue_revival_job(
                                revival_info=revival_info,
                                chat_id=telegram_chat_id,
                                message_id=message.id,
                                additional_context=clean_text,
                            )
                            await set_reaction(
                                client, event.chat_id, message.id, REACTION_RECEIVED
                            )
                            return
                except Exception as e:
                    logger.debug(f"Revival reply check error: {e}")

            # === STEERING CHECK: Reply to running session → inject, don't queue ===
            if is_reply_to_valor and message.reply_to_msg_id:
                try:
                    from models.sessions import AgentSession

                    active_sessions = AgentSession.query.filter(
                        session_id=session_id, status="active"
                    )
                    if active_sessions:
                        # Route to steering queue instead of job queue.
                        # push_steering_message auto-detects abort keywords.
                        from agent.steering import ABORT_KEYWORDS

                        is_abort = clean_text.strip().lower() in ABORT_KEYWORDS
                        push_steering_message(
                            session_id,
                            clean_text,
                            sender_name,
                            is_abort=is_abort,
                        )
                        ack_text = (
                            "Stopping current task."
                            if is_abort
                            else "Adding to current task"
                        )
                        await client.send_message(
                            event.chat_id, ack_text, reply_to=message.id
                        )
                        logger.info(
                            f"[{project_name}] Steered message into active session "
                            f"{session_id} ({'abort' if is_abort else 'steer'})"
                        )
                        return
                except Exception as e:
                    logger.warning(
                        f"[{project_name}] Steering check failed, falling through to queue: {e}"
                    )

            # Lightweight revival check (no SDK agent, just git state)
            working_dir_str = ""
            if project:
                working_dir_str = project.get(
                    "working_directory", DEFAULTS.get("working_directory", "")
                )
            if not working_dir_str:
                working_dir_str = str(Path(__file__).parent.parent)

            revival_info = check_revival(project_key, working_dir_str, telegram_chat_id)
            if revival_info:
                revival_msg = (
                    f"Unfinished work detected on branch `{revival_info['branch']}`"
                )
                if revival_info.get("plan_context"):
                    revival_msg += f"\n\n> {revival_info['plan_context']}"
                revival_msg += "\n\nReply to this message to resume."
                await client.send_message(event.chat_id, revival_msg)
                record_revival_cooldown(telegram_chat_id)
                logger.info(
                    f"[{project_name}] Sent revival prompt for branch {revival_info['branch']}"
                )

                # Mark the stale work as dormant so it doesn't re-trigger.
                # A reply to the revival message will re-queue via branch name in the text.
                try:
                    from agent.branch_manager import mark_work_done

                    mark_work_done(Path(working_dir_str), revival_info["branch"])
                    logger.info(
                        f"[{project_name}] Marked stale branch {revival_info['branch']} as dormant"
                    )
                except Exception as e:
                    logger.warning(
                        f"[{project_name}] Failed to mark stale work dormant: {e}"
                    )

            # Check if this is tracked work and create workflow if needed
            workflow_id = create_workflow_for_tracked_work(
                clean_text, working_dir_str, telegram_chat_id
            )

            # Serialize enrichment metadata for the job worker
            yt_urls_json = json.dumps(youtube_urls) if youtube_urls else None
            non_yt_urls_json = (
                json.dumps(non_youtube_urls) if non_youtube_urls else None
            )

            # Build and enqueue the job (HIGH priority — top of FILO stack)
            depth = await enqueue_job(
                project_key=project_key,
                session_id=session_id,
                working_dir=working_dir_str,
                message_text=clean_text,
                sender_name=sender_name,
                chat_id=telegram_chat_id,
                message_id=message.id,
                chat_title=chat_title,
                priority="high",
                sender_id=sender_id,
                workflow_id=workflow_id,
                has_media=has_media,
                media_type=media_type,
                youtube_urls=yt_urls_json,
                non_youtube_urls=non_yt_urls_json,
                reply_to_msg_id=message.reply_to_msg_id,
                chat_id_for_enrichment=telegram_chat_id,
            )
            if depth > 1:
                await client.send_message(
                    event.chat_id,
                    f"Queued (position {depth}). Working on a previous task first.",
                    reply_to=message.id,
                )

            logger.info(
                f"[{project_name}] Queued job for {sender_name} (msg {message_id}, depth={depth})"
            )

            # Record message as processed (dedup for catch_up replays)
            from bridge.dedup import record_message_processed

            await record_message_processed(event.chat_id, message.id)

        # === LEGACY MODE: Synchronous with retry ===
        else:
            try:
                agent_task = asyncio.create_task(
                    get_agent_response_with_retry(
                        clean_text,
                        session_id,
                        sender_name,
                        chat_title,
                        project,
                        telegram_chat_id,
                        client,
                        message.id,
                        sender_id,
                    )
                )

                # Wait for response (legacy blocking mode)
                response = await agent_task

                # Send response if there's content (files or text)
                sent_response = await send_response_with_files(client, event, response)

                # 👍 Thumbs up = Completed successfully
                await set_reaction(client, event.chat_id, message.id, REACTION_SUCCESS)

                if sent_response:
                    logger.info(
                        f"[{project_name}] Replied to {sender_name} (msg {message_id})"
                    )
                else:
                    logger.info(
                        f"[{project_name}] Processed message from "
                        f"{sender_name} (msg {message_id}) - no response needed"
                    )

                # Store in history
                try:
                    filtered_for_history = filter_tool_logs(response)
                    if filtered_for_history:
                        store_message(
                            chat_id=telegram_chat_id,
                            content=filtered_for_history[:1000],
                            sender="Valor",
                            timestamp=datetime.now(),
                            message_type="response",
                        )
                except Exception as e:
                    logger.warning(f"Failed to store response in history: {e}")

                # Log reply event
                log_event(
                    "reply_sent",
                    message_id=message_id,
                    project=project_name,
                    sender=sender_name,
                    response_length=len(response),
                )

            except Exception as e:
                # ❌ Error = Something went wrong
                await set_reaction(client, event.chat_id, message.id, REACTION_ERROR)
                logger.error(
                    f"[{project_name}] Error processing message from {sender_name}: {e}"
                )
                raise

    # Register signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()

    def _shutdown_handler(sig, frame):
        global SHUTTING_DOWN
        sig_name = signal.Signals(sig).name
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        SHUTTING_DOWN = True
        # Schedule client disconnect on the event loop
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(_graceful_shutdown(client))
        )

    async def _graceful_shutdown(tg_client):
        """Reset in-flight jobs and disconnect."""
        if USE_CLAUDE_SDK:
            from agent.job_queue import _reset_running_jobs

            for _pkey in ACTIVE_PROJECTS:
                try:
                    reset = await _reset_running_jobs(_pkey)
                    if reset:
                        logger.info(
                            f"[{_pkey}] Reset {reset} running job(s) to pending"
                        )
                except Exception as e:
                    logger.error(f"[{_pkey}] Failed to reset running jobs: {e}")
        logger.info("Waiting 2s for in-flight tasks to finish...")
        await asyncio.sleep(2)
        logger.info("Disconnecting Telegram client...")
        await tg_client.disconnect()

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # Clean up stale session locks before attempting to connect
    killed = _cleanup_session_locks()
    if killed:
        logger.info(f"Cleaned up {killed} stale process(es) holding session locks")

    # Start the client (retry on SQLite session lock with exponential backoff)
    # Backoff: 2s, 5s, 10s (with jitter added by cleanup function)
    logger.info("Starting Telegram bridge...")
    backoff_times = [2, 5, 10]
    for _attempt in range(1, 4):
        try:
            await client.start(phone=PHONE, password=PASSWORD)
            break
        except Exception as e:
            if "database is locked" in str(e) and _attempt < 3:
                # Try cleanup again before retry
                _cleanup_session_locks()
                wait_time = backoff_times[_attempt - 1]
                logger.warning(
                    f"Session DB locked (attempt {_attempt}/3), retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
            else:
                raise
    logger.info("Connected to Telegram")

    # Replay any dead-lettered messages from previous session
    try:
        from bridge.dead_letters import replay_dead_letters

        replayed = await replay_dead_letters(client)
        if replayed:
            logger.info(f"Replayed {replayed} dead-lettered message(s)")
    except Exception as e:
        logger.error(f"Dead letter replay failed: {e}")

    # Register job queue callbacks for each project
    if USE_CLAUDE_SDK:
        from agent.job_queue import (
            cleanup_stale_branches,
            register_project_config,
        )
        from agent.job_queue import register_callbacks as register_queue_callbacks

        for _pkey, _pconfig in CONFIG.get("projects", {}).items():
            # Register project config so job queue can read auto_merge etc.
            register_project_config(_pkey, _pconfig)
            _wd = _pconfig.get(
                "working_directory", DEFAULTS.get("working_directory", "")
            )
            if not _wd:
                continue

            # Create send callback that uses the Telegram client
            async def _make_send_cb(_client=client):
                async def _send(chat_id: str, text: str, reply_to_msg_id: int) -> None:
                    try:
                        filtered = filter_tool_logs(text)
                        if filtered:
                            sent = await send_response_with_files(
                                _client,
                                None,
                                filtered,
                                chat_id=int(chat_id),
                                reply_to=reply_to_msg_id,
                            )
                            if sent:
                                try:
                                    store_message(
                                        chat_id=chat_id,
                                        content=filtered[:1000],
                                        sender="Valor",
                                        timestamp=datetime.now(),
                                        message_type="response",
                                    )
                                except Exception:
                                    pass
                            elif filtered:
                                logger.error(
                                    f"Job queue send returned False for chat {chat_id} "
                                    f"({len(filtered)} chars)"
                                )
                    except Exception as e:
                        logger.error(
                            f"Job queue _send callback failed for chat {chat_id}: {e}",
                            exc_info=True,
                        )

                return _send

            async def _make_react_cb(_client=client):
                async def _react(chat_id: str, msg_id: int, emoji: str | None) -> None:
                    await set_reaction(_client, int(chat_id), msg_id, emoji)

                return _react

            register_queue_callbacks(
                _pkey,
                await _make_send_cb(),
                await _make_react_cb(),
            )
            logger.info(f"[{_pkey}] Registered job queue callbacks")

            # Clean up stale session branches on startup
            cleaned = await cleanup_stale_branches(_wd)
            if cleaned:
                logger.info(f"[{_pkey}] Cleaned {len(cleaned)} stale session branches")

        # Register "dm" callback so DM responses actually get sent
        register_queue_callbacks(
            "dm",
            await _make_send_cb(),
            await _make_react_cb(),
        )
        logger.info("[dm] Registered job queue callbacks")

    # Clear stale restart flag from previous update (bridge has already restarted with new code)
    if USE_CLAUDE_SDK:
        from agent.job_queue import clear_restart_flag

        if clear_restart_flag():
            logger.info("Cleared stale restart flag from previous update")

    # Recover interrupted jobs and restart workers for any persisted jobs
    if USE_CLAUDE_SDK:
        from agent.job_queue import (
            _ensure_worker,
            _get_pending_jobs_sync,
            _recover_interrupted_jobs,
        )

        for _pkey in ACTIVE_PROJECTS:
            recovered = _recover_interrupted_jobs(_pkey)
            if recovered:
                logger.info(f"[{_pkey}] Recovered {recovered} interrupted job(s)")
            pending_jobs = _get_pending_jobs_sync(_pkey)
            if pending_jobs:
                logger.info(
                    f"[{_pkey}] Found {len(pending_jobs)} persisted job(s), restarting worker"
                )
                _ensure_worker(_pkey)

    # Scan for missed messages during downtime (catchup) -- run concurrently
    if USE_CLAUDE_SDK:

        async def _run_catchup():
            logger.info("Starting catchup scan for missed messages...")
            try:
                from agent.job_queue import enqueue_job as _enqueue_job
                from bridge.catchup import scan_for_missed_messages

                caught_up = await scan_for_missed_messages(
                    client=client,
                    monitored_groups=ALL_MONITORED_GROUPS,
                    projects_config=CONFIG,
                    should_respond_fn=should_respond_async,
                    enqueue_job_fn=_enqueue_job,
                    find_project_fn=find_project_for_chat,
                )
                logger.info(f"Catchup scan complete: {caught_up} message(s) queued")
            except Exception as e:
                logger.error(f"Catchup scan failed: {e}", exc_info=True)

        asyncio.create_task(_run_catchup())

    # Start session watchdog
    try:
        from monitoring.session_watchdog import watchdog_loop

        asyncio.create_task(watchdog_loop(telegram_client=client))
        logger.info("Session watchdog started")
    except Exception as e:
        logger.error(f"Failed to start session watchdog: {e}")

    # Start message query polling loop
    async def message_query_loop():
        """Poll for message query requests every second."""
        while True:
            try:
                await check_message_query_request(client)
            except Exception as e:
                logger.error(f"Message query check failed: {e}", exc_info=True)
            await asyncio.sleep(1)

    asyncio.create_task(message_query_loop())
    logger.info("Message query polling started")

    # Heartbeat: log periodically so the external watchdog sees fresh logs
    # (watchdog kills the bridge if logs are stale for 5 minutes)
    _bridge_start_time = time.time()

    async def heartbeat_loop():
        while True:
            await asyncio.sleep(120)
            uptime_min = int((time.time() - _bridge_start_time) / 60)
            logger.info(f"[heartbeat] Bridge alive (uptime={uptime_min}m)")

    asyncio.create_task(heartbeat_loop())

    # Keep running
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
