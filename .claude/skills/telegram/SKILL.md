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
| `python tools/send_telegram.py` | PM session | Queues via Redis, relay sends via Telethon, records msg_id for summarizer bypass |
| `valor-telegram send` | Dev session / CLI | Sends directly via Telethon, no Redis queue, no summarizer bypass |

**PM sessions** should always use `tools/send_telegram.py`. It supports text, single file attachments, and multi-file albums via `--file` (repeatable, max 10 files). Using `valor-telegram send` from a PM session would bypass the Redis queue and break `has_pm_messages()` tracking.

### PM Tool Examples

```bash
# Text only
python tools/send_telegram.py "Status update message"

# Single file with caption
python tools/send_telegram.py "Screenshot attached" --file /path/to/screenshot.png

# Multi-file album (grouped as one Telegram album message)
python tools/send_telegram.py "PR review screenshots" --file before.png --file during.png --file after.png

# File only (no caption)
python tools/send_telegram.py --file /path/to/document.pdf
```

**Dev sessions** use `valor-telegram send` for direct CLI sends when needed.

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

# Explicit numeric chat ID -- bypasses name resolution entirely
valor-telegram read --chat-id -1001234567 --limit 10

# Force DM path with a whitelisted username
valor-telegram read --user tom --limit 10
```

`--chat`, `--chat-id`, and `--user` are **mutually exclusive** -- pick one.

### Freshness Header

Every successful read prints a header line BEFORE the messages so you can
tell which chat was queried and whether it is active:

```
[Dev: Valor · chat_id=-1001234567 · last activity: 3m ago]
```

Age format: `<1m ago` / `Xm ago` / `Xh ago` / `Xd ago` / `never`. If the
freshness header says `3d ago` but you expect recent activity, that is a
signal you resolved to the wrong chat -- re-run with `--chat-id` or a more
specific `--chat`.

### Handling Ambiguity (default: pick most recent + warn)

If `--chat` matches more than one chat, the **default** behavior is to pick
the **most recently active** candidate, log a warning to stderr listing all
candidates, and proceed with exit 0. The freshness header is still printed,
so you can tell which chat was actually used:

```
WARNING: Ambiguous chat name 'PsyOptimal' matched 2 candidates; picking most recent:
  -1001234567  PM: PsyOptimal       last: 3m ago   <-- selected
  -1009876543  PsyOptimal           last: 2d ago
[PM: PsyOptimal · chat_id=-1001234567 · last activity: 3m ago]
... messages ...
```

**Always read the freshness header** before trusting the messages. If the
selected chat is not the one you wanted, re-run with `--chat-id <id>` or a
more specific `--chat` string.

#### Strict mode (`--strict`): exit 1 with candidate list

For scripted callers that need a hard failure on ambiguity, pass `--strict`
to the `read` subcommand. Under `--strict`, the CLI exits non-zero and
prints the candidate list on stderr instead of picking:

```
valor-telegram read --chat "PsyOptimal" --strict --limit 10
```

```
Ambiguous chat name. 2 candidates (most recent first):
  -1001234567  PM: PsyOptimal       last: 3m ago
  -1009876543  PsyOptimal           last: 2d ago
Re-run with --chat-id <id> or a more specific --chat string.
```

**Parsing tip**: the first column is the `chat_id` (leading `-` for groups,
no prefix for users). Pick the right row and re-run with `--chat-id <id>`.
`--strict` is only on `read`; `send` always uses the most-recent default.

### Handling Zero-Match ("did you mean")

If no chat matched, the tool prints up to 3 nearest chats sorted by recency:

```
No chat matched 'PsyTeem'. Did you mean:
  -1001234567  PsyTeam                last: 3m ago
  -1009876543  PsyArchive             last: 2d ago
```

## Sending Messages (CLI -- Dev session only)

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
# All known chats (sorted by last message desc)
valor-telegram chats

# Filter by normalized substring of chat name
valor-telegram chats --search "psy"

# Normalization-aware: "PM psy" matches "PM: PsyOptimal"
valor-telegram chats --search "PM psy"

# JSON output
valor-telegram chats --search "psy" --json
```

## When to Use

- **Check what someone said**: `valor-telegram read --chat "Tom" --limit 10`
- **Find a past discussion**: `valor-telegram read --chat "Dev: Valor" --search "authentication"`
- **Get recent context**: `valor-telegram read --chat "Dev: Valor" --since "2 hours ago"`
- **Send a status update (Dev session)**: `valor-telegram send --chat "Dev: Valor" "Deployment complete"`
- **Share a file (Dev session)**: `valor-telegram send --chat "Tom" "Here's the report" --file ./report.pdf`
- **Discover chats by fragment**: `valor-telegram chats --search "psy"`

## Notes

- Chat names are resolved from the history database (groups) and DM whitelist (users)
- Messages are read from Redis via Popoto ORM (TelegramMessage model)
- Sending uses Telethon directly (requires bridge session and API credentials)
- Use `valor-telegram chats --search PATTERN` if unsure of the exact chat name
- **Always read the freshness header** before trusting the messages beneath it. A stale `last activity` value is the cheapest possible signal that you resolved to the wrong chat.
