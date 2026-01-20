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

import httpx

# Local tool imports for message and link storage
from tools.telegram_history import store_message, store_link, get_recent_messages, get_link_by_url
from tools.link_analysis import (
    extract_urls,
    summarize_url_content,
    get_metadata,
    extract_youtube_urls,
    process_youtube_urls_in_text,
)
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaPhoto,
    ReactionEmoji,
)

# =============================================================================
# Media Directories
# =============================================================================

# Directory for downloaded media files
MEDIA_DIR = Path(__file__).parent.parent / "data" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# File Detection and Sending
# =============================================================================

# Explicit file marker: <<FILE:/path/to/file>>
FILE_MARKER_PATTERN = re.compile(r'<<FILE:([^>]+)>>')

# =============================================================================
# Response Filtering - Remove Tool Logs
# =============================================================================

# Patterns for tool execution logs that should be filtered from responses
TOOL_LOG_PATTERNS = [
    re.compile(r'^🛠️\s*exec:', re.IGNORECASE),      # Bash execution
    re.compile(r'^📖\s*read:', re.IGNORECASE),       # File read
    re.compile(r'^🔎\s*web_search:', re.IGNORECASE), # Web search
    re.compile(r'^✏️\s*edit:', re.IGNORECASE),       # File edit
    re.compile(r'^📝\s*write:', re.IGNORECASE),      # File write
    re.compile(r'^🔍\s*search:', re.IGNORECASE),     # Search
    re.compile(r'^📁\s*glob:', re.IGNORECASE),       # Glob
    re.compile(r'^🌐\s*fetch:', re.IGNORECASE),      # Web fetch
]


def filter_tool_logs(response: str) -> str:
    """
    Remove tool execution traces from response.

    Clawdbot may include lines like "🛠️ exec: ls -la" in stdout.
    These are internal logs, not meant for the user.

    Returns:
        Filtered response, or empty string if only logs remain.
    """
    if not response:
        return ""

    lines = response.split('\n')
    filtered = []

    for line in lines:
        stripped = line.strip()
        # Skip lines matching tool log patterns
        if any(pattern.match(stripped) for pattern in TOOL_LOG_PATTERNS):
            continue
        filtered.append(line)

    result = '\n'.join(filtered).strip()

    # If filtering removed everything meaningful, return empty
    # (response was just tool logs)
    if not result or len(result) < 5:
        return ""

    return result

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


# =============================================================================
# Media Receiving and Processing
# =============================================================================

# Voice/audio extensions
VOICE_EXTENSIONS = {'.ogg', '.oga', '.mp3', '.wav', '.m4a', '.opus'}

# Supported image extensions for vision
VISION_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def get_media_type(message) -> str | None:
    """Determine the type of media in a message."""
    if not message.media:
        return None

    if isinstance(message.media, MessageMediaPhoto):
        return "photo"

    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if doc:
            # Check for voice message
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeAudio):
                    if attr.voice:
                        return "voice"
                    return "audio"
            # Check for other document types
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    filename = attr.file_name.lower()
                    if any(filename.endswith(ext) for ext in VISION_EXTENSIONS):
                        return "image"
                    if any(filename.endswith(ext) for ext in VOICE_EXTENSIONS):
                        return "audio"
            return "document"

    return None


async def download_media(client: TelegramClient, message, prefix: str = "media") -> Path | None:
    """
    Download media from a Telegram message.

    Returns the path to the downloaded file, or None if download failed.
    """
    try:
        # Generate unique filename with timestamp
        timestamp = message.date.strftime("%Y%m%d_%H%M%S")
        media_type = get_media_type(message)

        # Determine extension
        ext = ".bin"
        if isinstance(message.media, MessageMediaPhoto):
            ext = ".jpg"
        elif isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc:
                for attr in doc.attributes:
                    if isinstance(attr, DocumentAttributeFilename):
                        ext = Path(attr.file_name).suffix.lower() or ext
                        break
                    if isinstance(attr, DocumentAttributeAudio):
                        if attr.voice:
                            ext = ".ogg"  # Telegram voice messages are typically ogg
                        break

        filename = f"{prefix}_{timestamp}_{message.id}{ext}"
        filepath = MEDIA_DIR / filename

        # Download
        await client.download_media(message, filepath)

        if filepath.exists():
            return filepath
        return None

    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to download media: {e}")
        return None


