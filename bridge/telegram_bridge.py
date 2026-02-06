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

import httpx
from dotenv import load_dotenv

# Load environment variables FIRST before any env checks
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Feature flag for Claude Agent SDK migration
# Set USE_CLAUDE_SDK=true in .env to use the new SDK instead of clawdbot
USE_CLAUDE_SDK = os.getenv("USE_CLAUDE_SDK", "false").lower() == "true"

# Import SDK client and messenger if enabled (lazy import to avoid loading if not used)
if USE_CLAUDE_SDK:
    from agent import get_agent_response_sdk

# Local tool imports for message and link storage
from telethon import TelegramClient, events
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaPhoto,
    ReactionEmoji,
)

from tools.link_analysis import (
    extract_urls,
    extract_youtube_urls,
    get_metadata,
    process_youtube_urls_in_text,
    summarize_url_content,
)
from tools.telegram_history import (
    get_link_by_url,
    get_recent_messages,
    register_chat,
    store_link,
    store_message,
)

# =============================================================================
# Media Directories
# =============================================================================

# Directory for downloaded media files
MEDIA_DIR = Path(__file__).parent.parent / "data" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Message Queue for Graceful Restart
# =============================================================================

# Shutdown flag - set by signal handlers to stop accepting new messages
SHUTTING_DOWN = False

# Project directory (for running scripts, checking flags, etc.)
_BRIDGE_PROJECT_DIR = Path(__file__).parent.parent


def _get_running_jobs_info() -> tuple[int, list[str]]:
    """Check for running jobs across all projects. Returns (count, descriptions).

    Note: This is a point-in-time check for user visibility only. The actual
    restart timing is handled by the job queue's restart flag system, which
    checks between jobs. Sessions may finish before the restart actually occurs.
    """
    try:
        from agent.job_queue import RedisJob

        running_jobs = RedisJob.query.filter(status="running")
        if not running_jobs:
            return 0, []

        descriptions = []
        for job in running_jobs:
            msg_preview = (job.message_text or "")[:50]
            if len(job.message_text or "") > 50:
                msg_preview += "..."
            descriptions.append(f"  • [{job.project_key}] {msg_preview}")

        return len(running_jobs), descriptions

    except Exception as e:
        # Redis unavailable or query failed - degrade gracefully
        logging.getLogger(__name__).warning(f"Failed to check running jobs: {e}")
        return 0, []


