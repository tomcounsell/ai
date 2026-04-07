# Telegram Messaging

Unified interface for reading and sending Telegram messages via the `valor-telegram` CLI.

## Overview

Consolidates two previously separate skills (`searching-message-history` and `get-telegram-messages`) into a single unified tool. Messages are read from Redis (Popoto ORM) populated by the bridge, while sending routes through the Redis outbox relay (requires bridge to be running).

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

Requires the bridge to be running (`./scripts/valor-service.sh status`).

```bash
# Text message
valor-telegram send --chat "Dev: Valor" "Hello world"

# File attachment
valor-telegram send --chat "Tom" --file ./report.pdf "Here's the report"

# Image with caption
valor-telegram send --chat "Dev: Valor" --image ./screenshot.png "Check this"

# Audio file
valor-telegram send --chat "Dev: Valor" --audio ./recording.mp3

# Forum group / topic (reply-to required)
valor-telegram send --chat "Forum Group" --reply-to 123 "Message to topic"
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
Redis (Popoto TelegramMessage/Chat models)
    ↓
No results? → Telethon fallback (Telegram API)
    ↓
Format and display

valor-telegram send
    ↓
resolve_chat(name) → chat_id
    ↓
Redis outbox queue (telegram:outbox:cli-{timestamp})
    ↓
bridge/telegram_relay.py → Telegram API
```

### Chat Resolution

Chat names are resolved in order:
1. **Chat model** (Redis `Chat` Popoto model) — matches group names
2. **DM whitelist** (`tools/telegram_users.py`) — matches user names
3. **Raw numeric ID** — used directly if name looks like a number

### Data Sources

| Component | Source | Purpose |
|-----------|--------|---------|
| Reading | Redis (Popoto `TelegramMessage` model), Telethon fallback | Messages stored by bridge, with API fallback |
| Sending | Redis outbox relay (`bridge/telegram_relay.py`) | Queued via `telegram:outbox:cli-{timestamp}`; bridge delivers |
| Chat names | Redis (Popoto `Chat` model) | Group name → chat_id mapping |
| User names | `projects.json` (`dms.whitelist`) | Name → user_id mapping |

## Files

| File | Purpose |
|------|---------|
| `tools/valor_telegram.py` | CLI implementation |
| `.claude/skills/telegram/SKILL.md` | Agent skill documentation |
| `tests/unit/test_valor_telegram.py` | Test suite |

## PM Tool vs CLI Tool

Both `valor-telegram send` and `tools/send_telegram.py` route through the Redis outbox relay (`bridge/telegram_relay.py`), but use different session ID prefixes so the relay can distinguish their origins.

| Tool | Context | Session ID prefix | File Support |
|------|---------|------------------|--------------|
| `valor-telegram send` | Dev session / CLI | `cli-{unix_timestamp}` | `--file`, `--image`, `--audio`, `--reply-to` |
| `python tools/send_telegram.py` | PM session (PM) | Session UUID | `--file` (repeatable, max 10 for albums; auto-detects media type) |

See [PM Telegram Tool](pm-telegram-tool.md) for details on the PM send path.

## Related

- [Telegram History](telegram-history.md) — underlying Redis/Popoto storage
- [PM Telegram Tool](pm-telegram-tool.md) — PM session self-messaging with file attachments and multi-file albums
- `config/SOUL.md` — agent persona references to this tool
