"""Durable cross-boot kill-list for wedge-reap survivors (issue #2146).

When the session runner's teardown reap (``agent/session_runner/runner.py``)
cannot confirm a tool subtree's death — a ``setsid``'d child that escaped
``killpg``, or an ``EPERM`` group kill — the surviving PIDs are persisted here.
The worker's cross-process orphan reaper
(``agent/session_health.py::_reap_orphan_session_processes``) drains this list at
boot and on every hourly pass, ``create_time``-guarded so a recycled PID is never
collaterally killed.

This is worker infrastructure state, **not** a Popoto-managed model key, so it
uses the shared Redis client directly (same precedent as ``worker:registered_pid:*``
and the heartbeat keys). Every operation is fail-silent: the reap path must never
crash on persistence, and the drain must never crash the worker boot.

Design (see ``docs/plans/sdlc-2146.md``):
  - Key ``valor:reap:killlist`` is a Redis hash ``str(pid) -> json({pid,
    create_time, pgid, session_ref, ts})``.
  - ``add(entries)`` persists survivors (best-effort).
  - ``drain_and_kill()`` is a one-shot drain: each entry is killed-if-matched
    (``create_time`` recycle guard) then removed unconditionally.
  - A TTL bounds accumulation on a machine that never reboots.
"""

from __future__ import annotations

import json
import logging
import os
import signal
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Redis hash key. Worker infrastructure state — NOT a Popoto model key.
_KILLLIST_KEY = "valor:reap:killlist"

# TTL so a machine that never reboots does not accumulate stale entries.
_KILLLIST_TTL_S = int(os.environ.get("REAP_KILLLIST_TTL_S", str(24 * 3600)))

# create_time equality tolerance (seconds) — mirrors ``_pending_sigkill_orphans``
# in ``agent/session_health.py``.
_CREATE_TIME_TOL_S = 1e-3


def _redis():
    """Return the shared popoto Redis connection (same client as the rest of agent/)."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_proc_create_time(pid: int) -> float | None:
    """Live ``create_time`` for ``pid`` via psutil, or None if gone/unreadable."""
    try:
        import psutil  # noqa: PLC0415

        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001 — psutil missing, NoSuchProcess, AccessDenied
        return None


def add(entries: Iterable[tuple]) -> int:
    """Persist reap survivors to the durable kill-list. Fail-silent.

    ``entries`` is an iterable of ``(pid, create_time, pgid[, session_ref])``
    tuples. Returns the number of entries written. Never raises — a reap must
    not crash on persistence.
    """
    items = list(entries or [])
    if not items:
        return 0
    try:
        r = _redis()
    except Exception as e:  # noqa: BLE001
        logger.debug("[reap-killlist] add: redis unavailable (non-fatal): %s", e)
        return 0

    written = 0
    for item in items:
        try:
            pid = int(item[0])
            create_time = float(item[1])
            pgid = item[2] if len(item) > 2 else None
            session_ref = item[3] if len(item) > 3 else None
        except (TypeError, ValueError, IndexError):
            continue
        payload = json.dumps(
            {
                "pid": pid,
                "create_time": create_time,
                "pgid": pgid,
                "session_ref": session_ref,
                "ts": _now_iso(),
            }
        )
        try:
            r.hset(_KILLLIST_KEY, str(pid), payload)
            written += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("[reap-killlist] add: hset pid=%s failed: %s", pid, e)
    if written:
        try:
            r.expire(_KILLLIST_KEY, _KILLLIST_TTL_S)
        except Exception as e:  # noqa: BLE001
            logger.debug("[reap-killlist] add: expire failed (non-fatal): %s", e)
    return written


def drain_and_kill(
    kill_fn: Callable[[int, int], None] | None = None,
    proc_ctime_fn: Callable[[int], float | None] | None = None,
) -> int:
    """Drain the kill-list, SIGKILL each still-live ``create_time``-matched PID.

    Boot/hourly consumer, folded into
    ``agent/session_health.py::_reap_orphan_session_processes``. For each stored
    entry: verify the live process's ``create_time`` matches the stored value
    (PID-recycle guard); if it matches and the process is alive, SIGKILL it; then
    remove the entry **unconditionally** (one-shot drain — mismatched/dead
    entries are discarded). Returns the count actually killed. Fail-silent
    per-entry and overall.

    Idempotent and safe to invoke on every reaper pass (boot + hourly): a
    re-drain of an already-cleared or recycled PID is a no-op.

    Seams (``kill_fn`` / ``proc_ctime_fn``) let tests drive outcomes without real
    processes.
    """
    kill_fn = kill_fn or os.kill
    proc_ctime_fn = proc_ctime_fn or _default_proc_create_time
    try:
        r = _redis()
        raw = r.hgetall(_KILLLIST_KEY)
    except Exception as e:  # noqa: BLE001
        logger.debug("[reap-killlist] drain: read failed (non-fatal): %s", e)
        return 0
    if not raw:
        return 0

    killed = 0
    for field, val in raw.items():
        try:
            entry = json.loads(val.decode() if isinstance(val, (bytes, bytearray)) else val)
            pid = int(entry.get("pid"))
            stored_ct = float(entry.get("create_time"))
        except Exception:  # noqa: BLE001 — malformed entry
            _hdel(r, field)
            continue
        try:
            live_ct = proc_ctime_fn(pid)
            if live_ct is None:
                # Process gone (or unreadable) — nothing to kill.
                pass
            elif abs(live_ct - stored_ct) <= _CREATE_TIME_TOL_S:
                try:
                    kill_fn(pid, signal.SIGKILL)
                    killed += 1
                    logger.warning(
                        "[reap-killlist] SIGKILL'd boot-persisted survivor pid=%d "
                        "(pgid=%s, session=%s)",
                        pid,
                        entry.get("pgid"),
                        entry.get("session_ref"),
                    )
                except ProcessLookupError:
                    pass
                except Exception as e:  # noqa: BLE001
                    logger.debug("[reap-killlist] drain: kill pid=%s failed: %s", pid, e)
            else:
                logger.debug(
                    "[reap-killlist] drain: pid=%d recycled (create_time %s != %s), skip",
                    pid,
                    live_ct,
                    stored_ct,
                )
        finally:
            _hdel(r, field)  # one-shot drain — always remove
    return killed


def _hdel(r, field) -> None:
    try:
        r.hdel(_KILLLIST_KEY, field)
    except Exception as e:  # noqa: BLE001
        logger.debug("[reap-killlist] hdel failed (non-fatal): %s", e)