async def transcribe_voice(filepath: Path) -> str | None:
    """
    Transcribe voice/audio file using OpenAI Whisper API.

    Returns transcription text, or None if transcription failed.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logging.getLogger(__name__).warning("No OPENAI_API_KEY for voice transcription")
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(filepath, "rb") as f:
                files = {"file": (filepath.name, f, "audio/ogg")}
                data = {"model": "whisper-1"}
                headers = {"Authorization": f"Bearer {api_key}"}

                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    files=files,
                    data=data,
                    headers=headers,
                )

                if response.status_code == 200:
                    result = response.json()
                    return result.get("text", "").strip()
                else:
                    logging.getLogger(__name__).error(
                        f"Whisper API error: {response.status_code} - {response.text}"
                    )
                    return None

    except Exception as e:
        logging.getLogger(__name__).error(f"Voice transcription failed: {e}")
        return None


async def describe_image(filepath: Path) -> str | None:
    """
    Describe an image using Ollama LLaVA vision model.

    Returns image description text, or None if description failed.
    Falls back gracefully if Ollama or LLaVA is not available.
    """
    try:
        import ollama
    except ImportError:
        logging.getLogger(__name__).warning("ollama library not installed for image vision")
        return None

    try:
        # Run the synchronous ollama.chat in a thread pool to not block the event loop
        loop = asyncio.get_event_loop()

        def _describe():
            response = ollama.chat(
                model='llama3.2-vision',
                messages=[{
                    'role': 'user',
                    'content': 'Describe this image in detail. What do you see?',
                    'images': [str(filepath)]
                }]
            )
            return response['message']['content']

        description = await loop.run_in_executor(None, _describe)
        return description.strip() if description else None

    except Exception as e:
        logging.getLogger(__name__).error(f"Image description failed: {e}")
        return None


async def process_incoming_media(client: TelegramClient, message) -> tuple[str, list[Path]]:
    """
    Process media in an incoming message.

    Returns (description_text, list_of_file_paths).
    The description_text is meant to be prepended to the message for context.
    """
    media_type = get_media_type(message)
    if not media_type:
        return "", []

    # Download the media
    downloaded = await download_media(client, message, prefix=media_type)
    if not downloaded:
        return f"[User sent a {media_type} but download failed]", []

    files = [downloaded]
    description = ""

    if media_type == "voice":
        # Transcribe voice message
        transcription = await transcribe_voice(downloaded)
        if transcription:
            description = f"[Voice message transcription: \"{transcription}\"]"
        else:
            description = f"[User sent a voice message - saved to {downloaded.name}]"

    elif media_type in ("photo", "image"):
        # Use Ollama LLaVA to describe the image
        image_description = await describe_image(downloaded)
        if image_description:
            description = f"[User sent an image]\nImage description: {image_description}"
        else:
            # Fallback if vision model is not available
            description = f"[User sent an image - saved to {downloaded.name}]"

    elif media_type == "audio":
        # Try transcribing audio files too
        transcription = await transcribe_voice(downloaded)
        if transcription:
            description = f"[Audio file transcription: \"{transcription}\"]"
        else:
            description = f"[User sent an audio file - saved to {downloaded.name}]"

    elif media_type == "document":
        description = f"[User sent a document - saved to {downloaded.name}]"

    return description, files


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

# =============================================================================
# Logging Configuration
# =============================================================================

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Create formatters
CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s"

# Setup root logger
logging.basicConfig(
    level=logging.INFO,
    format=CONSOLE_FORMAT,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Add file handler for detailed logs
file_handler = logging.FileHandler(LOG_DIR / "bridge.log")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
logger.addHandler(file_handler)
logger.setLevel(logging.DEBUG)


def log_event(event_type: str, **kwargs) -> None:
    """Log a structured event for analysis."""
    import time
    event = {
        "timestamp": time.time(),
        "type": event_type,
        **kwargs
    }
    # Write to events log as JSON lines
    events_log = LOG_DIR / "bridge.events.jsonl"
    with open(events_log, "a") as f:
        f.write(json.dumps(event) + "\n")


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

# Link collectors - usernames whose links are automatically stored
# When these users share a URL, it gets saved with metadata
LINK_COLLECTORS = [name.strip().lower() for name in
    os.getenv("TELEGRAM_LINK_COLLECTORS", "").split(",") if name.strip()]

# Link summarization settings
MAX_LINKS_PER_MESSAGE = 5  # Don't summarize more than 5 links per message
LINK_SUMMARY_CACHE_HOURS = 24  # Don't re-summarize URLs within 24 hours

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


# =============================================================================
# Activity Context - What Valor is Working On
# =============================================================================

# Patterns that indicate the user is asking about current work/status
STATUS_QUESTION_PATTERNS = [
    re.compile(r"what.*(?:working|doing|up to)", re.IGNORECASE),
    re.compile(r"what.*status", re.IGNORECASE),
    re.compile(r"what'?s.*going on", re.IGNORECASE),
    re.compile(r"how.*going", re.IGNORECASE),
    re.compile(r"any.*updates?", re.IGNORECASE),
    re.compile(r"what.*progress", re.IGNORECASE),
    re.compile(r"what.*been doing", re.IGNORECASE),
    re.compile(r"catch me up", re.IGNORECASE),
    re.compile(r"what.*happening", re.IGNORECASE),
]


def is_status_question(text: str) -> bool:
    """Check if the message is asking about current work or status."""
    return any(pattern.search(text) for pattern in STATUS_QUESTION_PATTERNS)


def build_activity_context(working_dir: str | None = None) -> str:
    """
    Build context about recent project activity.

    This gives Valor awareness of recent work so status questions
    get informed answers instead of "nothing specific."
    """
    import subprocess

    context_parts = []

    # Use project working directory or default
    cwd = working_dir or str(Path(__file__).parent.parent)

    # Recent git commits (last 24h)
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--since=24 hours ago", "-5"],
            capture_output=True, text=True, timeout=5, cwd=cwd
        )
        if result.stdout.strip():
            context_parts.append(f"RECENT COMMITS (last 24h):\n{result.stdout.strip()}")
    except Exception as e:
        logger.debug(f"Could not get git log: {e}")

    # Current branch and status
    try:
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, cwd=cwd
        )
        if branch_result.stdout.strip():
            context_parts.append(f"CURRENT BRANCH: {branch_result.stdout.strip()}")

        status_result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=5, cwd=cwd
        )
        if status_result.stdout.strip():
            modified_files = status_result.stdout.strip().split('\n')[:5]
            context_parts.append(f"MODIFIED FILES:\n" + "\n".join(modified_files))
    except Exception as e:
        logger.debug(f"Could not get git status: {e}")

    # Recent plan docs
    plans_dir = Path(cwd) / "docs" / "plans"
    if plans_dir.exists():
        try:
            recent_plans = sorted(
                plans_dir.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )[:3]
            if recent_plans:
                plan_names = [p.stem for p in recent_plans]
                context_parts.append(f"ACTIVE PLANS: {', '.join(plan_names)}")
        except Exception as e:
            logger.debug(f"Could not get plan docs: {e}")

    if not context_parts:
        return ""

    return "ACTIVITY CONTEXT:\n" + "\n".join(context_parts)


def build_conversation_history(chat_id: str, limit: int = 5) -> str:
    """
    Build recent conversation history for context.

    Args:
        chat_id: Telegram chat ID (numeric, without prefix)
        limit: Number of recent messages to include

    Returns:
        Formatted conversation history string
    """
    result = get_recent_messages(str(chat_id), limit=limit)

    if "error" in result or not result.get("messages"):
        return ""

    messages = result["messages"]
    if not messages:
        return ""

    # Reverse to show oldest first (chronological order)
    messages = list(reversed(messages))

    history_lines = ["RECENT CONVERSATION:"]
    for msg in messages:
        sender = msg.get("sender", "Unknown")
        content = msg.get("content", "")
        # Truncate long messages
        if len(content) > 200:
            content = content[:200] + "..."
        history_lines.append(f"  {sender}: {content}")

    return "\n".join(history_lines)


# =============================================================================
# Link Summarization
# =============================================================================


async def get_link_summaries(
    text: str,
    sender: str,
    chat_id: str,
    message_id: int,
    timestamp,
) -> list[dict]:
    """
    Extract URLs from text and get summaries for each.

    Uses caching to avoid re-summarizing URLs we've seen recently.
    Applies rate limiting (max 5 links per message).

    Args:
        text: Message text containing URLs
        sender: Who shared the link
        chat_id: Telegram chat ID
        message_id: Telegram message ID
        timestamp: When the message was sent

    Returns:
        List of dicts with url, summary, title, and cached flag
    """
    # Extract URLs from message
    urls_result = extract_urls(text)
    urls = urls_result.get("urls", [])

    if not urls:
        return []

    # Rate limit: max 5 links per message
    urls = urls[:MAX_LINKS_PER_MESSAGE]
    if len(urls_result.get("urls", [])) > MAX_LINKS_PER_MESSAGE:
        logger.info(f"Rate limiting: only processing {MAX_LINKS_PER_MESSAGE} of {len(urls_result.get('urls', []))} links")

    summaries = []

    for url in urls:
        try:
            # Check cache: do we already have a summary for this URL?
            existing = get_link_by_url(url, max_age_hours=LINK_SUMMARY_CACHE_HOURS)

            if existing and existing.get("ai_summary"):
                # Use cached summary
                logger.debug(f"Using cached summary for: {url[:50]}...")
                summaries.append({
                    "url": url,
                    "summary": existing["ai_summary"],
                    "title": existing.get("title"),
                    "cached": True,
                })
                continue

            # Need to fetch new summary
            logger.info(f"Fetching summary for: {url[:50]}...")

            # Get metadata (title, description) synchronously
            metadata = get_metadata(url)
            title = metadata.get("title")
            description = metadata.get("description")
            final_url = metadata.get("final_url", url)

            # Get AI summary via Perplexity
            summary = await summarize_url_content(url)

            # Store the link with summary
            store_link(
                url=url,
                sender=sender,
                chat_id=chat_id,
                message_id=message_id,
                timestamp=timestamp,
                title=title,
                description=description,
                final_url=final_url,
                ai_summary=summary,
            )

            if summary:
                summaries.append({
                    "url": url,
                    "summary": summary,
                    "title": title,
                    "cached": False,
                })
                logger.info(f"Stored link with summary: {url[:50]}...")
            else:
                logger.warning(f"No summary generated for: {url[:50]}...")

        except Exception as e:
            logger.error(f"Error processing URL {url[:50]}...: {e}")
            continue

    return summaries


def format_link_summaries(summaries: list[dict]) -> str:
    """
    Format link summaries for inclusion in message context.

    Args:
        summaries: List of summary dicts from get_link_summaries()

    Returns:
        Formatted string to append to message
    """
    if not summaries:
        return ""

    parts = []
    for s in summaries:
        url = s["url"]
        summary = s["summary"]
        title = s.get("title", "")

        # Build the summary line
        if title:
            parts.append(f"[Link: {title}]\n{summary}")
        else:
            # Use a truncated URL as the header
            short_url = url[:60] + "..." if len(url) > 60 else url
            parts.append(f"[Link: {short_url}]\n{summary}")

    return "\n\n".join(parts)


# =============================================================================
# Reaction Status Workflow
# =============================================================================

# Reaction emojis for different stages
REACTION_RECEIVED = "👀"      # Message acknowledged
REACTION_PROCESSING = "🤔"    # Default thinking emoji
REACTION_SUCCESS = "👍"       # Completed successfully
REACTION_ERROR = "❌"         # Something went wrong

# Intent-specific processing emojis (classified by local Ollama)
INTENT_REACTIONS = {
    "search": "🔍",           # Searching the web
    "code_execution": "💻",   # Running code
    "image_generation": "🎨", # Creating an image
    "image_analysis": "👁️",   # Analyzing an image
    "file_operation": "📁",   # File operations
    "git_operation": "🔀",    # Git work
    "chat": "🤔",             # Thinking/conversation
    "tool_use": "🔧",         # Using a tool
    "system": "⚙️",           # System task
    "unknown": "🤔",          # Default thinking
}


def get_processing_emoji(message: str) -> str:
    """
    Get the appropriate processing emoji based on message intent.
    Uses local Ollama for fast classification.

    Args:
        message: The user's message

    Returns:
        Emoji string for the processing stage
    """
    try:
        from intent import classify_intent
        result = classify_intent(message, use_ollama=True)
        intent = result.get("intent", "unknown")
        return INTENT_REACTIONS.get(intent, REACTION_PROCESSING)
    except Exception as e:
        logger.debug(f"Intent classification failed: {e}")
        return REACTION_PROCESSING


async def get_processing_emoji_async(message: str) -> str:
    """
    Async wrapper for intent classification.
    Runs in executor to not block the event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_processing_emoji, message)


