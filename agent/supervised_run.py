"""Supervised-run signal (issue #2026, WS1 — single-owner lease).

The supervisor — the run that holds the per-issue SDLC lock for a whole
pipeline — publishes its verified ``run_id`` as a run-scoped SIGNAL that a
stage fork reads at spawn. When the signal is present and LIVE, a stage fork
inherits the supervisor's ``run_id`` instead of contesting the lock, and a
bare ``session-ensure`` under a live signal is refused
(``SUPERVISED_RUN_ACTIVE``) so no fork can mint a competing ``run_id``. This
is what makes fork inheritance *structurally* enforced rather than advisory:
enforcement lives in the tool (``tools/sdlc_session_ensure.py``), keyed on
this signal, not in prose (Risk 3).

**Liveness is anchored on the issue lock.** The signal is LIVE iff the issue
lock (``session:issuelock:{issue_number}``, owned by ``run_id``) is currently
held by the signal's ``run_id``. When the supervisor releases the lock on run
completion / graceful failure, or the lock TTL lapses on a crash, the signal
goes STALE and a bare ``session-ensure`` falls back to normal standalone
semantics. There is deliberately no second TTL to reason about: the lock is
the single source of truth for "is this run still the owner."

**Two carriers, written together** (both best-effort; either alone suffices):

- Redis key ``session:supervisedrun:{issue_number}`` — the primary carrier,
  available to any process. Same non-Popoto raw-Redis idiom as the issue lock
  (``models/session_lifecycle.py``), given the same TTL and refreshed on every
  acquire/renew so it never outlives the lease by more than the TTL.
- File ``{worktree}/.sdlc-run`` — a human/skill-visible marker written into
  the slug worktree (``.worktrees/{slug}/.sdlc-run``) when the session's
  ``working_dir`` resolves to one. Read as a fallback when Redis is
  unavailable.

**Every operation FAILS OPEN** — logs the swallowed error class and returns a
safe default (``read`` → ``None``; ``status`` → not-live; ``write`` / ``clear``
→ silent no-op). A Redis hiccup degrades to "no cross-process supervision"
(the bare ensure then mints standalone, itself fail-open on the lock), never a
crash into the supervisor or the ensure path.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from collections import namedtuple
from pathlib import Path

logger = logging.getLogger(__name__)

# The signal name for a bare session-ensure refused under a live supervised
# run. Callers (stage skills) read the payload's ``run_id`` and inherit it.
SUPERVISED_RUN_ACTIVE = "SUPERVISED_RUN_ACTIVE"

# Basename of the worktree-local marker file.
_SIGNAL_FILENAME = ".sdlc-run"


SupervisedRunStatus = namedtuple("SupervisedRunStatus", ["live", "run_id", "session_id"])


def _signal_key(issue_number: int) -> str:
    return f"session:supervisedrun:{issue_number}"


def _worktree_signal_path(working_dir: str | None) -> Path | None:
    """Return the ``.sdlc-run`` path for a slug worktree, or ``None``.

    Only slug worktrees carry the file: the path is returned solely when
    ``working_dir`` is a real, non-empty string whose parts include a
    ``.worktrees`` segment (i.e. ``.../.worktrees/{slug}/``). A bare repo-root
    ``working_dir`` (the anchor session's home) has no slug worktree, so the
    file carrier is skipped there and the Redis carrier alone is used.
    """
    if not working_dir or not isinstance(working_dir, str):
        return None
    try:
        path = Path(working_dir)
        if ".worktrees" not in path.parts:
            return None
        return path / _SIGNAL_FILENAME
    except Exception:
        return None


def write_supervised_run_signal(
    issue_number: int | None,
    run_id: str | None,
    session_id: str = "",
    working_dir: str | None = None,
    ttl: int | None = None,
) -> None:
    """Publish/refresh the supervised-run signal for ``issue_number``.

    Called by ``tools/sdlc_session_ensure.py`` immediately after it wins (or
    renews) the issue lock and binds ``run_id`` — so signal-writing and lock
    ownership stay in lockstep. Overwrites any existing key (the lock owner is
    the sole writer, gated upstream by the ``SET NX`` lock contest) and
    refreshes the TTL. Best-effort and fully exception-isolated.
    """
    if not issue_number or not run_id:
        return

    if ttl is None:
        # Lazy import keeps this module import-light and avoids a cycle with
        # session_lifecycle (which imports nothing from here at module scope).
        try:
            from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS

            ttl = ISSUE_LOCK_TTL_SECONDS
        except Exception:
            ttl = 1800

    payload = json.dumps(
        {
            "run_id": run_id,
            "session_id": session_id,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
        }
    )
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.set(_signal_key(issue_number), payload, ex=ttl)
    except Exception as e:
        logger.debug(
            "[supervised-run] signal write (redis) failed for issue #%s (%s: %s) -- "
            "continuing (lock remains authoritative)",
            issue_number,
            type(e).__name__,
            e,
        )

    path = _worktree_signal_path(working_dir)
    if path is not None:
        try:
            path.write_text(f"{run_id}\n")
        except Exception as e:
            logger.debug(
                "[supervised-run] signal write (file %s) failed for issue #%s (%s: %s)",
                path,
                issue_number,
                type(e).__name__,
                e,
            )


def read_supervised_run_signal(
    issue_number: int | None, working_dir: str | None = None
) -> dict | None:
    """Return the raw signal payload dict, or ``None`` when no signal exists.

    Redis is the primary carrier; the worktree file is a fallback consulted
    only when the Redis read yields nothing (missing key or Redis error).
    Fails open to ``None`` on any error.
    """
    if not issue_number:
        return None

    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        raw = _R.get(_signal_key(issue_number))
        if raw is not None:
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and payload.get("run_id"):
                    return payload
            except (TypeError, ValueError):
                logger.debug(
                    "[supervised-run] signal payload for issue #%s is malformed -- ignoring",
                    issue_number,
                )
    except Exception as e:
        logger.debug(
            "[supervised-run] signal read (redis) failed for issue #%s (%s: %s) -- "
            "trying file fallback",
            issue_number,
            type(e).__name__,
            e,
        )

    path = _worktree_signal_path(working_dir)
    if path is not None:
        try:
            if path.exists():
                run_id = path.read_text().strip()
                if run_id:
                    return {"run_id": run_id, "session_id": ""}
        except Exception as e:
            logger.debug(
                "[supervised-run] signal read (file %s) failed for issue #%s (%s: %s)",
                path,
                issue_number,
                type(e).__name__,
                e,
            )
    return None


def supervised_run_status(
    issue_number: int | None, working_dir: str | None = None
) -> SupervisedRunStatus:
    """Report whether a LIVE supervised run owns ``issue_number``.

    Returns ``SupervisedRunStatus(live, run_id, session_id)``:

    - ``live=True`` — a signal exists AND the issue lock is currently held by
      the signal's ``run_id``. A bare ``session-ensure`` must refuse with
      ``SUPERVISED_RUN_ACTIVE`` and the caller inherits ``run_id``.
    - ``live=False`` — no signal, OR the signal is stale/superseded (lock
      released, TTL lapsed, or now owned by a different run). The bare ensure
      proceeds with normal standalone mint semantics.

    Liveness is decided by a non-mutating lock peek. Fails open to not-live on
    any error, including a Redis outage (``touch_issue_lock`` peek itself fails
    open to ``owner_run_id=None``, which never matches the signal's ``run_id``).
    """
    signal = read_supervised_run_signal(issue_number, working_dir=working_dir)
    if not signal or not signal.get("run_id"):
        return SupervisedRunStatus(False, None, None)

    sig_run_id = signal["run_id"]
    sig_session_id = signal.get("session_id") or None

    try:
        from models.session_lifecycle import touch_issue_lock

        peek = touch_issue_lock(issue_number, None, peek=True)
    except Exception as e:
        logger.debug(
            "[supervised-run] liveness peek failed for issue #%s (%s: %s) -- "
            "treating signal as not-live (standalone fallback)",
            issue_number,
            type(e).__name__,
            e,
        )
        return SupervisedRunStatus(False, sig_run_id, sig_session_id)

    # A held lock reports the owner as ``owner_run_id`` (peek with run_id=None
    # never claims an owner-match). An unheld lock, or the fail-open path,
    # reports ``owner_run_id`` that is None or does not equal the signal's id.
    owner_run_id = getattr(peek, "owner_run_id", None)
    if owner_run_id and owner_run_id == sig_run_id:
        owner_session_id = getattr(peek, "owner_session_id", None) or sig_session_id
        return SupervisedRunStatus(True, sig_run_id, owner_session_id)
    return SupervisedRunStatus(False, sig_run_id, sig_session_id)


def clear_supervised_run_signal(
    issue_number: int | None,
    run_id: str | None,
    working_dir: str | None = None,
) -> None:
    """Remove the supervised-run signal via COMPARE-AND-DELETE.

    Called on the supervisor's terminal transition (run completion / graceful
    failure) so a subsequent bare ``session-ensure`` falls back to standalone
    semantics instead of inheriting a dead run_id. Deletes the Redis key only
    when its payload still carries ``run_id`` (Lua value-compare, mirroring
    ``release_issue_lock``), so a successor's freshly written signal is never
    clobbered by a delayed cleanup. Best-effort and exception-isolated.
    """
    if not issue_number or not run_id:
        return

    key = _signal_key(issue_number)
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        raw = _R.get(key)
        if raw is not None:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get("run_id") == run_id:
                # Compare-and-delete: only delete if the value is still ours.
                _R.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    key,
                    raw,
                )
    except Exception as e:
        logger.debug(
            "[supervised-run] signal clear (redis) failed for issue #%s (%s: %s)",
            issue_number,
            type(e).__name__,
            e,
        )

    path = _worktree_signal_path(working_dir)
    if path is not None:
        try:
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.debug(
                "[supervised-run] signal clear (file %s) failed for issue #%s (%s: %s)",
                path,
                issue_number,
                type(e).__name__,
                e,
            )
