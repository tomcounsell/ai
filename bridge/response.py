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
import os
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


# =============================================================================
# Session liveness tick counter (#2716)
# =============================================================================
#
# The watchdog advances a counter on the session's originating message so the
# human can see that an independent observer still has eyes on the session.
# The number is DURATION and nothing else: tick 4 means "roughly forty minutes
# since this counter started". It makes no claim about whether the session is
# making progress — a busy session and a wedged one tick identically.
#
# Why digits need the custom-emoji schema: keycap digits (1️⃣ … 9️⃣, in either
# the U+FE0F-suffixed or bare U+20E3 encoding) are NOT among the 74 standard
# reactions Telegram's server advertises. Do not add one to VALIDATED_REACTIONS
# — it will be rejected at the API exactly as ⏳ was. The only way to render a
# digit is ReactionCustomEmoji with a sticker-pack document_id, which requires
# a Premium sender, which is why every tick also carries a standard-glyph
# fallback that set_reaction degrades to automatically.

# Seconds per tick. Overridable locally for testing the ceiling without waiting
# 100 minutes. Grain of salt: 600s is a provisional, tunable choice — it is the
# smallest interval that keeps reaction traffic negligible against the relay's
# shared FLOOD_WAIT exposure.
# Floored at 1: the docstring above invites lowering this to exercise the
# ceiling, and a 0 would make the watchdog's `elapsed // interval` raise
# ZeroDivisionError once per running session on every scan.
HEARTBEAT_TICK_INTERVAL_SECONDS = max(
    1, int(os.environ.get("HEARTBEAT_TICK_INTERVAL_SECONDS", "600"))
)

# Highest tick the counter will render. Past it the counter refuses to advance
# and the session is steered to publish a progress message instead. With the
# default interval that is 9 digits over 90 minutes, plus the slot-0 👀 the
# ingestion path already placed — a forced progress message every 100 minutes.
HEARTBEAT_MAX_TICKS = 9

# Pinned custom-emoji document ids, tick number → document_id.
#
# EMPTY UNTIL PINNED. The digit glyphs live in the `Birthday Collection` pack
# (sticker set id 1901206392136531984), which is already installed on this
# account, but the per-digit document ids were never recorded anywhere in the
# repo and can only be read from a live authenticated Telethon client:
#
#     from telethon.tl.functions.messages import GetStickerSetRequest
#     from telethon.tl.types import InputStickerSetID
#
# Until this table is populated the counter runs entirely on
# HEARTBEAT_FALLBACK_ARC — which is the same degradation path taken when the
# pack is uninstalled or the account loses Premium, so the feature is correct
# either way; it simply shows an alternating arc instead of digits.
PREMIUM_DIGIT_REACTIONS: dict[int, int] = {}

# Standard-glyph fallback, used both when a digit is unpinned and when Telegram
# rejects the custom emoji. Slot 0 is 👀 (the acknowledgement the ingestion path
# already set); every later tick alternates 🥱 / 👨‍💻 so the human can see the
# reaction change even without digits.
#
# 🤔 is deliberately absent: it is REACTION_ERROR's pinned glyph, and a healthy
# session must never wear the error face. This tuple is NOT registered in
# _reaction_constants() — see heartbeat_reaction() for why that matters.
HEARTBEAT_FALLBACK_ARC = ("👀", "🥱", "👨‍💻")


