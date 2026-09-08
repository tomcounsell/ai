"""Bridge-level liveness signals for the stale-update-stream detector (#1712).

Writes four liveness keys to Redis.  Two are **positive health** signals that
record that something good *happened*, one is **positive failure** evidence,
and one is a **structured per-cycle outcome record** of the reconciler's
per-chat scan.  None of them let the watchdog infer failure from silence — the
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

- ``bridge:last_scan_outcome``: a JSON record written by the reconciler at the
  END of every scan that got past ``get_dialogs()``, describing what the
  per-chat loop actually did (issue #2691).  ``bridge:last_probe_ok`` is
  stamped *before* that loop, so a half-wedged client — dialog list resolves,
  every per-chat ``iter_messages`` faults — keeps the probe fresh, recovers
  nothing, stamps no missed-recovery evidence, and was previously invisible to
  the watchdog while messages were lost.  This record closes that blind spot.

  The record is **structurally** a positive statement, not an inferred one.
  "The scan ran and every chat faulted" is ``attempted > 0 and
  faulted == attempted`` on a record the scan itself wrote after finishing.
  "The scan never ran" is the *absence* of a fresh record — there is no value
  of this key that means it.  A reader therefore never has to derive one case
  from the other by timing arithmetic; a missing or stale record is
  inconclusive by construction and must never be treated as evidence.

  ``consecutive_total_fault_cycles`` is maintained by the writer, which is the
  only component that knows what a cycle is: it increments when this cycle was
  a total fault, resets to 0 when any chat succeeded, and is left alone by a
  cycle that attempted nothing.  A reader counting cycles from timestamps
  would be re-deriving the reconciler's own cadence, so it does not.  The
  record also carries the writing ``pid``; a run counter never carries across
  a bridge restart, matching #2475's rule that a restart clears the accusation.

All four keys are **freeform** (not Popoto-managed), so raw Redis
``get``/``set`` is correct here.  All other Redis writes in this codebase that
touch Popoto-managed keys must go through the ORM.  See issue #1408 for the
broader freeform-key convention used by ``bridge.dedup.record_last_event`` and
friends.

Every writer is best-effort: any exception logs a WARNING and never raises,
matching the same safety contract as ``bridge.dedup.record_last_event``.
"""

import json
import logging
import os
import time

import redis

logger = logging.getLogger(__name__)

_UPDATE_KEY = "bridge:last_update_received"
_PROBE_KEY = "bridge:last_probe_ok"
_MISSED_RECOVERY_KEY = "bridge:last_missed_recovery"
_SCAN_OUTCOME_KEY = "bridge:last_scan_outcome"
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


def record_scan_outcome(
    attempted: int,
    faulted: int,
    sample_error: str = "",
    redis_client=None,
) -> dict | None:
    """Write ``bridge:last_scan_outcome`` for one completed reconciler scan.

    Call this from the reconciler after the per-chat loop finishes, on every
    cycle that got past ``get_dialogs()`` — including cycles where nothing was
    attempted.  Never call it on a cycle that did not reach the loop: the
    absence of a fresh record is precisely how a reader learns the scan did not
    run, and a record written from outside the loop would destroy that
    distinction (issue #2691).

    ``attempted`` counts chats that entered the per-chat scan body; ``faulted``
    counts those whose scan raised.  ``consecutive_total_fault_cycles`` is
    carried forward from the previous record written by *this same process*:
    incremented when this cycle was a total fault (``attempted > 0 and
    faulted == attempted``), reset to 0 when any chat succeeded, and left
    unchanged by a cycle that attempted nothing.  A record written by a
    different pid restarts the run rather than continuing it.

    Returns the record that was written, or None on failure.  Best-effort: logs
    a WARNING and never raises.
    """
    try:
        r = redis_client if redis_client is not None else _get_redis()
        pid = os.getpid()

        prev_run = 0
        prev = _read_scan_outcome(r)
        if prev is not None and prev.get("pid") == pid:
            prev_run = int(prev.get("consecutive_total_fault_cycles", 0))

        if attempted <= 0:
            run = prev_run
        elif faulted >= attempted:
            run = prev_run + 1
        else:
            run = 0

        record = {
            "ts": time.time(),
            "pid": pid,
            "attempted": int(attempted),
            "faulted": int(faulted),
            "consecutive_total_fault_cycles": run,
            "sample_error": sample_error[:200],
        }
        r.set(_SCAN_OUTCOME_KEY, json.dumps(record), ex=_TTL_SECONDS)
        return record
    except Exception as e:
        logger.warning("liveness: record_scan_outcome failed: %s", e)
        return None


def _read_scan_outcome(r) -> dict | None:
    """Return the stored scan-outcome record, or None if absent/corrupt."""
    raw = r.get(_SCAN_OUTCOME_KEY)
    if raw is None:
        return None
    record = json.loads(raw)
    if not isinstance(record, dict):
        return None
    return record


def get_last_scan_outcome(redis_client=None) -> dict | None:
    """Return the last reconciler scan-outcome record, or None.

    Returns None when the key is missing (no scan has completed since the key
    was last set, which a reader must treat as inconclusive — never as
    evidence of failure) or the value is corrupt.  Never raises.
    """
    try:
        r = redis_client if redis_client is not None else _get_redis()
        return _read_scan_outcome(r)
    except Exception as e:
        logger.warning("liveness: get_last_scan_outcome failed: %s", e)
        return None
