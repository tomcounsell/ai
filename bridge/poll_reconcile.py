"""Poll reconciliation loop — the PRIMARY inbound mechanism, not a backstop.

``UpdateMessagePoll`` is not self-routing (no peer, no message id), so the
``events.Raw`` handler in ``bridge/telegram_bridge.py`` is a latency win layered
on top of this loop rather than the mechanism. Making ``GetPollResultsRequest``
reconciliation primary is what gives the feature restart-survivability, tolerance
of a dropped update, and independence from the un-gated question of whether the
push update reaches a user account at all.

Both paths call the same idempotent ``translate_poll_vote``, so adding the fast
path cannot introduce a second behavior.

The loop body lives here rather than inlined into ``bridge/telegram_bridge.py``,
which only imports and starts it next to ``relay_loop``.

See ``docs/features/telegram-poll-questions.md``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from telethon.errors import FloodWaitError

from bridge.poll_registry import (
    POLL_EXPIRY_WARN_AGE_S,
    POLL_RECONCILE_FAST_INTERVAL_S,
    POLL_RECONCILE_FAST_WINDOW_S,
    POLL_RECONCILE_HEARTBEAT_TTL_S,
    POLL_RECONCILE_SLOW_INTERVAL_S,
    RECONCILE_HEARTBEAT_KEY,
    _redis,
    iter_pending_polls,
    iter_unanswered_polls,
    mark_poll_warned,
    poll_steered,
    promote_pending_poll,
)

logger = logging.getLogger(__name__)

#: Consecutive GetPollResultsRequest failures before warning. Provisional.
POLL_RECONCILE_FAILURE_WARN_STREAK = 3


def _row_age_s(row: dict) -> float:
    try:
        created = datetime.fromisoformat(row["created_at"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (datetime.now(UTC) - created).total_seconds()


def write_heartbeat() -> None:
    """Stamp the loop's external liveness signal.

    Written at the top of **every** tick, before the scan. This is the only
    detector for "the loop itself died": ``poll_expired_unanswered`` is emitted
    from *inside* ``iter_unanswered_polls``, so if the loop is what died that
    signal cannot fire. A detector that lives inside the thing it detects is not
    a detector.

    Read by ``ui/app.py``'s ``/dashboard.json`` health payload, mirroring the
    existing email-bridge heartbeat check in that file.
    """
    try:
        _redis().set(
            RECONCILE_HEARTBEAT_KEY,
            datetime.now(UTC).isoformat(),
            ex=POLL_RECONCILE_HEARTBEAT_TTL_S,
        )
    except Exception as e:  # noqa: BLE001 — a liveness write must never kill the loop
        logger.debug("poll reconcile heartbeat write failed: %s", e)


def warn_if_expired_unanswered(poll_id: str, row: dict) -> None:
    """Emit the operator signal for the inbound half, exactly once per poll.

    **Keys on the absent ``steered_at`` marker, never on a missing claim.** A
    claim that survived a failure would otherwise make this signal blind to
    precisely the permanently-swallowed question it exists to surface.

    Deduplicated by ``mark_poll_warned`` — an atomic ``SET NX`` on its own key,
    never a field rewritten onto the descriptor row, which would race the
    translator's own marker writes.
    """
    age = _row_age_s(row)
    if age < POLL_EXPIRY_WARN_AGE_S or poll_steered(poll_id):
        return
    if not mark_poll_warned(poll_id):
        return
    logger.warning(
        "poll_expired_unanswered poll_id=%s chat_id=%s session_id=%s age_s=%.0f",
        poll_id,
        row.get("chat_id"),
        row.get("session_id"),
        age,
    )


async def adopt_orphaned_polls(client) -> None:
    """Adopt Race-6 provisional rows whose real row was lost to a restart.

    ``process_outbox`` consumes the work item with an atomic LPOP and the server
    assigns the poll id only after the send returns, so a restart between the
    send and the registry write leaves a poll visibly on screen with nothing able
    to route its vote — permanent loss, distinct from Race 1 (self-healing) and
    Race 5 (assumes the write landed).

    Matched on the correlation id **embedded in the poll's option bytes**, never
    on question text: an agent re-asking after an expired poll, or two sessions
    asking the same standard question in one chat, produce two candidates with no
    tie-break. On more than one match, adopt **nothing** and warn — an ambiguous
    adoption steers a session with someone else's answer, which is worse than a
    dropped question.
    """
    from bridge.telegram_relay import _find_already_sent_poll
    from utils.peer import numeric_peer

    for hint, row in iter_pending_polls():
        try:
            found = await _find_already_sent_poll(client, numeric_peer(row["chat_id"]), hint)
        except Exception as e:  # noqa: BLE001
            logger.warning("poll adoption scan failed for hint %s: %s", hint, e)
            continue
        if found is None:
            continue
        msg_id, server_poll_id = found
        promoted = promote_pending_poll(hint, server_poll_id, msg_id=msg_id)
        if promoted:
            logger.info(
                "poll_orphan_adopted hint=%s poll_id=%s msg_id=%s", hint, server_poll_id, msg_id
            )


async def reconcile_once(client) -> int:
    """One reconciliation pass. Returns the number of rows examined."""
    write_heartbeat()

    examined = 0
    failure_streak = 0
    from bridge.poll_vote import translate_poll_vote

    for poll_id, row in iter_unanswered_polls():
        examined += 1
        warn_if_expired_unanswered(poll_id, row)
        try:
            await translate_poll_vote(client, poll_id)
            failure_streak = 0
        except FloodWaitError:
            raise
        except Exception as e:  # noqa: BLE001
            failure_streak += 1
            logger.debug("reconcile: translate failed for %s: %s", poll_id, e)
            if failure_streak >= POLL_RECONCILE_FAILURE_WARN_STREAK:
                logger.warning("poll_reconcile_failures streak=%s last_error=%s", failure_streak, e)
                failure_streak = 0

    await adopt_orphaned_polls(client)
    return examined


def _interval(newest_row_age_s: float | None) -> float:
    """Adaptive cadence: fast right after a send, slow thereafter.

    A tap is most likely in the first couple of minutes; polling every 5s for a
    day would be a needless load on both Redis and Telegram.
    """
    if newest_row_age_s is not None and newest_row_age_s < POLL_RECONCILE_FAST_WINDOW_S:
        return POLL_RECONCILE_FAST_INTERVAL_S
    return POLL_RECONCILE_SLOW_INTERVAL_S


async def poll_reconcile_loop(client) -> None:
    """Run reconciliation forever. Never raises out; never dies quietly."""
    logger.info("Poll reconciliation loop started")
    while True:
        interval = POLL_RECONCILE_SLOW_INTERVAL_S
        try:
            await reconcile_once(client)
            youngest = None
            for _pid, row in iter_unanswered_polls():
                age = _row_age_s(row)
                youngest = age if youngest is None else min(youngest, age)
            interval = _interval(youngest)
        except FloodWaitError as flood:
            # Mirror the relay's backoff shape: back off and CONTINUE. Dying
            # here would take the primary inbound mechanism with it.
            wait = min(flood.seconds + 5, 300)
            logger.warning(
                "Poll reconcile: FloodWaitError, Telegram requests %ss — sleeping %ss",
                flood.seconds,
                wait,
            )
            interval = wait
        except asyncio.CancelledError:
            logger.info("Poll reconciliation loop cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("Poll reconcile tick failed: %s", e, exc_info=True)
        await asyncio.sleep(interval)


def heartbeat_age_s() -> float | None:
    """Age of the reconcile heartbeat in seconds, or ``None`` when absent.

    The read side of the loop's liveness signal, used by the dashboard health
    payload. ``None`` means degraded: either the loop never started or it has
    been dead for longer than the heartbeat TTL.
    """
    try:
        raw = _redis().get(RECONCILE_HEARTBEAT_KEY)
        if not raw:
            return None
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        stamped = datetime.fromisoformat(value)
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=UTC)
        return (datetime.now(UTC) - stamped).total_seconds()
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "adopt_orphaned_polls",
    "heartbeat_age_s",
    "poll_reconcile_loop",
    "reconcile_once",
    "warn_if_expired_unanswered",
    "write_heartbeat",
]
