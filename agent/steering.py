"""Steering Queue - Mid-execution course correction via Redis lists.

Allows the supervisor to inject messages into a running agent session
by pushing to a Redis list. The watchdog hook (PostToolUse) checks this
queue on every tool call, and the worker drains it at turn boundaries.

Queue design:
- Key:    steering:{session_id}  (legacy leg, session-scoped; mortal — dies
          with the session)
- Key:    steering:room:{room_id}  (Room leg, ``room_id`` =
          ``{project_key}|{addressee}``; see models/room.py — immortal)
- Type:   Redis List (RPUSH to add, LPOP to consume)
- Values: JSON strings with text, sender, timestamp, is_abort, target_agent (optional)
- TTL:    none on either key. The Room leg is age-bounded at drain time by
          ``TimeoutSettings.steering_room_max_age_s``; the legacy leg is not
          bounded, because it dies with its session.

Key selection is **selective** (issue #2642). A write targets the Room key
only when the caller supplies a truthy ``room_id`` AND the message is not an
abort:

- Conversation-level originating writes (a supervisor steer, a Telegram
  message routed into a live session, a resume steer, a PM steer) target the
  **Room** key, so the instruction outlives the session it was aimed at and is
  served to whichever session next drains that Room.
- **Aborts** target the legacy key, always. "You MUST stop immediately" is
  destructive and non-idempotent: delivered to the wrong session it kills
  innocent work. Stranding an abort with its dead session is the correct
  failure mode.
- **Session-scoped diagnostics** (the drafter self-draft nudge, the
  tool-timeout advisory, the watchdog loop-break steer) target the legacy
  key. Each describes the state of *this* session and is noise to a successor.
- A write with no resolvable Room falls back to the legacy key.
- A **requeue** of an already-drained message targets the leg it was drained
  from, read from the transient ``_leg`` stamp ``pop_all_steering_messages``
  applies.

The writer never looks a session up: ``room_id`` is derived by the caller via
``models.room.room_id_for_session`` (pure attribute reads). An internal
``AgentSession`` query by ``session_id`` costs ~2.4s — the field is unindexed,
so resolving it scans every session hash — and this module sits on the
inbound-Telegram fast path. Keep this module free of any model-layer import.

Every drain consumer dual-reads: the session key FIRST, then the Room key when
a ``room_id`` is provided.
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
    """Redis key for a Room's steering queue.

    ``room_id`` is the ``{project_key}|{addressee}`` composite from
    ``models.room.room_id_for_session``. Conversation-level writes target
    this key so an instruction outlives the session it was aimed at; aborts
    and session-scoped diagnostics stay on ``_queue_key`` (see the module
    docstring).
    """
    return f"steering:room:{room_id}"


def _steering_room_max_age_s() -> float:
    """Age bound (seconds) applied to the Room leg at read time.

    Read from settings rather than a module literal so a test can vary it
    and so ``TIMEOUTS__STEERING_ROOM_MAX_AGE_S`` is a live override.
    """
    from config.settings import settings

    return float(settings.timeouts.steering_room_max_age_s)


def _is_expired(msg: dict, max_age_seconds: float | None, now: float) -> float | None:
    """Age of ``msg`` when it is past ``max_age_seconds``, else ``None``.

    Fails **open**: a payload with a missing or non-numeric ``timestamp`` is
    never reported as expired. Dropping an entry we cannot date would delete
    a steer silently; keeping it costs one extra delivery at worst.
    """
    if max_age_seconds is None:
        return None
    stamp = msg.get("timestamp")
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
        return None
    age = now - float(stamp)
    return age if age > max_age_seconds else None


def _drain_list(key: str, max_age_seconds: float | None = None) -> list[dict]:
    """Destructively drain one steering list via sequential LPOPs (FIFO).

    Args:
        key: The Redis list to drain.
        max_age_seconds: When set, entries whose payload ``timestamp`` is
            older than this bound are **dropped** rather than returned, each
            logged at ``info`` with the key and the age so a missing steer
            stays diagnosable. Passed only for the Room key — the legacy key
            is never filtered, so every message that exists today behaves
            exactly as it does today. An entry with a missing or non-numeric
            ``timestamp`` is kept (fail open).
    """
    r = _get_redis()
    now = time.time()
    messages: list[dict] = []
    while True:
        raw = r.lpop(key)
        if raw is None:
            break
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[steering] Invalid JSON in queue {key}: {raw!r}")
            continue
        age = _is_expired(msg, max_age_seconds, now)
        if age is not None:
            logger.info(
                "[steering] Dropped stale message from %s (age=%.1fs > %.1fs): %r",
                key,
                age,
                max_age_seconds,
                str(msg.get("text", ""))[:80],
            )
            continue
        messages.append(msg)
    return messages


def _peek_list(key: str, max_age_seconds: float | None = None) -> list[dict]:
    """Non-destructive FIFO read of one steering list.

    Args:
        key: The Redis list to read.
        max_age_seconds: When set, entries older than this bound are
            **skipped** from the returned list. Nothing is deleted — the peek
            is non-destructive by contract and the next drain is what removes
            them. Passed only for the Room key, so the peek and the drain
            agree on what is still pending and ``valor-session status`` never
            advertises a steer the next drain will discard.
    """
    r = _get_redis()
    now = time.time()
    messages: list[dict] = []
    for raw in r.lrange(key, 0, -1):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[steering] Invalid JSON in queue {key}: {raw!r}")
            continue
        if _is_expired(msg, max_age_seconds, now) is not None:
            continue
        messages.append(msg)
    return messages


def push_steering_message(
    session_id: str,
    text: str,
    sender: str,
    is_abort: bool = False,
    target_agent: str | None = None,
    front: bool = False,
    room_id: str | None = None,
    timestamp: float | None = None,
) -> None:
    """Push a message to a steering queue — the Room leg, or the legacy leg.

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
            ``front`` orders **within the legacy leg**: consumers drain the
            legacy leg before the Room leg, so a ``front=True`` push onto a
            Room key would still sit behind every legacy-leg message. No new
            ``front=True`` push may target a Room without first resolving
            that cross-leg ordering question.
        room_id: The ``{project_key}|{addressee}`` composite the caller
            derived via ``models.room.room_id_for_session``. When truthy and
            the message is not an abort, the write targets the Room key so it
            outlives the session it was aimed at. **No ``room_id`` → the
            legacy key**, and **an abort → the legacy key regardless**,
            whether ``is_abort`` was passed explicitly or set by the
            ``ABORT_KEYWORDS`` auto-detect. The caller derives it because
            this writer deliberately never looks a session up: ``session_id``
            is unindexed, so a lookup here costs ~2.4s on the
            inbound-Telegram fast path.
        timestamp: Origination stamp to carry forward. A requeue of an
            already-drained message passes the entry's own ``timestamp`` so
            the Room leg's age bound measures time since origination rather
            than time since the last re-push. An originating caller passes
            nothing and gets ``time.time()``.
    """
    r = _get_redis()

    # Auto-detect abort keywords
    if not is_abort and text.strip().lower() in ABORT_KEYWORDS:
        is_abort = True

    # Key selection MUST sit below the auto-detect: reading `is_abort` above it
    # reads a stale value, and every keyword-detected abort would land on the
    # shared Room key where it could kill a session that was never targeted.
    key = _room_queue_key(room_id) if (room_id and not is_abort) else _queue_key(session_id)

    msg_dict: dict[str, str | float | bool | None] = {
        "text": text,
        "sender": sender,
        "timestamp": time.time() if timestamp is None else timestamp,
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

    Drains the queue via sequential LPOPs. Not atomic. The legacy leg has a
    single consumer per session; the Room leg is drained concurrently by every
    session serving that Room, so a drained entry may be served to a sibling
    session, and a consumer that fails between draining and re-pushing loses
    what it took.

    Dual-read: when ``room_id`` is provided, the Room key is drained AFTER the
    legacy session key. The Room drain applies the age bound
    ``TimeoutSettings.steering_room_max_age_s`` — an undrained Room-key entry
    is otherwise immortal, since a Room has no completion event. The legacy
    key is never filtered.

    Each returned dict contains:
        - text (str): The message body
        - sender (str): Who sent the message
        - timestamp (float): Unix timestamp of origination (a requeue carries
          the original stamp forward rather than restarting the clock)
        - is_abort (bool): Whether this is an abort signal
        - target_agent (str | None): Optional agent name this message
          is addressed to. Present only when the pusher specified one.
        - _leg (str): ``"legacy"`` or ``"room"`` — the leg this entry was
          drained from. **Transient and reader-set**: it is stamped here, is
          never a ``push_steering_message`` parameter, and never reaches
          Redis. A requeue writer reads it to put the message back on the leg
          it came from; absent ``_leg`` means legacy, the fail-safe default.
    """
    messages = _drain_list(_queue_key(session_id))
    for msg in messages:
        msg["_leg"] = "legacy"
    if room_id:
        room_messages = _drain_list(
            _room_queue_key(room_id), max_age_seconds=_steering_room_max_age_s()
        )
        for msg in room_messages:
            msg["_leg"] = "room"
        messages.extend(room_messages)
    return messages


