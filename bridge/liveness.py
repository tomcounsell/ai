"""Bridge-level liveness signals for the stale-update-stream detector (#1712).

Writes three liveness keys to Redis.  Two are **positive health** signals that
record that something good *happened*, and one is **positive failure**
evidence.  None of them let the watchdog infer failure from silence — the
anti-pattern rejected in issue #1172, and the one the wedge detector had
re-inherited before #2475.

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

- ``bridge:last_missed_recovery``: stamped by the reconciler when a scan
  recovers at least one message that the live update path never delivered.
  This is the only signal here that is *positive evidence of a wedge* rather
  than evidence of health, and it is the one the watchdog needs: silence on
  ``bridge:last_update_received`` cannot distinguish "the update loop stopped"
  from "nobody sent anything", and ``bridge:last_event:*`` cannot corroborate
  either, because the same ``NewMessage`` handler writes both — a handler that
  has stopped firing cannot testify to its own failure.  The reconciler reaches
  Telegram over an independent API path, so a message it recovers is proof the
  live path missed one.  A quiet account produces nothing to recover, which is
  exactly the desired silence (#2475).

All three keys are **freeform** (not Popoto-managed), so raw Redis
``get``/``set`` is correct here.  All other Redis writes in this codebase that
touch Popoto-managed keys must go through the ORM.  See issue #1408 for the
broader freeform-key convention used by ``bridge.dedup.record_last_event`` and
friends.

Every writer is best-effort: any exception logs a WARNING and never raises,
matching the same safety contract as ``bridge.dedup.record_last_event``.
"""

import logging
import time

import redis

logger = logging.getLogger(__name__)

_UPDATE_KEY = "bridge:last_update_received"
_PROBE_KEY = "bridge:last_probe_ok"
_MISSED_RECOVERY_KEY = "bridge:last_missed_recovery"
# Generous TTL — watchdog reads these frequently; keys must survive restarts.
_TTL_SECONDS = 604800  # 7 days


def _get_redis() -> redis.Redis:
    """The shared text Redis client (see utils/redis_client.py)."""
    from utils.redis_client import text_redis

    return text_redis()


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


def record_missed_recovery(redis_client=None) -> None:
    """Stamp ``bridge:last_missed_recovery`` with the current unix timestamp.

    Call this from the reconciler when a scan recovered one or more messages
    the live update path never delivered.  It is the watchdog's only
    non-circular evidence that the update loop is actually wedged rather than
    merely idle, so it must be stamped only on a real recovery — never on a
    scan that found nothing.

    Best-effort: logs a WARNING and never raises on any failure.
    """
    try:
        r = redis_client if redis_client is not None else _get_redis()
        r.set(_MISSED_RECOVERY_KEY, str(time.time()), ex=_TTL_SECONDS)
    except Exception as e:
        logger.warning("liveness: record_missed_recovery failed: %s", e)


def get_last_missed_recovery(redis_client=None) -> float | None:
    """Return the unix timestamp of the last missed-message recovery, or None.

    Returns None when the key is missing (no recovery has ever been needed, the
    healthy case) or the value is corrupt.  Never raises.
    """
    try:
        r = redis_client if redis_client is not None else _get_redis()
        raw = r.get(_MISSED_RECOVERY_KEY)
        if raw is None:
            return None
        return float(raw)
    except Exception as e:
        logger.warning("liveness: get_last_missed_recovery failed: %s", e)
        return None
