"""Transient cancel-reason signal for reason-aware interrupt messaging (#1877).

When a killer cancels a running session, the executor's ``CancelledError`` handler
(``agent/messenger.py``) and the completion runner (``agent/session_completion.py``)
send a best-effort "I was interrupted" Telegram message. Those send sites are
deliberately ORM-free, so they cannot re-read the session's authoritative status
to decide whether the interruption will resume. Instead each killer writes a
transient reason to a raw Redis key that the winning send site reads.

Convention:
  * Key: ``cancel-reason:{session_id}`` in ``POPOTO_REDIS_DB`` (the same raw-Redis
    access pattern the ``interrupted-sent:{session_id}`` dedup key already uses).
  * Values: ``"no_resume"`` (killer finalized the session terminal — nothing will
    resume) or ``"resume"`` (a recovery path re-queued the session to ``pending``).
  * TTL: 180 seconds. **The TTL is the sole cleanup mechanism** — there is no
    destructive pop or delete anywhere. This is load-bearing: both interrupt send
    sites race a single-winner ``interrupted-sent`` SET-NX dedup, and a destructive
    read by the *losing* (non-sending) site could starve the *winning* (sending)
    site into reading ``None`` and emitting the wrong copy. A non-destructive read
    (plus reading only inside the dedup-winner branch) closes that race. A stale
    key lingers at most 180s and only for its own unique ``session_id``, so there
    is no cross-session contamination.

Safe defaults:
  * Absent key (genuine worker shutdown / Branch 3, or a killer that raced ahead
    of its own write) -> ``get_cancel_reason`` returns ``None`` -> the send site
    uses ``INTERRUPT_RESUME``. This is the historical behavior, so a missed write
    degrades to "will resume", never to a crash.
  * Redis unavailable -> both helpers swallow the error. ``get_cancel_reason``
    returns ``None`` so it never raises into the ``CancelledError`` handler.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _cancel_reason_key(session_id: str) -> str:
    """Redis key for the transient cancel-reason signal."""
    return f"cancel-reason:{session_id}"


def set_cancel_reason(session_id: str, kind: str, ttl: int = 180) -> None:
    """Write the transient cancel-reason for ``session_id``.

    Best-effort: a Redis failure is swallowed (the send site degrades to the
    resume copy, i.e. current behavior). Must be called *before* the finalize /
    cancel that triggers the interrupt send so the winning send site can read it.

    Args:
        session_id: The session being cancelled.
        kind: ``"no_resume"`` (terminal, nothing resumes) or ``"resume"``
            (re-queued to pending).
        ttl: Key lifetime in seconds. The TTL is the only reclaimer — no code
            ever deletes the key.
    """
    if not session_id:
        return
    try:
        from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

        POPOTO_REDIS_DB.set(_cancel_reason_key(session_id), kind, ex=ttl)
    except Exception as exc:
        logger.debug("[cancel-reason] set failed for %s (non-fatal): %s", session_id, exc)


def get_cancel_reason(session_id: str) -> str | None:
    """Read the transient cancel-reason for ``session_id`` non-destructively.

    Never deletes the key (the 180s TTL is the sole reclaimer) and never raises
    — a Redis failure or a missing key both return ``None`` so the caller falls
    back to the resume copy. Callers should read this only inside the branch that
    won the ``interrupted-sent`` dedup, so a non-sending site cannot influence
    what the sender reads.

    Returns:
        ``"no_resume"`` / ``"resume"`` if set, else ``None``.
    """
    if not session_id:
        return None
    try:
        from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

        raw = POPOTO_REDIS_DB.get(_cancel_reason_key(session_id))
    except Exception as exc:
        logger.debug("[cancel-reason] get failed for %s (non-fatal): %s", session_id, exc)
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except Exception:
            return None
    return str(raw)
