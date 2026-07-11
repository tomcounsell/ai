"""Transient cancel-reason signal for reason-aware interrupt messaging (#1877;
narrowed to a terminal-only signal by the silent-resume inversion).

When a killer cancels a running session, the executor's ``CancelledError`` handler
(``agent/messenger.py``) and the completion runner (``agent/session_completion.py``)
decide whether to speak at all. An interruption the machinery will recover from
(re-queue + auto-resume) is SILENT; only a terminal, non-resumable stop earns a
best-effort ``INTERRUPT_NO_RESUME`` Telegram message. Those send sites are
deliberately ORM-free, so they cannot re-read the session's authoritative status
to decide whether the interruption is terminal. Instead each killer writes a
transient reason to a raw Redis key that the winning send site reads.

Convention:
  * Key: ``cancel-reason:{session_id}`` in ``POPOTO_REDIS_DB`` (the same raw-Redis
    access pattern the ``interrupted-sent:{session_id}`` dedup key already uses).
  * Values: ``"no_resume"`` is the value read by every ``== "no_resume"``
    consumer — the killer finalized the session terminal and nothing will
    resume automatically. Every non-terminal path (re-queue, plain worker
    shutdown, an unpredicted-terminal escalation before its re-stamp) writes
    nothing, leaving the key absent.
    A second value, ``"conflict_escalation"``, is written by the pop-loop's
    bounded ``StatusConflictError`` escalation
    (``agent_session_queue.py::_cancel_stuck_pending_on_conflict``) purely as
    a short-lived, best-effort operator-visibility marker — it is never read
    back by any ``== "no_resume"`` consumer and is inert to them. It is NOT a
    durable audit trail: it shares this key's 180s TTL, so it is gone long
    before an operator investigating an escalation minutes later would find
    it. The durable record of an escalation-cancel is the ``reason=`` string
    passed to ``finalize_session(...)`` at that same call site, which lands
    in the ``LIFECYCLE`` transition log.
  * TTL: 180 seconds. **The TTL is the sole cleanup mechanism** — there is no
    destructive pop or delete anywhere. This is load-bearing: both interrupt send
    sites race a single-winner ``interrupted-sent`` SET-NX dedup, and a destructive
    read by the *losing* (non-sending) site could starve the *winning* (sending)
    site into reading ``None`` and emitting the wrong copy. A non-destructive read
    (plus reading only inside the dedup-winner branch) closes that race. A stale
    key lingers at most 180s and only for its own unique ``session_id``, so there
    is no cross-session contamination.

Safe defaults:
  * Absent key (genuine worker shutdown / Branch 3, a killer that raced ahead of
    its own write, or any non-terminal prediction) -> ``get_cancel_reason``
    returns ``None`` -> the send site stays silent. The session later resumes
    and delivers its real answer, or a separate ``FAILURE_NOTICE`` path covers a
    genuine crash.
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

    Best-effort: a Redis failure is swallowed (the send site degrades to
    silence, i.e. no message is sent). Must be called *before* the finalize /
    cancel that triggers the interrupt send so the winning send site can read it.

    Args:
        session_id: The session being cancelled.
        kind: the value read by every ``== "no_resume"`` consumer is
            ``"no_resume"`` (terminal, nothing resumes automatically). A
            second value, ``"conflict_escalation"``, is written by the
            pop-loop's bounded escalation as a short-lived, best-effort
            operator-visibility marker only — see the module docstring for
            why that write does not make this key a durable audit trail. The
            function keeps a generic string signature so callers are free to
            write other values.
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
    back to silence (no message sent). Callers should read this only inside the
    branch that won the ``interrupted-sent`` dedup, so a non-sending site cannot
    influence what the sender reads.

    Every ``== "no_resume"`` consumer treats the signal as binary:
    ``"no_resume"`` present means a killer owns the terminal exit narrative
    and the terminal no-resume copy should be delivered; absent (or any
    other value, including ``"conflict_escalation"``) means silence to those
    consumers.

    Returns:
        Whatever was last written for ``session_id`` within its 180s TTL
        (``"no_resume"`` or ``"conflict_escalation"``), else ``None``. Only
        ``"no_resume"`` is meaningful to the ``== "no_resume"`` consumers —
        see the module docstring for the other value's narrower purpose.
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
