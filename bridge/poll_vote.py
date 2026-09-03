"""Translate a Telegram poll vote into a steering message. Idempotent.

The single translation function, reached by two callers: the ``events.Raw``
fast path and the reconciliation loop. Both call it, so adding the fast path
cannot introduce a second behavior.

**Reconciliation is primary; the Raw handler is only a latency win.**
``UpdateMessagePoll`` carries no peer and no message id, so it cannot route a
vote on its own — the registry does that. Making ``GetPollResultsRequest``
reconciliation the guaranteed mechanism also buys restart-survivability for
free, and de-risks the un-gated question of whether the push update reaches a
user account at all.

**It lives outside ``bridge/answer_routing.py`` on purpose.** That module's
entire justification is being a poll-independent seam shared with the reply-to
ladder, landed as its own commit so reverting it stays possible. Putting the
translator inside would re-fuse what that split separated.

See ``docs/features/telegram-poll-questions.md``.
"""

from __future__ import annotations

import logging

from bridge.answer_routing import (
    AnswerTargetKind,
    resolve_answer_target,
    resume_completed_session,
)
from bridge.poll_registry import (
    POLL_RECONCILE_SLOW_INTERVAL_S,
    claim_poll_answer,
    lookup_poll,
    mark_poll_dispatched,
    mark_poll_steered,
    poll_claim_age_s,
    poll_dispatched,
    release_poll_claim,
    takeover_poll_claim,
)

logger = logging.getLogger(__name__)

#: The literal final option. Tapping it is not an answer — it is a request for a
#: better question, answered by a plain-text followup the human replies to.
ESCAPE_HATCH_OPTION = "Other: wait for followup message"


def select_option(results, options: list[str]) -> int | None:
    """Pick the chosen option index deterministically. ``None`` when nobody voted.

    **The DM rule is dead.** "Any option with ``voters >= 1`` is unambiguously
    the human's choice" held only in a 1:1 chat. In a group several options can
    each carry ``voters >= 1``, and ``PollResults`` gives no ordering, so "the
    first vote" is simply not derivable from the aggregate.

    The rule, greppable and deterministic:

    1. Filter to entries with ``voters >= 1``.
    2. Zero → return ``None`` **without claiming**. A spurious update must not
       consume the one-shot claim.
    3. Exactly one → use it.
    4. More than one → warn naming the poll and the tied options, then take the
       highest ``voters``, breaking ties by **lowest decoded option index**.
    """
    from bridge.response import decode_option

    voted = [r for r in (getattr(results, "results", None) or []) if (r.voters or 0) >= 1]
    if not voted:
        return None

    decoded = []
    for r in voted:
        index, _prefix = decode_option(r.option)
        if index is not None and 0 <= index < len(options):
            decoded.append((index, r.voters or 0))
    if not decoded:
        return None

    if len(decoded) > 1:
        logger.warning(
            "poll_multiple_voters tied_options=%s — taking highest voters, "
            "ties broken by lowest option index",
            [(i, v) for i, v in decoded],
        )
    # Highest voters first, then lowest index. Deterministic on repeated runs.
    decoded.sort(key=lambda pair: (-pair[1], pair[0]))
    return decoded[0][0]


def build_steer_text(question: str, chosen: str) -> str:
    """The steer carries the question, not just the option.

    The resumed turn then has the binding without re-deriving it — which matters
    most on the ``COMPLETED`` path, where the resumed session is reading a
    context summary rather than its own live transcript.
    """
    base = f'Poll answer to your question "{question}": {chosen}'
    if chosen == ESCAPE_HATCH_OPTION:
        return (
            f"{base}\n\n"
            "None of the offered options fit. Send a narrowed plain-text followup "
            "naming exactly what you still need to know; the human answers it with "
            "an ordinary reply, which resumes this same session."
        )
    return base


def _resolve_sender_name(target) -> str:
    """Best-effort voter name for the steer line.

    **No ``GetPollVotesRequest`` attempt.** Polls are sent with
    ``public_voters=False``, and per-voter detail is only retrievable for a
    public poll — so that call can never resolve a voter here. Building it
    anyway would be dead weight on the inbound fast path. Making it work would
    mean publishing every voter's identity to the whole group, a privacy change
    not worth a name in a log line. Verified by the Task 2 gate.
    """
    session = getattr(target, "session", None)
    initial = getattr(session, "initial_telegram_message", None) or {}
    if isinstance(initial, dict):
        name = initial.get("sender_name")
        if name:
            return str(name)
    return "Telegram poll"


