# Bridge Message Query Tool

**Status**: Consolidated into `valor-telegram` (issue #1163)
**Created**: 2026-02-09
**Implemented**: 2026-02-09
**Consolidated**: 2026-04-24

---

## Status Update

The standalone DM-history CLI script has been **removed** as part of issue
[#1163](https://github.com/tomcounsell/ai/issues/1163) — it duplicated the
name-resolution surface of `valor-telegram` with a second identity space and
was a known source of silent wrong-matches. The bridge-side IPC handler
(`check_message_query_request` in `bridge/telegram_bridge.py`) remains in
place for back-compat (see No-Gos in the consolidation plan) but is no longer
invoked by any in-tree CLI.

**Migration path**: use `valor-telegram read --user USERNAME` for all DM
message-history queries. The `--user` flag forces resolution through the DM
whitelist (`tools/telegram_users.resolve_username`) and reads from Redis /
Telethon like any other `read` invocation, with the same ambiguity safety net
and freshness header.

```bash
# Before (removed)
# scripts/<removed-cli> tom 10

# After (current)
valor-telegram read --user tom --limit 10
```

See [`docs/features/telegram-messaging.md`](telegram-messaging.md) for the
canonical `valor-telegram` reference, including the new `--chat-id`, `--user`,
and `--search` flags and the `AmbiguousChatError` disambiguation UX.

## Legacy Implementation (retained for bridge-IPC context)

The sections below describe the bridge-side IPC handler that still exists in
`bridge/telegram_bridge.py` but is no longer actively exercised. This
documentation is preserved so readers tracing IPC code in the bridge can find
historical context.

### Overview

The Bridge Message Query Tool originally provided a command-line interface
(now removed) to fetch Telegram message history from whitelisted users. It
solved the problem of accessing Telegram messages when the Telegram session
is exclusively held by the running bridge process.

### How the Bridge IPC Works

A lightweight file-based IPC system allows a CLI tool to request data from
the bridge:

1. CLI writes request to `data/message_query_request.json`
2. Bridge polls for requests every second
3. Bridge queries Telegram API using its active connection
4. Bridge writes result to `data/message_query_result.json`
5. CLI reads result and displays formatted output

### Request/Response Format

**Request JSON (`data/message_query_request.json`)**:

```json
{
  "user_id": 179144806,
  "username": "tom",
  "limit": 10,
  "requested_at": "2026-02-09T14:23:15.123456"
}
```

**Response JSON (`data/message_query_result.json`)** — success:

```json
{
  "success": true,
  "username": "tom",
  "user_id": 179144806,
  "count": 5,
  "messages": [
    {
      "id": 12345,
      "sender": "Tom",
      "date": "2026-02-09T14:23:15",
      "text": "Hey, can you check that PR?"
    }
  ],
  "processed_at": "2026-02-09T14:23:16.789012"
}
```

### Bridge Components (still in code)

**Bridge Handler** (`bridge/telegram_bridge.py::check_message_query_request()`):

- Polls `data/message_query_request.json` every second
- Executes `client.get_messages(user_id, limit=N)` using active Telegram client
- Formats messages with sender, date, and text
- Writes result to `data/message_query_result.json`
- Removes request file after processing

**Bridge Main Loop** (`bridge/telegram_bridge.py::message_query_loop()`):

- Background asyncio task
- Calls `check_message_query_request()` every second
- Runs continuously alongside message handling

## Files

| File | Purpose |
|------|---------|
| `tools/telegram_users.py` | Username resolution and whitelist loading (still used by `valor-telegram`) |
| `bridge/telegram_bridge.py` | Message query IPC handler and polling loop (retained, dormant) |
| `data/message_query_request.json` | IPC request file (no longer written by any CLI) |
| `data/message_query_result.json` | IPC result file (no longer written by any CLI) |
