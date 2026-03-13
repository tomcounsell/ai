"""Chat model - maps chat_id to chat_name and metadata."""

import time

from popoto import Field, KeyField, Model, SortedField, UniqueKeyField


class Chat(Model):
    """Maps Telegram chat IDs to human-readable names.

    Replaces the SQLite chats table. UniqueKeyField on chat_id
    ensures one record per chat. SortedField on updated_at enables
    time-sorted listing.

    project_key enables direct project association, preparing for
    multi-chat-per-project routing.
    """

    chat_id = UniqueKeyField()
    chat_name = KeyField()
    chat_type = KeyField(null=True)  # private, group, supergroup, channel
    project_key = Field(null=True)  # Field (not KeyField) to avoid delete-and-recreate on change
    updated_at = SortedField(type=float)

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete chat records older than max_age_days. Returns count deleted.

        Note: A chat is 'old' if it hasn't been updated. Active chats
        are re-registered on each message, resetting updated_at.
        """
        cutoff = time.time() - (max_age_days * 86400)
        all_chats = cls.query.all()
        deleted = 0
        for chat in all_chats:
            if chat.updated_at and chat.updated_at < cutoff:
                chat.delete()
                deleted += 1
        return deleted
