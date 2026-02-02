"""Dead-letter queue for failed Telegram message deliveries.

When send_response_with_files fails to deliver a message, the payload
is persisted here. On bridge startup, pending dead letters are replayed.

Uses popoto Redis model for atomic persistence (no file race conditions).
"""

import logging
import time

from models.dead_letter import DeadLetter

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
    letters = await DeadLetter.query.async_filter()
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

        try:
            if len(text) > 4096:
                text = text[:4093] + "..."
            await client.send_message(
                int(chat_id), text, reply_to=letter.reply_to
            )
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
