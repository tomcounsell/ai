"""Dead-letter queue for failed Telegram message deliveries.

When send_response_with_files fails to deliver a message, the payload
is persisted here. On bridge startup, pending dead letters are replayed.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEAD_LETTER_PATH = Path(__file__).parent.parent / "data" / "dead_letters.jsonl"


async def persist_failed_delivery(
    chat_id: int,
    reply_to: int | None,
    text: str,
) -> None:
    """Append a failed delivery to the dead-letter file."""
    DEAD_LETTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "chat_id": chat_id,
        "reply_to": reply_to,
        "text": text,
        "timestamp": datetime.now().isoformat(),
    }
    with open(DEAD_LETTER_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    logger.warning(f"Persisted dead letter for chat {chat_id} ({len(text)} chars)")


async def replay_dead_letters(client) -> int:
    """Replay all pending dead letters. Returns count of successfully replayed."""
    if not DEAD_LETTER_PATH.exists():
        return 0

    lines = DEAD_LETTER_PATH.read_text().strip().splitlines()
    if not lines:
        return 0

    logger.info(f"Replaying {len(lines)} dead letter(s)...")
    remaining = []
    replayed = 0

    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(f"Skipping malformed dead letter line: {line[:100]}")
            continue

        chat_id = entry.get("chat_id")
        reply_to = entry.get("reply_to")
        text = entry.get("text", "")

        if not chat_id or not text:
            continue

        try:
            if len(text) > 4096:
                text = text[:4093] + "..."
            await client.send_message(chat_id, text, reply_to=reply_to)
            replayed += 1
            logger.info(f"Replayed dead letter to chat {chat_id}")
        except Exception as e:
            logger.error(f"Dead letter replay failed for chat {chat_id}: {e}")
            remaining.append(line)

    # Rewrite file with only the entries that still failed
    if remaining:
        DEAD_LETTER_PATH.write_text("\n".join(remaining) + "\n")
    else:
        DEAD_LETTER_PATH.unlink(missing_ok=True)

    logger.info(f"Dead letter replay: {replayed} sent, {len(remaining)} remaining")
    return replayed