def pop_steering_message(session_id: str, room_id: str | None = None) -> dict | None:
    """Pop the next steering message (FIFO, legacy leg first). None if empty.

    Deliberately unbounded: it has no production callers, so it carries
    neither the Room-leg age bound nor the ``_leg`` stamp. A future caller
    that wants either should route through ``pop_all_steering_messages``.
    """
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
    """Clear all pending steering messages (both legs). Returns count cleared.

    Cross-session blast radius: the Room leg is SHARED by every session
    serving that Room, so passing ``room_id`` deletes queued steers addressed
    to sibling sessions, not just ``session_id``'s. Pass ``room_id`` only when
    the intent is to clear the whole conversation's pending steers (e.g. an
    operator wiping a Room); for a single session's cleanup, omit it and only
    the legacy ``steering:{session_id}`` list is touched.
    """
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
    """Check if there are pending steering messages (either leg).

    Deliberately unbounded: it has no production callers, so it keeps its O(1)
    list-length fast path rather than reading and dating every entry. A future
    caller that needs the Room-leg age bound should route through
    ``peek_steering_messages``.
    """
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

    The Room leg is filtered by the same age bound the drain applies, so
    ``valor-session status`` — which reads through this function — never
    advertises a steer the next drain will silently discard. The skip is
    non-destructive; the drain is what removes stale entries. The legacy leg
    is never filtered.
    """
    messages = _peek_list(_queue_key(session_id))
    if room_id:
        messages.extend(
            _peek_list(_room_queue_key(room_id), max_age_seconds=_steering_room_max_age_s())
        )
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
