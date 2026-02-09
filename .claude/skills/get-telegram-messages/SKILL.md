---
name: get-telegram-messages
description: Fetch real-time Telegram messages via the running bridge. Use when you need the LATEST messages from Telegram (not cached in database).
allowed-tools: Bash
---

# Real-Time Telegram Messages

Fetches messages directly from Telegram API via the running bridge. Use this when you need the **actual latest messages** (the SQLite database may lag behind).

**CLI Tool**: `scripts/get-telegram-message-history`

## Usage

```bash
# Show help and available usernames
scripts/get-telegram-message-history --help

# Get last 5 messages from a user (default)
scripts/get-telegram-message-history Tom

# Get last N messages from a user
scripts/get-telegram-message-history Tom 10
scripts/get-telegram-message-history Kevin 3
```

## Available Users

Run `--help` to see current list. Common users:
- Tom
- Kevin
- Charlie

Usernames are case-insensitive.

## Output Format

```
=== Messages from Tom (179144806) ===
[2026-02-09 14:55] Tom: Message content here...
[2026-02-09 14:50] Valor: Response content here...
```

## When to Use This vs SQLite History

| Use Case | Tool |
|----------|------|
| Latest messages (real-time) | `get-telegram-message-history` |
| Search by keyword | SQLite (`searching-message-history` skill) |
| Messages from groups | SQLite |
| Historical analysis | SQLite |
| Links and summaries | SQLite |

## Requirements

- Bridge must be running (`./scripts/valor-service.sh status`)
- Tool times out after 10 seconds if bridge isn't responding

## Error Handling

- **Unknown user**: Shows error with list of valid usernames
- **Bridge not running**: "Bridge not responding - is it running?"
- **Timeout**: Check bridge status with `./scripts/valor-service.sh status`
