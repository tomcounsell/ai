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
import uuid
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
load_dotenv(Path.home() / "Desktop" / "Valor" / ".env")

# Initialize Sentry error tracking (skip gracefully if DSN not configured)
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    import sentry_sdk  # noqa: E402

    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
    )

# Claude Agent SDK is always used (legacy mode removed)

# Local tool imports for message and link storage
from telethon import TelegramClient, events  # noqa: E402
from telethon.errors import FloodWaitError  # noqa: E402

from agent import get_agent_response_sdk  # noqa: F401, E402
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
    REACTION_COMPLETE,
    REACTION_RECEIVED,
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
                                        logger.debug(f"Skipping young process {pid} (age: {etime})")
                                        continue
                    except (subprocess.TimeoutExpired, ValueError):
                        # Can't determine age, skip to be safe
                        continue

                    # Graceful shutdown: SIGTERM first, then SIGKILL if still alive
                    logger.warning(f"Sending SIGTERM to stale process {pid} holding {session_file}")
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
                        logger.warning(f"Process {pid} did not exit after SIGTERM, sending SIGKILL")
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


def _cleanup_orphaned_claude_processes() -> int:
    """Kill orphaned Claude Code CLI subprocesses from prior bridge runs.

    On bridge restart, SDK subprocesses from the old bridge may still be alive
    because the Python bridge only cancels asyncio tasks (not OS processes).
    These zombies block new workers via _ensure_worker's .done() check and
    consume resources.

    Finds all 'claude' processes whose parent is PID 1 (orphaned) or whose
    parent is the current bridge process (leftover from prior exec), then
    kills them with SIGTERM/SIGKILL.

    Returns the number of processes killed.
    """
    logger = logging.getLogger(__name__)
    killed = 0
    current_pid = os.getpid()

    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude_agent_sdk/_bundled/claude"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0

        pids = result.stdout.strip().split("\n")
        for pid_str in pids:
            try:
                pid = int(pid_str.strip())
                if pid == current_pid:
                    continue

                # Check parent PID — if PPID is 1 (orphaned) or our PID
                # (child of current bridge from a prior run), it's stale
                ppid_result = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if ppid_result.returncode != 0:
                    continue

                ppid = int(ppid_result.stdout.strip())

                # Only kill if truly orphaned (PPID=1, meaning parent died)
                if ppid != 1:
                    continue

                logger.warning(
                    "[cleanup] Killing orphaned Claude subprocess PID %d (PPID=%d)",
                    pid,
                    ppid,
                )
                os.kill(pid, signal.SIGTERM)
                # Wait up to 3 seconds for graceful exit
                for _ in range(6):
                    time.sleep(0.5)
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                else:
                    logger.warning("[cleanup] Force-killing Claude subprocess PID %d", pid)
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                killed += 1

            except (ValueError, ProcessLookupError, PermissionError) as e:
                logger.debug("[cleanup] Could not kill PID %s: %s", pid_str, e)

    except subprocess.TimeoutExpired:
        logger.warning("[cleanup] Timeout scanning for orphaned Claude processes")
    except Exception as e:
        logger.debug("[cleanup] Error scanning for orphaned processes: %s", e)

    return killed


# Configuration (environment already loaded at top of file)
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
PASSWORD = os.getenv("TELEGRAM_PASSWORD", "")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "valor_bridge")

# Active projects: derived from machine field in projects.json matched against hostname.
# Each project is handled by exactly one machine — the config is the single source of truth.


def _get_active_projects() -> list[str]:
    """Determine active projects for this machine from config."""
    from bridge.routing import _resolve_config_path

    config_path = _resolve_config_path()
    if not config_path.exists():
        return ["valor"]

    with open(config_path) as f:
        config = json.load(f)

    # Get this machine's name (e.g. "Valor the Captain")
    try:
        hostname = subprocess.check_output(["scutil", "--get", "ComputerName"], text=True).strip()
    except Exception:
        hostname = ""

    hostname_normalized = hostname.lower()

    # Match projects where machine field matches this hostname
    matched = []
    for key, project in config.get("projects", {}).items():
        machine = project.get("machine", "")
        if machine.lower() == hostname_normalized:
            matched.append(key.lower())

    if matched:
        return matched

    # Fallback to env var if no machine matches (e.g. dev/test)
    env_val = os.getenv("ACTIVE_PROJECTS", "")
    if env_val:
        return [p.strip().lower() for p in env_val.split(",") if p.strip()]

    return ["valor"]


