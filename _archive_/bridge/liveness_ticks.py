"""Redis state for the session liveness tick counter (#2716).

Two processes touch this state and neither owns the other:

- ``monitoring/session_watchdog.py`` (bridge process) derives the tick from
  wall clock, publishes reactions, and forces a progress message at the
  ceiling.
- ``bridge/telegram_relay.py`` re-anchors the counter when a progress message
  is actually delivered, and records who owns a message's reaction slot.

Keeping the key names and TTLs in one module is what stops the two from
drifting. Every function here is synchronous and fail-quiet: the watchdog loop
and the relay's 100 ms poll loop must never crash on counter bookkeeping. The
relay calls these through ``asyncio.to_thread`` like every other Redis read on
that loop.

Semantics reminder, because it is easy to get wrong: the counter is pure
wall-clock duration. It asserts that the watchdog has eyes on the session and
nothing more. It is not a stall detector and infers nothing about the session's
internal state.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

# TTLs. The anchor and the last-published tick must comfortably outlive the
# full ceiling window (HEARTBEAT_MAX_TICKS * HEARTBEAT_TICK_INTERVAL_SECONDS ≈
# 100 minutes) without outliving their session — an anchor key that survives
# its session is a leak. 6 hours covers the ceiling window several times over
# and is well under the watchdog's 2-hour DURATION_THRESHOLD abandon path.
ANCHOR_TTL_SECONDS = 21600
TICK_TTL_SECONDS = 21600
# The forced-progress marker (Race 3) only needs to outlive the window between
# the steer and the resulting message. It is cleared explicitly on delivery;
# this TTL is the safety bound for a steer that is never honored.
CEILING_TTL_SECONDS = 21600
# Slot ownership is per (chat_id, message_id) and only matters while a message
# is still a live anchor. A day is generous and self-cleaning.
SLOT_OWNER_TTL_SECONDS = 86400


def _redis():
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def anchor_key(session_id: str) -> str:
    """Redis key holding the counter's start time and anchor message id."""
    return f"heartbeat:anchor:{session_id}"


def tick_key(session_id: str) -> str:
    """Redis key holding the last tick number published for a session."""
    return f"heartbeat:tick:{session_id}"


def ceiling_key(session_id: str) -> str:
    """Redis key marking that the forced-progress steer has been sent."""
    return f"heartbeat:ceiling:{session_id}"


def slot_owner_key(chat_id, message_id) -> str:
    """Redis key holding the precedence rank that owns a message's slot."""
    return f"heartbeat:slot_owner:{chat_id}:{message_id}"


def read_anchor(session_id: str) -> tuple[float, int] | None:
    """Return ``(started_at, anchor_message_id)`` for a session's counter.

    Returns ``None`` when no counter is running (never started, expired, or
    explicitly stopped).
    """
    try:
        raw = _redis().get(anchor_key(session_id))
        if not raw:
            return None
        data = json.loads(raw)
        return float(data["ts"]), int(data["message_id"])
    except Exception as e:  # noqa: BLE001 — bookkeeping must never raise
        logger.debug("[liveness] anchor read failed for %s: %s", session_id, e)
        return None


def start_anchor(session_id: str, started_at: float, message_id: int) -> bool:
    """Start a counter if one is not already running.

    Atomic ``SET NX`` so two watchdog scans cannot start two counters.

    Returns:
        True when this call created the anchor.
    """
    try:
        payload = json.dumps({"ts": float(started_at), "message_id": int(message_id)})
        return bool(_redis().set(anchor_key(session_id), payload, nx=True, ex=ANCHOR_TTL_SECONDS))
    except Exception as e:  # noqa: BLE001
        logger.debug("[liveness] anchor start failed for %s: %s", session_id, e)
        return False


def reanchor(session_id: str, message_id: int) -> None:
    """Re-anchor a session's counter to a newly delivered message.

    Called by the relay when the session publishes any message: that message
    becomes the new anchor, the tick resets to zero, and the forced-progress
    marker is released so a later ceiling can fire again. Re-anchoring on any
    PM message rather than only a ceiling-forced one is intended — the human
    has been answered either way.
    """
    try:
        r = _redis()
        payload = json.dumps({"ts": time.time(), "message_id": int(message_id)})
        r.set(anchor_key(session_id), payload, ex=ANCHOR_TTL_SECONDS)
        r.delete(tick_key(session_id))
        r.delete(ceiling_key(session_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("[liveness] re-anchor failed for %s: %s", session_id, e)


def stop_counter(session_id: str) -> None:
    """Stop a session's counter without re-anchoring.

    The DELIVERED_NO_ID case: the relay reached Telegram but got no message id
    back, so there is no message to anchor to. Clearing the forced-progress
    marker without re-anchoring is the correct disposition — the human was
    answered, the counter simply stops, and the next inbound message starts a
    fresh one.
    """
    try:
        r = _redis()
        r.delete(anchor_key(session_id))
        r.delete(tick_key(session_id))
        r.delete(ceiling_key(session_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("[liveness] counter stop failed for %s: %s", session_id, e)


def read_last_tick(session_id: str) -> int:
    """Return the last tick published for a session.

    Defaults to ``0``: slot 0 is the 👀 the ingestion path already placed on
    the originating message, so the counter's first published reaction is
    tick 1.
    """
    try:
        raw = _redis().get(tick_key(session_id))
        return int(raw) if raw is not None else 0
    except Exception as e:  # noqa: BLE001
        logger.debug("[liveness] tick read failed for %s: %s", session_id, e)
        return 0


def record_tick(session_id: str, tick: int) -> None:
    """Record a tick as published.

    The marker advances at ENQUEUE, not delivery. A time-derived tick is
    self-correcting: if tick 4 never lands, tick 5 fires one interval later
    from the same wall-clock derivation and overwrites the slot correctly, so a
    missed digit is cosmetic rather than a stuck state. This is materially
    different from the one-shot latch the deleted ⏳ path used, where a single
    failure meant no reaction for the whole stall period with nothing to retry
    it.
    """
    try:
        _redis().set(tick_key(session_id), str(int(tick)), ex=TICK_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001
        logger.debug("[liveness] tick record failed for %s: %s", session_id, e)


def claim_ceiling(session_id: str) -> bool:
    """Claim the right to send the forced-progress steer, exactly once.

    Atomic ``SET NX`` (Race 3): two scans can both observe a tick past the
    ceiling before the progress message exists, and only one may steer. The
    marker is released by :func:`reanchor` or :func:`stop_counter`.

    Returns:
        True when this caller won the claim and should send the steer.
    """
    try:
        return bool(_redis().set(ceiling_key(session_id), "1", nx=True, ex=CEILING_TTL_SECONDS))
    except Exception as e:  # noqa: BLE001
        logger.debug("[liveness] ceiling claim failed for %s: %s", session_id, e)
        return False


def read_slot_owner(chat_id, message_id) -> int | None:
    """Return the precedence rank currently owning a message's reaction slot."""
    try:
        raw = _redis().get(slot_owner_key(chat_id, message_id))
        return int(raw) if raw is not None else None
    except Exception as e:  # noqa: BLE001
        logger.debug("[liveness] slot owner read failed for %s/%s: %s", chat_id, message_id, e)
        return None


def record_slot_owner(chat_id, message_id, priority: int) -> None:
    """Record which rank owns a message's reaction slot, after a landed set."""
    try:
        _redis().set(
            slot_owner_key(chat_id, message_id), str(int(priority)), ex=SLOT_OWNER_TTL_SECONDS
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[liveness] slot owner write failed for %s/%s: %s", chat_id, message_id, e)