async def _handle_update_command(tg_client, event):
    """Run remote update script and reply with results.

    The script pulls code and syncs deps but does NOT restart the bridge.
    If code changed, it writes a restart flag that the job queue picks up
    between jobs for a graceful restart when idle.

    If sessions are currently running, notifies the user that the restart
    will be queued until all work completes.
    """
    logger.info(f"[bridge] /update command received from chat {event.chat_id}")
    try:
        await set_reaction(tg_client, event.chat_id, event.message.id, "👀")
    except Exception:
        pass  # Reaction is nice-to-have

    # Check for running sessions before update
    running_count, running_descriptions = _get_running_jobs_info()
    sessions_notice = ""
    if running_count > 0:
        sessions_notice = (
            f"\n\n⚠️ {running_count} session(s) currently running:\n"
            + "\n".join(running_descriptions)
            + "\n\nRestart will be queued until all sessions complete."
        )
        logger.info(
            f"[bridge] /update: {running_count} session(s) running, restart will be queued"
        )

    script_path = _BRIDGE_PROJECT_DIR / "scripts" / "remote-update.sh"
    if not script_path.exists():
        await tg_client.send_message(
            event.chat_id,
            "scripts/remote-update.sh not found.",
            reply_to=event.message.id,
        )
        return

    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            cwd=str(_BRIDGE_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        if result.returncode != 0 and result.stderr.strip():
            output += f"\n\nSTDERR:\n{result.stderr.strip()}"

        # Append sessions notice if any were running
        output += sessions_notice

        # Truncate if too long for Telegram
        if len(output) > 4000:
            output = output[:4000] + "\n...(truncated)"
        await tg_client.send_message(event.chat_id, output, reply_to=event.message.id)
    except subprocess.TimeoutExpired:
        await tg_client.send_message(
            event.chat_id,
            "Update timed out after 120s",
            reply_to=event.message.id,
        )
    except Exception as e:
        logger.error(f"[bridge] /update command failed: {e}")
        await tg_client.send_message(
            event.chat_id, f"Update failed: {e}", reply_to=event.message.id
        )


# =============================================================================
# File Detection and Sending
# =============================================================================

# Explicit file marker: <<FILE:/path/to/file>>
FILE_MARKER_PATTERN = re.compile(r"<<FILE:([^>]+)>>")

# =============================================================================
# Response Filtering - Remove Tool Logs
# =============================================================================

# Patterns for tool execution logs that should be filtered from responses
# These are clawdbot internal logs that shouldn't be shown to users
TOOL_LOG_PATTERNS = [
    re.compile(r"^🛠️\s*exec:", re.IGNORECASE),  # Bash execution
    re.compile(r"^📖\s*read:", re.IGNORECASE),  # File read
    re.compile(r"^🔎\s*web_search:", re.IGNORECASE),  # Web search
    re.compile(r"^✏️\s*edit:", re.IGNORECASE),  # File edit
    re.compile(r"^📝\s*write:", re.IGNORECASE),  # File write
    re.compile(r"^✍️\s*write:", re.IGNORECASE),  # File write (alternate emoji)
    re.compile(r"^🔍\s*search:", re.IGNORECASE),  # Search
    re.compile(r"^📁\s*glob:", re.IGNORECASE),  # Glob
    re.compile(r"^🌐\s*fetch:", re.IGNORECASE),  # Web fetch
    re.compile(r"^🧰\s*process:", re.IGNORECASE),  # Process/task
    re.compile(r"^🔧\s*tool:", re.IGNORECASE),  # Tool usage
    re.compile(r"^⚙️\s*config:", re.IGNORECASE),  # Config
    re.compile(r"^📂\s*list:", re.IGNORECASE),  # Directory listing
    re.compile(r"^🗂️\s*file:", re.IGNORECASE),  # File operations
    re.compile(r"^💻\s*run:", re.IGNORECASE),  # Run command
    re.compile(r"^🖥️\s*shell:", re.IGNORECASE),  # Shell command
    re.compile(r"^📋\s*task:", re.IGNORECASE),  # Task
    re.compile(r"^🔄\s*sync:", re.IGNORECASE),  # Sync
    re.compile(r"^📦\s*package:", re.IGNORECASE),  # Package operations
    re.compile(r"^🗑️\s*delete:", re.IGNORECASE),  # Delete
    re.compile(r"^➡️\s*move:", re.IGNORECASE),  # Move
    re.compile(r"^📋\s*copy:", re.IGNORECASE),  # Copy
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

    lines = response.split("\n")
    filtered = []

    # Generic pattern: emoji followed by word and colon (catches most tool logs)
    generic_tool_pattern = re.compile(
        r"^[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]\s*\w+:", re.UNICODE
    )

    for line in lines:
        stripped = line.strip()

        # Skip empty lines in sequence (but keep some structure)
        if not stripped:
            # Only add blank line if last line wasn't blank
            if filtered and filtered[-1].strip():
                filtered.append(line)
            continue

        # Skip lines matching explicit tool log patterns
        if any(pattern.match(stripped) for pattern in TOOL_LOG_PATTERNS):
            continue

        # Skip lines matching generic emoji+word: pattern (tool logs)
        if generic_tool_pattern.match(stripped):
            continue

        # Skip backtick-wrapped command lines (like `cd foo && ls`)
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 2:
            inner = stripped[1:-1]
            if any(
                cmd in inner.lower()
                for cmd in [
                    "cd ",
                    "ls ",
                    "cat ",
                    "grep ",
                    "find ",
                    "mkdir ",
                    "rm ",
                    "mv ",
                    "cp ",
                ]
            ):
                continue

        filtered.append(line)

    result = "\n".join(filtered).strip()

    # Clean up multiple consecutive blank lines
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")

    # If filtering removed everything meaningful, return empty
    # (response was just tool logs)
    if not result or len(result) < 5:
        return ""

    return result


# Fallback: detect absolute paths to common file types
# Matches paths like /Users/foo/bar.png or /tmp/output.pdf
# Includes: images, documents, audio, video, code, data files
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(/(?:Users|home|tmp|var)[^\s'\"<>|]*\."
    r"(?:png|jpg|jpeg|gif|webp|bmp|svg|ico"  # Images
    r"|pdf|doc|docx|txt|md|rtf|csv|json|xml|yaml|yml"  # Documents
    r"|mp3|mp4|wav|ogg|m4a|flac|aac|webm|mov|avi"  # Audio/Video
    r"|py|js|ts|html|css|sh|sql|log"  # Code/logs
    r"|zip|tar|gz|rar))",  # Archives
    re.IGNORECASE,
)

# Relative paths in known output directories (resolved to absolute before sending)
# Matches: generated_images/foo.png, data/output.json, etc.
RELATIVE_PATH_PATTERN = re.compile(
    r"(?:^|[\s`'\"])("
    r"(?:generated_images|data|output|tmp)[^\s'\"<>|]*\."
    r"(?:png|jpg|jpeg|gif|webp|bmp|svg|ico"
    r"|pdf|doc|docx|txt|md|rtf|csv|json|xml|yaml|yml"
    r"|mp3|mp4|wav|ogg|m4a|flac|aac|webm|mov|avi"
    r"|py|js|ts|html|css|sh|sql|log"
    r"|zip|tar|gz|rar))",
    re.IGNORECASE,
)

# Image extensions (for choosing send method - images sent without caption)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# Video extensions (Telegram can preview these)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm"}

# Audio extensions (Telegram can play these)
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}


def extract_files_from_response(
    response: str, working_dir: Path | None = None
) -> tuple[str, list[Path]]:
    """
    Extract files to send from response text.

    Returns (cleaned_text, list_of_file_paths).

    Detection methods:
    1. Explicit markers: <<FILE:/path/to/file>>
    2. Absolute paths to existing media files
    3. Relative paths in known directories (generated_images/, data/, etc.)
    """
    files_to_send: list[Path] = []
    seen_paths: set[str] = set()  # Use resolved paths to avoid duplicates from symlinks

    # Default working directory for resolving relative paths
    if working_dir is None:
        working_dir = Path(__file__).parent.parent  # ai/ repo root

    # Method 1: Explicit file markers (highest priority)
    for match in FILE_MARKER_PATTERN.finditer(response):
        path_str = match.group(1).strip()
        path = Path(path_str)
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in seen_paths:
                files_to_send.append(path)
                seen_paths.add(resolved)

    # Method 2: Absolute paths to media files
    for match in ABSOLUTE_PATH_PATTERN.finditer(response):
        path_str = match.group(1).strip()
        path = Path(path_str)
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in seen_paths:
                files_to_send.append(path)
                seen_paths.add(resolved)

    # Method 3: Relative paths in known directories (resolve to absolute)
    for match in RELATIVE_PATH_PATTERN.finditer(response):
        path_str = match.group(1).strip()
        # Try resolving relative to working directory
        path = working_dir / path_str
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in seen_paths:
                files_to_send.append(path)
                seen_paths.add(resolved)
                logger.debug(f"Resolved relative path: {path_str} -> {path}")

    # Clean response: remove file markers
    cleaned = FILE_MARKER_PATTERN.sub("", response)

    # Optionally clean up lines that are just file paths (cosmetic)
    lines = cleaned.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just a detected file path
        if stripped and any(
            stripped == str(f) or stripped.endswith(str(f)) for f in files_to_send
        ):
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()

    return cleaned, files_to_send


# =============================================================================
# Media Receiving and Processing
# =============================================================================

# Voice/audio extensions
VOICE_EXTENSIONS = {".ogg", ".oga", ".mp3", ".wav", ".m4a", ".opus"}

# Supported image extensions for vision
VISION_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Magic bytes for file type validation
FILE_MAGIC_BYTES = {
    "pdf": b"%PDF",
    "png": b"\x89PNG",
    "jpg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": b"RIFF",  # RIFF....WEBP
}

# Text-extractable document extensions
TEXT_DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".css",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
    ".sh",
    ".bash",
    ".sql",
    ".r",
    ".rb",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
}


def validate_media_file(filepath: Path) -> tuple[bool, str]:
    """
    Validate a downloaded media file by checking magic bytes and basic structure.

    Returns (is_valid, error_reason). If valid, error_reason is empty.
    Uses stdlib only — no external dependencies.
    """
    if not filepath.exists():
        return False, "file does not exist"

    if filepath.stat().st_size == 0:
        return False, "file is empty (0 bytes)"

    ext = filepath.suffix.lower()

    try:
        with open(filepath, "rb") as f:
            header = f.read(32)

        if ext == ".pdf":
            if not header.startswith(b"%PDF"):
                return False, "file does not start with %PDF header"
            # Check for minimal PDF structure: must contain at least some content
            with open(filepath, "rb") as f:
                content = f.read()
            if b"%%EOF" not in content and b"endobj" not in content:
                return False, "PDF file is truncated or corrupted (no EOF marker)"

        elif ext in (".png",):
            if not header.startswith(b"\x89PNG"):
                return False, "file does not have PNG magic bytes"

        elif ext in (".jpg", ".jpeg"):
            if not header.startswith(b"\xff\xd8\xff"):
                return False, "file does not have JPEG magic bytes"

        elif ext in (".gif",):
            if not header.startswith((b"GIF87a", b"GIF89a")):
                return False, "file does not have GIF magic bytes"

        elif ext == ".webp":
            if not header.startswith(b"RIFF") or b"WEBP" not in header[:16]:
                return False, "file does not have WebP magic bytes"

        # For other extensions, just check it's not empty (already done above)
        return True, ""

    except Exception as e:
        return False, f"validation error: {e}"


def extract_document_text(filepath: Path, max_chars: int = 5000) -> str | None:
    """
    Extract text content from a document file.

    For text-based files, reads directly. For PDFs, extracts what we can with stdlib.
    Returns extracted text, or None if extraction failed.
    This allows us to inline document content so the agent doesn't need to read the raw file.
    """
    ext = filepath.suffix.lower()

    try:
        # Text-based documents: read directly
        if ext in TEXT_DOCUMENT_EXTENSIONS:
            content = filepath.read_text(errors="replace")
            if len(content) > max_chars:
                content = (
                    content[:max_chars]
                    + f"\n\n[... truncated, {len(filepath.read_bytes())} bytes total]"
                )
            return content

        # PDF: try to extract text with stdlib
        if ext == ".pdf":
            return _extract_pdf_text_stdlib(filepath, max_chars)

        return None

    except Exception as e:
        logging.getLogger(__name__).warning(
            f"Could not extract text from {filepath.name}: {e}"
        )
        return None


def _extract_pdf_text_stdlib(filepath: Path, max_chars: int = 5000) -> str | None:
    """
    PDF text extraction using pypdf library.

    Handles compressed streams, multiple pages, and most PDF formats.
    Won't work for scanned/image-only PDFs (would need OCR).
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(filepath)
        text_parts = []

        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text.strip())

        if text_parts:
            extracted = "\n\n".join(text_parts)
            if len(extracted) > max_chars:
                extracted = extracted[:max_chars] + "..."
            return extracted

        return None

    except Exception as e:
        logging.getLogger(__name__).debug(f"PDF extraction failed: {e}")
        return None


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


async def download_media(
    client: TelegramClient, message, prefix: str = "media"
) -> Path | None:
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
        logging.getLogger(__name__).warning(
            "ollama library not installed for image vision"
        )
        return None

    try:
        # Run the synchronous ollama.chat in a thread pool to not block the event loop
        loop = asyncio.get_event_loop()

        def _describe():
            response = ollama.chat(
                model="llama3.2-vision:11b",
                messages=[
                    {
                        "role": "user",
                        "content": "Describe this image in detail. What do you see?",
                        "images": [str(filepath)],
                    }
                ],
            )
            return response["message"]["content"]

        description = await loop.run_in_executor(None, _describe)
        return description.strip() if description else None

    except Exception as e:
        logging.getLogger(__name__).error(f"Image description failed: {e}")
        return None


async def process_incoming_media(
    client: TelegramClient, message
) -> tuple[str, list[Path]]:
    """
    Process media in an incoming message.

    Returns (description_text, list_of_file_paths).
    The description_text is meant to be prepended to the message for context.

    Files are validated after download. Invalid/corrupted files are described
    but not referenced by path, preventing downstream API errors when the agent
    tries to read them.
    """
    media_type = get_media_type(message)
    if not media_type:
        return "", []

    # Download the media
    downloaded = await download_media(client, message, prefix=media_type)
    if not downloaded:
        return f"[User sent a {media_type} but download failed]", []

    # Validate the downloaded file
    is_valid, validation_error = validate_media_file(downloaded)
    if not is_valid:
        logging.getLogger(__name__).warning(
            f"Invalid {media_type} file {downloaded.name}: {validation_error}"
        )
        # Try to extract text content even from invalid files (best effort)
        extracted = extract_document_text(downloaded)
        if extracted:
            return (
                f"[User sent a {media_type} (file appears corrupted: {validation_error}), "
                f"but partial text was extracted]\n\nExtracted content:\n{extracted}"
            ), []
        return (
            f"[User sent a {media_type} but the file is invalid/corrupted: {validation_error}. "
            f"File cannot be read.]"
        ), []

    files = [downloaded]
    description = ""

    if media_type == "voice":
        # Transcribe voice message
        transcription = await transcribe_voice(downloaded)
        if transcription:
            description = f'[Voice message transcription: "{transcription}"]'
        else:
            description = f"[User sent a voice message - saved to {downloaded.name}]"

    elif media_type in ("photo", "image"):
        # Use Ollama LLaVA to describe the image
        image_description = await describe_image(downloaded)
        if image_description:
            description = (
                f"[User sent an image]\nImage description: {image_description}"
            )
        else:
            # Fallback if vision model is not available
            description = f"[User sent an image - saved to {downloaded.name}]"

    elif media_type == "audio":
        # Try transcribing audio files too
        transcription = await transcribe_voice(downloaded)
        if transcription:
            description = f'[Audio file transcription: "{transcription}"]'
        else:
            description = f"[User sent an audio file - saved to {downloaded.name}]"

    elif media_type == "document":
        # Try to extract and inline document text content
        extracted = extract_document_text(downloaded)
        if extracted:
            description = (
                f"[User sent a document: {downloaded.name}]\n\n"
                f"Document content:\n{extracted}"
            )
        else:
            description = f"[User sent a document - saved to {downloaded.name}]"

    return description, files


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
    """Log a structured event to Redis via BridgeEvent model."""
    try:
        from models.bridge_event import BridgeEvent

        BridgeEvent.log(event_type, **kwargs)
    except Exception:
        # Fallback: don't let event logging break the bridge
        pass


def load_config() -> dict:
    """Load project configuration from projects.json."""
    config_path = Path(__file__).parent.parent / "config" / "projects.json"
    example_path = config_path.with_suffix(".json.example")

    if not config_path.exists():
        if example_path.exists():
            logger.error(
                f"Project config not found at {config_path}. "
                f"Copy the example: cp {example_path} {config_path}"
            )
        else:
            logger.warning(f"Project config not found at {config_path}, using defaults")
        return {"projects": {}, "defaults": {}}

    with open(config_path) as f:
        config = json.load(f)

    # Validate defaults section exists and has working_directory
    defaults = config.get("defaults", {})
    if not defaults:
        logger.warning(
            "No 'defaults' section in projects.json. "
            "Add a defaults section with working_directory and telegram settings. "
            "See config/projects.json.example for proper setup."
        )
    elif not defaults.get("working_directory"):
        logger.warning(
            "No 'working_directory' in defaults section of projects.json. "
            "Projects without working_directory will fail. "
            "See config/projects.json.example for proper setup."
        )

    # Validate each active project
    projects = config.get("projects", {})
    for project_key in ACTIVE_PROJECTS:
        if project_key not in projects:
            continue
        project = projects[project_key]
        working_dir = project.get("working_directory") or defaults.get(
            "working_directory"
        )
        if not working_dir:
            logger.error(
                f"Project '{project_key}' has no working_directory and no default set. "
                "The bridge WILL fail when processing messages for this project. "
                "Fix: add 'working_directory' to the project in config/projects.json"
            )
        elif not Path(working_dir).exists():
            logger.warning(
                f"Project '{project_key}' working_directory does not exist: {working_dir}"
            )

    return config


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
                logger.warning(
                    f"Group '{group}' is mapped to multiple projects, using first"
                )
                continue
            group_map[group_lower] = project
            logger.info(
                f"Mapping group '{group}' -> project '{project.get('name', project_key)}'"
            )

    return group_map


# Load config at startup
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


def get_user_permissions(sender_id: int | None) -> str:
    """Get the permission level for a whitelisted user.

    Returns:
        "full" - Can do anything (default)
        "qa_only" - Q&A only, no code changes allowed
    """
    if not sender_id or sender_id not in DM_WHITELIST_CONFIG:
        return "full"
    return DM_WHITELIST_CONFIG[sender_id].get("permissions", "full")


# Link collectors - usernames whose links are automatically stored
# When these users share a URL, it gets saved with metadata
LINK_COLLECTORS = [
    name.strip().lower()
    for name in os.getenv("TELEGRAM_LINK_COLLECTORS", "").split(",")
    if name.strip()
]

# Link summarization settings
MAX_LINKS_PER_MESSAGE = 5  # Don't summarize more than 5 links per message
LINK_SUMMARY_CACHE_HOURS = 24  # Don't re-summarize URLs within 24 hours

# Default mention triggers
DEFAULT_MENTIONS = DEFAULTS.get("telegram", {}).get(
    "mention_triggers", ["@valor", "valor", "hey valor"]
)


def find_project_for_chat(chat_title: str | None) -> dict | None:
    """Find which project a chat belongs to."""
    if not chat_title:
        return None

    chat_lower = chat_title.lower()
    for group_name, project in GROUP_TO_PROJECT.items():
        if group_name in chat_lower:
            return project

    return None


# Pattern to detect @mentions in messages
AT_MENTION_PATTERN = re.compile(r"@(\w+)")

# Known Valor usernames for @mention detection
VALOR_USERNAMES = {"valor", "valorengels"}


def extract_at_mentions(text: str) -> list[str]:
    """Extract all @mentions from text, returning lowercase usernames."""
    return [m.lower() for m in AT_MENTION_PATTERN.findall(text)]


def get_valor_usernames(project: dict | None) -> set[str]:
    """Get all usernames that should be treated as Valor."""
    usernames = VALOR_USERNAMES.copy()
    if project:
        mentions = project.get("telegram", {}).get("mention_triggers", DEFAULT_MENTIONS)
        for trigger in mentions:
            clean_trigger = trigger.lstrip("@").lower()
            usernames.add(clean_trigger)
    return usernames


def is_message_for_valor(text: str, project: dict | None) -> bool:
    """Check if message explicitly @mentions Valor."""
    at_mentions = extract_at_mentions(text)
    if not at_mentions:
        return False
    valor_usernames = get_valor_usernames(project)
    return any(mention in valor_usernames for mention in at_mentions)


def is_message_for_others(text: str, project: dict | None) -> bool:
    """Check if message is @directed to someone other than Valor."""
    at_mentions = extract_at_mentions(text)
    if not at_mentions:
        return False
    valor_usernames = get_valor_usernames(project)
    # If ALL @mentions are for others (none for Valor), it's directed elsewhere
    return not any(mention in valor_usernames for mention in at_mentions)


def classify_needs_response(text: str) -> bool:
    """
    Use Ollama to quickly classify if a message needs a response.

    Returns True if the message appears to be a work request, question, or
    instruction that needs action. Returns False for acknowledgments like
    "thanks", "ok", "got it", side conversations, etc.
    """
    # Fast path: very short messages are usually acknowledgments
    if len(text.strip()) < 3:
        return False

    # Fast path: common acknowledgments (case-insensitive)
    acknowledgments = {
        "thanks",
        "thank you",
        "thx",
        "ty",
        "ok",
        "okay",
        "k",
        "kk",
        "got it",
        "gotcha",
        "understood",
        "nice",
        "great",
        "awesome",
        "perfect",
        "cool",
        "yes",
        "yep",
        "yeah",
        "yup",
        "no",
        "nope",
        "👍",
        "👌",
        "✅",
        "🙏",
        "❤️",
        "🔥",
        "lol",
        "lmao",
        "haha",
        "heh",
        "brb",
        "afk",
        "bbl",
    }
    text_lower = text.strip().lower().rstrip("!.,")
    if text_lower in acknowledgments:
        return False

    # Use Ollama for more nuanced classification
    try:
        import ollama

        response = ollama.chat(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "user",
                    "content": f"""Classify this message. Reply with ONLY "work" or "ignore".

- "work" = question, request, instruction, bug report, or anything needing action
- "ignore" = acknowledgment, thanks, greeting, side chat, or social message

Message: {text[:200]}

Classification:""",
                }
            ],
            options={"temperature": 0},
        )
        result = response["message"]["content"].strip().lower()
        return "work" in result
    except Exception as e:
        logging.getLogger(__name__).debug(
            f"Ollama classification failed, defaulting to respond: {e}"
        )
        # Default to responding if Ollama fails
        return True


async def classify_needs_response_async(text: str) -> bool:
    """Async wrapper for Ollama classification."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, classify_needs_response, text)


def should_respond_sync(
    text: str,
    is_dm: bool,
    chat_title: str | None,
    project: dict | None,
    sender_name: str | None = None,
    sender_username: str | None = None,
    sender_id: int | None = None,
) -> bool:
    """
    Synchronous check for basic response conditions.
    Used for DMs and groups without respond_to_unaddressed.
    """
    if is_dm:
        if not RESPOND_TO_DMS:
            return False
        # Check whitelist if configured (matches on immutable Telegram user ID)
        if DM_WHITELIST:
            if sender_id not in DM_WHITELIST:
                return False
        return True

    # Must be in a monitored group
    if not project:
        return False

    telegram_config = project.get("telegram", {})

    # If respond_to_all is set, respond to everything
    if telegram_config.get("respond_to_all", True):
        return True

    # For groups NOT using respond_to_unaddressed, use mention-based logic
    if not telegram_config.get("respond_to_unaddressed", False):
        if telegram_config.get("respond_to_mentions", True):
            mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)
            text_lower = text.lower()
            return any(mention.lower() in text_lower for mention in mentions)

    return False


