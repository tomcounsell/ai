"""DedupRecord model - per-chat message deduplication tracking.

Replaces the raw Redis sets in bridge/dedup.py with a Popoto model.
Each chat gets its own DedupRecord keyed by chat_id, storing a set of
recently processed message IDs. TTL is settings-backed and coupled to
LastProcessedRecord's cursor TTL (config/settings.py
TimeoutSettings.dedup_record_ttl_s, default 30 days) -- NOT a fixed 2
hours. The dedup set must remember every dispatched message for at
least as long as the cursor can extend the startup-catchup lookback
(issue #1408's per-chat cutoff extension), or a handled-but-aged-out
message re-enqueues after a restart (see
docs/plans/catchup-rehandles-handled-messages.md).
"""

from popoto import KeyField, Model, SetField

from config.settings import settings


class DedupRecord(Model):
    """Tracks recently processed message IDs per Telegram chat.

    Used by bridge/dedup.py to prevent duplicate message processing
    during catch_up replays. Each chat gets an independent record whose
    TTL is coupled to the LastProcessedRecord cursor TTL (see module
    docstring) -- it is cursor-coupled, not a fixed short window.

    Fields:
        chat_id: Telegram chat ID (one record per chat)
        message_ids: Set of recently processed message ID strings
    """

    chat_id = KeyField()
    message_ids = SetField(default=set)

    class Meta:
        # Cursor-coupled, settings-backed (config/settings.py
        # TimeoutSettings.dedup_record_ttl_s). Env: TIMEOUTS__DEDUP_RECORD_TTL_S.
        ttl = int(settings.timeouts.dedup_record_ttl_s)

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
