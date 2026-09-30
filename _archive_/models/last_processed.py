"""LastProcessedRecord model - per-chat last-processed message cursor.

Tracks the latest message ID and timestamp the bridge successfully *dispatched*
for each chat. This is distinct from DedupRecord (which tracks a *set* of recent
IDs for membership checks): this model is a monotonic *cursor* used to compute a
smarter catchup lookback per chat.

Written by the live handler, the reconciler, and catchup on every successful
dispatch. Read by catchup on startup to compute a per-chat cutoff that closes the
gap between "bridge last heartbeated" (`data/last_connected`) and "bridge last
actually received a message from this group" (this cursor). See issue #1408.
"""

import time

from popoto import IntField, KeyField, Model

from config.settings import settings


class LastProcessedRecord(Model):
    """Per-chat cursor of the latest successfully dispatched message.

    The cursor advances monotonically: an update with a message ID less than or
    equal to the stored one is a no-op (later writes never regress to earlier
    message IDs). This keeps the cursor approximately correct even under
    concurrent writes from the live handler and the reconciler.

    Fields:
        chat_id: Telegram chat ID (one record per chat), the key.
        last_message_id: ID of the latest dispatched message.
        last_message_ts: Unix timestamp (int) of that message's date.
        updated_at: Unix timestamp (int) of the last cursor write.
    """

    chat_id = KeyField()
    last_message_id = IntField(default=0)
    last_message_ts = IntField(default=0)
    updated_at = IntField(default=0)

    class Meta:
        # 30 days — survives reasonable downtime, auto-expires inactive chats.
        # Sourced from settings so it's .env-overridable (issue #1968 Task 5).
        ttl = int(settings.timeouts.last_processed_ttl_s)

    @classmethod
    def get_or_create(cls, chat_id: str) -> "LastProcessedRecord":
        """Get existing record for a chat, or create a new (zeroed) one."""
        existing = cls.query.filter(chat_id=str(chat_id))
        if existing:
            return existing[0]
        return cls.create(
            chat_id=str(chat_id),
            last_message_id=0,
            last_message_ts=0,
            updated_at=0,
        )

    def advance(self, message_id: int, message_ts: int) -> bool:
        """Advance the cursor if message_id is newer than the stored one.

        Returns True if the cursor advanced (and was saved), False if the
        incoming message_id was not strictly greater than the stored value
        (no-op, monotonic guard).
        """
        if int(message_id) <= int(self.last_message_id):
            return False
        self.last_message_id = int(message_id)
        self.last_message_ts = int(message_ts)
        self.updated_at = int(time.time())
        self.save()
        return True