def heartbeat_reaction(tick: int):
    """Return the ``EmojiResult`` for liveness tick ``tick``.

    Deterministic lookup, never ``find_best_emoji`` — that function samples
    from a softmax over semantically similar candidates, which is correct for
    "pick a feeling" and wrong for "this is tick 4".

    The returned result carries the pinned custom-emoji ``document_id`` when
    one exists for this tick and the standard arc glyph as ``emoji``, so
    ``set_reaction`` renders the digit when it can and falls back silently
    when it cannot.

    Args:
        tick: Tick number, ``0`` through :data:`HEARTBEAT_MAX_TICKS`.

    Returns:
        An ``EmojiResult`` whose glyph is always a legal Telegram reaction.

    Raises:
        ValueError: For a negative tick, or one past
            :data:`HEARTBEAT_MAX_TICKS`. It never clamps — a clamped counter
            would sit at the last digit forever, which is precisely the
            "runs forever" failure the ceiling exists to prevent.
    """
    from tools.emoji_embedding import EmojiResult

    if tick < 0:
        raise ValueError(f"Liveness tick must be non-negative, got {tick}")
    if tick > HEARTBEAT_MAX_TICKS:
        raise ValueError(
            f"Liveness tick {tick} exceeds HEARTBEAT_MAX_TICKS={HEARTBEAT_MAX_TICKS}. "
            f"The counter refuses to advance past the ceiling; the caller must force "
            f"a progress message and re-anchor instead of clamping."
        )

    glyph = HEARTBEAT_FALLBACK_ARC[0] if tick == 0 else HEARTBEAT_FALLBACK_ARC[1 + (tick - 1) % 2]
    document_id = PREMIUM_DIGIT_REACTIONS.get(tick)
    return EmojiResult(
        emoji=glyph,
        document_id=document_id,
        is_custom=document_id is not None,
    )


