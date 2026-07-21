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
- `clean_message`: normalizes surrounding whitespace on inbound user text.
  It does NOT remove mention triggers — the agent sees the message verbatim,
  including its own name. Routing's @-mention detection is independent
  (`bridge.routing.is_message_for_valor`).
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
    # 🖕 is a valid Telegram reaction but offensive to send at a user — excluded.
    "👍", "👎", "👏", "🙏", "👌", "🤝", "✍",
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
REACTION_PROCESSING = "✍"  # Actively composing a reply (distinct from REACTION_ERROR's pinned 🤔)
# REACTION_ABORT parallels REACTION_RECEIVED for the steering-ack path: when a
# user's follow-up matches an abort keyword, the bridge salutes (🫡 = "understood,
# standing down") instead of the standard "noted" eyes. Selected inside
# _ack_steering_routed() in bridge/telegram_bridge.py — never at call sites.
REACTION_ABORT = "🫡"  # Steering abort acknowledged
# Applied at ingestion when this machine's worker is NOT alive (#1312): the
# message still enqueues, but ⚠ signals "paused, not lost" instead of a normal
# 👀 that would imply work is in progress.
REACTION_WORKER_DOWN = "⚠"  # Worker not alive — enqueued but not being processed

# REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS are re-exported from
# agent.constants (canonical location) — imported at top of file for
# backward compatibility with existing imports. These are EmojiResult objects,
# resolved lazily via find_best_emoji() on first access with hardcoded fallbacks.


def _reaction_constants() -> dict[str, str]:
    """Name → glyph mapping of every reaction constant this module exposes.

    The lazily-resolved EmojiResult constants (REACTION_SUCCESS,
    REACTION_COMPLETE, REACTION_ERROR) are compared by their str() glyph value;
    they are already resolved by this module's top-of-file import.
    """
    return {
        "REACTION_RECEIVED": REACTION_RECEIVED,
        "REACTION_PROCESSING": REACTION_PROCESSING,
        "REACTION_ABORT": REACTION_ABORT,
        "REACTION_WORKER_DOWN": REACTION_WORKER_DOWN,
        "REACTION_SUCCESS": str(REACTION_SUCCESS),
        "REACTION_COMPLETE": str(REACTION_COMPLETE),
        "REACTION_ERROR": str(REACTION_ERROR),
    }


def _assert_distinct(constants: dict[str, str] | None = None) -> None:
    """Raise ImportError if any two reaction constants share a glyph.

    Definition-site invariant (#2004 T1.8) for the issue #1961 defect class:
    a duplicated glyph between two constant groups (e.g. 🤔 doubling as both
    "processing" and "error") makes reactions ambiguous to the user. Executed
    at import time below, and shared with
    tests/integration/test_reply_delivery.py::TestReactionEmojiSelection so
    the distinctness rule has exactly one implementation.

    Args:
        constants: Optional name → glyph mapping to check; defaults to the
            module's full reaction-constant registry.

    Raises:
        ImportError: Naming the duplicated glyph and BOTH constant names.
    """
    if constants is None:
        constants = _reaction_constants()
    seen: dict[str, str] = {}
    for name, glyph in constants.items():
        other = seen.get(glyph)
        if other is not None:
            raise ImportError(
                f"Reaction emoji collision in bridge.response: {glyph!r} is used by "
                f"both {other} and {name}. Every reaction constant must map to a "
                f"distinct glyph (issue #1961)."
            )
        seen[glyph] = name


_assert_distinct()


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


def clean_message(text: str) -> str:
    """Normalize surrounding whitespace on inbound user text.

    The message is passed through verbatim apart from leading/trailing
    whitespace — mention triggers (``@valor``, the bare name "Valor", etc.)
    are deliberately NOT removed. The agent should see exactly what the user
    typed, including its own name in a salutation ("Hi Valor, ...") or
    mid-sentence ("Valor, here is a chat..."). Stripping the name corrupted
    the prompt and served no purpose: routing decides whether a message is
    addressed to Valor via independent @-mention detection
    (``bridge.routing.is_message_for_valor``), never by mutating the body.

    Whitespace is stripped so callers can use a falsy check to detect an
    empty/whitespace-only message and substitute a placeholder.
    """
    return text.strip()


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


async def react_if_worker_down(client, chat_id, message_id, session_id) -> None:
    """Apply the ⚠ worker-down reaction when this machine's worker is not alive.

    Ingestion-time liveness signal (#1312). Called immediately before each
    ``dispatch_telegram_session`` enqueue: if the worker loop beacon is not fresh
    (worker process down/wedged, or Redis unreadable → fail-closed), overwrite
    the message reaction with ``REACTION_WORKER_DOWN`` so the user sees "paused,
    not lost." The enqueue still proceeds unconditionally at the call site — no
    work is dropped; this helper only signals.

    When ⚠ is set, ``record_worker_down_reaction`` records the (session, chat,
    message) tuple so the already-merged worker-recovery path (#2178) can later
    clear the reaction once the worker is back. That is why ``session_id`` is
    required here — this helper only RECORDS; it never clears.

    Fully fail-quiet: never raises into the handler. A fresh beacon is a no-op
    (happy path byte-identical — no extra reaction).
    """
    try:
        from agent.session_health import worker_loop_beacon_fresh

        if worker_loop_beacon_fresh():
            return

        # Worker not alive: signal ⚠ (swallow set_reaction failures — non-fatal,
        # matching the existing "set_reaction failed (non-fatal)" pattern) and
        # record for the #2178 recovery-time clear.
        await set_reaction(client, chat_id, message_id, REACTION_WORKER_DOWN)
        from agent.worker_down_reactions import record_worker_down_reaction

        record_worker_down_reaction(session_id, chat_id, message_id)
    except Exception as e:
        logger.debug(f"react_if_worker_down failed (non-fatal): {e}")
