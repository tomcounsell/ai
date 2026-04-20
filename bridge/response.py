"""Reactions, file-marker extraction, tool-log filtering, and message cleaning.

This module is the slim residue of the pre-#1074 `bridge/response.py`. The
heavyweight delivery path (`send_response_with_files`) has been removed — the
worker path (`TelegramRelayOutputHandler.send` in `agent/output_handler.py`)
and the bridge's event-handler path (`bridge/telegram_bridge.py`) now both
deliver via the Redis outbox + relay, with the drafter running once at the
OutputHandler boundary.

What remains here:
- Reactions: `set_reaction`, `VALIDATED_REACTIONS`, `INVALID_REACTIONS`, and
  the `REACTION_*` backward-compat re-exports from `agent.constants`.
- `filter_tool_logs`: strips emoji-prefixed tool-trace lines. Used by the
  bridge's send callback before enqueuing agent output.
- `extract_files_from_response`: parses `<<FILE:/path>>` markers. Used by the
  bridge's direct send path to pull out file attachments.
- `clean_message`: strips @-mention triggers from inbound user text.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.emoji_embedding import EmojiResult

from telethon import TelegramClient
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionCustomEmoji, ReactionEmoji

from agent.constants import (
    REACTION_COMPLETE,  # noqa: F401
    REACTION_ERROR,  # noqa: F401
    REACTION_SUCCESS,  # noqa: F401
)

logger = logging.getLogger(__name__)

# =============================================================================
# File Marker Extraction
# =============================================================================

# Explicit file marker: <<FILE:/path/to/file>>
FILE_MARKER_PATTERN = re.compile(r"<<FILE:([^>]+)>>")

# =============================================================================
# Validated Reactions (tested 2026-02-13 via scripts/test_emoji_reactions.py)
# =============================================================================

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
    "💻", "🎨", "❌", "✅", "🔄", "⏳", "🚀", "💡", "📝", "🔍",
    # Emojis with U+FE0F variation selector (use base forms instead):
    "❤️", "❤️‍🔥", "✍️", "☃️", "🤷‍♂️", "🤷‍♀️",
    # Stars (all invalid, tested 2026-02-13)
    "⭐", "🌟", "✨", "💫", "🌠",
    # Checks/marks (all invalid - Telegram doesn't allow any check emojis!)
    "✔", "☑", "✓",
    # Stamps/seals/medals
    "🔖", "📌", "🏅", "🥇", "🥈", "🥉", "🎖",
    # Arrows/indicators
    "➡", "⬆", "↗", "▶",
    # "Done" candidates
    "🔔", "📣", "📢", "🎯", "🪄", "✌", "🤘", "🤙",
    "💪", "🙌", "🫶", "🤞", "💐", "🌹", "🌺",
    # Misc symbols
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

# REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS are re-exported from
# agent.constants (canonical location) — imported at top of file for
# backward compatibility with existing imports. These are EmojiResult objects,
# resolved lazily via find_best_emoji() on first access with hardcoded fallbacks.


# =============================================================================
# Tool Log Filtering
# =============================================================================

# Generic emoji-prefix pattern: catches lines like "🛠️ exec: ls", "📖 read: foo.py",
# "🔎 web_search: query". The pattern ranges cover the Misc Symbols, Dingbats, and
# Supplemental Symbols blocks where tool-trace emojis typically live. The
# U+FE0F variation selector is optional after the emoji.
_TOOL_LOG_GENERIC_PATTERN = re.compile(
    r"^[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]\uFE0F?\s*\w+:", re.UNICODE
)

# Backtick-wrapped shell command lines (e.g. "`cd foo && ls`") — these are
# typically tool-trace echoes, not real agent output. Detected after stripping.
_SHELL_COMMAND_HINTS = ("cd ", "ls ", "cat ", "grep ", "find ", "mkdir ", "rm ", "mv ", "cp ")


def filter_tool_logs(response: str) -> str:
    """Remove emoji-prefixed tool-trace lines from ``response``.

    Agent stdout can include traces like ``🛠️ exec: ls -la`` or ``📖 read: foo.py``.
    These are internal tooling artifacts, not meant for human readers. This
    filter strips them while preserving meaningful prose.

    Returns the filtered text. If filtering removes everything (i.e. the
    response was pure tooling output), returns ``""``.
    """
    if not response:
        return ""

    lines = response.split("\n")
    filtered: list[str] = []
    for line in lines:
        stripped = line.strip()

        # Preserve blank-line structure but collapse runs of blanks
        if not stripped:
            if filtered and filtered[-1].strip():
                filtered.append(line)
            continue

        # Drop emoji-prefix tool traces
        if _TOOL_LOG_GENERIC_PATTERN.match(stripped):
            continue

        # Drop backtick-wrapped shell command echoes
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 2:
            inner = stripped[1:-1].lower()
            if any(cmd in inner for cmd in _SHELL_COMMAND_HINTS):
                continue

        filtered.append(line)

    result = "\n".join(filtered).strip()
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")

    # If filtering removed everything meaningful, return empty string so the
    # caller can choose a fallback (e.g. "Done.").
    if not result or len(result) < 5:
        return ""
    return result


# =============================================================================
# File Extraction
# =============================================================================


def extract_files_from_response(
    response: str, working_dir: Path | None = None
) -> tuple[str, list[Path]]:
    """Pull file paths out of ``<<FILE:/path>>`` markers in ``response``.

    Returns a tuple of ``(cleaned_text, file_paths)`` where ``cleaned_text`` has
    the markers stripped and ``file_paths`` is a list of existing-on-disk
    ``Path`` objects referenced by markers (duplicates are dropped).

    Args:
        response: Raw response text.
        working_dir: Unused (retained for backward compatibility with callers).
    """
    _ = working_dir  # accepted but unused; callers may still pass it
    files_to_send: list[Path] = []
    seen_paths: set[str] = set()

    for match in FILE_MARKER_PATTERN.finditer(response):
        path_str = match.group(1).strip()
        path = Path(path_str)
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in seen_paths:
                files_to_send.append(path)
                seen_paths.add(resolved)

    # Clean response: remove file markers and strip now-empty lines
    cleaned = FILE_MARKER_PATTERN.sub("", response)
    lines = cleaned.split("\n")
    cleaned_lines = [
        line
        for line in lines
        if not (
            line.strip()
            and any(line.strip() == str(f) or line.strip().endswith(str(f)) for f in files_to_send)
        )
    ]
    cleaned = "\n".join(cleaned_lines).strip()

    return cleaned, files_to_send


# =============================================================================
# Mention Stripping
# =============================================================================


def clean_message(text: str, project: dict | None) -> str:
    """Strip @-mention triggers from inbound user text."""
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


# =============================================================================
# Reactions (Telegram message reactions)
# =============================================================================


async def set_reaction(
    client: TelegramClient, chat_id: int, msg_id: int, emoji: str | EmojiResult | None
) -> bool:
    """Set a reaction on a message.

    Supports both standard emoji strings and ``EmojiResult`` objects from the
    emoji embedding system. When an ``EmojiResult`` with ``is_custom=True`` is
    provided, attempts to set a custom emoji reaction via
    ``ReactionCustomEmoji(document_id=...)``; falls back to the standard emoji
    from the same result on failure (non-Premium, restricted chat, etc.).

    Args:
        client: Telegram client.
        chat_id: Chat ID.
        msg_id: Message ID.
        emoji: Emoji string, ``EmojiResult``, or ``None`` to remove reactions.

    Returns:
        ``True`` if successful, ``False`` otherwise.
    """
    from tools.emoji_embedding import EmojiResult

    # Normalize to EmojiResult if string
    if isinstance(emoji, str):
        emoji_result = EmojiResult(emoji=emoji)
    elif isinstance(emoji, EmojiResult):
        emoji_result = emoji
    elif emoji is None:
        # Remove reactions
        try:
            await client(SendReactionRequest(peer=chat_id, msg_id=msg_id, reaction=[]))
            return True
        except Exception as e:
            logger.debug(f"Could not remove reaction: {e}")
            return False
    else:
        logger.debug(f"set_reaction: unexpected emoji type {type(emoji)}")
        return False

    # Try custom emoji first if applicable
    if emoji_result.is_custom and emoji_result.document_id is not None:
        try:
            reaction = [ReactionCustomEmoji(document_id=emoji_result.document_id)]
            await client(SendReactionRequest(peer=chat_id, msg_id=msg_id, reaction=reaction))
            return True
        except Exception as e:
            logger.debug(
                f"Custom emoji reaction failed (doc_id={emoji_result.document_id}), "
                f"falling back to standard: {e}"
            )
            # Fall through to standard emoji

    # Standard emoji path
    standard_emoji = emoji_result.emoji or str(emoji_result)
    if not standard_emoji:
        return False

    try:
        reaction = [ReactionEmoji(emoticon=standard_emoji)]
        await client(SendReactionRequest(peer=chat_id, msg_id=msg_id, reaction=reaction))
        return True
    except Exception as e:
        logger.debug(f"Could not set reaction '{standard_emoji}': {e}")
        return False
