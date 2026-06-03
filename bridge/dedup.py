"""Per-chat deduplication AND per-chat last-processed cursor for the bridge.

This module has two distinct responsibilities, both Redis-backed:

1. **Dedup set** (``DedupRecord``): tracks the ~50 most recent processed message
   IDs per chat for membership checks (``is_duplicate_message`` /
   ``record_message_processed``). TTL of 2 hours, managed by the model's Meta.ttl.
2. **Last-processed cursor** (``LastProcessedRecord``): a monotonic per-chat
   cursor of the latest *dispatched* message (``record_last_processed`` /
   ``get_last_processed``). Used by catchup to compute a smarter per-chat
   lookback that closes the dead zone between ``data/last_connected`` and the
   last message actually received from a group. See issue #1408.

Both responsibilities share the safety contract that recording never raises:
a Redis outage logs a WARNING and falls back to today's behavior rather than
crashing the live handler, reconciler, or catchup scan.
"""

import logging
from datetime import UTC, datetime

from models.dedup import DedupRecord
from models.last_processed import LastProcessedRecord

logger = logging.getLogger(__name__)

# Max message IDs to track per chat (exposed for backward compatibility)
MAX_IDS_PER_CHAT = DedupRecord._MAX_IDS


async def is_duplicate_message(chat_id, message_id: int) -> bool:
    """Check if this message was already processed."""
    try:
        record = DedupRecord.get_or_create(str(chat_id))
        return record.has_message(message_id)
    except Exception as e:
        logger.debug(f"Dedup check failed (allowing through): {e}")
        return False


async def record_message_processed(chat_id, message_id: int) -> None:
    """Record that we processed this message.

    Failures are logged at WARNING (not debug): a silent dedup outage causes
    the reconciler to re-dispatch every message for the duration of the
    failure, which is exactly the class of bug this function exists to
    prevent. Exceptions are NOT re-raised -- dedup recording must never
    break the caller's control flow.
    """
    try:
        record = DedupRecord.get_or_create(str(chat_id))
        record.add_message(message_id)
    except Exception as e:
        logger.warning(
            "dedup record failed for chat=%s msg=%s: %s",
            chat_id,
            message_id,
            e,
        )


async def record_last_processed(chat_id, message_id: int, message_ts) -> None:
    """Advance the per-chat last-processed cursor (issue #1408).

    ``message_ts`` may be a tz-aware datetime, a unix timestamp, or ``None``
    (defensive: Telethon edge cases). ``None`` coerces to ``datetime.now(UTC)``.

    The cursor advances monotonically — an older ``message_id`` is a no-op.
    Failures log a WARNING and never raise (same safety contract as
    ``record_message_processed``); catchup falls back to the global cutoff.
    """
    try:
        from bridge.utc import to_unix_ts

        unix_ts = to_unix_ts(message_ts)
        if unix_ts is None:
            unix_ts = datetime.now(UTC).timestamp()

        record = LastProcessedRecord.get_or_create(str(chat_id))
        record.advance(int(message_id), int(unix_ts))
    except Exception as e:
        logger.warning(
            "last-processed cursor write failed for chat=%s msg=%s: %s",
            chat_id,
            message_id,
            e,
        )


async def get_last_processed(chat_id) -> tuple[int, datetime] | None:
    """Return ``(last_message_id, last_message_dt_utc)`` for a chat, or ``None``.

    Returns ``None`` when no record exists OR on any failure (callers fall back
    to the global ``last_connected`` cutoff). Never raises.
    """
    try:
        existing = LastProcessedRecord.query.filter(chat_id=str(chat_id))
        if not existing:
            return None
        record = existing[0]
        if not record.last_message_id:
            return None
        dt = datetime.fromtimestamp(int(record.last_message_ts), tz=UTC)
        return (int(record.last_message_id), dt)
    except Exception as e:
        logger.warning("last-processed cursor read failed for chat=%s: %s", chat_id, e)
        return None


# --- Per-chat last-event observability key (issue #1408) ---------------------
#
# `bridge:last_event:{chat_id}` records the unix timestamp of the most recent
# message the bridge RECEIVED for a chat (regardless of routing/dispatch). The
# silent-stream watcher compares this against `last_connected` to detect chats
# that have gone silent while the bridge is healthy. This key is freeform (not
# Popoto-managed), so raw redis get/set is acceptable here.

_LAST_EVENT_KEY_PREFIX = "bridge:last_event:"
# Match the cursor TTL so stale observability keys auto-expire.
_LAST_EVENT_TTL_SECONDS = 2592000  # 30 days


def _get_redis():
    """Return a decode_responses Redis client for the freeform observability key."""
    import os

    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


async def record_last_event(chat_id, event_ts=None) -> None:
    """Record the timestamp of the most recent received event for a chat.

    ``event_ts`` may be a datetime, unix timestamp, or None (coerced to now()).
    Best-effort: failures log a WARNING and never raise.
    """
    try:
        from bridge.utc import to_unix_ts

        unix_ts = to_unix_ts(event_ts)
        if unix_ts is None:
            unix_ts = datetime.now(UTC).timestamp()
        r = _get_redis()
        r.set(
            f"{_LAST_EVENT_KEY_PREFIX}{chat_id}",
            str(int(unix_ts)),
            ex=_LAST_EVENT_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("last-event write failed for chat=%s: %s", chat_id, e)


async def get_last_event_ts(chat_id) -> float | None:
    """Return the unix timestamp of the last received event for a chat, or None.

    Returns None when no key exists or on any failure. Never raises.
    """
    try:
        r = _get_redis()
        raw = r.get(f"{_LAST_EVENT_KEY_PREFIX}{chat_id}")
        if raw is None:
            return None
        return float(raw)
    except Exception as e:
        logger.warning("last-event read failed for chat=%s: %s", chat_id, e)
        return None
