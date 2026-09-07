"""Typed payloads for the three Redis wires between the bridge and the worker.

The outbox lists, the steering lists, and the session-notify channel all
carried plain dicts read with ``.get()``. A field renamed on one side of a
process boundary failed silently on the other, and a malformed entry was
discarded with a warning. These models make each wire a declared shape:
writers construct one and call ``model_dump_json()``, readers call
``model_validate_json()``, and a payload that fails validation becomes a
``DeadLetter`` carrying the raw string instead of vanishing.

Every model carries ``v: int = 1``. Bump it when a change is not
backward-compatible; readers accept any version and any unknown field, so an
additive change needs no coordination and a mixed-version fleet keeps
working.

``correlation_id`` rides on :class:`OutboxPayload` so a message's journey is
traceable from intake through the harness subprocess and back out to
delivery. Steering is deliberately correlation-free (see #3177).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Wire(BaseModel):
    """Base for the wire payloads: version-stamped and forward-compatible.

    ``extra="allow"`` is the forward-compatibility contract. A newer writer
    that adds a field must not dead-letter every message an older reader
    sees, and the reader keeps the unknown field on the model so a
    round-trip through an intermediate process preserves it.
    """

    model_config = ConfigDict(extra="allow")

    v: int = 1


class OutboxPayload(_Wire):
    """One entry on ``telegram:outbox:{session_id}`` or its email sibling.

    ``type`` MUST admit ``None``. An ordinary text message carries no
    ``type`` key at all — it is the highest-volume path in the system — and a
    bare ``Literal[...]`` here would dead-letter every one of them as an
    ``outbox_parse`` failure.
    """

    type: Literal["reaction", "custom_emoji_message", "poll"] | None = None
    chat_id: str | int | None = None
    session_id: str | None = None
    text: str | None = None
    reply_to: int | None = None
    timestamp: float | None = None
    file_paths: list[str] | None = None
    correlation_id: str | None = None
    project_key: str | None = None


class SteeringPayload(_Wire):
    """One entry on a steering list (legacy per-session key or a Room key)."""

    text: str
    sender: str
    timestamp: float
    is_abort: bool = False
    target_agent: str | None = None


class NotifyPayload(_Wire):
    """One message on the session-notify pubsub channel."""

    worker_key: str | None = None
    chat_id: str | None = None
    session_id: str | None = None
    is_project_keyed: bool = False


def dump(payload: _Wire) -> str:
    """Serialise a wire payload, omitting keys the writer left unset.

    ``exclude_none`` keeps the on-wire shape identical to the hand-built
    dicts these models replace: a text message still carries no ``type`` key
    and no ``file_paths`` key, so a reader that predates this change (an
    older bridge mid-deploy) sees exactly what it saw before.
    """
    return payload.model_dump_json(exclude_none=True)


__all__ = [
    "NotifyPayload",
    "OutboxPayload",
    "SteeringPayload",
    "dump",
]
