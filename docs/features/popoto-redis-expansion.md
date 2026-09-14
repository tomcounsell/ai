---
tracking: https://github.com/tomcounsell/ai/issues/22
---

# Popoto Models for Messages and Queues

The persistent state layer is backed by Popoto (Redis) models. The unified
`AgentSession` model (`models/agent_session.py`) is the source of truth for
session and job lifecycle. `TelegramMessage` is the sole source of truth for
messages. `DeadLetter` and `BridgeEvent` model the two event streams.

## DeadLetter

**File**: `bridge/dead_letters.py`

```python
class DeadLetter(Model):
    letter_id = AutoKeyField()
    chat_id = KeyField()
    reply_to = Field(type=int, null=True)
    text = Field(max_length=20_000)
    created_at = SortedField(type=float)
    attempts = IntField(default=0)
```

Failed deliveries are persisted via `persist_failed_delivery()` →
`DeadLetter.async_create()`. `replay_dead_letters()` queries all, attempts
send, `async_delete()`s on success, and increments attempts on failure.

## BridgeEvent

**File**: `bridge/telegram_bridge.py` (the `log_event()` function)

```python
class BridgeEvent(Model):
    event_id = AutoKeyField()
    event_type = KeyField()  # message_received, agent_request, agent_response, error
    chat_id = KeyField(null=True)
    project_key = KeyField(null=True)
    timestamp = SortedField(type=float)
    data = DictField(null=True)  # arbitrary metadata
```

`log_event()` → `BridgeEvent.create()`. Time-range analytics query
`BridgeEvent.query.filter()`.

## TelegramMessage

**File**: `models/telegram.py`

```python
class TelegramMessage(Model):
    msg_id = AutoKeyField()
    chat_id = KeyField()
    message_id = Field(type=int, null=True)  # Telegram's message ID
    direction = KeyField()  # "in" | "out"
    sender = KeyField()
    content = Field(max_length=20_000)
    timestamp = SortedField(type=float, partition_by="chat_id")
    message_type = KeyField(default="text")  # text, media, response, acknowledgment
    session_id = Field(null=True)
```

`TelegramMessage` is the sole source of truth for message traffic. It is
written on every incoming message and on every successful outgoing send.

## AgentSession

**File**: `models/agent_session.py`

The unified `AgentSession` model tracks session lifecycle with queryable
state: session identity, project key, status, chat, timestamps, tool-call
count, and the originating message. The queue worker creates a record on
session start and updates it on completion; the health-check watchdog updates
`tool_call_count` and `updated_at` on each check.

## What Stays As-Is

- **SQLite telegram_history** — durable long-term archive with full-text search
- **Config JSON files** — read-once-on-startup, no concurrency concern
- **Calendar queue JSONL** — offline CLI fallback, low volume, separate process