def _reaction_constants() -> dict[str, str]:
    """Name → glyph mapping of every reaction constant this module exposes.

    The lazily-resolved EmojiResult constants (REACTION_SUCCESS,
    REACTION_COMPLETE, REACTION_ERROR) are compared by their str() glyph value;
    they are already resolved by this module's top-of-file import.

    HEARTBEAT_FALLBACK_ARC is deliberately NOT registered here. It is a
    sequence, not a constant, and it reuses 👀 (REACTION_RECEIVED) at slot 0 on
    purpose — the counter's first slot IS the acknowledgement. Registering it
    would make _assert_distinct() raise ImportError and the bridge would not
    start. tests/unit/test_heartbeat_reactions.py asserts the non-registration.
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


# =============================================================================
# Polls (native Telegram polls, group chats only)
# =============================================================================
#
# Raw MTProto. `InputMediaPoll` needs no new dependency — Telethon 1.42 already
# ships every TL type used here, and this module already talks raw MTProto for
# reactions above.


# `PollAnswer.option` is an arbitrary bytes blob Telegram echoes back verbatim on
# read, and its only protocol constraint is uniqueness per option. We spend that
# blob on a correlation id so a poll discovered on the wire can be matched back
# to the outbox payload that produced it (Race 6).
#
# THE CEILING IS 8 BYTES, NOT 100. Measured empirically against live MTProto on
# 2026-09-02 while running the Task 1 gate: 8 bytes is accepted, 9 is rejected
# with `A poll option used invalid data (the data may be too long)` at the wire.
# The TL schema's `option:bytes` carries no visible bound and every reference the
# plan consulted said 100, so this is only knowable by probing. Treat the number
# below as a hard, verified protocol constant — a value that exceeds it fails at
# the wire with no local signal, which is exactly the failure mode that would
# ship a silently unroutable poll.
#
# The layout is therefore packed binary rather than the `f"{index}:{hex}"` text
# form the plan sketched, which cannot fit: 1 byte of option index, then the
# first 7 bytes of the 32-hex `poll_id_hint`. 56 bits is far more than orphan
# adoption needs to disambiguate a bounded window of recent outbound polls in one
# chat. A 2+-candidate collision is caught at scan time: the adoption rule bails
# with a warning on an ambiguous match and adopts nothing. A single false-positive
# match (the truncated prefix happens to match a different poll's hint) is caught
# one step later, at promotion: `promote_pending_poll`'s underlying `SET NX`
# refuses to overwrite that poll's already-registered row, so the caller treats
# the adoption as failed rather than silently swallowing the question.
_OPTION_MAX_BYTES = 8
_OPTION_HINT_BYTES = _OPTION_MAX_BYTES - 1  # 7 — the index takes the first byte


def encode_option(index: int, correlation_id: str | None = None) -> bytes:
    """Encode a poll option index (plus optional correlation id) into option bytes.

    Layout: ``bytes([index])`` followed by the first ``_OPTION_HINT_BYTES`` bytes
    of ``correlation_id`` decoded from hex. The bare one-byte form (no
    correlation id) is a probe-only affordance.

    The inverse of :func:`decode_option`. Both live here, next to the only
    producer, so the translator, the orphan-adoption scan and the gate probes all
    parse one encoding rather than three copies of it.
    """
    if not 0 <= index <= 255:
        raise ValueError(f"poll option index {index} does not fit in one byte")
    raw = bytes([index])
    if correlation_id:
        raw += _hint_bytes(correlation_id)
    if len(raw) > _OPTION_MAX_BYTES:  # unreachable by construction; a guard, not a branch
        raise ValueError(
            f"poll option bytes exceed Telegram's verified {_OPTION_MAX_BYTES}-byte "
            f"ceiling ({len(raw)} bytes)"
        )
    return raw


def decode_option(raw: bytes | None) -> tuple[int | None, str | None]:
    """Decode option bytes back into ``(index, correlation_id_prefix)``.

    The returned correlation id is the **7-byte prefix as 14 hex chars**, not the
    full 32-hex ``poll_id_hint`` — the ceiling does not allow carrying the whole
    id. Callers match with :func:`correlation_matches` rather than comparing to a
    full hint directly.

    Returns ``(index, None)`` for the bare probe-only form and ``(None, None)``
    when the bytes are absent or unparseable — a poll we did not send, or one
    from before this encoding existed. Never raises: this runs against whatever
    Telegram hands back.
    """
    if not raw:
        return (None, None)
    try:
        data = bytes(raw)
    except Exception:  # noqa: BLE001 — arbitrary server bytes
        return (None, None)
    if not data:
        return (None, None)
    index = data[0]
    tail = data[1:]
    return (index, tail.hex() if tail else None)


def correlation_matches(decoded_prefix: str | None, poll_id_hint: str | None) -> bool:
    """Whether option bytes decoded off the wire belong to ``poll_id_hint``.

    The option can only carry a 7-byte prefix of the hint, so an exact
    string compare against the full 32-hex hint would never match. This is the
    one place that asymmetry is handled; orphan adoption and the relay's
    already-sent lookup both go through it rather than comparing by hand.
    """
    if not decoded_prefix or not poll_id_hint:
        return False
    try:
        return decoded_prefix == _hint_bytes(poll_id_hint).hex()
    except Exception:  # noqa: BLE001
        return False


def _hint_bytes(correlation_id: str) -> bytes:
    """First ``_OPTION_HINT_BYTES`` bytes of a hex correlation id."""
    return bytes.fromhex(correlation_id[: _OPTION_HINT_BYTES * 2])


async def send_poll(
    client: TelegramClient,
    chat_id: int,
    question: str,
    options: list[str],
    *,
    reply_to: int | None = None,
    correlation_id: str | None = None,
) -> tuple[int, int] | None:
    """Send a native single-choice poll and return ``(msg_id, server_poll_id)``.

    **The caller-supplied ``Poll.id`` is a placeholder.** Telegram assigns the
    real poll id server-side and it is only knowable by reading it back off the
    sent message's ``MessageMediaPoll.poll.id``. Every downstream consumer — the
    registry, the vote translator, the reconciliation loop — keys on the
    server-assigned id, so this function returning it is load-bearing, not a
    convenience.

    **Group-only in practice.** A send into a 1:1 DM is rejected by MTProto with
    ``MediaInvalidError``; that is a settled fact of the protocol, not a
    transient failure to retry against. Enforcement lives in
    ``bridge/poll_gating.py``, which every production caller passes through
    first.

    Follows the local error idiom (log, return ``None``, never raise) — but the
    ``None`` stays **distinguishable** so the relay's retry / dead-letter path
    still engages rather than silently dropping a blocked agent's question.

    Args:
        correlation_id: The outbox payload's ``poll_id_hint``, embedded in every
            option's bytes so orphan adoption can match this poll exactly. Every
            production caller supplies it; ``None`` is a probe-only affordance
            and is logged loudly.
    """
    from telethon.tl.functions.messages import SendMediaRequest
    from telethon.tl.types import (
        InputMediaPoll,
        Poll,
        PollAnswer,
        TextWithEntities,
    )

    if correlation_id is None:
        logger.warning(
            "poll_sent_without_correlation_id chat_id=%s — orphan adoption cannot "
            "match this poll; every production caller must thread poll_id_hint",
            chat_id,
        )

    def _text(value: str) -> TextWithEntities:
        # Layer 1.42 takes TextWithEntities, not bare str. Pre-1.42 examples that
        # pass strings TypeError here.
        return TextWithEntities(text=value, entities=[])

    try:
        media = InputMediaPoll(
            poll=Poll(
                # Placeholder — the server assigns the real id, read back below.
                id=0,
                question=_text(question),
                answers=[
                    PollAnswer(text=_text(opt), option=encode_option(i, correlation_id))
                    for i, opt in enumerate(options)
                ],
                closed=False,
                public_voters=False,
                multiple_choice=False,
                quiz=False,
            )
        )
        result = await client(
            SendMediaRequest(
                peer=chat_id,
                media=media,
                message="",
                random_id=_random_id(),
                reply_to=_reply_to(reply_to),
            )
        )
    except Exception as e:  # noqa: BLE001 — surfaced to the relay as a retryable None
        logger.warning("send_poll failed chat_id=%s: %s", chat_id, e)
        return None

    msg_id, server_poll_id = _read_back_poll_ids(result)
    if msg_id is None or server_poll_id is None:
        logger.warning(
            "send_poll: could not read back msg_id/poll_id from the send result chat_id=%s",
            chat_id,
        )
        return None
    return (msg_id, server_poll_id)


async def close_poll(client: TelegramClient, chat_id: int, msg_id: int, poll_media) -> bool:
    """Close a poll by editing it with ``closed=True``.

    Closing is how a poll is marked answered at the source: it makes
    retract-and-revote impossible and gives the human a visible "already
    answered" state. In a group this is also the first-voter-wins boundary, and
    that is deliberate — the poll exists to unblock one agent, not to take a
    vote of the room.

    Fail-quiet: returns ``False`` on any error. A failure to close never blocks
    the translation that already claimed the vote.
    """
    from telethon.tl.functions.messages import EditMessageRequest
    from telethon.tl.types import InputMediaPoll, Poll

    try:
        poll = getattr(poll_media, "poll", None) or poll_media
        closed = Poll(
            id=poll.id,
            question=poll.question,
            answers=poll.answers,
            closed=True,
            public_voters=getattr(poll, "public_voters", False),
            multiple_choice=getattr(poll, "multiple_choice", False),
            quiz=getattr(poll, "quiz", False),
        )
        await client(EditMessageRequest(peer=chat_id, id=msg_id, media=InputMediaPoll(poll=closed)))
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("close_poll failed chat_id=%s msg_id=%s: %s", chat_id, msg_id, e)
        return False


def _random_id() -> int:
    import random

    return random.getrandbits(63)


def _reply_to(reply_to: int | None):
    if not reply_to:
        return None
    from telethon.tl.types import InputReplyToMessage

    return InputReplyToMessage(reply_to_msg_id=int(reply_to))


def _read_back_poll_ids(result) -> tuple[int | None, int | None]:
    """Pull ``(msg_id, server_poll_id)`` out of a ``SendMediaRequest`` result.

    The result is an ``Updates`` container; the poll arrives inside whichever
    update carries the new message. Walk rather than index — the update order is
    not part of the protocol contract.
    """
    from telethon.tl.types import MessageMediaPoll

    for update in getattr(result, "updates", []) or []:
        message = getattr(update, "message", None)
        if message is None or isinstance(message, str):
            continue
        media = getattr(message, "media", None)
        if isinstance(media, MessageMediaPoll):
            poll = getattr(media, "poll", None)
            if poll is not None:
                return (getattr(message, "id", None), getattr(poll, "id", None))
    return (None, None)
