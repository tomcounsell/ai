"""TelegramMessage model - source of truth for Telegram message traffic.

Stores all message metadata including media, URL, and classification data.
Cross-references AgentSession via agent_session_id for lifecycle tracking.
"""

import time

from popoto import AutoKeyField, Field, KeyField, Model, SortedField


class TelegramMessage(Model):
    """Source of truth for incoming/outgoing Telegram messages in Redis.

    Replaces the SQLite messages table. SortedField on timestamp partitioned
    by chat_id enables efficient time-range queries per chat via ZRANGEBYSCORE.

    Carries all message metadata (media, URLs, classification) that was
    previously stored on AgentSession. AgentSession now references this
    model via telegram_message_key.
    """

    msg_id = AutoKeyField()
    chat_id = KeyField()
    message_id = KeyField(null=True)  # Telegram's message ID (KeyField enables O(1) filter lookup)
    direction = KeyField()  # "in" | "out"
    sender = KeyField()
    content = Field()
    timestamp = SortedField(type=float, partition_by="chat_id")
    message_type = KeyField(default="text")  # text, media, response, acknowledgment
    session_id = Field(null=True)

    # === Project association ===
    project_key = KeyField(null=True)

    # === Media and enrichment metadata ===
    has_media = Field(type=bool, default=False)
    media_type = Field(null=True)
    youtube_urls = Field(null=True)  # JSON-encoded list of (url, video_id) tuples
    non_youtube_urls = Field(null=True)  # JSON-encoded list of URL strings
    reply_to_msg_id = Field(type=int, null=True)
    classification_type = Field(null=True)
    classification_confidence = Field(type=float, null=True)

    # === Cross-reference to AgentSession ===
    agent_session_id = Field(
        null=True
    )  # agent_session_id of the AgentSession that processed this message

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete message records older than max_age_days. Returns count deleted.

        Uses all() scan since SortedField is partitioned by chat_id.
        At 90 days x 50 msgs/day = ~4500 records — fast enough for maintenance.
        """
        cutoff = time.time() - (max_age_days * 86400)
        all_messages = cls.query.all()
        deleted = 0
        for msg in all_messages:
            if msg.timestamp and msg.timestamp < cutoff:
                msg.delete()
                deleted += 1
        return deleted