async def set_reaction(client: TelegramClient, chat_id: int, msg_id: int, emoji: str | None) -> bool:
    """
    Set a reaction on a message.

    Args:
        client: Telegram client
        chat_id: Chat ID
        msg_id: Message ID
        emoji: Emoji to react with, or None to remove reactions

    Returns:
        True if successful, False otherwise
    """
    try:
        reaction = [ReactionEmoji(emoticon=emoji)] if emoji else []
        await client(SendReactionRequest(
            peer=chat_id,
            msg_id=msg_id,
            reaction=reaction,
        ))
        return True
    except Exception as e:
        logger.debug(f"Could not set reaction '{emoji}': {e}")
        return False


async def send_response_with_files(client: TelegramClient, event, response: str) -> bool:
    """
    Send response to Telegram, handling both files and text.

    1. Filter out tool execution logs
    2. Extract any files from the response
    3. Send files first (as separate messages)
    4. Send remaining text (if any)

    Returns True if any content was sent, False otherwise.
    """
    # Filter out tool logs before processing
    response = filter_tool_logs(response)

    # If filtering removed everything, no response needed
    if not response:
        return False

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

    # Track if we sent anything
    sent_content = bool(files)

    # Send text if there's meaningful content
    if text and not text.isspace():
        # Truncate if needed
        max_length = DEFAULTS.get("response", {}).get("max_response_length", 4000)
        if len(text) > max_length:
            text = text[: max_length - 3] + "..."
        await event.reply(text)
        sent_content = True

    return sent_content


