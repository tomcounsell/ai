# Bridge Message Query

DM message-history queries go through `valor-telegram read`. The bridge-side IPC
handler remains in `bridge/telegram_bridge.py` but is not invoked by any in-tree
CLI.

## Usage

```bash
valor-telegram read --user tom --limit 10
```

The `--user` flag forces resolution through the DM whitelist
(`tools/telegram_users.resolve_username`) and reads from Redis / Telethon like
any other `read` invocation, with the same ambiguity safety net and freshness
header.

See [`docs/features/telegram-messaging.md`](telegram-messaging.md) for the
canonical `valor-telegram` reference, including the `--chat-id`, `--user`, and
`--search` flags and the `AmbiguousChatError` disambiguation UX.

## Bridge IPC Handler

The bridge retains a lightweight file-based IPC handler
(`check_message_query_request` in `bridge/telegram_bridge.py`) that lets a CLI
tool request data from the bridge:

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

### Bridge Components

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
| `tools/telegram_users.py` | Username resolution and whitelist loading (used by `valor-telegram`) |
| `bridge/telegram_bridge.py` | Message query IPC handler and polling loop (dormant) |
| `data/message_query_request.json` | IPC request file (not written by any in-tree CLI) |
| `data/message_query_result.json` | IPC result file (not written by any in-tree CLI) |
