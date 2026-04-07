"""Link model - stores URLs shared in Telegram chats."""

import time

from popoto import AutoKeyField, Field, KeyField, ListField, Model, SortedField


class Link(Model):
    """Stores URLs shared in Telegram chats with metadata.

    Replaces the SQLite links table. KeyFields (domain, sender, status)
    enable efficient exact-match queries; SortedField on timestamp
    enables time-range filtering via ZRANGEBYSCORE.

    Upsert pattern: get-or-create by url+chat_id. For updates that
    change KeyField values, use delete-and-recreate.
    """

    link_id = AutoKeyField()
    url = KeyField()
    chat_id = KeyField()
    project_key = KeyField(null=True)
    message_id = Field(type=int, null=True)
    domain = KeyField(null=True)
    sender = KeyField(null=True)
    status = KeyField(default="unread")  # unread, read, archived
    timestamp = SortedField(type=float)
    final_url = Field(null=True, max_length=2000)
    title = Field(null=True, max_length=1000)
    description = Field(null=True, max_length=2000)
    tags = ListField(null=True)
    notes = Field(null=True, max_length=5000)
    ai_summary = Field(null=True, max_length=50_000)

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete link records older than max_age_days. Returns count deleted.

        Uses SortedField timestamp for efficient range filtering.
        """
        cutoff = time.time() - (max_age_days * 86400)
        all_links = cls.query.all()
        deleted = 0
        for link in all_links:
            if link.timestamp and link.timestamp < cutoff:
                link.delete()
                deleted += 1
        return deleted
