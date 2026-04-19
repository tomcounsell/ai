# Telegram Message Edit Handling

## Overview

When a Telegram user edits a message after sending it, the bridge intercepts the `MessageEdited` event and routes it correctly: steering a still-running agent or spawning a fresh session for completed work.

## Problem Solved

Previously, the bridge only registered `events.NewMessage`. Telegram's `MessageEdited` events were silently dropped. A user who sent an incomplete message and quickly edited it would receive no response to the corrected version — the edit was invisible to the system.

**Concrete failure (2026-04-16):** A user sent an incomplete draft, the bridge immediately queued a session, then the user edited the message to add the real content. The session ran against the incomplete draft; the edit was never seen.

## How It Works

The bridge registers `@client.on(events.MessageEdited)` alongside the existing `events.NewMessage` handler.

### Session lookup

The handler derives the session ID using the same format as the original message: `tg_{project_key}_{chat_id}_{message_id}`. It queries `AgentSession` for that ID.

### Two routing branches

| Session state | Action |
|---|---|
| `running`, `active`, or `pending` | Injects `[Edit] {text}` as a steering message via `push_steering_message()` so the agent can course-correct mid-execution |
| `completed` | Spawns a fresh session with ID `{original_id}_edit` so the edited version is processed as a follow-up |
| No session found | Edit silently ignored — the original message was never processed |

### Guards

- Outgoing messages (`event.out`) are skipped — no self-edits.
- No project match → ignored.
- Empty edited text → ignored.
- `SHUTTING_DOWN` flag → ignored.

## Architecture

```
Telegram MessageEdited event
    |
    v
bridge/telegram_bridge.py (edit_handler)
    |-- Look up AgentSession by session_id
    |-- Running/active/pending? --> push_steering_message("[Edit] ...")
    |-- Completed? --> dispatch_telegram_session(session_id="{id}_edit")
    |-- Not found? --> ignore
```

## Related Features

- [Mid-Session Steering](mid-session-steering.md) — Steering queue mechanics used by the running-session branch
- [Bridge Workflow Gaps](bridge-workflow-gaps.md) — Session routing and output classification context
- [Steering Queue: Historical Spec](steering-implementation-spec.md) — Redis list push/pop implementation
