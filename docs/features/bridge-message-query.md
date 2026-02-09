# Bridge Message Query Tool

**Status**: Implemented
**Created**: 2026-02-09
**Implemented**: 2026-02-09

---

## Overview

The Bridge Message Query Tool provides a command-line interface to fetch Telegram message history from whitelisted users. It solves the problem of accessing Telegram messages when the Telegram session is exclusively held by the running bridge process.

## Problem Statement

The Telegram bridge maintains an exclusive connection to Telegram using Telethon. This means:

- Only one process can access the Telegram session at a time
- Direct database queries are limited to metadata, not actual Telegram content
- Fetching recent messages requires coordinating with the bridge process

Previously, there was no way to query actual message content from Telegram without stopping the bridge or building complex session-sharing mechanisms.

## Solution

A lightweight file-based IPC system allows CLI tools to request data from the bridge:

1. **CLI writes request** to `data/message_query_request.json`
2. **Bridge polls** for requests every second
3. **Bridge queries** Telegram API using its active connection
4. **Bridge writes result** to `data/message_query_result.json`
5. **CLI reads result** and displays formatted output

## Usage

### Basic Examples

```bash
# Show help with available usernames
get-telegram-message-history --help

# Get last 5 messages from Tom (default)
get-telegram-message-history tom

# Get last 10 messages from Kevin
get-telegram-message-history kevin 10

# Case-insensitive username matching
get-telegram-message-history TOM
```

### Output Format

```
Querying 5 messages for tom...

Found 5 messages:

[2026-02-09 14:23:15] Tom: Hey, can you check that PR?
[2026-02-09 13:45:02] Valor: Sure, looking at it now
[2026-02-09 12:30:44] Tom: I pushed the latest changes
[2026-02-09 11:15:23] Valor: Thanks for the update
[2026-02-09 10:05:12] Tom: Morning!
```

## How It Works

### Architecture

```
┌─────────────────┐         ┌──────────────────┐         ┌──────────────┐
│  CLI Tool       │         │  File-Based IPC  │         │    Bridge    │
│  (scripts/)     │         │  (data/*.json)   │         │  (asyncio)   │
└─────────────────┘         └──────────────────┘         └──────────────┘
         │                            │                            │
         │  1. Write request          │                            │
         ├───────────────────────────>│                            │
         │                            │                            │
         │  2. Poll for result        │    3. Poll for requests    │
         │    (every 0.5s)            │<───────────────────────────┤
         │                            │                            │
         │                            │    4. Read request         │
         │                            ├───────────────────────────>│
         │                            │                            │
         │                            │    5. Query Telegram API   │
         │                            │                            │
         │                            │    6. Write result         │
         │                            │<───────────────────────────┤
         │                            │                            │
         │  7. Read result            │                            │
         │<───────────────────────────┤                            │
         │                            │                            │
         │  8. Display & cleanup      │                            │
         │                            │                            │
```

### Components

**1. CLI Tool** (`scripts/get-telegram-message-history`)
- Validates username using `tools/telegram_users.py`
- Writes request JSON
- Polls for result with 10-second timeout
- Formats and displays messages
- Cleans up IPC files

**2. User Lookup** (`tools/telegram_users.py`)
- Loads whitelist from `~/Desktop/claude_code/dm_whitelist.json`
- Maps usernames to Telegram user IDs (case-insensitive)
- Provides validation before making requests

**3. Bridge Handler** (`bridge/telegram_bridge.py::check_message_query_request()`)
- Polls `data/message_query_request.json` every second
- Executes `client.get_messages(user_id, limit=N)` using active Telegram client
- Formats messages with sender, date, and text
- Writes result to `data/message_query_result.json`
- Removes request file after processing

**4. Bridge Main Loop** (`bridge/telegram_bridge.py::message_query_loop()`)
- Background asyncio task
- Calls `check_message_query_request()` every second
- Runs continuously alongside message handling

## Request/Response Format

### Request JSON (`data/message_query_request.json`)

```json
{
  "user_id": 179144806,
  "username": "tom",
  "limit": 10,
  "requested_at": "2026-02-09T14:23:15.123456"
}
```

