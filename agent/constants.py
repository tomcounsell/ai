"""
Shared constants for the agent session system.

These constants were originally defined in bridge/response.py but are used by
both the bridge and the standalone worker. Canonical definitions live here;
bridge/response.py re-exports them for backward compatibility.

Terminal reaction constants (REACTION_SUCCESS, REACTION_COMPLETE, REACTION_ERROR)
are resolved lazily via module __getattr__ on first access and cached in
_TERMINAL_EMOJI_CACHE — no HTTP call is made at import time and no retry is
performed on failure.

Resolution has two modes, encoded per-constant in _TERMINAL_EMOJI_CONFIG:

- **Semantic** (REACTION_SUCCESS, REACTION_COMPLETE): resolved via find_best_emoji()
  over the VALIDATED_REACTIONS index. Positive variety is desirable and provably
  safe — find_best_emoji filters out BLOCKED_REACTION_EMOJIS, so no hostile face can
  ever be drawn. If find_best_emoji() raises (missing API key, no embeddings file,
  network error) or returns the bare default, a hardcoded fallback EmojiResult is
  returned and cached: 👌 (SUCCESS), 👏 (COMPLETE).

- **Pinned** (REACTION_ERROR): resolved directly to a fixed emoji — never through
  the semantic resolver. It is pinned to 🤔 so a terminal-failure reaction placed on
  a user's own message is deterministic and never hostile: no lottery over the
  negative-faces cluster (👎 🤬 😡 🤮 😱 …). The pin is the single source of truth
  for the value 🤔; there is no dead feeling phrase or fallback.

All resolved emojis are confirmed in VALIDATED_REACTIONS.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Cache for lazily-resolved terminal reaction EmojiResult objects.
# Populated on first access; never retried after failure.
_TERMINAL_EMOJI_CACHE: dict[str, object] = {}


class _TerminalEmojiConfig(NamedTuple):
    """Resolution spec for one terminal reaction constant.

    The ``pinned`` flag — not tuple arity — selects the resolution mode, so the
    config itself is the single source of truth for whether a constant is fixed
    or semantically resolved.

    Attributes:
        feeling: Phrase passed to find_best_emoji() for semantic resolution.
            ``None`` for pinned constants (no semantic draw occurs).
        emoji: For pinned constants, the fixed emoji to resolve to. For semantic
            constants, the hardcoded fallback used when find_best_emoji() fails or
            returns the bare default.
        pinned: When True, resolve directly to ``emoji`` — skipping find_best_emoji()
            and the DEFAULT_EMOJI degraded-path check.
    """

    feeling: str | None
    emoji: str
    pinned: bool


# Per-constant resolution specs. REACTION_ERROR is pinned to 🤔 (never hostile,
# never a semantic lottery); the positive constants stay semantically resolved.
_TERMINAL_EMOJI_CONFIG: dict[str, _TerminalEmojiConfig] = {
    "REACTION_SUCCESS": _TerminalEmojiConfig(
        "acknowledged received silently noted", "\U0001f44c", False
    ),  # 👌 semantic
    "REACTION_COMPLETE": _TerminalEmojiConfig(
        "task completed successfully work done", "\U0001f44f", False
    ),  # 👏 semantic
    "REACTION_ERROR": _TerminalEmojiConfig(None, "\U0001f914", True),  # 🤔 pinned
}


# Heartbeat staleness threshold — used by both worker and bridge health checks.
# The worker writes its heartbeat every AGENT_SESSION_HEALTH_CHECK_INTERVAL seconds (300s).
# A threshold of 360s gives one full check-cycle grace period before declaring unhealthy.
HEARTBEAT_STALENESS_THRESHOLD_S: int = 360

# Worker-down threshold for CLI pre-flight checks (valor-session create/status,
# agent_session_scheduler status). The worker writes its heartbeat every 300s;
# 600s = 2x the write cadence, tolerating one fully missed write cycle before
# declaring the worker down. The dashboard keeps the tighter 360s
# HEARTBEAT_STALENESS_THRESHOLD_S above for its "ok" band.
WORKER_DOWN_THRESHOLD_S: int = 600


def _resolve_terminal_emoji(name: str, config: _TerminalEmojiConfig) -> object:
    """Resolve a terminal reaction emoji, caching the result.

    Pinned constants (config.pinned) resolve directly to their fixed emoji,
    caching an EmojiResult without any semantic draw. Semantic constants call
    find_best_emoji(config.feeling) and fall back to config.emoji when it raises
    or returns the bare DEFAULT_EMOJI (OPENROUTER_API_KEY absent, embeddings
    unavailable), keeping the constants distinct in degraded environments.

    Args:
        name: Constant name (e.g. "REACTION_SUCCESS") — used as cache key.
        config: Resolution spec for this constant.

    Returns:
        EmojiResult instance (always valid, never raises).
    """
    from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult

    # Pinned path: return the fixed emoji directly. This MUST short-circuit before
    # the DEFAULT_EMOJI degraded-path check below — REACTION_ERROR's pinned 🤔 *is*
    # DEFAULT_EMOJI, so routing it through that check would wrongly treat the pin as
    # a resolution failure. No find_best_emoji() call happens on this path.
    if config.pinned:
        pinned = EmojiResult(emoji=config.emoji)
        _TERMINAL_EMOJI_CACHE[name] = pinned
        return pinned

    try:
        from tools.emoji_embedding import find_best_emoji

        result = find_best_emoji(config.feeling)
        # If find_best_emoji returned the DEFAULT_EMOJI, the API was unavailable.
        # Use our own fallback so the constants remain distinct.
        if result.emoji == DEFAULT_EMOJI and not result.is_custom:
            raise ValueError(f"semantic resolution returned default emoji for {name!r}")
        _TERMINAL_EMOJI_CACHE[name] = result
        return result
    except Exception as exc:
        logger.debug(
            "find_best_emoji failed for %s (%r): %s — using fallback",
            name,
            config.feeling,
            exc,
        )
        fallback = EmojiResult(emoji=config.emoji)
        _TERMINAL_EMOJI_CACHE[name] = fallback
        return fallback


def __getattr__(name: str) -> object:
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
        return _resolve_terminal_emoji(name, _TERMINAL_EMOJI_CONFIG[name])

    raise AttributeError(f"module 'agent.constants' has no attribute {name!r}")
