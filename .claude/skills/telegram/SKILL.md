---
name: telegram
description: "Use when reading or sending Telegram messages. Triggered by requests to check recent messages, search conversation history, or send messages/media to chats."
allowed-tools: Bash
user-invocable: false
---

# Telegram

Unified interface for reading and sending Telegram messages.

## PM Tool vs CLI Tool

There are two sending interfaces. Use the correct one for your context:

| Tool | Context | How It Works |
|------|---------|--------------|
| `python tools/send_telegram.py` | ChatSession (PM) | Queues via Redis, relay sends via Telethon, records msg_id for summarizer bypass |
| `valor-telegram send` | DevSession / CLI | Sends directly via Telethon, no Redis queue, no summarizer bypass |

**ChatSession (PM)** should always use `tools/send_telegram.py`. It supports text and file attachments via `--file`. Using `valor-telegram send` from ChatSession would bypass the Redis queue and break `has_pm_messages()` tracking.

**DevSession** uses `valor-telegram send` for direct CLI sends when needed.

## Reading Messages

**CLI**: `valor-telegram`

```bash
# Recent messages from a chat
valor-telegram read --chat "Dev: Valor" --limit 10

# Recent messages from a DM user
valor-telegram read --chat "Tom" --limit 5

# Search messages by keyword
valor-telegram read --chat "Dev: Valor" --search "deployment"

# Messages from a time range
valor-telegram read --chat "Dev: Valor" --since "1 hour ago"

# JSON output for parsing
valor-telegram read --chat "Dev: Valor" --limit 5 --json
```

## Sending Messages (CLI -- DevSession only)

```bash
# Send text message
valor-telegram send --chat "Dev: Valor" "Hello world"

# Send with file attachment
valor-telegram send --chat "Tom" "Check this screenshot" --file ./screenshot.png

# Send image with caption
valor-telegram send --chat "Dev: Valor" --image ./photo.jpg "Caption here"

# Send audio
valor-telegram send --chat "Dev: Valor" --audio ./recording.mp3
```

## Listing Known Chats

```bash
valor-telegram chats
```

## When to Use

- **Check what someone said**: `valor-telegram read --chat "Tom" --limit 10`
- **Find a past discussion**: `valor-telegram read --chat "Dev: Valor" --search "authentication"`
- **Get recent context**: `valor-telegram read --chat "Dev: Valor" --since "2 hours ago"`
- **Send a status update (DevSession)**: `valor-telegram send --chat "Dev: Valor" "Deployment complete"`
- **Share a file (DevSession)**: `valor-telegram send --chat "Tom" "Here's the report" --file ./report.pdf`

## Notes

- Chat names are resolved from the history database (groups) and DM whitelist (users)
- Messages are read from Redis via Popoto ORM (TelegramMessage model)
- Sending uses Telethon directly (requires bridge session and API credentials)
- Use `valor-telegram chats` if unsure of the exact chat name
