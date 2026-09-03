"""Poll registry — the routing state that makes an inbound vote addressable.

``UpdateMessagePoll(poll_id, results, poll)`` carries **no peer and no message
id**, so a vote cannot be routed back to a chat or a session from the update
alone. This registry is the missing half: a row written at send time mapping the
server-assigned poll id to everything needed to act on a vote. Without it the
whole inbound path is impossible, which is why it is a first-class module rather
than a few keys tucked into the relay.

**Plain Redis strings, not Popoto.** Two existing registries already do exactly
this shape — ``bridge/job_router.py``'s ``reply:{message_key}`` index and
``bridge/context.py``'s ``session_root:{chat_id}:{msg_id}`` mapping — and the
deliberate non-Popoto choice keeps them outside index drift and
``rebuild_indexes()``. It also means **no** ``scripts/update/migrations.py``
entry: rows are created on demand and expire on their own.

**The descriptor row is immutable; every mutable marker is its own atomic key.**
Storing ``dispatched`` / ``steered_at`` / ``warned`` as fields inside one JSON
value would make every marker write a read-modify-write on a shared key, and the
reconciliation loop's warn write races the fast-path translator's marker writes.
A lost update there either drops ``steered_at`` (the row is re-yielded forever)
or drops ``dispatched`` (the double-enqueue the marker exists to prevent). Each
marker is therefore a single ``SET NX`` command — idempotent under retry with no
read step.

**Enumeration is index-backed, never a keyspace scan.** ``SCAN MATCH`` filters
server-side but still walks every key in the db, and this db is shared with
production Popoto keys — a full-keyspace walk at the fast reconcile interval.
The two index SETs below carry that load instead. The index is a *hint* and the
row stays authoritative: a lost ``SREM`` costs one wasted ``GET``, never a missed
poll, which is why both ``SADD``s live in the same helper as their write.

See ``docs/features/telegram-poll-questions.md``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

POLL_ROW_PREFIX = "telegram:poll:"
POLL_PENDING_PREFIX = "telegram:poll:pending:"
POLL_ANSWERED_PREFIX = "telegram:poll:answered:"
POLL_DISPATCHED_PREFIX = "telegram:poll:dispatched:"
POLL_STEERED_PREFIX = "telegram:poll:steered_at:"
POLL_WARNED_PREFIX = "telegram:poll:warned:"

#: Index SETs backing the two iterators. No TTL of their own — members are
#: swept lazily when their row is found expired (skip-and-``SREM``).
POLL_OPEN_INDEX = "telegram:poll:open"
POLL_PENDING_INDEX = "telegram:poll:pending:index"

RECONCILE_HEARTBEAT_KEY = "telegram:poll:reconcile:heartbeat"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
# All provisional and tunable — grain of salt. Every one is env-overridable so a
# machine can adjust without a code change.


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


#: How long a poll stays routable. Longer than any plausible human response
#: window; a question older than this is stale enough that answering it would
#: surprise the asker.
POLL_REGISTRY_TTL_S = _int_env("POLL_REGISTRY_TTL_S", 86400)

#: One-shot answer claim. Aligned with the row — a claim outliving its row would
#: block a re-registered poll.
POLL_ANSWER_CLAIM_TTL_S = _int_env("POLL_ANSWER_CLAIM_TTL_S", 86400)

#: Reconciliation cadence. Fast for the first couple of minutes after a send
#: (when a tap is most likely), slow thereafter.
POLL_RECONCILE_FAST_INTERVAL_S = _int_env("POLL_RECONCILE_FAST_INTERVAL_S", 5)
POLL_RECONCILE_SLOW_INTERVAL_S = _int_env("POLL_RECONCILE_SLOW_INTERVAL_S", 60)
POLL_RECONCILE_FAST_WINDOW_S = _int_env("POLL_RECONCILE_FAST_WINDOW_S", 120)

#: Heartbeat TTL — 2x the slow interval, so one missed tick is not a false alarm
#: but a dead loop surfaces within two.
POLL_RECONCILE_HEARTBEAT_TTL_S = _int_env(
    "POLL_RECONCILE_HEARTBEAT_TTL_S", POLL_RECONCILE_SLOW_INTERVAL_S * 2
)

#: Age at which an unanswered poll emits the operator signal.
POLL_EXPIRY_WARN_AGE_S = _int_env("POLL_EXPIRY_WARN_AGE_S", 3600)

#: How long the gate probes wait for a human tap.
POLL_PROBE_TAP_WAIT_S = _int_env("POLL_PROBE_TAP_WAIT_S", 1800)

#: How far back the orphan-adoption / already-sent scan looks in a chat's own
#: outbound history. Bounded on purpose — it runs per relay retry and per
#: reconcile tick with a surviving provisional row. Provisional and tunable.
POLL_ADOPTION_SCAN_LIMIT = _int_env("POLL_ADOPTION_SCAN_LIMIT", 50)


def _redis():
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _decode(value) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


# ---------------------------------------------------------------------------
# Descriptor rows
# ---------------------------------------------------------------------------


def register_poll(
    poll_id: int | str,
    *,
    chat_id: int,
    msg_id: int,
    session_id: str,
    question: str,
    options: list[str],
) -> bool:
    """Write the immutable descriptor row and index it, in one call.

    The ``SADD`` is deliberately in the same helper as the ``SET``: a lost
    ``SADD`` is the only way to lose a poll, so the two must not be separable by
    a caller who forgets one.
    """
    r = _redis()
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "msg_id": msg_id,
            "session_id": session_id,
            "question": question,
            "options": list(options),
            "created_at": _now_iso(),
        }
    )
    written = bool(r.set(f"{POLL_ROW_PREFIX}{poll_id}", payload, nx=True, ex=POLL_REGISTRY_TTL_S))
    r.sadd(POLL_OPEN_INDEX, str(poll_id))
    return written


def lookup_poll(poll_id: int | str) -> dict | None:
    """Read a descriptor row, or ``None`` for a poll we did not send / an expired row."""
    raw = _decode(_redis().get(f"{POLL_ROW_PREFIX}{poll_id}"))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("poll registry row for %s is unparseable", poll_id)
        return None


def register_pending_poll(
    poll_id_hint: str,
    *,
    chat_id: int,
    session_id: str,
    question: str,
    options: list[str],
) -> bool:
    """Write the Race-6 provisional row, before the send.

    The server assigns the real poll id, so a complete row genuinely cannot
    precede the send. This provisional row is the evidence that a send *may*
    have landed, and it is what orphan adoption adopts after a restart in the
    window between ``send_poll`` returning and the real row being written.
    """
    r = _redis()
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "session_id": session_id,
            "question": question,
            "options": list(options),
            "created_at": _now_iso(),
        }
    )
    written = bool(
        r.set(f"{POLL_PENDING_PREFIX}{poll_id_hint}", payload, nx=True, ex=POLL_REGISTRY_TTL_S)
    )
    r.sadd(POLL_PENDING_INDEX, poll_id_hint)
    return written


def promote_pending_poll(
    poll_id_hint: str,
    poll_id: int | str,
    *,
    msg_id: int,
) -> bool:
    """Promote a provisional row to a real one once the server poll id is known."""
    pending = lookup_pending_poll(poll_id_hint)
    if pending is None:
        logger.warning("promote_pending_poll: no provisional row for hint %s", poll_id_hint)
        return False
    written = register_poll(
        poll_id,
        chat_id=pending["chat_id"],
        msg_id=msg_id,
        session_id=pending["session_id"],
        question=pending["question"],
        options=pending["options"],
    )
    if not written:
        # poll_id is already registered under a different hint — the adoption
        # match that fed us this poll_id was wrong. Leave the pending row
        # intact so the question stays adoptable; do not clobber the other
        # hint's row.
        logger.warning(
            "promote_pending_poll: poll_id %s already registered, refusing to adopt for hint %s",
            poll_id,
            poll_id_hint,
        )
        return False
    # Carry a prose-answer close-out forward. The hint may have been marked
    # steered by `mark_session_polls_steered` while this payload was still in
    # the outbox; without this the promoted row is born unsteered and re-opens
    # the pause on a question the human already answered.
    if poll_steered(poll_id_hint):
        mark_poll_steered(poll_id)
    delete_pending_poll(poll_id_hint)
    return True


def lookup_pending_poll(poll_id_hint: str) -> dict | None:
    raw = _decode(_redis().get(f"{POLL_PENDING_PREFIX}{poll_id_hint}"))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def delete_pending_poll(poll_id_hint: str) -> None:
    """Drop a provisional row and de-index it.

    Called on promotion and on **every** decline branch of the relay's poll
    send, so a declined send never ages into a spurious orphan-adoption
    candidate.
    """
    r = _redis()
    r.delete(f"{POLL_PENDING_PREFIX}{poll_id_hint}")
    r.srem(POLL_PENDING_INDEX, poll_id_hint)


# ---------------------------------------------------------------------------
# Markers — each its own atomic key
# ---------------------------------------------------------------------------


def claim_poll_answer(poll_id: int | str) -> bool:
    """Take the one-shot translation claim. ``True`` when this caller won it.

    **The value is the ISO-8601 claim timestamp, never the constant 1.** Without
    a readable value a stale claim is indistinguishable from a live one, and the
    bridge-death recovery cannot execute at all: nothing releases a claim when
    the process dies holding it, so an unconditional bail on a lost claim would
    re-lose the vote on every tick until the TTL.
    """
    return bool(
        _redis().set(
            f"{POLL_ANSWERED_PREFIX}{poll_id}", _now_iso(), nx=True, ex=POLL_ANSWER_CLAIM_TTL_S
        )
    )


def poll_claim_age_s(poll_id: int | str) -> float | None:
    """Age of the current claim in seconds, or ``None`` when the key is absent."""
    raw = _decode(_redis().get(f"{POLL_ANSWERED_PREFIX}{poll_id}"))
    if not raw:
        return None
    try:
        claimed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - claimed).total_seconds()


def takeover_poll_claim(poll_id: int | str) -> bool:
    """Take over a stale claim by re-stamping it. ``XX`` — never resurrects an expired one.

    Emits ``poll_claim_takeover`` before proceeding so the accepted residual in
    the bridge-death recovery is diagnosable from logs. That residual: the
    ``dispatched`` marker is written one Redis command *after* the steer
    returns, so a death inside that window leaves a claim with no marker over a
    side effect that already happened, and this takeover will steer a second
    time. It is accepted deliberately — the alternative is never taking over,
    which is the permanent swallow this recovery exists to fix, and a duplicate
    resume of a completed session is degraded-but-safe while a swallowed
    question is not.
    """
    age = poll_claim_age_s(poll_id)
    logger.warning(
        "poll_claim_takeover poll_id=%s claim_age_s=%s dispatched=%s",
        poll_id,
        f"{age:.1f}" if age is not None else "unknown",
        poll_dispatched(poll_id),
    )
    return bool(
        _redis().set(
            f"{POLL_ANSWERED_PREFIX}{poll_id}", _now_iso(), xx=True, ex=POLL_ANSWER_CLAIM_TTL_S
        )
    )


def release_poll_claim(poll_id: int | str) -> None:
    """Delete the claim so the next reconciliation tick retries.

    Callers must check ``poll_dispatched`` first: releasing after a successful
    side effect re-runs the dispatch, which on the ``COMPLETED`` mainline means
    one vote and two enqueues.
    """
    _redis().delete(f"{POLL_ANSWERED_PREFIX}{poll_id}")


def mark_poll_dispatched(poll_id: int | str) -> None:
    """Record that the steer / re-enqueue side effect has happened.

    Distinct from ``steered_at``: this one bounds the claim release and must
    never be repeated. Collapsing the two markers re-opens either the swallowed
    question or the double-enqueue.
    """
    _redis().set(f"{POLL_DISPATCHED_PREFIX}{poll_id}", _now_iso(), nx=True, ex=POLL_REGISTRY_TTL_S)


def poll_dispatched(poll_id: int | str) -> bool:
    return bool(_redis().exists(f"{POLL_DISPATCHED_PREFIX}{poll_id}"))


def mark_poll_steered(poll_id: int | str) -> None:
    """Record that the translation completed cleanly, and de-index the poll.

    Written **only after** the steer returns. Both ``iter_unanswered_polls`` and
    the ``poll_expired_unanswered`` operator signal key on this marker's absence
    — deliberately, rather than on a missing claim, or the signal would be blind
    to a claim that survived a failure.

    Only ``POLL_OPEN_INDEX`` is de-indexed. Called with a *hint* (the pending-row
    close-out below), the row deliberately stays in ``POLL_PENDING_INDEX`` until
    its TTL: the payload may still be in the relay outbox, and ``promote_pending_poll``
    needs the row to carry this marker onto the real poll id. A consumer of that
    index that cares therefore checks ``poll_steered`` itself —
    ``session_has_open_poll`` does, to stop reporting an answered question.
    ``adopt_orphaned_polls`` deliberately does **not**: adoption is the only thing
    that makes a Race-6 orphan reachable by a later tap at all, so skipping a
    marked hint would let an unrelated inbound message swallow that tap.
    """
    _redis().set(f"{POLL_STEERED_PREFIX}{poll_id}", _now_iso(), nx=True, ex=POLL_REGISTRY_TTL_S)
    _redis().srem(POLL_OPEN_INDEX, str(poll_id))


def mark_session_polls_steered(session_id: str | None) -> int:
    """Close out every outstanding poll for ``session_id``. Returns how many.

    ``mark_poll_steered`` is written only by the vote path, so a human who
    *types* their answer instead of tapping leaves the row open for the full
    ``POLL_REGISTRY_TTL_S``. ``session_has_open_poll`` then keeps reporting a
    question that has already been answered, the nudge loop takes the pause
    branch every turn, and the session loses auto-continue for a day. Every
    inbound answer route calls this, not just the tap.

    A provisional row is marked by **hint** rather than deleted: the hint is
    still the only evidence that a send may have landed, so orphan adoption
    needs the row. ``promote_pending_poll`` carries the marker onto the real
    poll id.

    **Never raises** — a bookkeeping failure must not break the answer route it
    hangs off.
    """
    if not session_id:
        return 0
    closed = 0
    try:
        # Pending FIRST, open second — the order is load-bearing against a
        # concurrent `promote_pending_poll`. Promoting reads `poll_steered(hint)`
        # to carry the close-out onto the real poll id, then deletes the hint. Scan
        # the open index first and a promotion landing between the two loops adds
        # its row after loop 1 has passed and deletes the hint before loop 2
        # reaches it, so neither loop sees it and the close-out is lost. This way
        # round, either the hint is marked before the promotion reads it, or the
        # promotion already ran and the real row is in the open index for loop 2.
        for hint, row in iter_pending_polls():
            if row.get("session_id") == session_id and not poll_steered(hint):
                mark_poll_steered(hint)
                closed += 1
        for poll_id, row in iter_unanswered_polls():
            if row.get("session_id") == session_id:
                mark_poll_steered(poll_id)
                closed += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("mark_session_polls_steered failed for %s: %s", session_id, exc)
    if closed:
        logger.info("poll_closed_by_prose_answer session_id=%s polls=%d", session_id, closed)
    return closed


def poll_steered(poll_id: int | str) -> bool:
    return bool(_redis().exists(f"{POLL_STEERED_PREFIX}{poll_id}"))


def mark_poll_warned(poll_id: int | str) -> bool:
    """Deduplicate the ``poll_expired_unanswered`` warning. ``True`` on first warn."""
    return bool(
        _redis().set(f"{POLL_WARNED_PREFIX}{poll_id}", "1", nx=True, ex=POLL_REGISTRY_TTL_S)
    )


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def iter_unanswered_polls() -> Iterator[tuple[str, dict]]:
    """Yield ``(poll_id, row)`` for every registered poll with no ``steered_at``.

    A row whose claim exists but is **older than one slow reconcile interval**
    is still yielded: that is the bridge-death state, and the translator takes
    the stale claim over. Treating "claim present" as answered would make the
    recovery inert.
    """
    r = _redis()
    for member in r.sscan_iter(POLL_OPEN_INDEX):
        poll_id = _decode(member)
        if not poll_id:
            continue
        row = lookup_poll(poll_id)
        if row is None:
            # Row expired out from under the index. The row is authoritative;
            # sweep the stale member and move on.
            r.srem(POLL_OPEN_INDEX, poll_id)
            continue
        if poll_steered(poll_id):
            r.srem(POLL_OPEN_INDEX, poll_id)
            continue
        yield (poll_id, row)


def iter_pending_polls() -> Iterator[tuple[str, dict]]:
    """Yield ``(poll_id_hint, row)`` for every surviving Race-6 provisional row."""
    r = _redis()
    for member in r.sscan_iter(POLL_PENDING_INDEX):
        hint = _decode(member)
        if not hint:
            continue
        row = lookup_pending_poll(hint)
        if row is None:
            r.srem(POLL_PENDING_INDEX, hint)
            continue
        yield (hint, row)


def session_has_open_poll(session_id: str | None) -> bool:
    """Whether ``session_id`` has an outstanding, unanswered poll.

    The open-question read the nudge-loop pause branch is conditioned on. It
    introduces no new state — it reads exactly what the registry already writes.

    Both indexes are consulted. A poll enqueued by ``valor-ask-poll`` but not yet
    picked up by the relay exists only as a provisional row, and the relay is a
    polling loop while this predicate runs at turn end — so the window is real,
    and during it a session with an outstanding question would otherwise be
    nudged straight past it.

    **Never raises.** Any failure logs at warning and returns ``False``, so a
    Redis hiccup degrades to today's nudge behavior rather than wedging a
    session that has no outstanding question.
    """
    if not session_id:
        return False
    try:
        for _poll_id, row in iter_unanswered_polls():
            if row.get("session_id") == session_id:
                return True
        for hint, row in iter_pending_polls():
            if row.get("session_id") == session_id and not poll_steered(hint):
                return True
        return False
    except Exception as exc:  # noqa: BLE001 — a routing read must never wedge the nudge loop
        logger.warning("session_has_open_poll failed for %s: %s", session_id, exc)
        return False
