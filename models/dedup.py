"""DedupRecord model - per-chat message deduplication tracking.

Replaces the raw Redis sets in bridge/dedup.py with a Popoto model.
Each chat gets its own DedupRecord keyed by chat_id, storing a set of
recently processed message IDs. TTL ensures automatic cleanup after 2 hours.
"""

from popoto import KeyField, Model, SetField


class DedupRecord(Model):
    """Tracks recently processed message IDs per Telegram chat.

    Used by bridge/dedup.py to prevent duplicate message processing
    during catch_up replays. Each chat gets an independent record
    with a 2-hour TTL matching the original manual expire() behavior.

    Fields:
        chat_id: Telegram chat ID (one record per chat)
        message_ids: Set of recently processed message ID strings
    """

    chat_id = KeyField()
    message_ids = SetField(default=set)

    class Meta:
        ttl = 7200  # 2 hours, matching original expire() behavior

    # Max message IDs to track per chat
    _MAX_IDS = 50

    def add_message(self, message_id: int) -> None:
        """Add a message ID and trim to MAX_IDS if needed."""
        self.message_ids.add(str(message_id))
        if len(self.message_ids) > self._MAX_IDS * 2:
            sorted_ids = sorted(self.message_ids, key=lambda x: int(x))
            self.message_ids = set(sorted_ids[-self._MAX_IDS :])
        self.save()

    def has_message(self, message_id: int) -> bool:
        """Check if a message ID has been recorded."""
        return str(message_id) in self.message_ids

    @classmethod
    def get_or_create(cls, chat_id: str) -> "DedupRecord":
        """Get existing record for a chat, or create a new one."""
        # C3 (#1817): DedupRecord's short 2h TTL makes it the most likely
        # model to accumulate ghost index members (hash expired, class-set
        # membership survives). get_or_create() runs on every inbound
        # message, so it is a good place to opportunistically self-heal the
        # index instead of waiting up to 24h for the nightly
        # popoto-index-cleanup reflection. Rate-limited internally (at most
        # once/60s) -- safe to call unconditionally on every read.
        # query.filter() itself already silently drops ghost members from
        # `existing` (never attaches a dead record's data); this only
        # accelerates removing the ghost from the index. See
        # models/ghost_reconcile.py for the full rationale.
        from models.ghost_reconcile import reconcile_ghost_members

        reconcile_ghost_members(cls)

        existing = cls.query.filter(chat_id=str(chat_id))
        if existing:
            return existing[0]
        return cls.create(chat_id=str(chat_id), message_ids=set())
