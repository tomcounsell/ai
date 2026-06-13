"""Steering Queue - Mid-execution course correction via Redis lists.

Allows the supervisor to inject messages into a running agent session
by pushing to a per-session Redis list. The watchdog hook (PostToolUse)
checks this queue on every tool call and injects messages via the SDK.

Queue design:
- Key:    steering:{session_id}
- Type:   Redis List (RPUSH to add, LPOP to consume)
- Values: JSON strings with text, sender, timestamp, is_abort, target_agent (optional)
- TTL:    None (persist until consumed or session completion)
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

ABORT_KEYWORDS = frozenset({"stop", "cancel", "abort", "nevermind"})


def _get_redis():
    """Get the popoto Redis connection."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _queue_key(session_id: str) -> str:
    """Redis key for a session's steering queue."""
    return f"steering:{session_id}"


def push_steering_message(
    session_id: str,
    text: str,
    sender: str,
    is_abort: bool = False,
    target_agent: str | None = None,
) -> None:
    """Push a message to a session's steering queue.

    Args:
        session_id: The active session to steer
        text: Message text from supervisor
        sender: Name of the sender
        is_abort: If True, signals the session should abort
        target_agent: Optional agent name this message is addressed to.
            When set, only the named agent should act on it. Consumers
            do not filter by this field yet -- it is stored for future use.
    """
    r = _get_redis()
    key = _queue_key(session_id)

    # Auto-detect abort keywords
    if not is_abort and text.strip().lower() in ABORT_KEYWORDS:
        is_abort = True

    msg_dict: dict[str, str | float | bool | None] = {
        "text": text,
        "sender": sender,
        "timestamp": time.time(),
        "is_abort": is_abort,
    }
    if target_agent is not None:
        msg_dict["target_agent"] = target_agent

    payload = json.dumps(msg_dict)
    r.rpush(key, payload)
    target_suffix = f" target={target_agent}" if target_agent else ""
    logger.info(
        f"[steering] Pushed {'ABORT' if is_abort else 'message'} to {key}: "
        f"{text[:80]!r} (from {sender}){target_suffix}"
    )


def pop_all_steering_messages(session_id: str) -> list[dict]:
    """Pop ALL pending steering messages (FIFO order).

    Drains the queue via sequential LPOPs. Not strictly atomic, but safe
    because only one consumer exists per session (the watchdog hook).
    Returns empty list if no messages.

    Each returned dict contains:
        - text (str): The message body
        - sender (str): Who sent the message
        - timestamp (float): Unix timestamp when pushed
        - is_abort (bool): Whether this is an abort signal
        - target_agent (str | None): Optional agent name this message
          is addressed to. Present only when the pusher specified one.
    """
    r = _get_redis()
    key = _queue_key(session_id)
    messages = []

    while True:
        raw = r.lpop(key)
        if raw is None:
            break
        try:
            msg = json.loads(raw)
            messages.append(msg)
        except json.JSONDecodeError:
            logger.warning(f"[steering] Invalid JSON in queue {key}: {raw!r}")

    return messages


def pop_steering_message(session_id: str) -> dict | None:
    """Pop the next steering message (FIFO). Returns None if empty."""
    r = _get_redis()
    key = _queue_key(session_id)

    raw = r.lpop(key)
    if raw is None:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[steering] Invalid JSON in queue {key}: {raw!r}")
        return None


def clear_steering_queue(session_id: str) -> int:
    """Clear all pending steering messages. Returns count cleared."""
    r = _get_redis()
    key = _queue_key(session_id)

    count = r.llen(key)
    if count > 0:
        r.delete(key)
        logger.info(f"[steering] Cleared {count} message(s) from {key}")
    return count


def has_steering_messages(session_id: str) -> bool:
    """Check if there are pending steering messages without consuming them."""
    r = _get_redis()
    key = _queue_key(session_id)
    return r.llen(key) > 0


def peek_steering_sender(session_id: str) -> str | None:
    """Peek at the sender of the most recent steering message without consuming it.

    Uses LINDEX -1 to read the tail (most recently pushed) message.
    Returns the sender string, or None if the queue is empty or unreadable.
    """
    r = _get_redis()
    key = _queue_key(session_id)
    raw = r.lindex(key, -1)
    if raw is None:
        return None
    try:
        msg = json.loads(raw)
        return msg.get("sender")
    except (json.JSONDecodeError, AttributeError):
        return None


# ── Self-draft attempt budget ─────────────────────────────────────────────────
# Redis counter that tracks how many consecutive times the drafter has injected
# a self-draft steering message for a session. Prevents infinite steering loops
# when the agent's self-draft also fails validation.
#
# SELF_DRAFT_MAX_ATTEMPTS: Cap on consecutive self-draft steering injections.
# When the drafter sets needs_self_draft=True (wire-format violation or empty
# promise), _inject_self_draft_steering in output_handler.py bumps this counter
# and injects a steering nudge asking the agent to rewrite. If the agent's
# rewrite also fails, the counter bumps again. At cap (>= SELF_DRAFT_MAX_ATTEMPTS)
# the handler falls through to the narration fallback instead of steering again.
# This is NOT an AgentSession field — it is a Redis key only, scoped per
# session_id and TTL-expiring after 1 hour so abandoned sessions don't leak.

SELF_DRAFT_MAX_ATTEMPTS = 2

_SELF_DRAFT_ATTEMPTS_TTL = 3600  # 1 hour — abandoned sessions don't leak


def _self_draft_attempts_key(session_id: str) -> str:
    """Redis key for the self-draft attempt counter."""
    return f"steering:attempts:{session_id}"


def bump_self_draft_attempts(session_id: str) -> int:
    """Atomically increment the self-draft attempt counter and return the new value.

    Uses Redis INCR (atomic) so concurrent drafter calls for the same session
    cannot double-count. Sets a 1-hour TTL on first bump (count == 1) so
    counters for abandoned sessions expire automatically without a cleanup step.
    The counter is stored at ``steering:attempts:{session_id}`` — it is NOT
    a field on AgentSession.

    Args:
        session_id: The session whose counter to increment.

    Returns:
        Post-increment count (1 on first bump, 2 on second, …). Caller
        compares against SELF_DRAFT_MAX_ATTEMPTS to decide whether to steer
        or fall through to the narration fallback.
    """
    r = _get_redis()
    key = _self_draft_attempts_key(session_id)
    count = r.incr(key)
    if count == 1:
        # First bump — set TTL so the key expires if the session is abandoned.
        r.expire(key, _SELF_DRAFT_ATTEMPTS_TTL)
    logger.debug("[steering] Self-draft attempts for %s: %d", session_id, count)
    return count


def reset_self_draft_attempts(session_id: str) -> None:
    """Reset the self-draft attempt counter for a session (Redis DELETE).

    Called on clean (non-self-draft) delivery immediately before the
    STEERING_DEFERRED early-return in the output handler, so a subsequent
    flagged message in the same session starts fresh from zero rather than
    inheriting a stale count from a previous violation.

    The counter is a Redis key (``steering:attempts:{session_id}``), not an
    AgentSession field. Deletion is idempotent — safe to call even if the
    key does not exist.

    Args:
        session_id: The session whose counter to reset.
    """
    r = _get_redis()
    key = _self_draft_attempts_key(session_id)
    r.delete(key)
    logger.debug("[steering] Reset self-draft attempts for %s", session_id)
