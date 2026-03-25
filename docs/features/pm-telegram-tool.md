# PM Telegram Tool

ChatSession (the PM persona) composes and sends its own Telegram messages directly, bypassing the summarizer. This gives the PM full control over tone and content when communicating with stakeholders.

## Architecture

### Problem

Previously, all ChatSession output was rewritten by the summarizer (a Haiku-powered compressor in `bridge/response.py`). The PM persona had communication guidelines but they were effectively discarded -- the summarizer overwrote everything with structured bullet points.

### Solution

A Redis-based IPC mechanism lets ChatSession queue messages from a subprocess, and the bridge relay delivers them via Telethon. The summarizer becomes a safety net that only fires if the PM ends a session without self-messaging.

```
ChatSession (Claude Code subprocess)
    |
    | python tools/send_telegram.py "message text"
    v
Redis list: telegram:outbox:{session_id}
    |
    | bridge/telegram_relay.py (async poll loop)
    v
Telethon send_markdown() -> Telegram
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| Send tool | `tools/send_telegram.py` | CLI script called by ChatSession via Bash. Validates input, applies linkification, enforces 4096-char limit, pushes to Redis queue. |
| Bridge relay | `bridge/telegram_relay.py` | Async task in the bridge event loop. Polls `telegram:outbox:*` keys, sends via Telethon, records message IDs on AgentSession. |
| Formatting | `bridge/formatting.py` | Shared `linkify_references()` utility extracted from the summarizer. Converts `PR #N` and `Issue #N` to markdown links. |
| Summarizer bypass | `bridge/response.py` | Before calling the summarizer, checks `session.has_pm_messages()`. If the PM already sent messages, skips summarizer and returns True. |
| AgentSession field | `models/agent_session.py` | `pm_sent_message_ids` ListField tracks Telegram message IDs sent by the PM during a session. Helper methods: `record_pm_message()`, `has_pm_messages()`. |
| Env injection | `agent/sdk_client.py` | Injects `TELEGRAM_CHAT_ID` and `TELEGRAM_REPLY_TO` environment variables for chat-type sessions. |

## IPC Mechanism: Redis Queue

ChatSession runs as a Claude Code subprocess and cannot access the bridge's Telethon client directly. Redis lists provide the IPC channel.

### Queue Contract

- **Key pattern**: `telegram:outbox:{session_id}`
- **Message format**: JSON object with fields:
  - `chat_id` (string) -- target Telegram chat ID
  - `reply_to` (int or null) -- message ID to reply to
  - `text` (string) -- message content, already linkified and length-checked
  - `session_id` (string) -- session ID for routing
  - `timestamp` (float) -- Unix timestamp when queued
- **TTL**: 1 hour, set by the tool as a safety net for crashed sessions
- **Ordering**: RPUSH by producer, LPOP by consumer (FIFO)
- **Atomicity**: LPOP is atomic -- safe even with hypothetical concurrent consumers

### Drain Wait

When a session completes, the delivery path in `agent/job_queue.py` checks whether the outbox queue still has pending entries. If so, it polls with 100ms intervals up to 2 seconds for the relay to drain before checking `pm_sent_message_ids`. This prevents a race between queueing and session completion.

## Fallback Behavior

The summarizer is retained as a safety net. The decision tree in `bridge/response.py` is:

1. Refresh the AgentSession from Redis to get the latest `pm_sent_message_ids`.
2. If `session.has_pm_messages()` returns True: skip summarizer, return True. The PM already delivered its own messages.
3. If the PM did not self-message: fall through to the existing summarizer path. The response text is compressed and sent as before.

This means:
- If the PM persona crashes before calling the tool, the summarizer catches the output.
- If the PM deliberately returns text without using the tool, the summarizer formats and sends it.
- If the tool or Redis fails, ChatSession sees a Bash tool error and can fall back to returning text.

## Environment Variables

The following environment variables are injected by `sdk_client.py` for chat-type sessions:

| Variable | Source | Purpose |
|----------|--------|---------|
| `TELEGRAM_CHAT_ID` | Job's `chat_id` field | Target Telegram chat for the tool |
| `TELEGRAM_REPLY_TO` | Job's `message_id` field | Message ID to reply to in thread |
| `VALOR_SESSION_ID` | Already injected | Routes messages to the correct outbox queue |

## Error Handling

| Failure | Behavior |
|---------|----------|
| Redis connection failure in tool | Tool exits with non-zero code; ChatSession sees Bash error |
| Missing `TELEGRAM_CHAT_ID` | Tool exits with error explaining it is only available in ChatSession context |
| Empty message text | Tool rejects with clear error |
| Telethon send failure in relay | Message re-pushed to queue tail for retry; logged as error |
| AgentSession save failure | Non-fatal warning; message is still delivered to Telegram |
| Malformed queue entry | Skipped and logged; relay continues processing |

## Related

- Issue: [#497](https://github.com/tomcounsell/ai/issues/497)
- Plan: `docs/plans/pm-telegram-tool.md`
- Prior art on summarizer architecture: PR #275 (semantic session routing), PR #456 (summarizer evidence hardening)
- [Summarizer Format](summarizer-format.md) -- the existing summarizer that this feature partially bypasses
- [Chat Dev Session Architecture](chat-dev-session-architecture.md) -- ChatSession/DevSession split that this feature extends