async def get_agent_response(message: str, session_id: str, sender_name: str, chat_title: str | None, project: dict | None, chat_id: str | None = None) -> str:
    """Call clawdbot agent and get response."""
    import time
    start_time = time.time()
    request_id = f"{session_id}_{int(start_time)}"

    # CRITICAL: Determine working directory to prevent agent from wandering into wrong directories
    if project:
        working_dir = project.get("working_directory", DEFAULTS.get("working_directory"))
    else:
        working_dir = DEFAULTS.get("working_directory")

    # Fallback to current directory if not configured (shouldn't happen)
    if not working_dir:
        working_dir = str(Path(__file__).parent.parent)
        logger.warning(f"[{request_id}] No working_directory configured, using {working_dir}")

    try:
        # Build context-enriched message
        context = build_context_prefix(project, chat_title is None)

        # Add recent conversation history for continuity
        history = ""
        if chat_id:
            history = build_conversation_history(chat_id, limit=5)

        # Check if this is a status question - inject activity context
        activity_context = ""
        if is_status_question(message):
            activity_context = build_activity_context(working_dir)
            logger.debug(f"[{request_id}] Status question detected, injecting activity context")

        enriched_message = context
        if activity_context:
            enriched_message += f"\n\n{activity_context}"
        if history:
            enriched_message += f"\n\n{history}"
        enriched_message += f"\n\nFROM: {sender_name}"
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

        # Log full request details
        logger.info(f"[{request_id}] Calling clawdbot agent for {project_name}")
        logger.debug(f"[{request_id}] Session: {session_id}")
        logger.debug(f"[{request_id}] Working directory: {working_dir}")
        logger.debug(f"[{request_id}] Command: {' '.join(cmd[:6])}...")
        logger.debug(f"[{request_id}] Enriched message:\n{enriched_message}")

        # Log structured event
        log_event(
            "agent_request",
            request_id=request_id,
            session_id=session_id,
            project=project_name,
            working_dir=working_dir,
            sender=sender_name,
            chat=chat_title,
            message_length=len(message),
            enriched_length=len(enriched_message),
        )

        timeout = DEFAULTS.get("response", {}).get("timeout_seconds", 300)

        # Run with timeout - CRITICAL: cwd ensures agent works in correct project directory
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "")},
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Kill the process and try to capture partial output
            elapsed = time.time() - start_time
            logger.error(f"[{request_id}] Agent request timed out after {elapsed:.1f}s")

            # Try to terminate gracefully first
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

            # Log structured timeout event
            log_event(
                "agent_timeout",
                request_id=request_id,
                session_id=session_id,
                elapsed_seconds=elapsed,
                timeout_seconds=timeout,
            )

            return "Request timed out. Please try again."

        elapsed = time.time() - start_time

        if process.returncode != 0:
            stderr_text = stderr.decode()
            logger.error(f"[{request_id}] Clawdbot error (exit {process.returncode}) after {elapsed:.1f}s")
            logger.error(f"[{request_id}] Stderr: {stderr_text[:500]}")

            log_event(
                "agent_error",
                request_id=request_id,
                session_id=session_id,
                exit_code=process.returncode,
                elapsed_seconds=elapsed,
                stderr_preview=stderr_text[:200],
            )

            return f"Error processing request: {stderr_text[:200]}"

        response = stdout.decode().strip()
        stderr_text = stderr.decode().strip()

        # Log success with timing
        logger.info(f"[{request_id}] Agent responded in {elapsed:.1f}s ({len(response)} chars)")
        logger.debug(f"[{request_id}] Response preview: {response[:200]}...")
        if stderr_text:
            logger.debug(f"[{request_id}] Stderr: {stderr_text[:200]}")

        log_event(
            "agent_response",
            request_id=request_id,
            session_id=session_id,
            elapsed_seconds=elapsed,
            response_length=len(response),
            has_stderr=bool(stderr_text),
        )

        return response

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[{request_id}] Error calling agent after {elapsed:.1f}s: {e}")
        logger.exception(f"[{request_id}] Full traceback:")

        log_event(
            "agent_exception",
            request_id=request_id,
            session_id=session_id,
            elapsed_seconds=elapsed,
            error=str(e),
            error_type=type(e).__name__,
        )

        return f"Error: {str(e)}"