### Response JSON (`data/message_query_result.json`)

Success:
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
    },
    {
      "id": 12344,
      "sender": "Valor",
      "date": "2026-02-09T13:45:02",
      "text": "Sure, looking at it now"
    }
  ],
  "processed_at": "2026-02-09T14:23:16.789012"
}
```

Error:
```json
{
  "success": false,
  "error": "Failed to fetch messages: User not found",
  "username": "unknown",
  "processed_at": "2026-02-09T14:23:16.789012"
}
```

## Error Cases & Troubleshooting

### Unknown Username

```bash
$ get-telegram-message-history unknown
Error: Unknown username 'unknown'

Available usernames:
  - kevin
  - tom
```

**Fix**: Use a valid username from the whitelist (`~/Desktop/claude_code/dm_whitelist.json`)

### Bridge Not Running

```bash
$ get-telegram-message-history tom

Querying 5 messages for tom...

Error: Bridge not responding - is it running?
Check bridge status: ./scripts/valor-service.sh status
```

**Fix**: Start or restart the bridge:
```bash
./scripts/valor-service.sh status
./scripts/valor-service.sh start
```

### Permission Issues

```bash
Error writing request file: [Errno 13] Permission denied
```

**Fix**: Ensure `data/` directory exists and is writable:
```bash
mkdir -p data/
chmod 755 data/
```

### Timeout with Running Bridge

If bridge is running but not responding:

1. **Check bridge logs**:
   ```bash
   tail -20 logs/bridge.log
   ```

2. **Look for errors** in message query polling loop

3. **Restart bridge**:
   ```bash
   ./scripts/valor-service.sh restart
   ```

## Technical Details

### Polling Parameters

**CLI polling**:
- Interval: 0.5 seconds
- Timeout: 10 seconds (20 attempts)
- Action on timeout: Error message + exit

**Bridge polling**:
- Interval: 1 second
- Continuous: Runs until bridge stops
- Error handling: Logs exceptions, continues polling

### File Cleanup

The CLI tool ensures cleanup in all cases:
- On success: Removes both request and result files
- On error: Removes request file to prevent stale requests
- On timeout: Removes request file before exit

The bridge removes the request file after processing to signal completion.

### Message Formatting

Messages include:
- **Date**: ISO format converted to human-readable timestamp
- **Sender**: Display name from Telegram
- **Text**: Message content (empty for media-only messages)

Media messages show empty text; future enhancement could add media type indicators.

## Integration Points

### Whitelist Configuration

The tool uses the existing DM whitelist system:
- **Location**: `~/Desktop/claude_code/dm_whitelist.json`
- **Format**: Same as bridge whitelist
- **Shared**: Both bridge and CLI use `tools/telegram_users.py`

### Data Directory

All IPC files live in `data/`:
- `data/message_query_request.json` - CLI writes, bridge reads
- `data/message_query_result.json` - Bridge writes, CLI reads

This directory is `.gitignore`d and local to each machine.

## Future Enhancements

Potential improvements (not yet implemented):

- **Search queries**: Full-text search instead of just recent messages
- **Date range filtering**: Fetch messages within specific time windows
- **Media support**: Include media type and file references in output
- **JSON output mode**: Machine-readable output for scripting
- **Multiple users**: Query messages from multiple users in one call
- **Conversation threads**: Fetch entire conversation context
- **Export formats**: Save to CSV, Markdown, or other formats

## Files

| File | Purpose |
|------|---------|
| `scripts/get-telegram-message-history` | CLI tool entry point |
| `tools/telegram_users.py` | Username resolution and whitelist loading |
| `bridge/telegram_bridge.py` | Message query handler and polling loop |
| `data/message_query_request.json` | IPC request file (transient) |
| `data/message_query_result.json` | IPC result file (transient) |

## Design Principles

Per CLAUDE.md development principles:

- **Intelligent over rigid**: Uses existing Telegram session intelligently
- **No legacy tolerance**: Clean file-based IPC, no complex infrastructure
- **Context collection**: Messages provide context for agent conversations
- **Minimal tooling**: Simple CLI, no new dependencies or services