async def translate_poll_vote(client, poll_id) -> None:
    """Translate one vote into a steer or a completed-session re-enqueue.

    Idempotent and safe to call from both observers concurrently. Never raises
    into a Telethon update loop or a background task.
    """
    row = lookup_poll(poll_id)
    if row is None:
        # A poll we did not send, or a registry row that has expired.
        logger.debug("translate_poll_vote: no registry row for poll_id=%s", poll_id)
        return

    chat_id = row["chat_id"]
    msg_id = row["msg_id"]
    session_id = row["session_id"]
    options = row["options"]

    # Confirm through GetPollResultsRequest rather than trusting the update:
    # PollResults can arrive with min=True, where the per-option breakdown is
    # absent or account-relative.
    try:
        from telethon.tl.functions.messages import GetPollResultsRequest

        from utils.peer import numeric_peer

        response = await client(GetPollResultsRequest(peer=numeric_peer(chat_id), msg_id=msg_id))
        results = response.updates[0].results
    except Exception as e:  # noqa: BLE001 — a read failure is retried next tick
        logger.warning("translate_poll_vote: GetPollResultsRequest failed for %s: %s", poll_id, e)
        return

    chosen_index = select_option(results, options)
    if chosen_index is None:
        # Return WITHOUT claiming — a spurious update must not consume the
        # one-shot claim and permanently swallow a real answer.
        return

    # ── The one-shot claim, with a dispatched-guarded stale takeover ──
    # An unconditional `return` on a lost claim would make the whole recovery
    # inert for the failure it exists to fix: on a bridge death after the claim
    # no `except` handler ever runs to release it, iter_unanswered_polls
    # dutifully re-yields the row, and this line would bail every tick until the
    # claim TTL expires.
    if not claim_poll_answer(poll_id):
        age = poll_claim_age_s(poll_id)
        if age is None:
            # The key vanished between the SET NX and the GET. Retry once.
            if not claim_poll_answer(poll_id):
                return
        elif age < POLL_RECONCILE_SLOW_INTERVAL_S:
            # A genuine concurrent translator holds a live claim. This is the
            # ordinary race, and returning is correct.
            return
        elif poll_dispatched(poll_id):
            # The side effect already happened. Re-attempt only the completion
            # marker — never re-steer, never re-dispatch. This is the
            # load-bearing half of the guard.
            mark_poll_steered(poll_id)
            return
        elif not takeover_poll_claim(poll_id):
            # A peer took it over first.
            return

    try:
        await _dispatch_answer(
            client,
            poll_id=poll_id,
            row=row,
            chosen=options[chosen_index],
        )
    except Exception as e:  # noqa: BLE001
        # Release the claim so the next reconciliation tick retries — but ONLY
        # when the side effect has not already happened. A blanket release would
        # re-open the mirror failure (one vote, two enqueues) on the COMPLETED
        # branch, which is the mainline.
        if not poll_dispatched(poll_id):
            release_poll_claim(poll_id)
        logger.warning(
            "translate_poll_vote failed for poll_id=%s session_id=%s: %s",
            poll_id,
            session_id,
            e,
        )
        return

    # Written LAST, and only after the steer returned. Both
    # iter_unanswered_polls and poll_expired_unanswered key on its absence.
    mark_poll_steered(poll_id)


async def _dispatch_answer(client, *, poll_id, row: dict, chosen: str) -> None:
    """Close the poll and route the answer. Everything here is inside the claim."""
    from bridge.response import close_poll
    from models.room import room_id_for_session
    from utils.peer import numeric_peer

    chat_id = row["chat_id"]
    msg_id = row["msg_id"]
    session_id = row["session_id"]

    # Closing makes retract-and-revote impossible at the source and gives the
    # human a visible "already answered" state. In a group this is also the
    # first-voter-wins boundary, and that is intended: the poll exists to
    # unblock one agent, not to take a vote of the room.
    try:
        fetched = await client.get_messages(numeric_peer(chat_id), ids=msg_id)
        await close_poll(client, numeric_peer(chat_id), msg_id, getattr(fetched, "media", None))
    except Exception as e:  # noqa: BLE001 — a failure to close never blocks the answer
        logger.debug("translate_poll_vote: could not close poll %s: %s", poll_id, e)

    target = resolve_answer_target(session_id)
    sender_name = _resolve_sender_name(target)
    steer_text = build_steer_text(row["question"], chosen)

    if target.kind in (
        AnswerTargetKind.LIVE,
        AnswerTargetKind.PENDING,
        AnswerTargetKind.LIVE_GUARD,
    ):
        from agent.steering import push_steering_message

        # room_id is MANDATORY, not optional. push_steering_message selects the
        # Room key only when the caller supplies it and deliberately never looks
        # the session up itself; with none it silently writes the legacy
        # steering:{session_id} key while every peer caller writes the Room leg.
        # That is a silent regression, not a crash. We already hold the record,
        # so this is a pure attribute read.
        push_steering_message(
            session_id,
            steer_text,
            sender_name,
            room_id=room_id_for_session(target.session),
        )
        # Marked immediately after the side effect and before anything else that
        # can throw, so a later exception cannot release the claim and re-run it.
        mark_poll_dispatched(poll_id)
        logger.info(
            "poll_vote_steered poll_id=%s session_id=%s kind=%s", poll_id, session_id, target.kind
        )
        # No _ack_steering_routed: there is no inbound message to react to, and
        # closing the poll is already the visible acknowledgment. Also no
        # abort-keyword detection — a poll option is never an abort, and
        # push_steering_message force-routes a detected abort to the legacy key
        # regardless of room_id.
        return

    if target.kind == AnswerTargetKind.COMPLETED:
        # THE MAINLINE, not an edge case. /ask-me ends its turn on the
        # needs_human edge and PM_NEEDS_HUMAN is clean and wrap-up-eligible, so
        # the asking session is finalized "completed" long before a human taps.
        # Dropping this branch loses every vote, not a rare one.
        #
        # A vote has no reply chain, so the summary-only preamble
        # _build_completed_resume_text already produces is the correct output —
        # nothing is being skipped silently, it genuinely does not exist.
        #
        # The poll's own msg_id is a safe dedup key: dispatch_telegram_session
        # claims (chat_id, telegram_message_id) via claim_message, which is
        # inbound-only — an outbound send never claims it — so it is unused,
        # unique and stable for exactly this re-enqueue.
        await resume_completed_session(
            completed=target.session,
            text=steer_text,
            sender_name=sender_name,
            telegram_chat_id=str(chat_id),
            telegram_message_id=msg_id,
            reply_chain_context=None,
        )
        mark_poll_dispatched(poll_id)
        logger.info("poll_vote_resumed_completed poll_id=%s session_id=%s", poll_id, session_id)
        return

    # NONE — log and stop. Never create a session from a vote.
    logger.info(
        "poll_vote_no_target poll_id=%s session_id=%s — no session record, dropping",
        poll_id,
        session_id,
    )
    mark_poll_dispatched(poll_id)
