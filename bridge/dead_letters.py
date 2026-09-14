"""The dead-letter sink and its per-stage replay handlers.

Every terminal sink in the pipeline calls :func:`record`. A row names its
``stage``, carries the payload that was lost, and says whether replaying it
means anything. :data:`HANDLERS` maps a stage to the function that re-runs
one row; the ``dead-letter-replay`` reflection walks the replayable rows and
hands each to its handler, then evicts the overflow past
:data:`DEAD_LETTER_STAGE_CAP`.

Two indexes ride alongside the rows, both plain (non-Popoto) keys reached
through ``utils.redis_client.text_redis()``:

``{project}:dead_letters:count``
    A hash of stage -> row count. Advisory: TTL expiry removes rows without
    decrementing it, so the eviction pass reconciles it from the index each
    time it runs.
``{project}:dead_letters:{stage}``
    A sorted set of ``letter_id`` scored by first-seen epoch. This is the
    eviction *index*; the rows themselves are only ever removed through the
    ORM (``instance.delete()``), never by a raw Redis delete.

Both writes are O(log n) and run on the per-message failure branch, so the
flood the cap exists for cannot turn the cap into the bottleneck. Nothing
here runs a census or an ordering pass at ``record()`` time.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from models.dead_letter import DeadLetter
from utils.peer import numeric_peer
from utils.utc import utc_now

logger = logging.getLogger(__name__)

# Every stage a row can carry. A stage outside this set is a typo, and a typo
# would silently create a shard nothing replays and no tile shows.
STAGES: frozenset[str] = frozenset(
    {
        "telegram_send",
        "email_send",
        "outbox_parse",
        "steering_parse",
        "notify_parse",
        "extraction",
        "session_recovery_cap",
        "session_init_hang",
        "session_corrupt_row",
        "archive_restore",
        # Reserved for the improvement control plane (#3177).
        "improve_intent",
    }
)

# Replay attempts a row gets before ``replayable`` is flipped off and it is
# left to age out under the model's TTL.
MAX_REPLAY_ATTEMPTS = 3

# Per-stage row ceiling (Risk 4). Enforced by the eviction pass in the
# ``dead-letter-replay`` reflection, never on the write path.
DEAD_LETTER_STAGE_CAP = 10_000


def _project_namespace(project_key: str | None) -> str:
    return project_key or "valor"


def count_key(project_key: str | None) -> str:
    """Redis key of the advisory per-stage row-count hash."""
    return f"{_project_namespace(project_key)}:dead_letters:count"


def index_key(project_key: str | None, stage: str) -> str:
    """Redis key of the per-stage eviction index (a sorted set of row ids)."""
    return f"{_project_namespace(project_key)}:dead_letters:{stage}"


def _bump_indexes(project_key: str | None, stage: str, letter_id: str, score: float) -> None:
    """Write the two O(log n) index entries. Never raises."""
    try:
        from utils.redis_client import text_redis

        client = text_redis()
        client.hincrby(count_key(project_key), stage, 1)
        client.zadd(index_key(project_key, stage), {str(letter_id): score})
    except Exception as e:  # noqa: BLE001 -- an index write must never break a sink
        logger.debug("dead-letter index write failed for stage=%s (non-fatal): %s", stage, e)


def record(
    stage: str,
    payload: Any = None,
    reason: str = "",
    *,
    replayable: bool = True,
    project_key: str | None = None,
    chat_id: str | None = None,
    reply_to: int | None = None,
    text: str | None = None,
    attempts: int = 0,
) -> DeadLetter:
    """Persist one dead letter. Synchronous; returns the created row.

    ``payload`` is JSON-encoded when it is a dict or list and stored verbatim
    when it is already a string (an unparseable wire payload *is* the string
    that failed to parse, and re-encoding it would lose that).

    Raises ``ValueError`` on an empty or unknown ``stage``: a mislabelled row
    is invisible to both the dashboard tile and the replayer, which is worse
    than a loud failure. Every other error belongs to the caller; callers on a
    hot path wrap this in their own ``try``.
    """
    if not stage:
        raise ValueError("dead_letters.record() requires a stage")
    if stage not in STAGES:
        raise ValueError(
            f"dead_letters.record(): unknown stage {stage!r} (see dead_letters.STAGES)"
        )

    if payload is None:
        payload_json = None
    elif isinstance(payload, str):
        payload_json = payload
    else:
        try:
            payload_json = json.dumps(payload, default=str)
        except (TypeError, ValueError):
            payload_json = repr(payload)

    now = utc_now()
    letter = DeadLetter.create(
        stage=stage,
        chat_id=str(chat_id) if chat_id is not None else None,
        project_key=project_key,
        reply_to=reply_to,
        text=text,
        payload_json=payload_json,
        reason=reason or "",
        replayable=replayable,
        first_seen=now,
        last_seen=now,
        created_at=time.time(),
        attempts=attempts,
    )
    _bump_indexes(project_key, stage, letter.letter_id, now.timestamp())
    logger.warning("Dead letter recorded: stage=%s reason=%s", stage, reason)
    return letter


async def arecord(*args, **kwargs) -> DeadLetter:
    """:func:`record` for async callers: the Redis writes go to a thread."""
    import asyncio

    return await asyncio.to_thread(lambda: record(*args, **kwargs))


async def persist_failed_delivery(
    chat_id: int,
    reply_to: int | None,
    text: str,
    *,
    reason: str = "relay retry cap exceeded",
    attempts: int = 0,
    project_key: str | None = None,
) -> None:
    """Persist a failed Telegram delivery as a ``telegram_send`` dead letter."""
    await arecord(
        "telegram_send",
        None,
        reason,
        replayable=True,
        project_key=project_key,
        chat_id=str(chat_id),
        reply_to=reply_to,
        text=text,
        attempts=attempts,
    )
    logger.warning(f"Persisted dead letter for chat {chat_id} ({len(text)} chars)")


async def replay_dead_letters(client) -> int:
    """Replay pending ``telegram_send`` dead letters. Returns the count sent.

    This is the ``telegram_send`` replay path; the bridge connect sequence
    also calls it directly as an eager first pass.
    """
    letters = [
        letter
        for letter in await DeadLetter.query.async_all()
        if (getattr(letter, "stage", None) or "telegram_send") == "telegram_send"
    ]
    if not letters:
        return 0

    logger.info(f"Replaying {len(letters)} dead letter(s)...")
    replayed = 0

    for letter in letters:
        chat_id = letter.chat_id
        text = letter.text or ""

        if not chat_id or not text:
            await letter.async_delete()
            continue

        # Guard against peers Telegram cannot accept. Clean up any stuck dead
        # letters from previous relay bugs.
        # Narrowed from <= 0 in lockstep with telegram_relay.py:_dead_letter_message —
        # group/supergroup IDs are legitimately negative (#1749 defect 3).
        # The parse is `utils.peer`'s, the same one every send path and the
        # persist side use. A local `int()` here disagreed with all of them:
        # `int("+5")` is 5, so a stored record with chat_id="+5" was replayed to
        # peer 5 while every send path would have dropped it. Unparseable and
        # zero collapse to one branch — `numeric_peer` returns None for the
        # former, which the old `except -> 0` was already folding into the
        # latter, so the outcome is unchanged for every other input (#2644).
        chat_id_int = numeric_peer(chat_id)
        if chat_id_int is None or chat_id_int == 0:
            logger.warning(
                f"Dead letter replay: discarding record with an undeliverable peer "
                f"(not a valid Telegram peer): {chat_id!r}"
            )
            await letter.async_delete()
            continue

        try:
            if len(text) > 4096:
                text = text[:4093] + "..."
            await client.send_message(chat_id_int, text, reply_to=letter.reply_to)
            await letter.async_delete()
            replayed += 1
            logger.info(f"Replayed dead letter to chat {chat_id}")
        except Exception as e:
            logger.error(f"Dead letter replay failed for chat {chat_id}: {e}")
            _fail_replay(letter, str(e))
            await letter.async_save()

    remaining = len(letters) - replayed
    logger.info(f"Dead letter replay: {replayed} sent, {remaining} remaining")
    return replayed


async def _replay_side_effect(letter: DeadLetter) -> bool:
    """Re-enqueue a side-effect job whose handler exhausted its attempts.

    Declines (returns ``False``) when the reconstructed payload cannot
    satisfy the handler's signature. Re-enqueueing it anyway would recreate
    the identical job, which fails identically, dead-letters identically, and
    is offered for replay again -- a self-sustaining cycle with no exit,
    because a successful re-enqueue always deletes the letter (see
    ``replay_stage``) regardless of whether the new job can actually run.
    The concrete case this closes: the ``/update`` back-enqueue minting a
    ``memory_extraction`` job with no ``response_text`` in its payload, which
    used to regenerate itself roughly every 12 minutes on every fleet
    machine (#3183 review). Declining routes the row through `_fail_replay` /
    `MAX_REPLAY_ATTEMPTS` instead, so it eventually stops being offered.
    """
    import asyncio
    import inspect

    from agent.side_effects import enqueue, resolve_handler

    payload = json.loads(letter.payload_json or "{}")
    kind = payload.pop("_kind", "memory_extraction")
    session_id = payload.pop("_session_id", None)
    if not session_id:
        return False

    handler = resolve_handler(kind)
    if handler is not None:
        try:
            inspect.signature(handler).bind(session_id, **(payload or {}))
        except TypeError as e:
            logger.warning(
                "Dead-letter replay declined for %s/%s: payload cannot satisfy "
                "the %s handler's signature (%s)",
                kind,
                session_id,
                kind,
                e,
            )
            return False

    await asyncio.to_thread(
        lambda: enqueue(kind, session_id, letter.project_key or "", payload or None)
    )
    return True


# stage -> a coroutine taking one row and returning True when it was replayed.
# ``telegram_send`` is not here: its replay is the batch function above, which
# owns its own row deletion and needs a Telethon client. ``replay_stage``
# routes that stage there.
HANDLERS: dict[str, Callable[[DeadLetter], Any]] = {
    "extraction": _replay_side_effect,
}


async def replay_stage(stage: str, client=None) -> int:
    """Replay every replayable row of ``stage``. Returns the count replayed."""
    if stage == "telegram_send":
        if client is None:
            logger.warning("replay_stage('telegram_send') needs a Telethon client; skipping")
            return 0
        return await replay_dead_letters(client)

    handler = HANDLERS.get(stage)
    if handler is None:
        logger.debug("No replay handler registered for dead-letter stage %s", stage)
        return 0

    replayed = 0
    for letter in await DeadLetter.query.async_all():
        if (getattr(letter, "stage", None) or "") != stage:
            continue
        if not is_replayable(letter):
            continue
        try:
            if await handler(letter):
                await letter.async_delete()
                replayed += 1
                continue
            _fail_replay(letter, "handler declined")
        except Exception as e:  # noqa: BLE001 -- one bad row never stops the stage
            _fail_replay(letter, str(e))
        await letter.async_save()
    return replayed


def is_replayable(letter: DeadLetter) -> bool:
    """Strict bool for ``replayable``, which Popoto can hand back as a string."""
    value = getattr(letter, "replayable", True)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _fail_replay(letter: DeadLetter, error: str) -> None:
    """Count one failed replay on the row; retire it at the attempt cap."""
    letter.attempts = (letter.attempts or 0) + 1
    letter.last_seen = utc_now()
    if letter.attempts >= MAX_REPLAY_ATTEMPTS:
        letter.replayable = False
    logger.warning(
        "Dead-letter replay failed (stage=%s attempt=%s): %s",
        getattr(letter, "stage", "?"),
        letter.attempts,
        error,
    )


def evict_overflow(project_key: str | None = None) -> int:
    """Trim each stage back to :data:`DEAD_LETTER_STAGE_CAP`. Returns rows deleted.

    Runs on the replay reflection's cadence, never on the write path. The
    count hash is reconciled from the index each pass because TTL expiry
    removes rows without touching it.
    """
    from utils.redis_client import text_redis

    deleted = 0
    client = text_redis()
    counts = count_key(project_key)
    for stage in sorted(STAGES):
        index = index_key(project_key, stage)
        try:
            live = int(client.zcard(index) or 0)
        except Exception as e:  # noqa: BLE001 -- index unreadable; nothing to trim
            logger.debug("dead-letter eviction: index size read failed for %s: %s", index, e)
            continue
        overflow = live - DEAD_LETTER_STAGE_CAP
        if overflow > 0:
            for letter_id in client.zrange(index, 0, overflow - 1) or []:
                row = DeadLetter.query.get(letter_id=letter_id)
                if row is not None:
                    row.delete()
                    deleted += 1
                client.zrem(index, letter_id)
            live = int(client.zcard(index) or 0)
        # Reconcile the advisory counter from the index rather than trusting it.
        if live:
            client.hset(counts, stage, live)
        else:
            client.hdel(counts, stage)
    return deleted


def counts_by_stage(project_key: str | None = None) -> dict[str, int]:
    """Row count per stage, read from the advisory hash. Never raises."""
    try:
        from utils.redis_client import text_redis

        raw = text_redis().hgetall(count_key(project_key)) or {}
        return {stage: int(value) for stage, value in raw.items()}
    except Exception as e:  # noqa: BLE001 -- dashboards degrade, they do not crash
        logger.debug("dead-letter counts read failed: %s", e)
        return {}