# =============================================================================
# Retry with Self-Healing
# =============================================================================

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]  # Seconds between retries


async def attempt_self_healing(error: str, session_id: str) -> None:
    """
    Attempt to fix the cause of failure before retry.

    This runs basic diagnostics and cleanup to improve retry success.
    """
    logger.info(f"Attempting self-healing for session {session_id}: {error[:100]}")

    try:
        # Kill any stuck clawdbot processes
        kill_result = await asyncio.create_subprocess_exec(
            "pkill", "-f", "clawdbot agent",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(kill_result.wait(), timeout=5)
        logger.debug("Killed stuck clawdbot processes")
    except Exception as e:
        logger.debug(f"No stuck processes to kill: {e}")

    # Brief pause to let processes terminate
    await asyncio.sleep(1)


async def create_failure_plan(message: str, error: str, session_id: str) -> None:
    """
    Create a plan doc for failures that couldn't be self-healed.

    Instead of showing errors to the user, we document them for later review.
    """
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan_path = Path(__file__).parent.parent / "docs" / "plans" / f"fix-bridge-failure-{timestamp}.md"

    # Ensure plans directory exists
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    content = f"""# Fix Bridge Failure

**Status**: Todo
**Created**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
**Session**: {session_id}

## Error
{error}

## Original Message
{message[:500]}{"..." if len(message) > 500 else ""}

## Investigation Needed
- [ ] Review logs for this session
- [ ] Identify root cause
- [ ] Implement fix
- [ ] Test with similar message
"""

    plan_path.write_text(content)
    logger.info(f"Created failure plan: {plan_path.name}")

    # Log structured event
    log_event(
        "failure_plan_created",
        session_id=session_id,
        plan_file=plan_path.name,
        error_preview=error[:200],
    )


async def get_agent_response_with_retry(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
    client: TelegramClient | None = None,
    msg_id: int | None = None,
) -> str:
    """
    Call agent with retry and self-healing on failure.

    On timeout or error:
    1. Attempt self-healing (kill stuck processes)
    2. Wait with progressive backoff
    3. Retry up to MAX_RETRIES times

    If all retries fail, create a plan doc instead of showing error to user.
    """
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            # Update reaction to show retry attempt
            if attempt > 0 and client and msg_id:
                await set_reaction(client, int(chat_id) if chat_id else 0, msg_id, "🔄")
                logger.info(f"Retry attempt {attempt + 1}/{MAX_RETRIES}")

            response = await get_agent_response(
                message, session_id, sender_name,
                chat_title, project, chat_id
            )

            # Check if response looks like an error
            if response.startswith("Error:") or response.startswith("Request timed out"):
                last_error = response
                if attempt < MAX_RETRIES - 1:
                    await attempt_self_healing(response, session_id)
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue

            # Check if response is just tool logs (will be filtered to empty)
            filtered = filter_tool_logs(response)
            if not filtered and response:
                # Response was just logs - could indicate an issue
                last_error = "Response contained only tool logs"
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue

            return response

        except asyncio.TimeoutError:
            last_error = "timeout"
            if attempt < MAX_RETRIES - 1:
                await attempt_self_healing("timeout", session_id)
                await asyncio.sleep(RETRY_DELAYS[attempt])

        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                await attempt_self_healing(str(e), session_id)
                await asyncio.sleep(RETRY_DELAYS[attempt])

    # All retries failed - create plan doc for future fix
    await create_failure_plan(message, last_error or "Unknown error", session_id)

    # Return empty response - reaction will indicate status
    return ""


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
    if LINK_COLLECTORS:
        logger.info(f"Link collectors: {LINK_COLLECTORS}")
    else:
        logger.info("Link collectors: (none - not storing links)")

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

        # Store ALL incoming messages for history (regardless of whether we respond)
        try:
            store_result = store_message(
                chat_id=str(event.chat_id),
                content=text,
                sender=sender_name,
                message_id=message.id,
                timestamp=message.date,
                message_type="text" if not message.media else get_media_type(message) or "media",
            )
            if store_result.get("stored"):
                logger.debug(f"Stored message {message.id} from {sender_name}")
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

        # Check if we should respond
        if not should_respond(text, is_dm, chat_title, project, sender_name, sender_username):
            if is_dm and DM_WHITELIST:
                logger.debug(f"Ignoring DM from {sender_name} (@{sender_username}) - not in whitelist")
            return

        project_name = project.get("name", "DM") if project else "DM"
        message_id = message.id
        logger.info(f"[{project_name}] Message {message_id} from {sender_name} in {chat_title or 'DM'}: {text[:50]}...")
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

        # Process any incoming media (images, voice, documents)
        media_description = ""
        media_files = []
        if message.media:
            media_description, media_files = await process_incoming_media(client, message)
            if media_description:
                logger.info(f"Processed media: {media_description[:100]}...")

        # Clean the message
        clean_text = clean_message(text, project)

        # Combine text with media description
        if media_description:
            if clean_text:
                clean_text = f"{media_description}\n\n{clean_text}"
            else:
                clean_text = media_description

        if not clean_text:
            clean_text = "Hello"

        # Process YouTube URLs in the message (transcribe videos)
        youtube_urls = extract_youtube_urls(text)
        if youtube_urls:
            logger.info(f"Found {len(youtube_urls)} YouTube URL(s), processing...")
            try:
                enriched_text, youtube_results = await process_youtube_urls_in_text(text)
                # Count successful transcriptions
                successful = sum(1 for r in youtube_results if r.get("success"))
                if successful > 0:
                    # Replace clean_text with enriched version that includes transcripts
                    clean_text = clean_message(enriched_text, project)
                    if media_description:
                        clean_text = f"{media_description}\n\n{clean_text}"
                    logger.info(f"Successfully transcribed {successful}/{len(youtube_urls)} YouTube video(s)")
                else:
                    # Log errors but continue with original text
                    for r in youtube_results:
                        if r.get("error"):
                            logger.warning(f"YouTube processing failed for {r.get('video_id')}: {r.get('error')}")
            except Exception as e:
                logger.error(f"Error processing YouTube URLs: {e}")
                # Continue with original text on error

        # Get link summaries for any non-YouTube URLs in the message
        link_summaries = await get_link_summaries(
            text=text,
            sender=sender_name,
            chat_id=str(event.chat_id),
            message_id=message.id,
            timestamp=message.date,
        )
        link_summary_text = format_link_summaries(link_summaries)

        # Append link summaries to the message for context
        if link_summary_text:
            clean_text = f"{clean_text}\n\n--- LINK SUMMARIES ---\n{link_summary_text}"
            logger.info(f"Added {len(link_summaries)} link summaries to message context")

        # Build session ID with reply-based continuity
        # - Reply to Valor's message → continue that session
        # - New message (no reply) → fresh session using message ID
        project_key = project.get("_key", "dm") if project else "dm"
        telegram_chat_id = str(event.chat_id)  # For history lookup

        # Check if this is a reply to one of Valor's messages
        is_continuation = False
        continuation_msg_id = None

        if message.reply_to_msg_id:
            try:
                replied_msg = await client.get_messages(event.chat_id, ids=message.reply_to_msg_id)
                if replied_msg and replied_msg.out:  # .out means it was sent by us (Valor)
                    is_continuation = True
                    continuation_msg_id = message.reply_to_msg_id
                    logger.debug(f"Reply to Valor's message {continuation_msg_id} - continuing session")
            except Exception as e:
                logger.debug(f"Could not check replied message: {e}")

        if is_continuation and continuation_msg_id:
            # Continue the session from the replied message
            session_id = f"tg_{project_key}_{event.chat_id}_{continuation_msg_id}"
        else:
            # Fresh session - use this message's ID as unique identifier
            session_id = f"tg_{project_key}_{event.chat_id}_{message.id}"

        logger.debug(f"Session ID: {session_id} (continuation: {is_continuation})")

        # === REACTION WORKFLOW ===
        # 1. 👀 Eyes = Message received/acknowledged
        await set_reaction(client, event.chat_id, message.id, REACTION_RECEIVED)

        try:
            # 2. Start parallel tasks:
            #    - Fast: Ollama classifies intent -> update reaction emoji
            #    - Slow: Claude processes message -> get response

            async def classify_and_update_reaction():
                """Classify intent with Ollama and update reaction emoji."""
                emoji = await get_processing_emoji_async(clean_text)
                await set_reaction(client, event.chat_id, message.id, emoji)
                logger.debug(f"Intent classified, reaction set to {emoji}")

            # Start both tasks in parallel
            classification_task = asyncio.create_task(classify_and_update_reaction())
            agent_task = asyncio.create_task(
                get_agent_response_with_retry(
                    clean_text, session_id, sender_name, chat_title, project,
                    telegram_chat_id, client, message.id
                )
            )

            # Wait for both (classification will finish first, updating the reaction)
            # Agent response is what we actually need
            response = await agent_task

            # Cancel classification if it's somehow still running
            if not classification_task.done():
                classification_task.cancel()

            # Send response if there's content (files or text)
            sent_response = await send_response_with_files(client, event, response)

            # 3. 👍 Thumbs up = Completed successfully
            await set_reaction(client, event.chat_id, message.id, REACTION_SUCCESS)

            if sent_response:
                logger.info(f"[{project_name}] Replied to {sender_name} (msg {message_id})")
            else:
                logger.info(f"[{project_name}] Processed message from {sender_name} (msg {message_id}) - no response needed")

        except Exception as e:
            # 4. ❌ Error = Something went wrong
            await set_reaction(client, event.chat_id, message.id, REACTION_ERROR)
            logger.error(f"[{project_name}] Error processing message from {sender_name}: {e}")
            raise  # Re-raise to be caught by outer handler if needed

        # Store Valor's response in history for conversation continuity
        try:
            from datetime import datetime as dt
            store_message(
                chat_id=telegram_chat_id,
                content=response[:1000],  # Truncate long responses for history
                sender="Valor",
                timestamp=dt.now(),
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

    # Start the client
    logger.info("Starting Telegram bridge...")
    await client.start(phone=PHONE, password=PASSWORD)
    logger.info("Connected to Telegram")

    # Keep running
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
