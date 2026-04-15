"""
Shared constants for the agent session system.

These constants were originally defined in bridge/response.py but are used by
both the bridge and the standalone worker. Canonical definitions live here;
bridge/response.py re-exports them for backward compatibility.

Terminal reaction constants (REACTION_SUCCESS, REACTION_COMPLETE, REACTION_ERROR)
are resolved lazily via module __getattr__ using find_best_emoji() on first access.
Each constant is cached in _TERMINAL_EMOJI_CACHE after first resolution — no HTTP
call is made at import time and no retry is performed on failure. If find_best_emoji()
raises any exception (missing API key, no embeddings file, network error), a hardcoded
fallback EmojiResult is returned and cached: 👌 (SUCCESS), 👏 (COMPLETE), 😢 (ERROR).
All fallback emojis are confirmed in VALIDATED_REACTIONS.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.emoji_embedding import EmojiResult

logger = logging.getLogger(__name__)

# Cache for lazily-resolved terminal reaction EmojiResult objects.
# Populated on first access; never retried after failure.
_TERMINAL_EMOJI_CACHE: dict[str, "EmojiResult"] = {}

# Feeling strings and fallback emojis for each terminal reaction constant.
_TERMINAL_EMOJI_CONFIG: dict[str, tuple[str, str]] = {
    "REACTION_SUCCESS": ("acknowledged received silently noted", "\U0001f44c"),  # 👌
    "REACTION_COMPLETE": ("task completed successfully work done", "\U0001f44f"),  # 👏
    "REACTION_ERROR": ("error occurred something went wrong", "\U0001f622"),  # 😢
}


def _resolve_terminal_emoji(name: str, feeling: str, fallback_emoji: str) -> "EmojiResult":
    """Resolve a terminal reaction emoji, caching the result.

    Calls find_best_emoji(feeling) and caches the EmojiResult in
    _TERMINAL_EMOJI_CACHE. Falls back to a hardcoded fallback EmojiResult
    when find_best_emoji() raises an exception or returns the DEFAULT_EMOJI
    (which happens when OPENROUTER_API_KEY is absent or embeddings are
    unavailable). This ensures all three terminal constants remain distinct
    even in degraded environments.

    Args:
        name: Constant name (e.g. "REACTION_SUCCESS") — used as cache key.
        feeling: Human-readable feeling phrase passed to find_best_emoji().
        fallback_emoji: Unicode emoji to use when find_best_emoji() fails or
            returns the default fallback emoji.

    Returns:
        EmojiResult instance (always valid, never raises).
    """
    from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult

    try:
        from tools.emoji_embedding import find_best_emoji

        result = find_best_emoji(feeling)
        # If find_best_emoji returned the DEFAULT_EMOJI, the API was unavailable.
        # Use our own fallback so the three constants remain distinct.
        if result.emoji == DEFAULT_EMOJI and not result.is_custom:
            raise ValueError(f"find_best_emoji returned default emoji for {name!r}")
        _TERMINAL_EMOJI_CACHE[name] = result
        return result
    except Exception as exc:
        logger.debug("find_best_emoji failed for %s (%r): %s — using fallback", name, feeling, exc)
        fallback = EmojiResult(emoji=fallback_emoji)
        _TERMINAL_EMOJI_CACHE[name] = fallback
        return fallback


def __getattr__(name: str) -> "EmojiResult":
    """Lazily resolve terminal reaction constants on first access.

    Handles REACTION_SUCCESS, REACTION_COMPLETE, and REACTION_ERROR.
    The resolved EmojiResult is cached in _TERMINAL_EMOJI_CACHE so
    subsequent attribute accesses are a dict lookup with no HTTP calls.

    Raises:
        AttributeError: For any name not in _TERMINAL_EMOJI_CONFIG.
    """
    if name in _TERMINAL_EMOJI_CACHE:
        return _TERMINAL_EMOJI_CACHE[name]

    if name in _TERMINAL_EMOJI_CONFIG:
        feeling, fallback = _TERMINAL_EMOJI_CONFIG[name]
        return _resolve_terminal_emoji(name, feeling, fallback)

    raise AttributeError(f"module 'agent.constants' has no attribute {name!r}")
