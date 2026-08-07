"""Steering Queue - Mid-execution course correction via Redis lists.

Allows the supervisor to inject messages into a running agent session
by pushing to a per-session Redis list. The watchdog hook (PostToolUse)
checks this queue on every tool call and injects messages via the SDK.

Queue design:
- Key:    steering:{session_id}  (legacy, session-scoped — writers target this)
- Key:    steering:room:{room_id}  (Room-scoped, ``room_id`` =
          ``{project_key}|{addressee}``; see models/room.py)
- Type:   Redis List (RPUSH to add, LPOP to consume)
- Values: JSON strings with text, sender, timestamp, is_abort, target_agent (optional)
- TTL:    None (persist until consumed or session completion)

Room dual-read (durability plan Task 11 phase 1, issue #2494): every drain
consumer reads the legacy session key FIRST, then the Room key when a
``room_id`` is provided. Writers still push to the legacy key only — the
writer flip to the Room key is a SEPARATE release, deployable only after the
dual-read consumer has reached every machine (Race 1 two-phase deploy). The
Room inbox makes a steer addressed to a dead session impossible by
construction once writers flip: the Room is immortal, so the message waits
for whichever session next drains the Room.
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
    """Redis key for a session's steering queue (legacy leg)."""
    return f"steering:{session_id}"


def _room_queue_key(room_id: str) -> str:
    """Redis key for a Room's steering queue (dual-read leg).

    ``room_id`` is the ``{project_key}|{addressee}`` composite from
    ``models.room.room_id_for_session``. No writer targets this key yet —
    the writer flip is a separate release (Race 1).
    """
    return f"steering:room:{room_id}"


def _drain_list(key: str) -> list[dict]:
    """Destructively drain one steering list via sequential LPOPs (FIFO)."""
    r = _get_redis()
    messages: list[dict] = []
    while True:
        raw = r.lpop(key)
        if raw is None:
            break
        try:
            messages.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning(f"[steering] Invalid JSON in queue {key}: {raw!r}")
    return messages


def _peek_list(key: str) -> list[dict]:
    """Non-destructive FIFO read of one steering list."""
    r = _get_redis()
    messages: list[dict] = []
    for raw in r.lrange(key, 0, -1):
        try:
            messages.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning(f"[steering] Invalid JSON in queue {key}: {raw!r}")
    return messages


def push_steering_message(
    session_id: str,
    text: str,
    sender: str,
    is_abort: bool = False,
    target_agent: str | None = None,
    front: bool = False,
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
        front: When True, prepend to the queue (LPUSH) so this message is
            the next one drained, ahead of anything already queued. Used
            for urgent advisories (e.g. a tool-timeout recovery hint) that
            should be consumed before older, lower-priority messages.
            When False (default), append to the queue (RPUSH) as normal.
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
    if front:
        r.lpush(key, payload)
    else:
        r.rpush(key, payload)
    target_suffix = f" target={target_agent}" if target_agent else ""
    front_suffix = " (front)" if front else ""
    logger.info(
        f"[steering] Pushed {'ABORT' if is_abort else 'message'} to {key}: "
        f"{text[:80]!r} (from {sender}){target_suffix}{front_suffix}"
    )


def pop_all_steering_messages(session_id: str, room_id: str | None = None) -> list[dict]:
    """Pop ALL pending steering messages (FIFO order), legacy leg first.

    Drains the queue via sequential LPOPs. Not strictly atomic, but safe
    because only one consumer exists per session (the watchdog hook).
    Returns empty list if no messages.

    Dual-read (Task 11 phase 1): when ``room_id`` is provided, the Room key
    is drained AFTER the legacy session key. During the transition writers
    only push to the legacy key, so old workers keep working; once every
    consumer runs this code, the writer flip to the Room key is safe.

    Each returned dict contains:
        - text (str): The message body
        - sender (str): Who sent the message
        - timestamp (float): Unix timestamp when pushed
        - is_abort (bool): Whether this is an abort signal
        - target_agent (str | None): Optional agent name this message
          is addressed to. Present only when the pusher specified one.
    """
    messages = _drain_list(_queue_key(session_id))
    if room_id:
        messages.extend(_drain_list(_room_queue_key(room_id)))
    return messages


def pop_steering_message(session_id: str, room_id: str | None = None) -> dict | None:
    """Pop the next steering message (FIFO, legacy leg first). None if empty."""
    r = _get_redis()
    keys = [_queue_key(session_id)]
    if room_id:
        keys.append(_room_queue_key(room_id))

    for key in keys:
        raw = r.lpop(key)
        if raw is None:
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[steering] Invalid JSON in queue {key}: {raw!r}")
            return None
    return None


def clear_steering_queue(session_id: str, room_id: str | None = None) -> int:
    """Clear all pending steering messages (both legs). Returns count cleared."""
    r = _get_redis()
    keys = [_queue_key(session_id)]
    if room_id:
        keys.append(_room_queue_key(room_id))

    cleared = 0
    for key in keys:
        count = r.llen(key)
        if count > 0:
            r.delete(key)
            logger.info(f"[steering] Cleared {count} message(s) from {key}")
            cleared += count
    return cleared


def has_steering_messages(session_id: str, room_id: str | None = None) -> bool:
    """Check if there are pending steering messages (either leg)."""
    r = _get_redis()
    if r.llen(_queue_key(session_id)) > 0:
        return True
    return bool(room_id) and r.llen(_room_queue_key(room_id)) > 0


def peek_steering_messages(session_id: str, room_id: str | None = None) -> list[dict]:
    """Peek at all pending steering messages without consuming them.

    Uses LRANGE (non-destructive) so callers -- status dumps, CLI inspection --
    can inspect the queue without racing the turn-boundary consumer. Returns
    messages in the same FIFO order pop_all_steering_messages would return
    them (legacy leg first, then the Room leg when ``room_id`` is provided).
    Returns an empty list if the queue is empty or unreadable.
    """
    messages = _peek_list(_queue_key(session_id))
    if room_id:
        messages.extend(_peek_list(_room_queue_key(room_id)))
    return messages


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
