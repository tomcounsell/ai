---
tracking: https://github.com/tomcounsell/ai/issues/22
---

# Plan: Expand Popoto Redis Models for Messages and Queues

## Problem Statement

The codebase uses JSONL files and JSON files for temporary state in several places — dead letters, bridge events, calendar caches. These are susceptible to race conditions (read-modify-write on files), don't support atomic operations, and require parsing entire files for queries. Meanwhile, `RedisJob` in `agent/job_queue.py` already demonstrates popoto working well for the job queue.

Additionally, all incoming/outgoing Telegram messages and agent session state exist only in SQLite (long-term archive) or ephemerally in memory. There's no fast, queryable, real-time view of current message flow and session activity.

## Scope: 5 Popoto Models

### 1. DeadLetter — Replace `data/dead_letters.jsonl`

**File**: `bridge/dead_letters.py`

Replace file-based dead letter queue with a popoto model. Eliminates the read-all-rewrite-file pattern that races if two processes restart simultaneously.

```python
class DeadLetter(Model):
    letter_id = AutoKeyField()
    chat_id = KeyField()
    reply_to = Field(type=int, null=True)
    text = Field(max_length=20_000)
    created_at = SortedField(type=float)
    attempts = IntField(default=0)
```

**Changes**:
- `persist_failed_delivery()` → `DeadLetter.async_create()`
- `replay_dead_letters()` → query all, attempt send, `async_delete()` on success, increment attempts on failure
- Remove JSONL file I/O entirely

### 2. BridgeEvent — Replace `logs/bridge.events.jsonl`

**File**: `bridge/telegram_bridge.py` (the `log_event()` function at line 649)

Replace append-only JSONL with a queryable model. Enables time-range analytics without parsing a growing file.

```python
class BridgeEvent(Model):
    event_id = AutoKeyField()
    event_type = KeyField()  # message_received, agent_request, agent_response, error
    chat_id = KeyField(null=True)
    project_key = KeyField(null=True)
    timestamp = SortedField(type=float)
    data = DictField(null=True)  # arbitrary metadata
```

**Changes**:
- `log_event()` → `BridgeEvent.create()` (sync is fine here, it's fire-and-forget)
- `scripts/analyze_logs.py` → rewrite queries against `BridgeEvent.query.filter()`
- Consider TTL / periodic cleanup (e.g. delete events older than 7 days)

### 3. TelegramMessage — Mirror all message traffic

**New file**: `models/telegram.py`

Create a Redis mirror of every incoming and outgoing Telegram message. The existing `store_message()` calls in the bridge are the insertion points. SQLite remains the durable long-term archive; Redis is the fast recent-access layer.

```python
class TelegramMessage(Model):
    msg_id = AutoKeyField()
    chat_id = KeyField()
    message_id = Field(type=int, null=True)  # Telegram's message ID
    direction = KeyField()  # "in" | "out"
    sender = KeyField()
    content = Field(max_length=20_000)
    timestamp = SortedField(type=float, sort_by="chat_id")
    message_type = KeyField(default="text")  # text, media, response, acknowledgment
    session_id = Field(null=True)
```

**Changes**:
- `store_message()` in `tools/telegram_history/__init__.py` — add `TelegramMessage.create()` alongside SQLite insert
- `send_response_with_files()` — create outgoing TelegramMessage record after successful send
- BossMessenger.send() — create outgoing record on success

### 4. AgentSession — Track session lifecycle in Redis

**New file**: `models/sessions.py`

Track active/dormant/completed sessions with queryable state. Currently session state is implicit (scattered across branch names, in-memory dicts, and process state JSONs).

```python
class AgentSession(Model):
    session_id = UniqueKeyField()
    project_key = KeyField()
    status = KeyField(default="active")  # active, dormant, completed, failed
    chat_id = Field()
    sender = Field()
    started_at = SortedField(type=float, sort_by="project_key")
    last_activity = SortedField(type=float)
    tool_call_count = IntField(default=0)
    branch_name = Field(null=True)
    message_text = Field(max_length=20_000, null=True)  # original request
```

**Changes**:
- `_execute_job()` in `agent/job_queue.py` — create AgentSession on job start, update on completion
- Health check watchdog — update `tool_call_count` and `last_activity` on each check
- Revival detection — query `AgentSession.query.filter(status="active")` instead of git state checks
- Bridge shutdown — mark active sessions as dormant

### 5. PubSub Message Bus — Event-driven architecture

**New file**: `models/events.py`

Use popoto's Publisher/Subscriber to decouple message handling from side effects (logging, history storage, analytics, session tracking).

```python
from popoto import Publisher, Subscriber

class MessageEventPublisher(Publisher):
    """Publishes on channel 'telegram_messages'."""
    pass

class BridgeEventPublisher(Publisher):
    """Publishes on channel 'bridge_events'."""
    pass
```

**Changes**:
- Bridge message handler — publish `{"direction": "in", "chat_id": ..., "text": ...}` on every incoming message
- `send_response_with_files()` — publish `{"direction": "out", ...}` on every outgoing message
- Subscribers can handle: TelegramMessage creation, BridgeEvent logging, analytics, future dashboard feeds
- This is the final step — models 1-4 should work first, then PubSub can optionally wire them together

## Implementation Order

1. **DeadLetter** — smallest scope, replaces code written today, validates the pattern
2. **BridgeEvent** — replaces another JSONL, introduces DictField usage
3. **TelegramMessage** — the core message mirror, wires into existing store_message() calls
4. **AgentSession** — depends on understanding TelegramMessage patterns
5. **PubSub** — ties everything together, optional decoupling layer

## Files Modified

| File | Changes |
|------|---------|
| `bridge/dead_letters.py` | Rewrite with DeadLetter model |
| `bridge/telegram_bridge.py` | Update log_event(), add TelegramMessage writes |
| `tools/telegram_history/__init__.py` | Add Redis mirror alongside SQLite |
| `agent/job_queue.py` | Add AgentSession lifecycle |
| `agent/health_check.py` | Update session tracking |
| `models/__init__.py` | New package |
| `models/telegram.py` | New — TelegramMessage model |
| `models/sessions.py` | New — AgentSession model |
| `models/events.py` | New — PubSub publishers/subscribers |
| `scripts/analyze_logs.py` | Rewrite against BridgeEvent queries |

## What Stays As-Is

- **SQLite telegram_history** — durable long-term archive with full-text search
- **Config JSON files** — read-once-on-startup, no concurrency concern
- **Calendar queue JSONL** — offline CLI fallback, low volume, separate process
- **CalendarEventCache JSON** — could migrate later, but low priority (runs outside bridge)

## Verification

- Run `pytest tests/` after each model migration
- Verify Redis connectivity: `redis-cli ping`
- Test dead letter: kill bridge mid-send, restart, confirm replay from Redis
- Test message mirroring: send Telegram message, query `TelegramMessage.query.filter(chat_id=...)`
- Test session tracking: trigger agent work, query `AgentSession.query.filter(status="active")`

## Execution Steps

1. Copy this plan to `docs/plans/popoto-redis-expansion.md`
2. Create GitHub issue on `tomcounsell/ai` with the 5 model specs as task list
3. Update the tracking link in the plan frontmatter
4. Commit plan and push
