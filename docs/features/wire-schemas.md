# Wire Schemas

## Overview

Three Redis wires carry structured data between the bridge and the worker: the
outbox lists, the steering lists, and the session-notify channel. Each is
declared once as a pydantic model in `bridge/wire_schemas.py`. Writers construct
the model and serialize it; readers validate it; a payload that fails validation
becomes a [dead letter](pipeline-dead-letters.md) carrying the raw string.

Before this, all three were plain dicts read with `.get()`, and the only check
anywhere was a membership test against a set of message types. A field renamed
on one side of a process boundary failed silently on the other, and a malformed
entry was logged and dropped — already popped off the list, so gone.

## The three models

| Model | Wire | Writers | Readers |
|---|---|---|---|
| `OutboxPayload` | `telegram:outbox:{session_id}` and its email sibling | `build_telegram_outbox_payload` | `process_outbox` |
| `SteeringPayload` | the per-session and per-Room steering lists | `push_steering_message` | `_drain_list`, `_peek_list` |
| `NotifyPayload` | the session-notify pubsub channel | `publish_session_notify`, `_push_agent_session` | `_session_notify_listener` |

## Versioning

Every model carries `v: int = 1`. Readers accept any version and any unknown
field (`extra="allow"`), so an additive change needs no coordination and a
mixed-version fleet keeps working. Bump `v` when a change is not
backward-compatible.

`dump()` serializes with `exclude_none=True`, which keeps the on-wire shape
identical to the hand-built dicts these models replaced: a text message still
carries no `type` key and no `file_paths` key, so a bridge that predates this
change sees exactly what it saw before.

The relay depends on `extra="allow"` for a second reason: it mutates
`_relay_attempts` on the dict it dumps back out when re-queueing a failed send.

## `type` must admit `None`

`OutboxPayload.type` is
`Literal["reaction", "custom_emoji_message", "poll"] | None = None`. This
replaces the relay's `KNOWN_MESSAGE_TYPES` set, whose members included `None`,
because an ordinary text message carries no `type` key at all.

A bare `Literal[...]` here would dead-letter every plain text message as an
`outbox_parse` failure — the single highest-volume path in the system.
`tests/unit/test_wire_schemas.py::test_type_none_parses` is the row that holds
it.

## Parse-failure disposition

Each reader dead-letters at `stage=f"{wire}_parse"` with the raw string as the
payload and `replayable=False`. A payload that failed validation cannot be
meaningfully re-sent; the row exists so the loss is diagnosable.

One asymmetry, in steering: `_drain_list` has already LPOPped the entry, so an
unparseable one is gone and gets a row. `_peek_list` leaves the entry on the
list for the next drain to handle, so it only logs — dead-lettering there would
write one row per peek for the same entry.

## What is deliberately not typed

`extra_context` is an open bag by design (`tests/unit/test_bridge_context_guards.py`
polices dead keys); wire schemas stop at the Redis lists. Steering carries no
`correlation_id`: it is correlation-free by design (#3177).

## Related

- [Pipeline Dead Letters](pipeline-dead-letters.md) — where a parse failure goes
- [Correlation IDs](correlation-ids.md) — the `correlation_id` on `OutboxPayload`
- [Bridge/Worker Architecture](bridge-worker-architecture.md) — the wires themselves
- [Session Steering](session-steering.md)
