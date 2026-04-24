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

# Explicit numeric chat ID — bypasses the name resolver
valor-telegram read --chat-id -1001234567 --limit 10

# DM user by whitelisted username (replaces the removed bridge-IPC script)
valor-telegram read --user tom --limit 10
```

The three target flags `--chat`, `--chat-id`, and `--user` are **mutually
exclusive**. Pick one.

#### Output Header (freshness signal)

Each successful read prepends a one-line header showing chat name, chat_id,
and last-activity age so the reader can tell whether the chat is active or
quiet:

```
[Dev: Valor · chat_id=-1001234567 · last activity: 3m ago]
[2026-04-24 10:45] Tom: shipped the fix
...
```

Age format: `<1m ago`, `5m ago`, `3h ago`, `2d ago`, or `never` (chat
registered but never updated). When `--chat-id` points to a chat with no
stored metadata, the header falls back to `[chat_id=N · last activity: never]`.

#### Ambiguity Errors

If a `--chat` name matches more than one chat, the tool exits non-zero with
a stderr candidate list rather than silently picking one:

```
Ambiguous chat name. 2 candidates (most recent first):
  -1001234567  PM: PsyOptimal       last: 3m ago
  -1009876543  PsyOptimal           last: 2d ago
Re-run with --chat-id <id> or a more specific --chat string.
```

This replaces the previous silent first-match behavior (issue #1163). The
resolver now collects all candidates through a 3-stage cascade (exact →
case-insensitive exact → normalized substring), sorts them by last activity
desc, and raises `AmbiguousChatError` when more than one survives.

#### Zero-Match "Did You Mean"

When the name resolves to zero candidates, the tool prints the top-3 nearest
chats (by last activity) to stderr:

```
No chat matched 'PsyTeem'. Did you mean:
  -1001234567  PsyTeam                last: 3m ago
  -1009876543  PsyArchive             last: 2d ago
```

#### Name Normalization

Comparisons are symmetric on both sides: lowercase, whitespace-collapse, and
strip `: - | _`. That means `PM PsyOptimal` resolves to `PM: PsyOptimal`
(missing colon tolerated), and `dev_valor` / `dev valor` are treated as the
same name. Emoji and non-ASCII text are preserved.

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
# All known chats (sorted by last message desc)
valor-telegram chats

# Filter by normalized substring of chat name
valor-telegram chats --search "psy"

# Normalization-aware: "PM psy" matches "PM: PsyOptimal"
valor-telegram chats --search "PM psy"

# JSON output
valor-telegram chats --search "psy" --json
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
1. **Chat model** (Redis `Chat` Popoto model) — matches group names through
   `resolve_chat_candidates`, a 3-stage cascade (exact → case-insensitive
   exact → normalized substring) that collects **all** candidates per stage
   and sorts by `Chat.updated_at` desc. When more than one candidate
   survives, raises `AmbiguousChatError` (see above).
2. **DM whitelist** (`tools/telegram_users.py`) — matches user names. The
   `--user` flag forces this path exclusively.
3. **Raw numeric ID** — used directly when `--chat` looks like a number, or
   explicitly via `--chat-id`.

`resolve_chat_id(name, *, allow_ambiguous=False)` is a thin wrapper that
delegates to `resolve_chat_candidates` and returns the chat_id when a unique
match is found. `allow_ambiguous=True` returns the most-recent candidate and
emits a `logger.warning`; the CLI never sets this flag (production callers
surface the exception so the user disambiguates explicitly).

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
- `config/personas/segments/tools.md` -- agent persona references to this tool