async def should_respond_async(
    client,
    event,
    text: str,
    is_dm: bool,
    chat_title: str | None,
    project: dict | None,
    sender_name: str | None = None,
    sender_username: str | None = None,
    sender_id: int | None = None,
) -> tuple[bool, bool]:
    """
    Async response decision with full context.

    Returns (should_respond, is_reply_to_valor) tuple.

    Decision logic for groups with respond_to_unaddressed:
    - Case 1: Unaddressed message → Ollama classifies if it needs work
    - Case 2: Reply to Valor → Always respond (continue session)
    - Case 3: @valor → Always respond
    - Case 4: @someoneelse → Always ignore
    """
    message = event.message

    # DMs: use sync logic
    if is_dm:
        return (
            should_respond_sync(
                text,
                is_dm,
                chat_title,
                project,
                sender_name,
                sender_username,
                sender_id,
            ),
            False,
        )

    # Must be in a monitored group
    if not project:
        return False, False

    telegram_config = project.get("telegram", {})

    # respond_to_all means respond to everything
    if telegram_config.get("respond_to_all", True):
        return True, False

    # For groups NOT using respond_to_unaddressed, use sync mention-based logic
    if not telegram_config.get("respond_to_unaddressed", False):
        return (
            should_respond_sync(
                text,
                is_dm,
                chat_title,
                project,
                sender_name,
                sender_username,
                sender_id,
            ),
            False,
        )

    # === respond_to_unaddressed logic (the 4 cases) ===

    # Case 2: Reply to Valor's message → always respond (no Ollama needed)
    is_reply_to_valor = False
    if message.reply_to_msg_id:
        try:
            replied_msg = await client.get_messages(
                event.chat_id, ids=message.reply_to_msg_id
            )
            if replied_msg and replied_msg.out:  # .out means sent by us (Valor)
                is_reply_to_valor = True
                logger.debug("Case 2: Reply to Valor - responding")
                return True, True
        except Exception as e:
            logger.debug(f"Could not check replied message: {e}")

    # Case 3: @valor → always respond (no Ollama needed)
    if is_message_for_valor(text, project):
        logger.debug("Case 3: @valor mentioned - responding")
        return True, False

    # Case 4: @someoneelse → always ignore (no Ollama needed)
    if is_message_for_others(text, project):
        logger.debug("Case 4: Message @directed to others - ignoring")
        return False, False

    # Case 1: Unaddressed message → use Ollama to classify
    logger.debug("Case 1: Unaddressed message - classifying with Ollama")
    needs_response = await classify_needs_response_async(text)
    if not needs_response:
        logger.info(f"Ollama classified as ignore: {text[:50]}...")
    return needs_response, False


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


