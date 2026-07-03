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


def peek_steering_messages(session_id: str) -> list[dict]:
    """Peek at all pending steering messages without consuming them.

    Uses LRANGE (non-destructive) so callers -- status dumps, CLI inspection --
    can inspect the queue without racing the turn-boundary consumer. Returns
    messages in the same FIFO order pop_all_steering_messages would return
    them. Returns an empty list if the queue is empty or unreadable.
    """
    r = _get_redis()
    key = _queue_key(session_id)
    raw_messages = r.lrange(key, 0, -1)
    messages = []
    for raw in raw_messages:
        try:
            messages.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning(f"[steering] Invalid JSON in queue {key}: {raw!r}")
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


# ── Wedge-nudge steering channel ───────────────────────────────────────────
# A *separate* recovery channel used to drain a single `continue` nudge into
# a granite PTY session that is parked mid-turn (inside `_await_turn_end`)
# with a frozen normalized frame — see docs/plans/
# granite-mid-run-steering-drain-continue-nudge.md (issue #1879).
#
# CHANNEL-ISOLATION CONTRACT (load-bearing, not incidental):
#   This channel is DISTINCT from ordinary operator steering
#   (`steering:{session_id}`, drained by `pop_all_steering_messages` at the
#   top of a completed turn). It must NEVER be conflated with, read from, or
#   drained together with the ordinary queue:
#     - `pop_all_steering_messages` / `pop_steering_message` never touch
#       `steering:nudge:{session_id}`.
#     - `pop_wedge_nudges` never touches `steering:{session_id}`.
#   Reusing the ordinary queue for this purpose would let a mid-turn wedge
#   drain strip pending operator steering the top-of-turn path owns — see
#   "Rabbit Holes" in the plan above. Three independent Redis keys are used:
#     1. `steering:nudge:{session_id}`       — the signal channel (this pop
#        drains and clears it; mirrors `pop_all_steering_messages`).
#     2. `steering:nudge:latch:{session_id}` — a durable TTL latch that
#        SURVIVES the signal-channel drain. A single GETDEL flag would clear
#        on drain and re-open the one-per-window guarantee (a producer tick
#        running after the consumer drains would see the key absent and fire
#        a second nudge within the same turn-wait window). The latch is
#        therefore a distinct key, set via `SET NX EX` (atomic acquire) and
#        checked independently; only its own TTL expiry — never the
#        signal-channel drain — clears it.
#     3. `steering:{session_id}`             — ordinary operator steering
#        (pre-existing, above in this module); untouched by everything below.

_WEDGE_NUDGE_LATCH_TTL_DEFAULT = 600  # seconds; mirrors the default
# `_hook_turn_end_wait_s` turn-wait budget (config/settings.py) so at most
# one nudge fires per turn-wait window. Callers (the session-health producer)
# may pass an explicit ttl_seconds to match the resolved runtime value.


def _wedge_nudge_key(session_id: str) -> str:
    """Redis key for a session's wedge-nudge signal channel.

    Distinct from `_queue_key` (`steering:{session_id}`) — see the
    channel-isolation contract above.
    """
    return f"steering:nudge:{session_id}"


def _wedge_nudge_latch_key(session_id: str) -> str:
    """Redis key for a session's wedge-nudge TTL latch.

    Distinct from both `_queue_key` and `_wedge_nudge_key` — see the
    channel-isolation contract above. This key survives signal-channel
    drains; it is cleared only by its own TTL expiry.
    """
    return f"steering:nudge:latch:{session_id}"


def push_wedge_nudge(session_id: str, sender: str = "wedge-nudge") -> None:
    """Push a single `continue` wedge-nudge onto the session's nudge channel.

    CHANNEL ISOLATION: this writes to `steering:nudge:{session_id}` — a key
    entirely separate from the ordinary operator steering queue
    (`steering:{session_id}`). It must never be read by, or conflated with,
    `pop_all_steering_messages` / `pop_steering_message`. Only
    `pop_wedge_nudges` (below) drains this channel.

    Fail-silent: any Redis error is caught, logged at `warning`, and
    swallowed. This function must never raise into the caller (the
    session-health producer loop).

    Args:
        session_id: The wedged session to nudge.
        sender: Label recorded on the pushed message for observability.
    """
    try:
        r = _get_redis()
        key = _wedge_nudge_key(session_id)
        msg_dict: dict[str, str | float | bool] = {
            "text": "continue",
            "sender": sender,
            "timestamp": time.time(),
            "is_abort": False,
        }
        r.rpush(key, json.dumps(msg_dict))
        logger.info(f"[steering] Pushed wedge-nudge to {key} (from {sender})")
    except Exception:
        logger.warning(
            "[steering] Failed to push wedge-nudge for session %s", session_id, exc_info=True
        )


