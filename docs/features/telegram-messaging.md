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

# Cross-chat project read — unions every chat tagged with project_key="psyoptimal"
valor-telegram read --project psyoptimal --limit 20
```

The four target flags `--chat`, `--chat-id`, `--user`, and `--project` are
**mutually exclusive**. Pick one.

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

#### Ambiguity Handling

If a `--chat` name matches more than one chat, the **default** behavior is
to pick the **most recently active** candidate, log a stderr warning listing
all candidates, and proceed with exit 0:

```
WARNING: Ambiguous chat name 'PsyOptimal' matched 2 candidates; picking most recent:
  -1001234567  PM: PsyOptimal       last: 3m ago   <-- selected
  -1009876543  PsyOptimal           last: 2d ago
[PM: PsyOptimal · chat_id=-1001234567 · last activity: 3m ago]
... messages ...
```

The freshness header that follows confirms which chat was selected; always
read it before trusting the messages.

For scripted callers that need a hard failure on ambiguity, pass `--strict`
to the `read` subcommand. Under `--strict`, the CLI exits non-zero with a
stderr candidate list instead of picking:

```
$ valor-telegram read --chat "PsyOptimal" --strict --limit 10
Ambiguous chat name. 2 candidates (most recent first):
  -1001234567  PM: PsyOptimal       last: 3m ago
  -1009876543  PsyOptimal           last: 2d ago
Re-run with --chat-id <id> or a more specific --chat string.
```

This replaces the previous silent first-match behavior (issue #1163). The
resolver now collects all candidates through a 3-stage cascade (exact →
case-insensitive exact → normalized substring) and sorts them by last
activity desc; the default path picks the head of that sorted list and
warns, while `--strict` raises `AmbiguousChatError`. `--strict` is only on
`read`; `send` always uses the most-recent default.

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

### Cross-Chat Project Reads

A single project (e.g. PsyOPTIMAL) often spans multiple Telegram chats — a
main group, a PM sidebar, a Dev channel. Use `--project PROJECT_KEY` to read
the most-recent N messages **across every chat** with that `project_key`,
interleaved chronologically:

```bash
valor-telegram read --project psyoptimal --limit 20
```

Output begins with a one-line **project freshness header** summarizing the
unioned chat set and last activity across the union, followed by each
message tagged with the originating chat name:

```
[project=psyoptimal · 3 chats: PsyOPTIMAL, PM: PsyOptimal, Dev: PsyOPTIMAL · last activity: 3m ago]
[2026-04-25 09:30] [PsyOPTIMAL] alice: kicking off the sprint
[2026-04-25 09:32] [PM: PsyOptimal] tom: I'll grab the standup notes
[2026-04-25 10:15] [Dev: PsyOPTIMAL] bob: shipped the auth fix
...
```

Per-line `[chat_name]` tags are truncated to 25 characters with an
ellipsis when longer; the full name is in the header (and in JSON output).
If the project header would list more than 5 chats, it truncates to the 5
most-recent and appends `... +M more`.

#### `--limit` semantics

`--limit` applies to the **merged total**, NOT per chat. `--limit 20`
returns the 20 most-recent messages across the entire union. To bias
coverage toward each chat, run a single-chat `read` per chat instead.

#### JSON output

`--project --json` emits a list of message dicts, each enriched with
`chat_id` and `chat_name` so downstream consumers can re-attribute messages
to their source chat:

```bash
valor-telegram read --project psyoptimal --json
```

```json
[
  {
    "id": "...",
    "message_id": 1234,
    "chat_id": "-1001234567",
    "chat_name": "PsyOPTIMAL",
    "sender": "alice",
    "content": "kicking off the sprint",
    "timestamp": "2026-04-25T09:30:00",
    "message_type": "text"
  },
  ...
]
```

The single-chat JSON shape is **unchanged** — `chat_id` and `chat_name` are
added only under `--project`.

#### Zero-match path

If no chats are tagged with the requested `project_key`, the CLI exits 1
with a stderr hint:

```
$ valor-telegram read --project unknown
No chats found for project 'unknown'. Run `valor-telegram chats --project unknown` to verify.
```

Use `valor-telegram chats --project PROJECT_KEY` to list every chat that
would be unioned (see [Listing Chats](#listing-chats) below).

#### Mutex with `--strict`

`--strict` is a **name-resolution** flag — it has no meaning under
`--project` (which never goes through name resolution). Combining the two
is rejected explicitly:

```
$ valor-telegram read --project psyoptimal --strict
Error: --strict has no effect with --project; remove one of them.
```

#### Project tagging

`Chat.project_key` is written by the bridge on every message receipt, so
any active chat will have a current value. Chats with `project_key=None`
(never tagged, or registered before the bridge gained the writes) are
**never** matched by `--project` — they appear only in unfiltered
`chats` output. No cleanup script is needed; inactive stale rows naturally
fall out of the project set.

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

# Filter by project_key (every chat that --project psyoptimal would union)
valor-telegram chats --project psyoptimal

# Both filters apply when combined
valor-telegram chats --project psyoptimal --search "dev"

# JSON output (always includes `project_key` per chat)
valor-telegram chats --json
valor-telegram chats --project psyoptimal --json
```

`--project` filters by exact match on `Chat.project_key`. `--json` output
includes `project_key` on every chat dict regardless of whether `--project`
is set. Empty/whitespace `--project` is rejected with exit 1.

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
   and sorts by `Chat.updated_at` desc (deterministic tiebreak on `chat_id`).
   When more than one candidate survives, the **default path returns the
   most-recently-active candidate and emits a `logger.warning` listing all
   candidates**; `strict=True` (CLI `--strict`) raises `AmbiguousChatError`
   instead.
2. **DM whitelist** (`tools/telegram_users.py`) — matches user names. The
   `--user` flag forces this path exclusively.
3. **Raw numeric ID** — used directly when `--chat` looks like a number, or
   explicitly via `--chat-id`.

`resolve_chat_id(name, *, strict=False)` is a thin wrapper that delegates
to `resolve_chat_candidates` and returns a `chat_id`:
- Zero match → `None`.
- Single match → that `chat_id`.
- Multiple matches, `strict=False` (default) → the most-recent candidate's
  `chat_id`, plus a `logger.warning` listing all candidates for audit.
- Multiple matches, `strict=True` → raises `AmbiguousChatError(candidates)`.

For the cross-chat project path, `resolve_chats_by_project(project_key)`
scans `Chat.query.all()` and returns `list[ChatCandidate]` for every chat
whose `Chat.project_key` matches exactly. Sorted by `last_activity_ts`
desc with the same `chat_id` ascending tiebreak as the single-chat
resolver. Chats with `project_key=None` are never returned. Failure
returns `[]` and logs a warning.

A defensive invariant also raises `AmbiguousChatError` **regardless of
`strict`** if the selection logic ever picks a non-max candidate — fail
loud rather than silently return the wrong answer. Production CLI callers
pass `strict=True` only when `--strict` is on the command line; they let
the `logger.warning` surface the ambiguity to the user on the default
path and exit 0.

Empty or whitespace-only values for `--chat`, `--user`, and
`chats --search` are rejected at the CLI layer before reaching the
resolver — an empty string is never a valid chat reference and would
either substring-match all records or pass through silently.

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
