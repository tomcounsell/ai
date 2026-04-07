"""Per-chat message deduplication for catch_up replay protection.

Uses Redis sets to track recently processed message IDs per chat.
Keeps ~50 most recent IDs per chat to bound memory usage.
"""

import logging
import os

import redis

logger = logging.getLogger(__name__)

# Redis connection (same as popoto uses)
_redis_client = None

# Max message IDs to track per chat
MAX_IDS_PER_CHAT = 50


def _get_redis():
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = redis.from_url(redis_url)
    return _redis_client


def _dedup_key(chat_id) -> str:
    return f"bridge:dedup:{chat_id}"


async def is_duplicate_message(chat_id, message_id: int) -> bool:
    """Check if this message was already processed."""
    try:
        r = _get_redis()
        return r.sismember(_dedup_key(chat_id), str(message_id))
    except Exception as e:
        logger.debug(f"Dedup check failed (allowing through): {e}")
        return False


async def record_message_processed(chat_id, message_id: int) -> None:
    """Record that we processed this message."""
    try:
        r = _get_redis()
        key = _dedup_key(chat_id)
        r.sadd(key, str(message_id))
        # Trim to keep only most recent MAX_IDS_PER_CHAT
        size = r.scard(key)
        if size > MAX_IDS_PER_CHAT * 2:
            # Get all members, sort, keep newest
            members = r.smembers(key)
            sorted_ids = sorted(members, key=lambda x: int(x))
            to_remove = sorted_ids[:-MAX_IDS_PER_CHAT]
            if to_remove:
                r.srem(key, *to_remove)
        # Set TTL of 2 hours (messages older than that won't be replayed)
        r.expire(key, 7200)
    except Exception as e:
        logger.debug(f"Dedup record failed (non-fatal): {e}")
