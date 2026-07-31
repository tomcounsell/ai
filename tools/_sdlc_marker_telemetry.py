"""Best-effort per-run marker-write telemetry (issue #2451).

A pipeline run can have its stage-marker writes fail *repeatedly* (``LEASE_ABSENT``,
state-machine rejection) while the agent keeps passing the correct run_id, and
still land the final ledger by retry -- so the run reports success end-to-end
with a ledger that was broadly unwritable throughout, and nothing notices. These
counters make that storm observable: ``tools/sdlc_stage_marker.write_marker``
increments them on every write outcome, ``tools/sdlc_run_health`` reads them
back and reports a disposition, and ``tools/sdlc_review_finalize`` asserts a run
recorded at least one confirmed ok write before selfcheck passes.

Redis keys (plain, non-Popoto-managed -- raw access allowed here)::

    sdlc:marker_writes:{issue}:{run_id}:ok                (INCR)
    sdlc:marker_writes:{issue}:{run_id}:fail              (INCR)
    sdlc:marker_writes:{issue}:{run_id}:last_failed_stage (SET)

TTL is REFRESHED on EVERY increment (CONCERN 1): a full pipeline spans
hours-to-days with review pauses, so a set-once TTL scaled to the ~1800s lock
would expire the first ok-write's counter before REVIEW and fail-close the
``>=1-ok-write`` selfcheck on a *healthy* run. ``INCR`` then ``EXPIRE`` on every
write keeps the key alive as long as the run is actively writing markers, with a
generous window so a legitimately paused run at REVIEW still reads >=1 ok write.

Every operation is best-effort: a counter-write failure NEVER changes the
marker write's own outcome, and reads fail soft to zero counts.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# TTL for the per-run marker counters. Refreshed on every increment so expiry
# always tracks the most recent write.
# GRAIN OF SALT: 24h (86400s) is PROVISIONAL/TUNABLE -- generous enough to
# survive a run paused at REVIEW for human sign-off, bounded so abandoned-run
# counters self-clean. Override via SDLC_MARKER_COUNTER_TTL_SECONDS.
MARKER_COUNTER_TTL_SECONDS = int(
    os.environ.get("SDLC_MARKER_COUNTER_TTL_SECONDS", str(24 * 60 * 60))
)

_PREFIX = "sdlc:marker_writes"


def _key(issue_number: int, run_id: str, suffix: str) -> str:
    return f"{_PREFIX}:{issue_number}:{run_id}:{suffix}"


def _to_int(value) -> int:
    """Coerce a Redis GET result (bytes / str / None) to int, defaulting 0."""
    if value is None:
        return 0
    if isinstance(value, bytes):
        try:
            value = value.decode()
        except Exception:
            return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode()
        except Exception:
            return None
    return str(value)


def record_marker_write(
    issue_number: int | None,
    run_id: str | None,
    ok: bool,
    stage: str | None = None,
) -> None:
    """Increment the per-run ok/fail counter (best-effort; never raises).

    Args:
        issue_number: The issue whose ledger the write targeted.
        run_id: The run identity the write was attempted under.
        ok: True for a successful (or idempotent) write; False for a
            non-degraded failure (state-machine rejection / LEASE_ABSENT).
            The degraded (Redis-absent) path must NOT call this with ok=False.
        stage: The stage name -- recorded as last_failed_stage on a failure.
    """
    if not issue_number or not run_id:
        return
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        suffix = "ok" if ok else "fail"
        key = _key(issue_number, run_id, suffix)
        _R.incr(key)
        _R.expire(key, MARKER_COUNTER_TTL_SECONDS)
        if not ok and stage:
            lk = _key(issue_number, run_id, "last_failed_stage")
            _R.set(lk, stage, ex=MARKER_COUNTER_TTL_SECONDS)
    except Exception as e:
        logger.debug(
            "_sdlc_marker_telemetry: counter write failed for issue #%s run_id=%s (%s: %s) "
            "-- marker outcome unaffected",
            issue_number,
            run_id,
            type(e).__name__,
            e,
        )


def read_marker_counters(issue_number: int | None, run_id: str | None) -> dict:
    """Read the per-run counters. Fails soft to zero counts (never raises).

    Returns ``{"ok_writes": int, "fail_writes": int, "last_failed_stage": str|None}``.
    """
    empty = {"ok_writes": 0, "fail_writes": 0, "last_failed_stage": None}
    if not issue_number or not run_id:
        return empty
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        return {
            "ok_writes": _to_int(_R.get(_key(issue_number, run_id, "ok"))),
            "fail_writes": _to_int(_R.get(_key(issue_number, run_id, "fail"))),
            "last_failed_stage": _to_str(_R.get(_key(issue_number, run_id, "last_failed_stage"))),
        }
    except Exception as e:
        logger.debug(
            "_sdlc_marker_telemetry: counter read failed for issue #%s run_id=%s (%s: %s)",
            issue_number,
            run_id,
            type(e).__name__,
            e,
        )
        return empty


def marker_ok_write_count(issue_number: int | None, run_id: str | None) -> int:
    """Convenience: the ok-write count for a run (0 on any error / no counters)."""
    return read_marker_counters(issue_number, run_id)["ok_writes"]
