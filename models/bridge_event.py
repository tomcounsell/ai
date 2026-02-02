"""BridgeEvent model - structured bridge events queryable in Redis."""

import time

from popoto import AutoKeyField, DictField, KeyField, Model, SortedField


class BridgeEvent(Model):
    """A structured event from the Telegram bridge.

    Replaces the append-only bridge.events.jsonl file. SortedField on
    timestamp enables time-range analytics without parsing a growing file.
    """

    event_id = AutoKeyField()
    event_type = KeyField()  # message_received, agent_request, agent_response, error
    chat_id = KeyField(null=True)
    project_key = KeyField(null=True)
    timestamp = SortedField(type=float)
    data = DictField(null=True)  # arbitrary metadata

    @classmethod
    def log(cls, event_type: str, **kwargs) -> "BridgeEvent":
        """Create a bridge event (sync, fire-and-forget).

        Drop-in replacement for the old log_event() function.
        Extracts chat_id and project_key from kwargs if present,
        stores the rest in the data dict.
        """
        chat_id = kwargs.pop("chat_id", None)
        project_key = kwargs.pop("project", kwargs.pop("project_key", None))
        return cls.create(
            event_type=event_type,
            chat_id=str(chat_id) if chat_id is not None else None,
            project_key=project_key,
            timestamp=time.time(),
            data=kwargs if kwargs else None,
        )

    @classmethod
    def cleanup_old(cls, max_age_seconds: float = 7 * 86400) -> int:
        """Delete events older than max_age (default 7 days). Returns count deleted."""
        cutoff = time.time() - max_age_seconds
        all_events = cls.query.all()
        deleted = 0
        for event in all_events:
            if event.timestamp and event.timestamp < cutoff:
                event.delete()
                deleted += 1
        return deleted
