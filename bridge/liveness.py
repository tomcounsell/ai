"""Bridge-level liveness signals for the stale-update-stream detector.

Writes two positive liveness keys to Redis:

- ``bridge:last_update_received``: stamped by the NewMessage handler on every
  incoming Telethon update, before dedup.  A gap here means the update loop
  has silently stalled (bridge is alive but Telethon stopped firing events).

- ``bridge:last_probe_ok``: stamped by the reconciler each time
  ``get_dialogs()`` succeeds.  A gap here means the TCP/API layer is broken
  even though the process is alive.

Both keys are freeform (not Popoto-managed), so raw Redis get/set is correct.
Both writers are best-effort: any exception logs a WARNING and never raises,
matching the same safety contract as ``bridge.dedup.record_last_event``.
"""

import logging
import os
import time

import redis

logger = logging.getLogger(__name__)

_UPDATE_KEY = "bridge:last_update_received"
_PROBE_KEY = "bridge:last_probe_ok"
# Generous TTL — watchdog reads these frequently; keys must survive restarts.
_TTL_SECONDS = 604800  # 7 days


def _get_redis() -> redis.Redis:
    """Return a decode_responses Redis client."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def record_update_received(redis_client=None) -> None:
    """Stamp ``bridge:last_update_received`` with the current unix timestamp.

    Call this from the NewMessage handler **before** the dedup early-return so
    the key reflects every received Telethon event, not just novel ones.

    Best-effort: logs a WARNING and never raises on any failure.
    """
    try:
        r = redis_client if redis_client is not None else _get_redis()
        r.set(_UPDATE_KEY, str(time.time()), ex=_TTL_SECONDS)
    except Exception as e:
        logger.warning("liveness: record_update_received failed: %s", e)


def get_last_update_received(redis_client=None) -> float | None:
    """Return the unix timestamp of the last received update, or None.

    Returns None when the key is missing (cold start) or the value is corrupt.
    Never raises.
    """
    try:
        r = redis_client if redis_client is not None else _get_redis()
        raw = r.get(_UPDATE_KEY)
        if raw is None:
            return None
        return float(raw)
    except Exception as e:
        logger.warning("liveness: get_last_update_received failed: %s", e)
        return None


def record_probe_ok(redis_client=None) -> None:
    """Stamp ``bridge:last_probe_ok`` with the current unix timestamp.

    Call this from the reconciler after a successful ``get_dialogs()`` call.

    Best-effort: logs a WARNING and never raises on any failure.
    """
    try:
        r = redis_client if redis_client is not None else _get_redis()
        r.set(_PROBE_KEY, str(time.time()), ex=_TTL_SECONDS)
    except Exception as e:
        logger.warning("liveness: record_probe_ok failed: %s", e)


def get_last_probe_ok(redis_client=None) -> float | None:
    """Return the unix timestamp of the last successful probe, or None.

    Returns None when the key is missing or the value is corrupt.  Never raises.
    """
    try:
        r = redis_client if redis_client is not None else _get_redis()
        raw = r.get(_PROBE_KEY)
        if raw is None:
            return None
        return float(raw)
    except Exception as e:
        logger.warning("liveness: get_last_probe_ok failed: %s", e)
        return None
