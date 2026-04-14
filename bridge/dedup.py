"""Per-chat message deduplication for catch_up replay protection.

Uses a Popoto DedupRecord model to track recently processed message IDs per chat.
Keeps ~50 most recent IDs per chat to bound memory usage.
TTL of 2 hours is managed by the model's Meta.ttl.
"""

import logging

from models.dedup import DedupRecord

logger = logging.getLogger(__name__)

# Max message IDs to track per chat (exposed for backward compatibility)
MAX_IDS_PER_CHAT = DedupRecord._MAX_IDS


async def is_duplicate_message(chat_id, message_id: int) -> bool:
    """Check if this message was already processed."""
    try:
        record = DedupRecord.get_or_create(str(chat_id))
        return record.has_message(message_id)
    except Exception as e:
        logger.debug(f"Dedup check failed (allowing through): {e}")
        return False


async def record_message_processed(chat_id, message_id: int) -> None:
    """Record that we processed this message.

    Failures are logged at WARNING (not debug): a silent dedup outage causes
    the reconciler to re-dispatch every message for the duration of the
    failure, which is exactly the class of bug this function exists to
    prevent. Exceptions are NOT re-raised -- dedup recording must never
    break the caller's control flow.
    """
    try:
        record = DedupRecord.get_or_create(str(chat_id))
        record.add_message(message_id)
    except Exception as e:
        logger.warning(
            "dedup record failed for chat=%s msg=%s: %s",
            chat_id,
            message_id,
            e,
        )
