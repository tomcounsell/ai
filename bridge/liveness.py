"""Bridge-level liveness signals for the stale-update-stream detector (#1538).

Writes two **positive** liveness keys to Redis.  Positive signals record that
something good *happened* — they do not infer health from absence of bad events.
This avoids the "silence-equals-failure" anti-pattern rejected in issue #1172.

Keys:
- ``bridge:last_update_received``: stamped by the NewMessage handler on every
  incoming Telethon update, **before dedup**.  A gap here — while
  ``bridge:last_probe_ok`` is fresh — means the update loop has silently stalled
  (bridge alive, TCP up, but Telethon stopped firing events).

  **Important**: only the NewMessage handler writes this key.  The reconciler
  must NOT write it, even though it also "receives" data from Telegram.  If the
  reconciler stamped this key, a bridge whose update loop was wedged but whose
  reconciler was healthy would look fine — defeating the detector entirely.

- ``bridge:last_probe_ok``: stamped by the reconciler each time
  ``get_dialogs()`` succeeds.  A gap here means the TCP/API layer itself is
  broken, distinguishing a wedged update loop from a full disconnect.  The
  watchdog only fires a wedge restart when this probe is fresh — a stale probe
  means the bridge may simply be disconnected, and restarting mid-reconnect
  would be counterproductive.

Both keys are **freeform** (not Popoto-managed), so raw Redis ``get``/``set`` is
correct here.  All other Redis writes in this codebase that touch Popoto-managed
keys must go through the ORM.  See issue #1408 for the broader freeform-key
convention used by ``bridge.dedup.record_last_event`` and friends.

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