ACTIVE_PROJECTS = _get_active_projects()

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
# Use JSON formatter for structured logging (parseable by log aggregation tools)
# Falls back to plain text if the module can't be imported
try:
    from bridge.log_format import StructuredJsonFormatter

    file_handler.setFormatter(StructuredJsonFormatter())
except ImportError:
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
    CONFIG.get("projects", {}).get(p, {}).get("telegram", {}).get("respond_to_dms", True)
    for p in ACTIVE_PROJECTS
)

# DM whitelist - only respond to DMs from these Telegram user IDs
# Loaded from ~/Desktop/Valor/dm_whitelist.json
# Falls back to TELEGRAM_DM_WHITELIST env var
# Format: {"users": {"123456": {"name": "Name", "permissions": "full|qa_only"}}}
DM_WHITELIST: set[int] = set()
DM_WHITELIST_CONFIG: dict[int, dict] = {}  # Full config per user for permissions lookup
_dm_whitelist_path = Path.home() / "Desktop" / "Valor" / "dm_whitelist.json"
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

from bridge.update import (  # noqa: E402
    handle_force_update_command,
    handle_update_command,
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
            sender_name = getattr(sender, "first_name", "Unknown") if sender else "Unknown"

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

        logger.info(f"Message query completed: fetched {len(formatted_messages)} messages")

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
    logger.info("Agent backend: Claude Agent SDK")
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
            logger.debug(f"Skipping duplicate message {event.message.id} (catch_up replay)")
            return

        # === BRIDGE COMMANDS (bypass agent entirely) ===
        _raw_text = (event.message.text or "").strip().lower()
        if _raw_text in ("/update", "/update --force", "/update \u2014force"):
            # Only respond to /update if DMs are enabled or sender is whitelisted
            if event.is_private:
                sender = await event.get_sender()
                sender_id = getattr(sender, "id", None)
                if not RESPOND_TO_DMS and sender_id not in DM_WHITELIST:
                    logger.debug("Ignoring /update from DM - DMs disabled on this instance")
                    return
            if _raw_text in ("/update --force", "/update \u2014force"):
                await handle_force_update_command(client, event)
            else:
                await handle_update_command(client, event)
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
        _early_project_key = project.get("_key", "dm") if project else "dm"
        stored_msg_id = None  # Track for telegram_message_key cross-reference
        try:
            store_result = store_message(
                chat_id=str(event.chat_id),
                content=text,
                sender=sender_name,
                message_id=message.id,
                timestamp=message.date,
                message_type=("text" if not message.media else get_media_type(message) or "media"),
                project_key=_early_project_key,
                has_media=bool(message.media),
                media_type=get_media_type(message) if message.media else None,
                reply_to_msg_id=message.reply_to_msg_id,
            )
            if store_result.get("stored"):
                stored_msg_id = store_result.get("id")
                logger.debug(f"Stored message {message.id} from {sender_name}")
                # Register chat mapping for CLI lookup
                if chat_title:
                    chat_type = "private" if is_dm else "group"
                    register_chat(
                        chat_id=str(event.chat_id),
                        chat_name=chat_title,
                        chat_type=chat_type,
                        project_key=_early_project_key,
                    )
            elif store_result.get("error"):
                logger.warning(f"Failed to store message: {store_result['error']}")
        except Exception as e:
            logger.error(f"Error storing message: {e}")

        # Save to subconscious memory (non-fatal, never crashes bridge)
        try:
            if text and text.strip() and not getattr(sender, "bot", False):
                from popoto import InteractionWeight

                from models.memory import Memory

                Memory.safe_save(
                    agent_id=sender_name or "unknown",
                    project_key=_early_project_key,
                    content=text[:500],
                    importance=InteractionWeight.HUMAN,
                    source="human",
                )
        except Exception as e:
            logger.warning(f"Memory save failed (non-fatal): {e}")

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
                        project_key=_early_project_key,
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
                logger.debug(f"Ignoring DM from {sender_name} (id={sender_id}) - not in whitelist")
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
            logger.info(f"[routing] Session {session_id} (continuation=True)")
        else:
            # No reply-to: try semantic routing before creating a fresh session
            session_id = None

            try:
                from bridge.session_router import (
                    find_matching_session,
                    is_semantic_routing_enabled,
                )

                if is_semantic_routing_enabled():
                    matched_id, confidence = await find_matching_session(
                        chat_id=telegram_chat_id,
                        message_text=clean_text,
                        project_key=project_key,
                    )
                    if matched_id:
                        # Check if matched session is active (running/active).
                        # If so, queue the message as a steering message instead
                        # of creating a competing job. (#318)
                        try:
                            from models.agent_session import AgentSession

                            matched_sessions = list(
                                AgentSession.query.filter(session_id=matched_id)
                            )
                            matched_session = matched_sessions[0] if matched_sessions else None
                            if matched_session and matched_session.status in (
                                "running",
                                "active",
                            ):
                                # Active session: queue steering message, ack, return
                                from agent.steering import ABORT_KEYWORDS, push_steering_message

                                is_abort = clean_text.strip().lower() in ABORT_KEYWORDS
                                push_steering_message(
                                    matched_id,
                                    clean_text,
                                    sender_name,
                                    is_abort=is_abort,
                                )
                                ack_text = (
                                    "Stopping current task."
                                    if is_abort
                                    else "Noted \u2014 I'll incorporate this on my next checkpoint."
                                )
                                from bridge.markdown import send_markdown

                                await send_markdown(
                                    client, event.chat_id, ack_text, reply_to=message.id
                                )
                                await set_reaction(
                                    client, event.chat_id, message.id, REACTION_RECEIVED
                                )
                                action = "abort" if is_abort else "steer"
                                logger.info(
                                    f"[routing] Semantic routing: steered unthreaded message "
                                    f"into {matched_session.status} session {matched_id} "
                                    f"({action}, confidence: {confidence:.2f})"
                                )
                                return
                        except Exception as e:
                            # Steering into active session failed — fall through
                            # to normal routing (use matched_id as session_id)
                            logger.warning(
                                f"Semantic routing active session check failed (non-fatal): {e}"
                            )

                        # Dormant or other status: use matched session as before
                        session_id = matched_id
                        logger.info(
                            f"[routing] Semantic routing: matched session {session_id} "
                            f"(confidence: {confidence:.2f})"
                        )
                    else:
                        logger.info("[routing] Semantic routing: no_match")
            except Exception as e:
                # Semantic routing failures are non-fatal — fall through
                # to fresh session creation
                logger.warning(f"Semantic routing failed (non-fatal): {e}")

            if not session_id:
                # Fresh session - use this message's ID as unique identifier
                session_id = f"tg_{project_key}_{event.chat_id}_{message.id}"
                logger.info(f"[routing] Session {session_id} (continuation=False)")

        # === REACTION WORKFLOW ===
        # 1. 👀 Eyes = Message received/acknowledged
        await set_reaction(client, event.chat_id, message.id, REACTION_RECEIVED)

        # Classify intent with Ollama (fast, for reaction emoji)
        classification_result = {}  # Mutable container for async classification result

        async def classify_and_update_reaction():
            """Classify intent with Ollama and update reaction emoji."""
            emoji = await get_processing_emoji_async(clean_text)
            await set_reaction(client, event.chat_id, message.id, emoji)
            logger.debug(f"Intent classified, reaction set to {emoji}")
            # Also classify work type (non-blocking, result stored for enqueue)
            try:
                from tools.classifier import classify_request_async

                result = await classify_request_async(clean_text)
                classification_result["type"] = result.get("type")
                classification_result["confidence"] = result.get("confidence")
                logger.debug(
                    f"Work classified as {result.get('type')} "
                    f"(confidence: {result.get('confidence')})"
                )
            except Exception as e:
                logger.debug(f"Work classification failed (non-fatal): {e}")

        # Start intent classification (don't await)
        asyncio.create_task(classify_and_update_reaction())

        # Synchronous fast-path: PR/issue references always mean SDLC work.
        # The async classifier above may not finish before enqueue_job runs,
        # causing classification_type=None → default "question". This fast-path
        # guarantees correct classification for PR/issue messages. See issue #478 postmortem.
        import re as _re_cls

        if _re_cls.search(
            r"(?:issue|pr|pull request)\s+#?\d+", clean_text.lower()
        ) or _re_cls.match(r"^#\d+$", clean_text.strip().lower()):
            classification_result["type"] = "sdlc"
            logger.info(
                f"[routing] Fast-path SDLC classification (PR/issue reference): {clean_text[:120]}"
            )

        # === Job queue with per-session branching ===
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
                replied_msg = await client.get_messages(event.chat_id, ids=message.reply_to_msg_id)
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
                        await set_reaction(client, event.chat_id, message.id, REACTION_RECEIVED)
                        return
            except Exception as e:
                logger.debug(f"Revival reply check error: {e}")

        # === STEERING CHECK: Reply to running session → inject, don't queue ===
        # This is the FAST PATH for direct Telegram replies to running sessions.
        # The intake classifier (below) handles non-reply interjections.
        if is_reply_to_valor and message.reply_to_msg_id:
            try:
                from models.agent_session import AgentSession

                # Check both "running" and "active" statuses -- "running" is the
                # primary status during agent execution (set by _pop_job), while
                # "active" is set later by _execute_job for auto-continue deferral.
                # Both represent "agent is currently working" for steering purposes.
                matching_session = None
                for check_status in ("running", "active"):
                    sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
                    if sessions:
                        matching_session = sessions[0]
                        break

                if matching_session:
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
                    ack_text = "Stopping current task." if is_abort else "Adding to current task"
                    from bridge.markdown import send_markdown

                    await send_markdown(client, event.chat_id, ack_text, reply_to=message.id)
                    action = "abort" if is_abort else "steer"
                    logger.info(
                        f"[{project_name}] Steered message into "
                        f"{matching_session.status} session "
                        f"{session_id} ({action})"
                    )
                    return
                else:
                    # No running/active session found -- check for pending (race window)
                    pending_sessions = AgentSession.query.filter(
                        session_id=session_id, status="pending"
                    )
                    if pending_sessions:
                        logger.info(
                            f"[{project_name}] Steering check found session {session_id} "
                            f"in 'pending' status -- message will queue normally and be "
                            f"consumed when the job starts via PostToolUse hook"
                        )
            except (ConnectionError, OSError) as e:
                # Redis/DB connection errors -- log at ERROR with traceback
                logger.error(
                    f"[{project_name}] Steering check failed due to connection error, "
                    f"falling through to queue: {e}",
                    exc_info=True,
                )
            except Exception as e:
                # Unexpected errors -- log at ERROR with traceback for visibility
                logger.error(
                    f"[{project_name}] Steering check failed unexpectedly, "
                    f"falling through to queue: {e}",
                    exc_info=True,
                )

        # === INTAKE CLASSIFIER: Haiku triage for non-reply messages (#320) ===
        # Runs on messages that didn't hit the reply-to fast path above.
        # Classifies intent as interjection/new_work/acknowledgment to decide routing.
        # This catches follow-up messages sent WITHOUT using Telegram's reply feature.
        if not (is_reply_to_valor and message.reply_to_msg_id):
            try:
                from models.agent_session import AgentSession

                # Find active/running/dormant sessions in this chat
                active_sessions = []
                for check_status in ("running", "active", "dormant"):
                    sessions = AgentSession.query.filter(
                        chat_id=telegram_chat_id, status=check_status
                    )
                    if sessions:
                        active_sessions.extend(sessions)

                if active_sessions:
                    # Pick the most recent session (by last_activity or created_at)
                    target_session = max(
                        active_sessions,
                        key=lambda s: s.last_activity or s.created_at or 0,
                    )

                    # Classify message intent with Haiku
                    from tools.classifier import classify_message_intent_async

                    intent_result = await classify_message_intent_async(
                        message=clean_text,
                        session_context=target_session.context_summary or "",
                        session_expectations=target_session.expectations or "",
                        session_status=target_session.status or "",
                    )

                    intent = intent_result.get("intent", "new_work")
                    confidence = intent_result.get("confidence", 0.0)
                    reason = intent_result.get("reason", "")

                    logger.info(
                        f"[{project_name}] Intake classifier: intent={intent} "
                        f"confidence={confidence:.2f} reason={reason!r} "
                        f"target_session={target_session.session_id}"
                    )

                    if intent == "interjection":
                        # Re-check session status (Race 1 mitigation: session may
                        # have completed during classification)
                        fresh_session = None
                        for check_status in ("running", "active"):
                            sessions = AgentSession.query.filter(
                                session_id=target_session.session_id,
                                status=check_status,
                            )
                            if sessions:
                                fresh_session = sessions[0]
                                break

                        if fresh_session:
                            # Push to AgentSession's queued_steering_messages
                            # for ChatSession to read
                            fresh_session.push_steering_message(clean_text)
                            from bridge.markdown import send_markdown

                            await send_markdown(
                                client,
                                event.chat_id,
                                "Adding to current task",
                                reply_to=message.id,
                            )
                            logger.info(
                                f"[{project_name}] Intake classifier routed "
                                f"interjection to session "
                                f"{fresh_session.session_id}"
                            )
                            # Also push to Redis steering queue so the
                            # PostToolUse hook picks it up immediately
                            push_steering_message(
                                fresh_session.session_id,
                                clean_text,
                                sender_name,
                            )
                            # Record as processed and return
                            from bridge.dedup import record_message_processed

                            await record_message_processed(event.chat_id, message.id)
                            return
                        else:
                            logger.info(
                                f"[{project_name}] Intake classifier: session "
                                f"{target_session.session_id} no longer "
                                f"running/active, falling through to enqueue"
                            )

                    elif intent == "acknowledgment":
                        # Only acknowledge dormant sessions with expectations
                        if target_session.status == "dormant" and target_session.expectations:
                            target_session.status = "completed"
                            target_session.log_lifecycle_transition(
                                "completed",
                                f"Acknowledged by {sender_name}: {clean_text[:80]}",
                            )
                            target_session.save()
                            await set_reaction(
                                client,
                                event.chat_id,
                                message.id,
                                REACTION_COMPLETE,
                            )
                            logger.info(
                                f"[{project_name}] Intake classifier: "
                                f"acknowledged session "
                                f"{target_session.session_id} as complete"
                            )
                            from bridge.dedup import record_message_processed

                            await record_message_processed(event.chat_id, message.id)
                            return
                        else:
                            logger.info(
                                f"[{project_name}] Intake classifier: "
                                f"acknowledgment but session is "
                                f"{target_session.status} (not dormant with "
                                f"expectations), falling through to enqueue"
                            )

                    # intent == "new_work" or fallthrough: continue to enqueue

            except (ConnectionError, OSError) as e:
                logger.error(
                    f"[{project_name}] Intake classifier failed due to "
                    f"connection error, falling through to enqueue: {e}",
                    exc_info=True,
                )
            except Exception as e:
                logger.warning(
                    f"[{project_name}] Intake classifier failed, falling through to enqueue: {e}"
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
            revival_msg = f"Unfinished work detected on branch `{revival_info['branch']}`"
            checkpoint_ctx = revival_info.get("checkpoint_context", "")
            if checkpoint_ctx:
                revival_msg += f"\n\n{checkpoint_ctx}"
            elif revival_info.get("plan_context"):
                revival_msg += f"\n\n> {revival_info['plan_context']}"
            revival_msg += "\n\nReply to this message to resume."
            from bridge.markdown import send_markdown

            await send_markdown(client, event.chat_id, revival_msg)
            record_revival_cooldown(telegram_chat_id)
            logger.info(f"[{project_name}] Sent revival prompt for branch {revival_info['branch']}")

            # Mark the stale work as dormant so it doesn't re-trigger.
            # A reply to the revival message will re-queue via branch name in the text.
            try:
                from agent.branch_manager import mark_work_done

                mark_work_done(Path(working_dir_str), revival_info["branch"])
                logger.info(
                    f"[{project_name}] Marked stale branch {revival_info['branch']} as dormant"
                )
            except Exception as e:
                logger.warning(f"[{project_name}] Failed to mark stale work dormant: {e}")

        # Serialize URL metadata for TelegramMessage storage
        yt_urls_json = json.dumps(youtube_urls) if youtube_urls else None
        non_yt_urls_json = json.dumps(non_youtube_urls) if non_youtube_urls else None

        # Update TelegramMessage with URL metadata
        # (has_media, media_type, reply_to_msg_id were set at store_message time)
        if stored_msg_id and (yt_urls_json or non_yt_urls_json):
            try:
                from models.telegram import TelegramMessage

                stored_msgs = list(TelegramMessage.query.filter(msg_id=stored_msg_id))
                if stored_msgs:
                    tm = stored_msgs[0]
                    if yt_urls_json:
                        tm.youtube_urls = yt_urls_json
                    if non_yt_urls_json:
                        tm.non_youtube_urls = non_yt_urls_json
                    tm.save()
            except Exception as e:
                logger.debug(f"Failed to update TelegramMessage with URL metadata: {e}")

        # Generate correlation ID for end-to-end request tracing
        correlation_id = uuid.uuid4().hex[:12]
        logger.info(
            f"[{correlation_id}] Message received from {sender_name} "
            f"in {chat_title or 'DM'} (session={session_id})"
        )

        # Classification inheritance: if this is a reply-to continuation and
        # the async classifier hasn't completed yet, inherit classification_type
        # from the original session. This prevents the race condition where
        # enqueue_job gets classification_type=None because the async task
        # hasn't finished. See issue #375 Bug 2.
        if is_reply_to_valor and message.reply_to_msg_id and not classification_result.get("type"):
            try:
                from models.agent_session import AgentSession

                existing_sessions = list(AgentSession.query.filter(session_id=session_id))
                if existing_sessions and existing_sessions[0].classification_type:
                    classification_result["type"] = existing_sessions[0].classification_type
                    logger.info(
                        f"[routing] Inherited classification_type="
                        f"{classification_result['type']} from existing session "
                        f"{session_id}"
                    )
            except Exception as e:
                logger.debug(f"Classification inheritance lookup failed (non-fatal): {e}")

        # Determine session_type based on chat title prefix.
        # "Dev: X" groups → DevSession (full permissions, dev persona)
        # Everything else → ChatSession (PM persona, orchestrates DevSessions)
        _classification = classification_result.get("type")
        if chat_title and chat_title.startswith("Dev:"):
            _session_type = "dev"  # DevSession — Dev persona, full permissions
            logger.info(f"[{project_name}] Dev group detected: {chat_title!r} → session_type=dev")
        else:
            _session_type = "chat"  # ChatSession — PM persona, handles both SDLC and Q&A

        # Enqueue: session_type drives ChatSession vs DevSession creation.
        depth = await enqueue_job(
            project_key=project_key,
            session_id=session_id,
            working_dir=working_dir_str,
            message_text=clean_text,
            sender_name=sender_name,
            chat_id=telegram_chat_id,
            telegram_message_id=message.id,
            chat_title=chat_title,
            priority="normal",
            sender_id=sender_id,
            classification_type=_classification,
            correlation_id=correlation_id,
            telegram_message_key=stored_msg_id,
            session_type=_session_type,
        )
        logger.info(
            f"[{project_name}] Queued job for {sender_name} (msg {message_id}, depth={depth})"
        )

        # Record message as processed (dedup for catch_up replays)
        from bridge.dedup import record_message_processed

        await record_message_processed(event.chat_id, message.id)

    # Register signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()

    def _shutdown_handler(sig, frame):
        global SHUTTING_DOWN
        sig_name = signal.Signals(sig).name
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        SHUTTING_DOWN = True
        # Schedule client disconnect on the event loop
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_graceful_shutdown(client)))

    async def _graceful_shutdown(tg_client):
        """Cancel workers, kill SDK subprocesses, and disconnect.

        Running jobs will be recovered at next startup by
        _recover_interrupted_jobs_startup() which resets all running
        jobs to pending unconditionally.
        """
        from agent.job_queue import _active_workers

        # Cancel all worker asyncio tasks
        for _pkey, worker_task in list(_active_workers.items()):
            if worker_task and not worker_task.done():
                worker_task.cancel()
                logger.info(f"[{_pkey}] Cancelled worker task")
        _active_workers.clear()

        # Kill SDK subprocesses so they don't survive as orphans
        orphans = _cleanup_orphaned_claude_processes()
        if orphans:
            logger.info(f"Killed {orphans} SDK subprocess(es) during shutdown")

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

    # Kill orphaned Claude Code CLI subprocesses from prior bridge runs
    orphans_killed = _cleanup_orphaned_claude_processes()
    if orphans_killed:
        logger.info(f"Killed {orphans_killed} orphaned Claude Code subprocess(es)")

    # Connect using existing session only — never trigger SendCodeRequest.
    # The bridge cannot collect auth codes interactively; use telegram_login.py for that.
    # Retries handle transient network errors with exponential backoff.
    import random

    logger.info("Starting Telegram bridge...")
    max_attempts = 8
    for _attempt in range(1, max_attempts + 1):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.error(
                    "Telegram session is not authorized. "
                    "Run 'python scripts/telegram_login.py' to authenticate first."
                )
                raise SystemExit(1)
            break
        except FloodWaitError as e:
            logger.warning(
                "FloodWaitError: Telegram requires a %d second wait. Sleeping until ready...",
                e.seconds,
            )
            await asyncio.sleep(e.seconds + 5)
            continue
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as e:
            if _attempt >= max_attempts:
                logger.error(
                    "Failed to connect to Telegram after %d attempts: %s",
                    max_attempts,
                    e,
                )
                raise
            # Exponential backoff with jitter, capped at 256s
            base_delay = min(2**_attempt, 256)
            jitter = random.uniform(0, base_delay * 0.2)
            wait_time = base_delay + jitter
            logger.warning(
                "Connection attempt %d/%d failed (%s: %s), retrying in %.1fs...",
                _attempt,
                max_attempts,
                type(e).__name__,
                str(e)[:200],
                wait_time,
            )
            if "database is locked" in str(e):
                _cleanup_session_locks()
            await asyncio.sleep(wait_time)
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
    from agent.job_queue import (
        cleanup_stale_branches,
        register_project_config,
    )
    from agent.job_queue import register_callbacks as register_queue_callbacks

    for _pkey, _pconfig in CONFIG.get("projects", {}).items():
        # Register project config so job queue can read auto_merge etc.
        register_project_config(_pkey, _pconfig)
        _wd = _pconfig.get("working_directory", DEFAULTS.get("working_directory", ""))
        if not _wd:
            continue

        # Create send callback that uses the Telegram client
        async def _make_send_cb(_client=client):
            async def _send(chat_id: str, text: str, reply_to_msg_id: int, session=None) -> None:
                try:
                    filtered = filter_tool_logs(text)
                    if filtered:
                        sent = await send_response_with_files(
                            _client,
                            None,
                            filtered,
                            chat_id=int(chat_id),
                            reply_to=reply_to_msg_id,
                            session=session,
                        )
                        if sent:
                            try:
                                store_message(
                                    chat_id=chat_id,
                                    content=filtered,  # full content, no truncation
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
    from agent.job_queue import clear_restart_flag

    if clear_restart_flag():
        logger.info("Cleared stale restart flag from previous update")

    # Recover interrupted jobs and restart workers for any persisted jobs
    from agent.job_queue import (
        _ensure_worker,
        _get_pending_jobs_sync,
        _recover_interrupted_jobs_startup,
    )

    # Clean up stale Redis keys with invalid job_id format (e.g. 60-char
    # keys from old data). This silences the popoto "auto key value is length
    # N" validation errors that spam the error log on every query.
    # Temporarily suppress popoto's "{clean} is for debugging" warning.
    try:
        from models.agent_session import AgentSession

        _popoto_keys_logger = logging.getLogger("POPOTO.Query")
        _prev_level = _popoto_keys_logger.level
        _popoto_keys_logger.setLevel(logging.ERROR)
        try:
            AgentSession.query.keys(clean=True)
        finally:
            _popoto_keys_logger.setLevel(_prev_level)
        logger.info("Cleaned stale Redis keys for AgentSession")
    except Exception as _clean_err:
        logger.warning(f"Redis key cleanup failed (non-fatal): {_clean_err}")

    # Unified startup recovery: reset ALL running jobs to pending
    # (at startup, all running jobs are orphaned from previous process)
    recovered = _recover_interrupted_jobs_startup()
    if recovered:
        logger.info(f"Recovered {recovered} interrupted job(s) at startup")

    # Restart workers for pending jobs across all projects
    for _pkey in ACTIVE_PROJECTS:
        pending_jobs = _get_pending_jobs_sync(_pkey)
        if pending_jobs:
            logger.info(f"[{_pkey}] Found {len(pending_jobs)} persisted job(s), restarting workers")
            started_chats = set()
            for _job in pending_jobs:
                _cid = _job.chat_id or _pkey
                if _cid not in started_chats:
                    _ensure_worker(_cid)
                    started_chats.add(_cid)

    # Scan for missed messages during downtime (catchup) -- run concurrently

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

    # Start unified reflection scheduler (subsumes job health monitor and all recurring tasks)
    try:
        from agent.reflection_scheduler import ReflectionScheduler

        _reflection_scheduler = ReflectionScheduler()
        asyncio.create_task(_reflection_scheduler.start())
        logger.info("Reflection scheduler started (replaces standalone health monitor)")
    except Exception as e:
        logger.error(f"Failed to start reflection scheduler: {e}")
        # Fall back to standalone health monitor
        try:
            from agent.job_queue import _job_health_loop

            asyncio.create_task(_job_health_loop())
            logger.info("Fell back to standalone job health monitor")
        except Exception as e2:
            logger.error(f"Failed to start fallback health monitor: {e2}")

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

    # Start PM message relay (processes outbox queue from tools/send_telegram.py)
    try:
        from bridge.telegram_relay import relay_loop

        asyncio.create_task(relay_loop(client))
        logger.info("PM Telegram relay started")
    except Exception as e:
        logger.error(f"Failed to start PM Telegram relay: {e}")

    # Heartbeat: log periodically so the external watchdog sees fresh logs
    # (watchdog kills the bridge if logs are stale for 5 minutes)
    _bridge_start_time = time.time()

    async def heartbeat_loop():
        _last_zombie_cleanup = time.time()

        while True:
            await asyncio.sleep(30)
            uptime_min = int((time.time() - _bridge_start_time) / 60)

            # Check for orphaned pending jobs when no workers are active
            from agent.job_queue import _active_workers

            active_count = sum(1 for w in _active_workers.values() if not w.done())
            if active_count == 0:
                try:
                    from models.agent_session import AgentSession

                    for _pkey in ACTIVE_PROJECTS:
                        pending = await AgentSession.query.async_filter(
                            project_key=_pkey, status="pending"
                        )
                        for job in pending:
                            cid = job.chat_id or _pkey
                            _ensure_worker(cid)
                            logger.info(
                                f"[heartbeat] Started worker for orphaned job "
                                f"{job.job_id} (chat={cid})"
                            )
                except Exception as e:
                    logger.debug(f"[heartbeat] Job poll error: {e}")

            # Periodic zombie cleanup every 5 minutes
            now = time.time()
            if now - _last_zombie_cleanup >= 300:
                _last_zombie_cleanup = now
                try:
                    killed = _cleanup_orphaned_claude_processes()
                    if killed:
                        logger.warning(
                            f"[heartbeat] Zombie cleanup killed {killed} orphaned process(es)"
                        )
                except Exception as e:
                    logger.debug(f"[heartbeat] Zombie cleanup error: {e}")

            # Log heartbeat every 4th tick (~2min) for watchdog
            if uptime_min > 0 and uptime_min % 2 == 0:
                logger.info(
                    f"[heartbeat] Bridge alive (uptime={uptime_min}m, workers={active_count})"
                )

    asyncio.create_task(heartbeat_loop())

    # Keep running
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
