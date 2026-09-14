"""Where an answer to an agent's question goes — the seam, without the caller.

Two functions factored out of the reply-to steering ladder in
``bridge/telegram_bridge.py``, so a **poll vote** can reach a `running`,
`pending` or `completed` session exactly the way a typed reply does.

**This module is deliberately poll-independent.** ``translate_poll_vote`` lives
elsewhere (``bridge/poll_vote.py``) and imports from here. The separation is the
whole reason this module exists as its own commit: it restructures the primary
inbound path for every typed Telegram reply on every machine, and reverting the
poll feature does not revert that restructure. Keeping the translator out means
``git revert`` of the extraction stays a real option. Do not move the translator
in here.

**Why two functions rather than one.** The obvious `(session_id, text, sender)`
signature cannot carry this ladder: most of it consumes objects a vote does not
have. ``_ack_steering_routed`` branches on ``message.media`` and reacts on
``message.id``; the completed branch calls ``is_duplicate_message``,
``fetch_reply_chain`` and ``react_if_worker_down``. A single narrow function
would force whoever wires the vote path to drop the completed branch — and that
branch is the **mainline** for a poll answer, not an edge case.

So the split is: a pure state read that both callers share, and the
completed-session re-enqueue that both callers share. Everything
caller-specific — acks, dedup short-circuit, reply-chain hydration, worker-down
reaction — stays in the caller. That is what makes "behavior must not change" a
checkable claim rather than a wish.

See ``docs/features/session-steering.md``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class AnswerTargetKind(StrEnum):
    """Which state the session an answer is aimed at turned out to be in."""

    #: A ``running`` or ``active`` record exists — steer into it.
    LIVE = "live"
    #: No live record, but a ``pending`` one — steer into it.
    PENDING = "pending"
    #: A ``completed`` record exists, but a live one appeared concurrently.
    #: Belt-and-suspenders against a concurrent reply having already resumed it.
    LIVE_GUARD = "live_guard"
    #: Completed, and nothing live. Re-enqueue with prior context.
    COMPLETED = "completed"
    #: No record at all. Log and stop — never create a session.
    NONE = "none"


@dataclass(frozen=True)
class AnswerTarget:
    """The resolved destination for an answer.

    ``session`` is the record itself, not just a kind, because **the caller must
    derive ``room_id`` from it**. ``push_steering_message`` selects the Room key
    only when handed a ``room_id`` and deliberately never looks a session up
    itself (``session_id`` is unindexed and a lookup on the inbound fast path
    would cost seconds). Returning only a kind would strand both callers with no
    way to derive the Room, silently downgrading every steer to the legacy key.

    ``session`` is ``None`` only on ``NONE``, which never steers.
    """

    kind: AnswerTargetKind
    session: Any | None
    #: Carried because the LIVE and LIVE_GUARD log lines embed the status.
    matched_status: str | None = None
    #: Carried because the PENDING log line embeds ``age=%.1f``.
    pending_age_s: float | None = None
    #: Every ``completed`` record found, for a caller that needs the full set
    #: (the reply-to handler's dedup short-circuit runs before picking one).
    completed_sessions: list | None = None


def _completed_created_at(s) -> float:
    """Verbatim from the ladder — handles both datetime and float timestamps."""
    ts = getattr(s, "created_at", None)
    if ts is None:
        return 0
    if hasattr(ts, "timestamp"):
        return ts.timestamp()
    return float(ts)


def resolve_answer_target(session_id: str) -> AnswerTarget:
    """Read which state ``session_id`` is in. Pure — no side effects, no acks.

    A **restructure, not a verbatim lift**: the source interleaves this ladder
    with ``await _ack_steering_routed(...)`` + ``return`` at every branch, so the
    side effects are pulled out to the caller and only the state read remains.

    Status ordering is preserved exactly: ``running``/``active`` first (both mean
    "the agent is working" for steering purposes — ``running`` is set by
    ``_pop_agent_session``, ``active`` later by ``_execute_agent_session`` for
    auto-continue deferral), then ``pending``, then ``completed`` with the
    live re-check guard.
    """
    from models.agent_session import AgentSession

    for check_status in ("running", "active"):
        # Newest-first is load-bearing on the LIVE branch: the Room is derived
        # from whichever record this picks, so it must be the live one.
        live = AgentSession.newest_for_session_id(session_id, status=check_status)
        if live is not None:
            return AnswerTarget(
                kind=AnswerTargetKind.LIVE, session=live, matched_status=live.status
            )

    pending = AgentSession.newest_for_session_id(session_id, status="pending")
    if pending is not None:
        age = _pending_age_seconds(pending.created_at, time.time())
        return AnswerTarget(
            kind=AnswerTargetKind.PENDING,
            session=pending,
            matched_status="pending",
            pending_age_s=age,
        )

    completed_sessions = AgentSession.rows_for_session_id(session_id, status="completed")
    if completed_sessions:
        # Belt-and-suspenders: a concurrent reply may have created a
        # pending/running record between the checks above.
        for guard_status in ("pending", "running", "active"):
            guard = AgentSession.newest_for_session_id(session_id, status=guard_status)
            if guard is not None:
                return AnswerTarget(
                    kind=AnswerTargetKind.LIVE_GUARD,
                    session=guard,
                    matched_status=guard.status,
                )
        # Most-recent completed record, for the best `context_summary` —
        # NOT `completed_sessions[0]`, which silently degrades the preamble.
        completed = max(completed_sessions, key=_completed_created_at)
        return AnswerTarget(
            kind=AnswerTargetKind.COMPLETED,
            session=completed,
            matched_status="completed",
            completed_sessions=completed_sessions,
        )

    return AnswerTarget(kind=AnswerTargetKind.NONE, session=None)


def _pending_age_seconds(created_at, now: float) -> float:
    from bridge.telegram_bridge import _pending_session_age_seconds

    return _pending_session_age_seconds(created_at, now)


async def resume_completed_session(
    *,
    completed,
    text: str,
    sender_name: str,
    telegram_chat_id: str,
    telegram_message_id: int,
    chat_title: str | None = None,
    sender_id: int | None = None,
    project: dict | None = None,
    project_key: str | None = None,
    working_dir: str | None = None,
    telegram_message_key: str | None = None,
    reply_chain_context: str | None = None,
    extra_context_overrides: dict | None = None,
    message_ts=None,
) -> None:
    """Re-enqueue a completed session with its prior context.

    Every ``project`` / ``project_key`` / ``working_dir`` / ``session_type``
    argument left ``None`` falls back to the corresponding field on the
    ``completed`` record itself — which is precisely why a caller holding no
    ``project`` dict (a poll vote) can still use this.

    Returns ``None``. The caller owns its own ``_steering_session_enqueued``
    bookkeeping, its ``react_if_worker_down`` (which needs the inbound
    ``message.id``), and its dedup short-circuit.
    """
    import asyncio
    from pathlib import Path

    from bridge.dispatch import dispatch_telegram_session
    from bridge.poll_registry import mark_session_polls_steered
    from bridge.telegram_bridge import DEFAULTS, _build_completed_resume_text
    from config.enums import SessionType

    augmented_text = _build_completed_resume_text(
        completed, text, reply_chain_context=reply_chain_context
    )

    resolved_project = (
        project if project is not None else getattr(completed, "project_config", None)
    )
    resolved_project_key = project_key or getattr(completed, "project_key", None) or ""

    resolved_working_dir = working_dir or ""
    if not resolved_working_dir and resolved_project:
        resolved_working_dir = resolved_project.get(
            "working_directory", DEFAULTS.get("working_directory", "")
        )
    if not resolved_working_dir:
        resolved_working_dir = getattr(completed, "working_dir", "") or ""
    if not resolved_working_dir:
        resolved_working_dir = str(Path(__file__).parent.parent)

    await dispatch_telegram_session(
        project_key=resolved_project_key,
        session_id=completed.session_id,
        working_dir=resolved_working_dir,
        message_text=augmented_text,
        sender_name=sender_name,
        chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        chat_title=chat_title,
        priority="normal",
        sender_id=sender_id,
        telegram_message_key=telegram_message_key,
        project_config=resolved_project,
        extra_context_overrides=extra_context_overrides,
        session_type=getattr(completed, "session_type", None) or SessionType.ENG,
        message_ts=message_ts,
    )

    # Written LAST, and only after the dispatch returned — the same invariant the
    # vote path states at bridge/poll_vote.py:219. `mark_poll_steered` srem's the
    # row from POLL_OPEN_INDEX, and both `iter_unanswered_polls` and
    # `poll_expired_unanswered` key on the marker's absence; closing before the
    # dispatch would mean a raising `dispatch_telegram_session` leaves
    # `translate_poll_vote`'s release-and-retry handler with nothing left to
    # re-yield, permanently swallowing the human's tap.
    #
    # Any answer route closes the question, not only a tap. The marker is
    # idempotent, so the vote path's own `mark_poll_steered` after this returns is
    # a no-op; the case this exists for is the typed reply, which without it
    # leaves the row open for its full TTL and pauses the resumed session on an
    # already-answered question every turn.
    #
    # Guarded: this call sits directly ahead of both callers' anti-duplicate
    # sentinels — `_steering_session_enqueued = True` in telegram_bridge.py
    # (set right after this function returns) and `mark_poll_dispatched(poll_id)`
    # in poll_vote.py. `mark_session_polls_steered` itself never raises, but
    # `asyncio.to_thread` can (e.g. a closed or shutting-down event loop), and an
    # unhandled raise here would unwind past both sentinels — a duplicate enqueue
    # on the prose path, or a released claim and re-dispatch on the vote path.
    # The close-out is best-effort bookkeeping; it must never be the thing that
    # breaks the sentinels it precedes.
    try:
        await asyncio.to_thread(mark_session_polls_steered, completed.session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mark_session_polls_steered failed for session_id=%s: %s",
            completed.session_id,
            exc,
            exc_info=True,
        )
