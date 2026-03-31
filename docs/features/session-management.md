# Session Management: Reply-Chain Root Resolution

## Overview

When a user replies to any message in a Telegram conversation thread — including Valor's own responses — the bridge resolves the **canonical session_id** by walking the reply chain to find the original human message that started the thread.

This ensures that all replies in a conversation map to **one AgentSession** regardless of which message in the thread is being replied to.

## Problem

Prior to this fix, session_id derivation used `reply_to_msg_id` directly:

```python
# Before (broken for replies to Valor's responses):
session_id = f"tg_{project_key}_{chat_id}_{message.reply_to_msg_id}"
```

This worked for the first reply (directly to the original human message), but broke when the user replied to Valor's response instead:

| Step | Message | reply_to | session_id derived |
|------|---------|----------|-------------------|
| 1 | User msg_8111 | — | `tg_..._8111` (correct) |
| 2 | Valor msg_8113 | — | (outbound) |
| 3 | User msg_8114 | reply_to=8113 | `tg_..._8113` (wrong!) |

Step 3 created a new AgentSession instead of resuming the original, producing duplicate dashboard rows.

## Solution

`resolve_root_session_id()` in `bridge/context.py` walks the reply chain backward to find the oldest non-Valor message, then derives the session_id from that message's ID.

```python
# After (correct):
session_id = await resolve_root_session_id(
    client, event.chat_id, message.reply_to_msg_id, project_key
)
```

## Resolution Strategy

The resolver uses a three-step fallback chain:

1. **Cache-first walk** (`_cache_walk_root`): Query `TelegramMessage.query.filter(chat_id=X, message_id=Y)` to walk the chain using Redis records. No Telegram API calls needed when records are cached. `message_id` is a `KeyField` enabling O(1) indexed lookup.

2. **API fallback** (`fetch_reply_chain`): If any cache lookup misses (record not in Redis), fall back to the Telegram API chain walk (up to 20 hops). Walks the full thread and finds the oldest non-Valor message.

3. **Final fallback**: On any exception (network error, Redis unavailable), return a session_id derived directly from `reply_to_msg_id`. This preserves the old behavior as a safe degraded path — no message delivery is blocked.

## Outbound message_id Storage

As a prerequisite, `send_response_with_files` was updated to return `Message | None` instead of `bool`. The `_send` callback in `bridge/telegram_bridge.py` now captures the returned Telegram `Message.id` and passes it to `store_message`:

```python
sent_msg_id = getattr(sent, "id", None)
store_message(..., message_id=sent_msg_id)
```

This populates `TelegramMessage.message_id` for outbound records, making future cache-first walks effective even for chains that start with a Valor response.

## Data Model Change

`TelegramMessage.message_id` was promoted from a plain `Field` to a `KeyField`:

```python
# Before:
message_id = Field(type=int, null=True)

# After:
message_id = KeyField(null=True)
```

`KeyField` adds a set-based index in Redis that enables `filter(message_id=X)` as an O(1) lookup. No data migration is required — `KeyField` is a superset of `Field` for nullable values.

## Graceful Degradation

The resolver never blocks message delivery:

- Cache miss → API walk (transparent to user)
- API failure → `reply_to_msg_id` direct (old behavior, one extra AgentSession at worst)
- Any exception → `reply_to_msg_id` direct (safe fallback logged at DEBUG level)

## Race Conditions

Two rapid replies both derive the same root session_id before either is processed. This is handled by `enqueue_agent_session()`'s same-session_id supersede logic (lines 318–336 of `telegram_bridge.py`). No shared mutable state in the chain walk.

## Related Features

- [Mid-Session Steering](mid-session-steering.md) — Steering check that routes replies to running sessions uses the same canonical session_id.
- [Semantic Session Routing](semantic-session-routing.md) — Handles unthreaded messages (no reply-to); this feature handles explicit reply-to chains.
- [Session Isolation](session-isolation.md) — Two-tier task list scoping that depends on consistent session_id derivation.
- [Agent Session Model](agent-session-model.md) — AgentSession lifecycle model tracking per session_id.
