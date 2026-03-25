"""Message cleaning, tool log filtering, file extraction,
response sending, and reaction management."""

import asyncio
import logging
import re
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

logger = logging.getLogger(__name__)

# =============================================================================
# File Detection and Sending
# =============================================================================

# Explicit file marker: <<FILE:/path/to/file>>
FILE_MARKER_PATTERN = re.compile(r"<<FILE:([^>]+)>>")

# =============================================================================
# Response Filtering - Remove Tool Logs
# =============================================================================

# Patterns for tool execution logs that should be filtered from responses
# These are internal logs that shouldn't be shown to users
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

# Validated 73 emojis on 2026-02-13 via scripts/test_emoji_reactions.py
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

# Known INVALID reactions - do not use these (tested 2026-02-13)
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
    # Stars (all invalid, tested 2026-02-13)
    "⭐", "🌟", "✨", "💫", "🌠",
    # Checks/marks (all invalid - Telegram doesn't allow any check emojis!)
    "✔", "☑", "✓",
    # Stamps/seals/medals (all invalid)
    "🔖", "📌", "🏅", "🥇", "🥈", "🥉", "🎖",
    # Arrows/indicators (all invalid)
    "➡", "⬆", "↗", "▶",
    # "Done" candidates (all invalid)
    "🔔", "📣", "📢", "🎯", "🪄", "✌", "🤘", "🤙",
    "💪", "🙌", "🫶", "🤞", "💐", "🌹", "🌺",
    # Misc symbols (all invalid)
    "♥", "☀", "🌈", "⚽", "🏈", "🎲", "🧩",
    "🎵", "🎶", "🔑", "💎", "🧲", "🪬", "🧿",
    # Animals (all invalid - only 🕊🐳🦄🙈🙉🙊 work)
    "🐶", "🐱", "🐸", "🐔", "🦅", "🐝", "🦋", "🐢", "🐙",
    # Faces (all invalid)
    "🥳", "😏", "🫠", "🥺", "😤", "🫣", "🫢",
]
# fmt: on

# Reaction emojis for different stages (all validated 2026-02-13)
REACTION_RECEIVED = "👀"  # Message acknowledged
REACTION_PROCESSING = "🤔"  # Default thinking emoji
REACTION_SUCCESS = "👍"  # Simple ack, no text reply needed
REACTION_COMPLETE = "🏆"  # Work done, text reply attached
REACTION_ERROR = "😱"  # Something went wrong

# Intent-specific processing emojis (classified by local Ollama)
# All emojis validated 2026-02-13 via scripts/test_emoji_reactions.py
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


