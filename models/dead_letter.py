"""DeadLetter model - failed Telegram message deliveries persisted in Redis."""

from popoto import AutoKeyField, Field, IntField, KeyField, Model, SortedField


class DeadLetter(Model):
    """A failed message delivery, persisted atomically in Redis.

    Replaces the JSONL file-based dead letter queue. Atomic operations
    eliminate the read-modify-write race condition on the file.
    """

    letter_id = AutoKeyField()
    chat_id = KeyField()
    reply_to = Field(type=int, null=True)
    text = Field(max_length=20_000)
    created_at = SortedField(type=float)
    attempts = IntField(default=0)