def build_context_prefix(
    project: dict | None, is_dm: bool, sender_id: int | None = None
) -> str:
    """Build project context to inject into agent prompt."""
    context_parts = []

    # Check user permissions and add restrictions if needed
    permissions = get_user_permissions(sender_id)
    if permissions == "qa_only":
        context_parts.append(
            "RESTRICTION: This user has Q&A-only access. "
            "Do NOT make any code changes, file edits, git commits, or run destructive commands. "
            "Answer questions, explain code, and provide guidance only. "
            "If they ask you to make changes, politely explain you can only help with Q&A for them."
        )

    if not project:
        if is_dm:
            context_parts.append(
                "CONTEXT: Direct message to Valor (no specific project context)"
            )
        return "\n".join(context_parts) if context_parts else ""

    context_parts.append(
        f"PROJECT: {project.get('name', project.get('_key', 'Unknown'))}"
    )

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
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if result.stdout.strip():
            context_parts.append(f"RECENT COMMITS (last 24h):\n{result.stdout.strip()}")
    except Exception as e:
        logger.debug(f"Could not get git log: {e}")

    # Current branch and status
    try:
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if branch_result.stdout.strip():
            context_parts.append(f"CURRENT BRANCH: {branch_result.stdout.strip()}")

        status_result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if status_result.stdout.strip():
            modified_files = status_result.stdout.strip().split("\n")[:5]
            context_parts.append("MODIFIED FILES:\n" + "\n".join(modified_files))
    except Exception as e:
        logger.debug(f"Could not get git status: {e}")

    # Recent plan docs
    plans_dir = Path(cwd) / "docs" / "plans"
    if plans_dir.exists():
        try:
            recent_plans = sorted(
                plans_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
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

    NOTE: This is NOT called by default. The agent should use the valor-history
    CLI tool to fetch relevant history when context cues suggest prior messages
    may be relevant (e.g., "what do you think of these", "as I mentioned",
    references to recent discussions, etc.). For explicit threading, users
    can use Telegram's reply-to feature.

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

        # Filter tool logs from Valor's historical responses
        if sender == "Valor":
            content = filter_tool_logs(content)
            if not content:
                continue  # Skip if response was only tool logs

        # Truncate long messages
        if len(content) > 200:
            content = content[:200] + "..."
        history_lines.append(f"  {sender}: {content}")

    # If we only have the header, return empty
    if len(history_lines) <= 1:
        return ""

    return "\n".join(history_lines)


async def fetch_reply_chain(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    max_depth: int = 20,
) -> list[dict]:
    """
    Fetch the entire reply chain for a message.

    Walks backward through reply_to_msg_id references to build the full thread.
    Returns messages in chronological order (oldest first).

    Args:
        client: Telegram client
        chat_id: Chat ID to fetch from
        message_id: Starting message ID (the one being replied to)
        max_depth: Maximum number of messages to fetch in the chain

    Returns:
        List of message dicts with 'sender', 'content', 'message_id', 'date'
    """
    chain = []
    current_id = message_id
    seen_ids = set()

    for _ in range(max_depth):
        if current_id in seen_ids:
            break  # Avoid infinite loops
        seen_ids.add(current_id)

        try:
            msg = await client.get_messages(chat_id, ids=current_id)
            if not msg:
                break

            # Get sender info
            sender = await msg.get_sender()
            sender_name = getattr(sender, "first_name", "Unknown")

            # Check if this is our own message (Valor's response)
            if msg.out:
                sender_name = "Valor"

            chain.append(
                {
                    "sender": sender_name,
                    "content": msg.text or "[media]",
                    "message_id": msg.id,
                    "date": msg.date,
                }
            )

            # Move to parent message
            if msg.reply_to_msg_id:
                current_id = msg.reply_to_msg_id
            else:
                break  # No more parents

        except Exception as e:
            logger.debug(f"Could not fetch message {current_id} in reply chain: {e}")
            break

    # Reverse to get chronological order (oldest first)
    chain.reverse()
    return chain


def format_reply_chain(chain: list[dict]) -> str:
    """
    Format a reply chain for inclusion in agent context.

    Args:
        chain: List of message dicts from fetch_reply_chain()

    Returns:
        Formatted string showing the thread
    """
    if not chain:
        return ""

    lines = ["REPLY THREAD CONTEXT (oldest to newest):"]
    lines.append("-" * 40)

    for msg in chain:
        sender = msg["sender"]
        content = msg["content"]

        # Filter tool logs from Valor's messages
        if sender == "Valor":
            content = filter_tool_logs(content)
            if not content:
                continue

        # Valor's messages are already summarized — include in full
        # so resumed sessions have complete context of what was sent.
        # Other users' messages get truncated to keep context manageable.
        max_len = 2000 if sender == "Valor" else 500
        if len(content) > max_len:
            content = content[:max_len] + "..."

        # Format with timestamp if available
        date_str = ""
        if msg.get("date"):
            date_str = msg["date"].strftime(" [%H:%M]")

        lines.append(f"{sender}{date_str}: {content}")
        lines.append("")  # Blank line between messages

    lines.append("-" * 40)
    return "\n".join(lines)


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
        logger.info(
            f"Rate limiting: only processing {MAX_LINKS_PER_MESSAGE} of {len(urls_result.get('urls', []))} links"
        )

    summaries = []

    for url in urls:
        try:
            # Check cache: do we already have a summary for this URL?
            existing = get_link_by_url(url, max_age_hours=LINK_SUMMARY_CACHE_HOURS)

            if existing and existing.get("ai_summary"):
                # Use cached summary
                logger.debug(f"Using cached summary for: {url[:50]}...")
                summaries.append(
                    {
                        "url": url,
                        "summary": existing["ai_summary"],
                        "title": existing.get("title"),
                        "cached": True,
                    }
                )
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
                summaries.append(
                    {
                        "url": url,
                        "summary": summary,
                        "title": title,
                        "cached": False,
                    }
                )
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

# =============================================================================
# TELEGRAM REACTIONS - VALIDATED BY ACTUAL TESTING
# =============================================================================
# IMPORTANT: Do NOT trust Telegram's GetAvailableReactionsRequest API - it lies!
# These were validated on 2026-02-05 by actually setting each as a reaction.
# Re-validate periodically with: python scripts/test_emoji_reactions.py
#
# Key findings from testing:
# - Emojis with U+FE0F variation selector fail (use base forms: ❤ not ❤️)
# - 😂 (tears of joy) is NOT a valid reaction despite being common
# - 💻 🎨 ❌ ✅ 🔄 ⏳ 🚀 💡 📝 🔍 are NOT valid reactions
# - "Saved Messages" requires Premium; test in real DMs/groups
# =============================================================================

# Validated 73 emojis on 2026-02-05 via scripts/test_emoji_reactions.py
# fmt: off
VALIDATED_REACTIONS = [
    # Hearts/love
    "❤", "❤‍🔥", "💔", "💘", "😍", "🥰", "😘", "💋",
    # Hands
    "👍", "👎", "👏", "🙏", "👌", "🤝", "✍", "🖕",
    # Positive faces
    "😁", "🤣", "🤩", "😇", "😎", "🤓", "🤗", "🫡",
    # Negative faces
    "😱", "🤯", "🤬", "😢", "😭", "🤮", "😨", "😡",
    # Neutral/other faces
    "🤔", "🥱", "🥴", "😴", "😐", "🤨", "🤪",
    # Characters
    "🤡", "👻", "👾", "😈", "💩", "🎅", "👨‍💻",
    # Animals/nature
    "🕊", "🐳", "🦄", "🙈", "🙉", "🙊",
    # Objects/symbols
    "🔥", "⚡", "💯", "🏆", "🎉", "🎃", "🎄", "☃", "🗿", "💊", "🆒",
    # Food
    "🍌", "🍓", "🌭", "🍾",
    # Other
    "🌚", "💅", "👀", "🤷", "🤷‍♂", "🤷‍♀",
]
# fmt: on

# Known INVALID reactions - do not use these
# fmt: off
INVALID_REACTIONS = [
    "😂",  # ReactionInvalidError - tears of joy not allowed!
    "💻",  # Laptop - not a reaction
    "🎨",  # Art palette - not a reaction
    "❌",  # Cross mark - not a reaction
    "✅",  # Check mark - not a reaction
    "🔄",  # Refresh - not a reaction
    "⏳",  # Hourglass - not a reaction
    "🚀",  # Rocket - not a reaction
    "💡",  # Light bulb - not a reaction
    "📝",  # Memo - not a reaction
    "🔍",  # Magnifying glass - not a reaction
    # Emojis with U+FE0F variation selector (use base forms instead):
    "❤️", "❤️‍🔥", "✍️", "☃️", "🤷‍♂️", "🤷‍♀️",
]
# fmt: on

# Reaction emojis for different stages (all validated 2026-02-05)
REACTION_RECEIVED = "👀"  # Message acknowledged
REACTION_PROCESSING = "🤔"  # Default thinking emoji
REACTION_SUCCESS = "👍"  # Completed successfully
REACTION_ERROR = "😱"  # Something went wrong

# Intent-specific processing emojis (classified by local Ollama)
# All emojis validated 2026-02-05 via scripts/test_emoji_reactions.py
INTENT_REACTIONS = {
    "search": "👀",  # Searching/looking
    "code_execution": "👨‍💻",  # Running code
    "image_generation": "🤩",  # Creating an image
    "image_analysis": "🤓",  # Analyzing an image
    "file_operation": "✍",  # File operations/writing
    "git_operation": "👨‍💻",  # Git work
    "chat": "😎",  # Casual conversation
    "tool_use": "🫡",  # Executing command
    "system": "👾",  # System task
    "unknown": "🤔",  # Default thinking
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


async def set_reaction(
    client: TelegramClient, chat_id: int, msg_id: int, emoji: str | None
) -> bool:
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
        await client(
            SendReactionRequest(
                peer=chat_id,
                msg_id=msg_id,
                reaction=reaction,
            )
        )
        return True
    except Exception as e:
        logger.debug(f"Could not set reaction '{emoji}': {e}")
        return False


async def send_response_with_files(
    client: TelegramClient,
    event,
    response: str,
    chat_id: int | None = None,
    reply_to: int | None = None,
) -> bool:
    """
    Send response to Telegram, handling both files and text.

    1. Filter out tool execution logs
    2. Extract any files from the response
    3. Send files first (as separate messages)
    4. Send remaining text (if any)

    Can be called with event (handler context) or chat_id+reply_to (queue context).
    Returns True if any content was sent, False otherwise.
    """
    # Resolve chat_id and reply_to from event or explicit params
    _chat_id = chat_id or (event.chat_id if event else None)
    _reply_to = reply_to or (
        event.message.id if event and hasattr(event, "message") else None
    )

    if not _chat_id:
        logger.error("send_response_with_files: no chat_id available")
        return False

    # Filter out tool logs before processing
    response = filter_tool_logs(response)

    # If filtering removed everything, no response needed
    if not response:
        return False

    text, files = extract_files_from_response(response)

    # Summarize long responses before sending
    if text and len(text) > 500:
        try:
            from bridge.summarizer import summarize_response

            summarized = await summarize_response(text)
            text = summarized.text
            if summarized.full_output_file:
                files.append(summarized.full_output_file)
            if summarized.was_summarized:
                logger.info(
                    f"Summarized response: {len(response)} -> {len(text)} chars"
                )
        except Exception as e:
            logger.warning(f"Summarization failed, using original: {e}")

    # Send files first
    for file_path in files:
        try:
            ext = file_path.suffix.lower()
            is_image = ext in IMAGE_EXTENSIONS
            is_video = ext in VIDEO_EXTENSIONS
            is_audio = ext in AUDIO_EXTENSIONS

            # Choose appropriate send options based on file type
            if is_image:
                # Images: send as photo (no caption, Telegram displays inline)
                await client.send_file(
                    _chat_id,
                    file_path,
                    reply_to=_reply_to,
                    caption=None,
                )
            elif is_video:
                # Videos: send as video (Telegram can preview/play)
                await client.send_file(
                    _chat_id,
                    file_path,
                    reply_to=_reply_to,
                    caption=f"🎬 {file_path.name}",
                    supports_streaming=True,
                )
            elif is_audio:
                # Audio: send as audio (Telegram shows player)
                await client.send_file(
                    _chat_id,
                    file_path,
                    reply_to=_reply_to,
                    caption=f"🎵 {file_path.name}",
                )
            else:
                # Other files: send as document with filename caption
                await client.send_file(
                    _chat_id,
                    file_path,
                    reply_to=_reply_to,
                    caption=f"📎 {file_path.name}",
                    force_document=True,
                )
            logger.info(
                f"Sent file: {file_path} (type: {'image' if is_image else 'video' if is_video else 'audio' if is_audio else 'document'})"
            )
        except Exception as e:
            logger.error(f"Failed to send file {file_path}: {e}")
            await client.send_message(
                _chat_id, f"Failed to send file: {file_path.name}"
            )

    # Track if we sent anything
    sent_content = bool(files)

    # Send text if there's meaningful content
    if text and not text.isspace():
        # Safety truncation at Telegram's limit (summarizer handles graceful shortening)
        if len(text) > 4096:
            text = text[:4093] + "..."
        try:
            await client.send_message(_chat_id, text, reply_to=_reply_to)
            sent_content = True
        except Exception as e:
            logger.error(
                f"Failed to send text message to chat {_chat_id} "
                f"({len(text)} chars): {e}"
            )
            # Persist to dead-letter queue for later retry
            try:
                from bridge.dead_letters import persist_failed_delivery

                await persist_failed_delivery(
                    chat_id=_chat_id,
                    reply_to=_reply_to,
                    text=text,
                )
            except Exception as dl_err:
                logger.error(f"Dead-letter persist also failed: {dl_err}")

    return sent_content


async def get_agent_response_clawdbot(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
    sender_id: int | None = None,
) -> str:
    """Call clawdbot agent and get response (legacy implementation)."""

    start_time = time.time()
    request_id = f"{session_id}_{int(start_time)}"

    # CRITICAL: Determine working directory to prevent agent from wandering into wrong directories
    if project:
        working_dir = project.get(
            "working_directory", DEFAULTS.get("working_directory")
        )
    else:
        working_dir = DEFAULTS.get("working_directory")

    # Fallback to current directory if not configured (shouldn't happen)
    if not working_dir:
        working_dir = str(Path(__file__).parent.parent)
        logger.warning(
            f"[{request_id}] No working_directory configured, using {working_dir}"
        )

    try:
        # Build context-enriched message (includes user permission restrictions)
        context = build_context_prefix(project, chat_title is None, sender_id)

        # Note: Recent conversation history is NOT injected by default.
        # The agent should use valor-history CLI to fetch relevant context
        # when subtle cues suggest prior messages may be relevant.
        # Users can also use Telegram's reply-to feature for explicit threading.

        # Check if this is a status question - inject activity context
        activity_context = ""
        if is_status_question(message):
            activity_context = build_activity_context(working_dir)
            logger.debug(
                f"[{request_id}] Status question detected, injecting activity context"
            )

        enriched_message = context
        if activity_context:
            enriched_message += f"\n\n{activity_context}"
        enriched_message += f"\n\nFROM: {sender_name}"
        if chat_title:
            enriched_message += f" in {chat_title}"
        enriched_message += f"\nMESSAGE: {message}"

        project_name = project.get("name", "Valor") if project else "Valor"

        # Use subprocess to call clawdbot agent
        # Use --json to get clean output without tool execution logs mixed in
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
            "--json",
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
        except TimeoutError:
            # Kill the process and try to capture partial output
            elapsed = time.time() - start_time
            logger.error(f"[{request_id}] Agent request timed out after {elapsed:.1f}s")

            # Try to terminate gracefully first
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
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
            logger.error(
                f"[{request_id}] Clawdbot error (exit {process.returncode}) after {elapsed:.1f}s"
            )
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

        raw_output = stdout.decode().strip()
        stderr_text = stderr.decode().strip()

        # Parse JSON response from clawdbot --json mode
        # Structure: {"payloads": [{"text": "...", "mediaUrl": null}], "meta": {...}}
        try:
            result = json.loads(raw_output)
            payloads = result.get("payloads", [])
            if payloads and payloads[0].get("text"):
                response = payloads[0]["text"]
            else:
                # Fallback to raw output if JSON parsing succeeds but no text
                response = raw_output
                logger.warning(f"[{request_id}] JSON response had no text payload")
        except json.JSONDecodeError:
            # Fallback to raw output if not valid JSON (shouldn't happen with --json)
            response = raw_output
            logger.warning(
                f"[{request_id}] Failed to parse JSON response, using raw output"
            )

        # Log success with timing
        logger.info(
            f"[{request_id}] Agent responded in {elapsed:.1f}s ({len(response)} chars)"
        )
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


async def get_agent_response(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
    sender_id: int | None = None,
) -> str:
    """
    Route to appropriate agent backend based on USE_CLAUDE_SDK flag.

    When USE_CLAUDE_SDK=true, uses the Claude Agent SDK directly.
    Otherwise, uses the legacy clawdbot subprocess approach.
    """
    if USE_CLAUDE_SDK:
        logger.debug(f"Using Claude Agent SDK for session {session_id}")
        return await get_agent_response_sdk(
            message, session_id, sender_name, chat_title, project, chat_id, sender_id
        )
    else:
        return await get_agent_response_clawdbot(
            message, session_id, sender_name, chat_title, project, chat_id, sender_id
        )


# =============================================================================
# Background Task Configuration
# =============================================================================

# How long to wait before sending "I'm working on this" acknowledgment
# Only sends if no message has been sent to the chat yet
ACKNOWLEDGMENT_TIMEOUT_SECONDS = 180  # 3 minutes

# Message to send when work is taking a while
ACKNOWLEDGMENT_MESSAGE = "I'm working on this."

# =============================================================================
# Retry with Self-Healing (Legacy - for Clawdbot backend)
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
            "pkill",
            "-f",
            "clawdbot agent",
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
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan_path = (
        Path(__file__).parent.parent
        / "docs"
        / "plans"
        / f"fix-bridge-failure-{timestamp}.md"
    )

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
    sender_id: int | None = None,
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
            # Note: 🔄 is not a valid Telegram reaction, use 🔥 (fire/trying hard) instead
            if attempt > 0 and client and msg_id:
                await set_reaction(client, int(chat_id) if chat_id else 0, msg_id, "🔥")
                logger.info(f"Retry attempt {attempt + 1}/{MAX_RETRIES}")

            response = await get_agent_response(
                message,
                session_id,
                sender_name,
                chat_title,
                project,
                chat_id,
                sender_id,
            )

            # Check if response looks like an error
            if response.startswith("Error:") or response.startswith(
                "Request timed out"
            ):
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

        except TimeoutError:
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


def _get_github_repo_url(working_dir: str) -> str | None:
    """Get GitHub repo URL from git remote."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=working_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Convert git@github.com:user/repo.git to https://github.com/user/repo
            if url.startswith("git@github.com:"):
                url = url.replace("git@github.com:", "https://github.com/")
            # Remove .git suffix
            if url.endswith(".git"):
                url = url[:-4]
            return url
    except Exception:
        pass
    return None


def _match_plan_by_name(message_text: str, working_dir: str) -> str | None:
    """
    Match plan files by natural language name.

    Examples:
        "workflow state persistence plan" -> docs/plans/workflow-state-persistence.md
        "the auth system plan" -> docs/plans/auth-system.md
        "issue classification" -> docs/plans/issue-classification-commands.md
    """
    plans_dir = Path(working_dir) / "docs" / "plans"
    if not plans_dir.exists():
        return None

    plan_files = list(plans_dir.glob("*.md"))
    if not plan_files:
        return None

    message_lower = message_text.lower()

    # First try exact .md filename match
    plan_pattern = r"(?:docs/plans/)?([a-z0-9-]+)\.md"
    plan_match = re.search(plan_pattern, message_lower)
    if plan_match:
        plan_name = plan_match.group(1)
        plan_path = plans_dir / f"{plan_name}.md"
        if plan_path.exists():
            return f"docs/plans/{plan_name}.md"

    # Try natural language matching
    best_match = None
    best_score = 0

    for plan_file in plan_files:
        # Convert filename to words: "workflow-state-persistence.md" -> ["workflow", "state", "persistence"]
        plan_name = plan_file.stem  # without .md
        plan_words = plan_name.replace("-", " ").split()

        # Count how many plan words appear in the message
        matches = sum(1 for word in plan_words if word in message_lower)

        # Require at least 2 matching words (or all words if plan name is short)
        min_required = min(2, len(plan_words))
        if matches >= min_required and matches > best_score:
            best_score = matches
            best_match = f"docs/plans/{plan_name}.md"

    return best_match


def _detect_issue_number(message_text: str, working_dir: str) -> str | None:
    """
    Detect issue number references and convert to full GitHub URL.

    Matches: #55, issue 55, issue #55, issue-55, issue55
    """
    # Patterns for issue references
    patterns = [
        r"#(\d+)",  # #55
        r"issue\s*#?(\d+)",  # issue 55, issue #55, issue55
        r"issue-(\d+)",  # issue-55
    ]

    for pattern in patterns:
        match = re.search(pattern, message_text.lower())
        if match:
            issue_num = match.group(1)
            repo_url = _get_github_repo_url(working_dir)
            if repo_url:
                return f"{repo_url}/issues/{issue_num}"

    return None


def detect_tracked_work(
    message_text: str, working_dir: str
) -> tuple[str | None, str | None]:
    """
    Detect if message references tracked work (plan file + tracking URL).

    Workflows are only created for tracked work that has both:
    - A plan document in docs/plans/*.md
    - A tracking issue (GitHub) or task (Notion)

    Detection is smart about natural language:
    - "issue 55" or "#55" -> expands to full GitHub URL
    - "workflow state plan" -> matches docs/plans/workflow-state-persistence.md

    Args:
        message_text: The message text to analyze
        working_dir: Working directory to check for plan files

    Returns:
        Tuple of (plan_file, tracking_url) or (None, None) if not tracked work
    """
    # Detect plan file (supports natural language matching)
    plan_file = _match_plan_by_name(message_text, working_dir)

    # Detect tracking URL
    tracking_url = None

    # First try full URLs
    github_pattern = r"https://github\.com/[^/]+/[^/]+/issues/\d+"
    notion_pattern = r"https://www\.notion\.so/[^\s]+"

    github_match = re.search(github_pattern, message_text)
    notion_match = re.search(notion_pattern, message_text)

    if github_match:
        tracking_url = github_match.group(0)
    elif notion_match:
        tracking_url = notion_match.group(0)
    else:
        # Try issue number shorthand (#55, issue 55, etc.)
        tracking_url = _detect_issue_number(message_text, working_dir)

    # Only return if we have BOTH plan file and tracking URL
    if plan_file and tracking_url:
        return plan_file, tracking_url

    return None, None


def create_workflow_for_tracked_work(
    message_text: str,
    working_dir: str,
    chat_id: str | None,
) -> str | None:
    """
    Create workflow state for tracked work if detected.

    Args:
        message_text: The message text to analyze
        working_dir: Working directory to check for plan files
        chat_id: Telegram chat ID for notifications

    Returns:
        workflow_id if workflow created, None otherwise
    """
    if not USE_CLAUDE_SDK:
        return None

    plan_file, tracking_url = detect_tracked_work(message_text, working_dir)

    if not plan_file or not tracking_url:
        return None

    try:
        from agent.workflow_state import WorkflowState, generate_workflow_id

        workflow_id = generate_workflow_id()
        workflow = WorkflowState(workflow_id)

        # Initialize workflow state
        workflow.update(
            plan_file=plan_file,
            tracking_url=tracking_url,
            telegram_chat_id=int(chat_id) if chat_id else None,
        )

        # Save with initial phase
        workflow.save(phase="plan")

        logger.info(
            f"Created workflow {workflow_id} for tracked work: {plan_file} -> {tracking_url}"
        )

        return workflow_id

    except Exception as e:
        logger.error(f"Failed to create workflow state: {e}")
        return None


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
    client = TelegramClient(str(session_path), API_ID, API_HASH)

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

        # === BRIDGE COMMANDS (bypass agent entirely) ===
        _raw_text = (event.message.text or "").strip().lower()
        if _raw_text == "/update":
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
            f"[{project_name}] Message {message_id} from {sender_name} in {chat_title or 'DM'}: {text[:50]}..."
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

        # Process any incoming media (images, voice, documents)
        media_description = ""
        media_files = []
        if message.media:
            media_description, media_files = await process_incoming_media(
                client, message
            )
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
                enriched_text, youtube_results = await process_youtube_urls_in_text(
                    text
                )
                # Count successful transcriptions
                successful = sum(1 for r in youtube_results if r.get("success"))
                if successful > 0:
                    # Replace clean_text with enriched version that includes transcripts
                    clean_text = clean_message(enriched_text, project)
                    if media_description:
                        clean_text = f"{media_description}\n\n{clean_text}"
                    logger.info(
                        f"Successfully transcribed {successful}/{len(youtube_urls)} YouTube video(s)"
                    )
                else:
                    # Log errors but continue with original text
                    for r in youtube_results:
                        if r.get("error"):
                            logger.warning(
                                f"YouTube processing failed for {r.get('video_id')}: {r.get('error')}"
                            )
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
            logger.info(
                f"Added {len(link_summaries)} link summaries to message context"
            )

        # Fetch reply chain context if this message is replying to something
        # This gives Valor full thread context when someone replies to an old message
        reply_chain_context = ""
        if message.reply_to_msg_id:
            try:
                reply_chain = await fetch_reply_chain(
                    client,
                    event.chat_id,
                    message.reply_to_msg_id,
                    max_depth=20,
                )
                if reply_chain:
                    reply_chain_context = format_reply_chain(reply_chain)
                    logger.info(f"Fetched reply chain with {len(reply_chain)} messages")
            except Exception as e:
                logger.warning(f"Could not fetch reply chain: {e}")

        # Prepend reply chain context if available (gives thread history)
        if reply_chain_context:
            clean_text = f"{reply_chain_context}\n\nCURRENT MESSAGE:\n{clean_text}"

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

            # Check if this is a reply to a revival notification (stateless: read the replied-to message)
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
                                f"[{project_name}] Reply to revival notification, queuing revival with context"
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
                        f"[{project_name}] Processed message from {sender_name} (msg {message_id}) - no response needed"
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

    # Start the client (retry on SQLite session lock from prior process)
    logger.info("Starting Telegram bridge...")
    for _attempt in range(1, 4):
        try:
            await client.start(phone=PHONE, password=PASSWORD)
            break
        except Exception as e:
            if "database is locked" in str(e) and _attempt < 3:
                logger.warning(
                    f"Session DB locked (attempt {_attempt}/3), retrying in {_attempt * 2}s..."
                )
                await asyncio.sleep(_attempt * 2)
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

    # Scan for missed messages during downtime (catchup)
    if USE_CLAUDE_SDK:
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

    # Start session watchdog
    try:
        from monitoring.session_watchdog import watchdog_loop

        asyncio.create_task(watchdog_loop(telegram_client=client))
        logger.info("Session watchdog started")
    except Exception as e:
        logger.error(f"Failed to start session watchdog: {e}")

    # Keep running
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
