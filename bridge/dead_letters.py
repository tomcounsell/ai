"""Dead-letter queue for failed Telegram message deliveries.

When the relay (``bridge/telegram_relay.py``) fails to deliver a message after
exhausting its retry budget, the payload is persisted here. On bridge startup,
pending dead letters are replayed.

Uses popoto Redis model for atomic persistence (no file race conditions).
"""

import logging
import time

from models.dead_letter import DeadLetter
from utils.peer import numeric_peer

logger = logging.getLogger(__name__)


async def persist_failed_delivery(
    chat_id: int,
    reply_to: int | None,
    text: str,
) -> None:
    """Persist a failed delivery to Redis via DeadLetter model."""
    await DeadLetter.async_create(
        chat_id=str(chat_id),
        reply_to=reply_to,
        text=text,
        created_at=time.time(),
        attempts=0,
    )
    logger.warning(f"Persisted dead letter for chat {chat_id} ({len(text)} chars)")


async def replay_dead_letters(client) -> int:
    """Replay all pending dead letters. Returns count of successfully replayed."""
    letters = await DeadLetter.query.async_all()
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
            letter.attempts = (letter.attempts or 0) + 1
            await letter.async_save()

    remaining = len(letters) - replayed
    logger.info(f"Dead letter replay: {replayed} sent, {remaining} remaining")
    return replayed
