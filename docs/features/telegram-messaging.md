# Telegram Messaging

Unified interface for reading and sending Telegram messages via the `valor-telegram` CLI.

## Overview

Consolidates two previously separate skills (`searching-message-history` and `get-telegram-messages`) into a single unified tool. Messages are read from a local SQLite cache populated by the bridge, while sending uses Telethon directly.

## CLI Reference

### Reading Messages

```bash
# Recent messages from a group
valor-telegram read --chat "Dev: Valor" --limit 10

# Recent messages from a DM user
valor-telegram read --chat "Tom" --limit 5

# Search by keyword
valor-telegram read --chat "Dev: Valor" --search "deployment"

# Time-filtered messages
valor-telegram read --chat "Dev: Valor" --since "1 hour ago"

# JSON output for programmatic use
valor-telegram read --chat "Dev: Valor" --limit 5 --json
```

### Sending Messages

```bash
# Text message
valor-telegram send --chat "Dev: Valor" "Hello world"

# File attachment
valor-telegram send --chat "Tom" --file ./report.pdf "Here's the report"

# Image with caption
valor-telegram send --chat "Dev: Valor" --image ./screenshot.png "Check this"

# Audio file
valor-telegram send --chat "Dev: Valor" --audio ./recording.mp3
```

### Listing Chats

```bash
valor-telegram chats
```

## Architecture

```
valor-telegram read
    ↓
resolve_chat(name) → chat_id
    ↓
SQLite cache (~/.valor/telegram_history.db)
    ↓
Format and display

valor-telegram send
    ↓
resolve_chat(name) → chat_id
    ↓
Telethon client (data/valor_bridge session)
    ↓
Telegram API
```

### Chat Resolution

Chat names are resolved in order:
1. **History database** (`chats` table) — matches group names
2. **DM whitelist** (`tools/telegram_users.py`) — matches user names
3. **Raw numeric ID** — used directly if name looks like a number

### Data Sources

| Component | Source | Purpose |
|-----------|--------|---------|
| Reading | SQLite (`~/.valor/telegram_history.db`) | Cached messages from bridge |
| Sending | Telethon (direct API) | Real-time message delivery |
| Chat names | SQLite `chats` table | Group name → chat_id mapping |
| User names | `dm_whitelist.json` | Username → user_id mapping |

## Files

| File | Purpose |
|------|---------|
| `tools/valor_telegram.py` | CLI implementation |
| `.claude/skills/telegram/SKILL.md` | Agent skill documentation |
| `tests/test_valor_telegram.py` | Test suite |

## Related

- [Telegram History](telegram-history.md) — underlying SQLite storage
- `config/SOUL.md` — agent persona references to this tool