def pop_wedge_nudges(session_id: str) -> list[dict]:
    """Drain ALL pending wedge-nudge messages from the session's nudge channel.

    CHANNEL ISOLATION: this drains `steering:nudge:{session_id}` only. It
    never reads or clears the ordinary steering queue
    (`steering:{session_id}`), and never touches the latch key
    (`steering:nudge:latch:{session_id}`) — draining the signal channel must
    NOT clear the latch; the latch survives independently until its own TTL
    expires.

    Mirrors `pop_all_steering_messages`'s sequential-LPOP drain pattern, plus
    fail-silent Redis-error handling: on any Redis error this returns `[]`
    and never raises. Returns `[]` if the channel is empty.

    Returns:
        A list of message dicts (each with `text`, `sender`, `timestamp`,
        `is_abort`), in FIFO order. Empty list if nothing pending or on
        error.
    """
    try:
        r = _get_redis()
        key = _wedge_nudge_key(session_id)
        messages = []
        while True:
            raw = r.lpop(key)
            if raw is None:
                break
            try:
                messages.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.warning(f"[steering] Invalid JSON in wedge-nudge channel {key}: {raw!r}")
        return messages
    except Exception:
        logger.warning(
            "[steering] Failed to pop wedge-nudges for session %s", session_id, exc_info=True
        )
        return []


def set_wedge_nudge_latch(
    session_id: str, ttl_seconds: int = _WEDGE_NUDGE_LATCH_TTL_DEFAULT
) -> bool:
    """Atomically acquire the wedge-nudge latch for a turn-wait window.

    Uses `SET NX EX` — a single atomic Redis command — so concurrent
    producer ticks cannot both acquire the latch (test-and-set is not
    split into a separate read + write). This is deliberately a distinct
    key (`steering:nudge:latch:{session_id}`) from the signal channel
    (`steering:nudge:{session_id}`): the latch must SURVIVE
    `pop_wedge_nudges` draining the signal channel, so at most one nudge
    fires per turn-wait window regardless of drain timing. See the
    channel-isolation contract above for why a single GETDEL flag is wrong.

    Fail-silent: a Redis error is caught, logged at `warning`, and this
    returns False (fail-safe — a caller that treats False as "already
    nudged / do not nudge" avoids spamming the PTY on a flaky Redis).

    Args:
        session_id: The session to latch.
        ttl_seconds: How long the latch holds before it can be re-acquired.
            Should match the turn-wait budget (`_hook_turn_end_wait_s`) so
            the latch and the turn-wait window coincide.

    Returns:
        True if this call newly acquired the latch (caller should proceed
        to push the nudge). False if the latch was already held by an
        earlier acquire in this window, or on Redis failure.
    """
    try:
        r = _get_redis()
        key = _wedge_nudge_latch_key(session_id)
        acquired = r.set(key, "1", nx=True, ex=ttl_seconds)
        return bool(acquired)
    except Exception:
        logger.warning(
            "[steering] Failed to set wedge-nudge latch for session %s", session_id, exc_info=True
        )
        return False


def has_wedge_nudge_latch(session_id: str) -> bool:
    """Check whether the wedge-nudge latch is currently held for a session.

    Non-destructive read of `steering:nudge:latch:{session_id}` — does not
    consume or affect the signal channel (`steering:nudge:{session_id}`) or
    the ordinary steering queue (`steering:{session_id}`).

    Fail-silent: a Redis error is caught, logged at `warning`, and this
    returns False.

    Args:
        session_id: The session to check.

    Returns:
        True if a latch is currently held (already nudged this window).
        False if no latch is held, or on Redis failure.
    """
    try:
        r = _get_redis()
        key = _wedge_nudge_latch_key(session_id)
        return bool(r.exists(key))
    except Exception:
        logger.warning(
            "[steering] Failed to check wedge-nudge latch for session %s",
            session_id,
            exc_info=True,
        )
        return False
