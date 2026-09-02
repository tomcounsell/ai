"""Poll eligibility — the single answer to "does this surface take a poll?".

A native Telegram poll ships only when BOTH conditions hold:

1. The destination is a **group** chat. A user account cannot send a poll into a
   1:1 DM — MTProto rejects it with ``MediaInvalidError``. That is a settled fact
   of the protocol, verified by live probe, not a condition to retry against.
2. The session asking the question is an **eng** session. Polls are an
   engineering affordance; a ``teammate`` session gets prose even in an eligible
   group.

Anything else degrades to today's plain-text behavior. This predicate is read
**twice on purpose**: at ask time by ``tools/ask_poll.py`` (so degradation
happens once, at a single decision point, before a poll payload ever exists) and
again at send time by the relay's poll branch (the relay is the last writer
before the wire, and an outbox payload can sit across a session-type change).

``is_group_chat`` is **imported**, not owned here: it is a generic Telegram
peer-type predicate whose existing consumer is read-the-room. Hosting it in a
feature module would make read-the-room import from a poll module — the naming
inversion that later invites a second, drifting copy.

See ``docs/features/telegram-poll-questions.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bridge.read_the_room import is_group_chat

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PollEligibility:
    """Whether a poll may ship, and the named reason when it may not.

    ``reason`` is always populated — ``"eligible"`` on the happy path — so an
    ineligible question is diagnosable from a single log line rather than
    inferred from an absence.
    """

    ok: bool
    reason: str


def poll_eligible(chat_id: str | int | None, session_id: str | None) -> PollEligibility:
    """Return whether a poll may be rendered into ``chat_id`` for ``session_id``.

    **Fails closed on every ambiguity and never raises.** A missing AgentSession
    record, a ``null`` or unrecognized ``session_type``, an unparseable chat id,
    or any exception all resolve to ineligible. The asymmetry is deliberate: a
    question rendered as prose to an eng session is a cosmetic loss, while a poll
    rendered into a teammate chat is a scope violation.

    Reasons: ``eligible``, ``not_a_group``, ``not_eng_session``,
    ``unknown_session_type``, ``eligibility_error``.

    Synchronous by design — ``tools/ask_poll.py`` runs off the event loop and
    calls it directly. The relay, which does not, must reach it through
    ``asyncio.to_thread``: the ``AgentSession`` lookup below is an unindexed
    scan and would stall every other bridge coroutine if awaited inline.
    """
    try:
        if not is_group_chat(chat_id):
            return PollEligibility(ok=False, reason="not_a_group")

        if not session_id:
            return PollEligibility(ok=False, reason="unknown_session_type")

        from config.enums import SessionType
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return PollEligibility(ok=False, reason="unknown_session_type")
        # Newest record wins, matching the relay's own lookup idiom — a stale
        # duplicate row must not decide a live session's eligibility.
        sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
        session = sessions[0]

        session_type = getattr(session, "session_type", None)
        if session_type == SessionType.ENG:
            return PollEligibility(ok=True, reason="eligible")
        if session_type == SessionType.TEAMMATE:
            return PollEligibility(ok=False, reason="not_eng_session")
        return PollEligibility(ok=False, reason="unknown_session_type")
    except Exception as exc:  # noqa: BLE001 — fail closed, never raise at a delivery seam
        logger.warning(
            "poll_eligibility_error chat_id=%s session_id=%s err=%s", chat_id, session_id, exc
        )
        return PollEligibility(ok=False, reason="eligibility_error")
