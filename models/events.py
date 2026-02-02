"""PubSub message bus - event-driven architecture via popoto Publisher/Subscriber.

Decouples message handling from side effects (logging, history storage,
analytics, session tracking). Publish on every incoming/outgoing message;
subscribers handle TelegramMessage creation, BridgeEvent logging, etc.
"""

from popoto import Publisher, Subscriber


class MessageEventPublisher(Publisher):
    """Publishes on channel 'telegram_messages'.

    Events include direction, chat_id, sender, content, message_type.
    """

    class Meta:
        channel = "telegram_messages"


class BridgeEventPublisher(Publisher):
    """Publishes on channel 'bridge_events'.

    Events include event_type, chat_id, project_key, and arbitrary data.
    """

    class Meta:
        channel = "bridge_events"


class MessageEventSubscriber(Subscriber):
    """Subscribes to 'telegram_messages' channel."""

    class Meta:
        channel = "telegram_messages"

    @staticmethod
    def on_message(data: dict) -> None:
        """Handle incoming message events.

        Override this method or subclass to implement custom handling.
        Default implementation creates a TelegramMessage record.
        """
        from models.telegram import TelegramMessage

        TelegramMessage.create(
            chat_id=str(data.get("chat_id", "")),
            message_id=data.get("message_id"),
            direction=data.get("direction", "in"),
            sender=data.get("sender", "unknown"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp", 0),
            message_type=data.get("message_type", "text"),
            session_id=data.get("session_id"),
        )


class BridgeEventSubscriber(Subscriber):
    """Subscribes to 'bridge_events' channel."""

    class Meta:
        channel = "bridge_events"

    @staticmethod
    def on_message(data: dict) -> None:
        """Handle bridge event notifications.

        Override this method or subclass to implement custom handling.
        Default implementation creates a BridgeEvent record.
        """
        from models.bridge_event import BridgeEvent

        BridgeEvent.log(
            event_type=data.get("event_type", "unknown"),
            **{k: v for k, v in data.items() if k != "event_type"},
        )