def filter_tool_logs(response: str) -> str:
    """
    Remove tool execution traces from response.

    Agent may include lines like "🛠️ exec: ls -la" in stdout.
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
        if stripped and any(stripped == str(f) or stripped.endswith(str(f)) for f in files_to_send):
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()

    return cleaned, files_to_send


def clean_message(text: str, project: dict | None) -> str:
    """Remove mention triggers from message for cleaner processing."""
    # Import here to avoid circular dependencies
    from bridge.routing import DEFAULT_MENTIONS

    mentions = DEFAULT_MENTIONS
    if project:
        telegram_config = project.get("telegram", {})
        mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)

    result = text
    for mention in mentions:
        result = re.sub(re.escape(mention), "", result, flags=re.IGNORECASE)
    return result.strip()


async def send_response_with_files(
    client: TelegramClient,
    event,
    response: str,
    chat_id: int | None = None,
    reply_to: int | None = None,
    session=None,
) -> bool:
    """
    Send response to Telegram, handling both files and text.

    1. Filter out tool execution logs
    2. Extract any files from the response
    3. Send files first (as separate messages)
    4. Send remaining text (if any) with Markdown formatting

    Can be called with event (handler context) or chat_id+reply_to (queue context).
    Returns True if any content was sent, False otherwise.

    Args:
        session: Optional AgentSession for summarizer context enrichment.
    """
    # Resolve chat_id and reply_to from event or explicit params
    _chat_id = chat_id or (event.chat_id if event else None)
    _reply_to = reply_to or (event.message.id if event and hasattr(event, "message") else None)

    if not _chat_id:
        logger.error("send_response_with_files: no chat_id available")
        return False

    # Filter out tool logs before processing
    original_response = response
    response = filter_tool_logs(response)

    # If filtering removed everything but original had content, use fallback
    if not response:
        if original_response and original_response.strip():
            logger.warning(
                f"filter_tool_logs stripped entire response ({len(original_response)} chars), "
                f"using fallback"
            )
            response = "Done."
        else:
            return False

    text, files = extract_files_from_response(response)

    # Summarize SDK agent responses for consistent PM-quality output.
    # SDLC sessions: always summarize (need stage lines + link footers).
    # Non-SDLC short responses (< 200 chars): skip — these are typically
    # programmatic skill output (e.g., /update) that's already formatted.
    # Re-read session from Redis for fresh stage/link data written during execution
    if session and hasattr(session, "session_id") and session.session_id:
        try:
            from models.agent_session import AgentSession

            fresh = list(AgentSession.query.filter(session_id=session.session_id))
            if fresh:
                session = fresh[0]
        except Exception:
            pass  # Fall back to existing session object

    # PM self-messaging bypass: if the PM already sent messages via the
    # send_telegram tool during this session, skip the summarizer entirely.
    # The PM authored its own messages — the summarizer would be redundant.
    # Only set emoji reaction (handled by the caller). See issue #497.
    if session and hasattr(session, "has_pm_messages") and session.has_pm_messages():
        logger.info(
            f"Skipping summarizer: PM self-messaged during session "
            f"{getattr(session, 'session_id', 'unknown')} "
            f"(pm_sent_message_ids={getattr(session, 'pm_sent_message_ids', [])})"
        )
        return True  # Signal that content was "sent" (PM already delivered it)

    is_sdlc = session and hasattr(session, "is_sdlc") and session.is_sdlc
    should_summarize = text and (is_sdlc or len(text) >= 200)
    if should_summarize:
        try:
            from bridge.summarizer import summarize_response

            summarized = await summarize_response(text, session=session)
            text = summarized.text
            if summarized.full_output_file:
                files.append(summarized.full_output_file)
            if summarized.was_summarized:
                logger.info(f"Summarized response: {len(response)} -> {len(text)} chars")

            # Persist semantic routing fields to session for future routing
            if session and summarized.was_summarized:
                try:
                    if summarized.context_summary:
                        session.context_summary = summarized.context_summary
                    if summarized.expectations is not None:
                        session.expectations = summarized.expectations
                    if summarized.context_summary or summarized.expectations is not None:
                        session.save()
                        logger.debug(
                            f"Persisted routing fields to session "
                            f"{session.session_id}: "
                            f"context_summary={bool(summarized.context_summary)}, "
                            f"expectations={bool(summarized.expectations)}"
                        )
                except Exception as persist_err:
                    # Non-fatal: routing field persistence should never
                    # block message delivery
                    logger.warning(f"Failed to persist routing fields (non-fatal): {persist_err}")
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
            file_type = (
                "image"
                if is_image
                else "video"
                if is_video
                else "audio"
                if is_audio
                else "document"
            )
            logger.info(f"Sent file: {file_path} (type: {file_type})")
        except Exception as e:
            logger.error(f"Failed to send file {file_path}: {e}")
            await client.send_message(_chat_id, f"Failed to send file: {file_path.name}")

    # Track if we sent anything
    sent_content = bool(files)

    # Send text if there's meaningful content
    if text and not text.isspace():
        # Safety truncation at Telegram's limit (summarizer handles graceful shortening)
        if len(text) > 4096:
            text = text[:4093] + "..."
        try:
            # Use markdown parse mode with plain-text fallback
            from bridge.markdown import send_markdown

            await send_markdown(client, _chat_id, text, reply_to=_reply_to)
            sent_content = True
        except Exception as e:
            logger.error(f"Failed to send text message to chat {_chat_id} ({len(text)} chars): {e}")
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
        # intent/ module classifies message type (search, code, chat, etc.)
        # to pick a contextual reaction emoji. Falls back to default on failure.
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
