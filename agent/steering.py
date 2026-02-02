"""Steering Queue - Mid-execution course correction via Redis lists.

Allows the supervisor to inject messages into a running agent session
by pushing to a per-session Redis list. The watchdog hook (PostToolUse)
checks this queue on every tool call and injects messages via the SDK.

Queue design:
- Key:    steering:{session_id}
- Type:   Redis List (RPUSH to add, LPOP to consume)
- Values: JSON strings with text, sender, timestamp, is_abort
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
) -> None:
    """Push a message to a session's steering queue.

    Args:
        session_id: The active session to steer
        text: Message text from supervisor
        sender: Name of the sender
        is_abort: If True, signals the session should abort
    """
    r = _get_redis()
    key = _queue_key(session_id)

    # Auto-detect abort keywords
    if not is_abort and text.strip().lower() in ABORT_KEYWORDS:
        is_abort = True

    payload = json.dumps(
        {
            "text": text,
            "sender": sender,
            "timestamp": time.time(),
            "is_abort": is_abort,
        }
    )
    r.rpush(key, payload)
    logger.info(
        f"[steering] Pushed {'ABORT' if is_abort else 'message'} to {key}: "
        f"{text[:80]!r} (from {sender})"
    )


def pop_all_steering_messages(session_id: str) -> list[dict]:
    """Pop ALL pending steering messages (FIFO order).

    Drains the queue via sequential LPOPs. Not strictly atomic, but safe
    because only one consumer exists per session (the watchdog hook).
    Returns empty list if no messages.
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
