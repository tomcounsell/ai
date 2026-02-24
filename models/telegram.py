"""TelegramMessage model - source of truth for Telegram message traffic."""

import time

from popoto import AutoKeyField, Field, KeyField, Model, SortedField

MSG_MAX_CHARS = 50_000  # Full responses, no artificial cap


class TelegramMessage(Model):
    """Source of truth for incoming/outgoing Telegram messages in Redis.

    Replaces the SQLite messages table. SortedField on timestamp partitioned
    by chat_id enables efficient time-range queries per chat via ZRANGEBYSCORE.
    """

    msg_id = AutoKeyField()
    chat_id = KeyField()
    message_id = Field(type=int, null=True)  # Telegram's message ID
    direction = KeyField()  # "in" | "out"
    sender = KeyField()
    content = Field(max_length=MSG_MAX_CHARS)
    timestamp = SortedField(type=float, partition_by="chat_id")
    message_type = KeyField(default="text")  # text, media, response, acknowledgment
    session_id = Field(null=True)

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
