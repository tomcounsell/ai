"""TelegramMessage model - Redis mirror of Telegram message traffic."""

from popoto import AutoKeyField, Field, KeyField, Model, SortedField

MSG_MAX_CHARS = 20_000


class TelegramMessage(Model):
    """Mirror of incoming/outgoing Telegram messages in Redis.

    SQLite remains the durable long-term archive; this model provides
    fast recent-access queries keyed on chat_id with sorted timestamps.
    """

    msg_id = AutoKeyField()
    chat_id = KeyField()
    message_id = Field(type=int, null=True)  # Telegram's message ID
    direction = KeyField()  # "in" | "out"
    sender = KeyField()
    content = Field(max_length=MSG_MAX_CHARS)
    timestamp = SortedField(type=float, sort_by="chat_id")
    message_type = KeyField(default="text")  # text, media, response, acknowledgment
    session_id = Field(null=True)
