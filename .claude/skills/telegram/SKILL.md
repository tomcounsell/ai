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
| `python tools/send_message.py` | Agent session (PM/Teammate) | Queues via the canonical `TelegramRelayOutputHandler` pipeline (drafter validation → redundancy/RTR filters → `telegram:outbox:{session_id}`), records msg_id for summarizer bypass |
| `valor-telegram send` | Human operator / CLI | Operator CLI — **not an agent delivery path**. Queues via the same Redis relay (`bridge/telegram_relay.py`) but skips the canonical handler pipeline and summarizer-bypass recording |

**Agent sessions** should always use `tools/send_message.py`. It supports text, single file attachments, and multi-file albums via `--file` (repeatable, max 10 files), and prints the delivery outcome (sent / suppressed / deferred). `valor-telegram send` is the human-operator CLI: using it from an agent session would skip the canonical pipeline and summarizer-bypass recording and break `has_pm_messages()` tracking.

### Agent Tool Examples

```bash
# Text only
python tools/send_message.py "Status update message"

# Single file with caption
python tools/send_message.py "Screenshot attached" --file /path/to/screenshot.png

# Multi-file album (grouped as one Telegram album message)
python tools/send_message.py "PR review screenshots" --file before.png --file during.png --file after.png

# File only (no caption)
python tools/send_message.py --file /path/to/document.pdf
```

`valor-telegram send` remains available as the human-operator CLI (see below); it is not an agent delivery path.

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

# Cross-chat project read -- unions every chat tagged with project_key
valor-telegram read --project psyoptimal --limit 20
```

`--chat`, `--chat-id`, `--user`, and `--project` are **mutually exclusive** -- pick one.

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

#### Strict mode (`--strict`): exit 1 with candidate list

For scripted callers that need a hard failure on ambiguity, pass `--strict`
to the `read` subcommand. Under `--strict`, the CLI exits non-zero and
prints the candidate list on stdout instead of picking:

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

### Cross-Chat Project Reads (`--project`)

A project (e.g. PsyOPTIMAL) often spans multiple Telegram chats. Pass
`--project PROJECT_KEY` to union messages across every chat with the
matching `Chat.project_key`, interleaved chronologically:

```
valor-telegram read --project psyoptimal --limit 20
```

Output starts with a one-line **project freshness header** summarizing the
unioned chat set:

```
[project=psyoptimal · 3 chats: PsyOPTIMAL, PM: PsyOptimal, Dev: PsyOPTIMAL · last activity: 3m ago]
[2026-04-25 09:30] [PsyOPTIMAL] alice: kicking off the sprint
[2026-04-25 09:32] [PM: PsyOptimal] tom: I'll grab the standup notes
[2026-04-25 10:15] [Dev: PsyOPTIMAL] bob: shipped the auth fix
```

Each line is tagged with the originating `[chat_name]` (truncated to 25
chars for long names). `--limit` applies to the **merged total**, not per
chat — `--limit 20` returns the 20 most recent across the union.

`--json` output enriches each message dict with `chat_id` and `chat_name`.
The single-chat JSON shape is unchanged — these fields appear only under
`--project`.

`--strict` is rejected with `--project` (it has no name to resolve). To
discover which chats would be unioned, run `valor-telegram chats --project KEY`.

## Sending Messages (CLI -- human operator only)

```bash
# Send text message
valor-telegram send --chat "Dev: Valor" "Hello world"

# Send with file attachment
valor-telegram send --chat "Tom" "Check this screenshot" --file ./screenshot.png

# Send image with caption
valor-telegram send --chat "Dev: Valor" --image ./photo.jpg "Caption here"

# Send audio
valor-telegram send --chat "Dev: Valor" --audio ./recording.mp3

# Reply to a message / post into a forum topic (message ID required for topics)
valor-telegram send --chat "Forum Group" --reply-to 123 "Message to topic"

# Native voice-message bubble (OGG/Opus via --audio; relay deletes the temp file after send)
valor-telegram send --chat "Dev: Valor" --voice-note --cleanup-after-send --audio /tmp/out.ogg

# E2E probe a registered bot -- blocks until its streamed reply settles
valor-telegram send --chat 8837490628 --await-reply --timeout 900 "deploy status?"
```

`--await-reply` is only valid against a bot registered under
`projects.<key>.telegram.bots[]`; add `--json` for a structured transcript.

## Listing Known Chats

```bash
# All known chats (sorted by last message desc)
valor-telegram chats

# Filter by normalized substring of chat name
valor-telegram chats --search "psy"

# Normalization-aware: "PM psy" matches "PM: PsyOptimal"
valor-telegram chats --search "PM psy"

# Filter by project_key (every chat that --project would union)
valor-telegram chats --project psyoptimal

# Combine both filters
valor-telegram chats --project psyoptimal --search "dev"

# JSON output (always includes project_key)
valor-telegram chats --search "psy" --json
```

## Notes

- Chat names are resolved from the history database (groups) and DM whitelist (users); use `valor-telegram chats --search PATTERN` if unsure of the exact name
- Reads hit Redis via Popoto ORM (TelegramMessage model); sends route through the bridge relay (requires the bridge to be running)
