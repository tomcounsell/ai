# Telegram History & Link Collection

**Status**: Implemented
**Created**: 2026-01-19
**Implemented**: 2026-01-20
**Backend migrated to Redis**: 2026-02-24
**Storage gated to owned chats**: 2026-07 (issue #2020)

---

## Overview

The Telegram History & Link Collection feature provides:

1. **Message history (cache, not archive)** - Incoming messages **from machine-owned chats** (plus registered-bot messages) are stored in Redis via Popoto; unowned chats are read-through from the Telegram API on demand
2. **Link collection** - URLs from whitelisted users are automatically extracted and stored with metadata
3. **Chat registry** - Chat ID to name mappings maintained in Redis

### Cache-not-archive model

Redis is a **working-set cache** for machine-owned chats, not a durable archive of every Telegram message this machine ever sees. Telegram itself remains the complete, durable source of truth for chats this machine doesn't own.

- **Machine-owned chats** (any chat resolving to a project this machine serves, per [Single-Machine Ownership](single-machine-ownership.md)) have every inbound message written to `TelegramMessage` as before.
- **Registered bots** (`projects.<key>.telegram.bots[]`) are also stored even when they resolve no owned project, because `valor-telegram send --await-reply` polls recorded history to detect a bot's settled reply (issue #1574).
- **Unowned chats** — large group chats with no project configured on this machine — are no longer written to Redis at all. `valor-telegram read` falls back live to the Telegram/Telethon API for these chats, so history is still available, just not cached locally.

The gating predicate is `should_store_inbound(early_project_key, sender_id)` in `bridge/telegram_bridge.py`: it returns `True` if the chat already resolved to an owned project (`early_project_key is not None`), or if the sender is a registered bot (`find_project_for_bot(sender_id) is not None`). Otherwise the message is processed (responses, reactions, etc. still work) but never persisted to `TelegramMessage`. See issue #2020 for the gating rationale and #1574 for the bot carve-out.

### Retention unchanged

Gating storage collapses the volume of stored messages to just the machine's owned chats, which is what makes the existing 90-day TTL and the daily `redis-ttl-cleanup` sweep sensible again — TTL sweep cost and memory footprint now scale with owned-chat volume, not with every chat this machine happens to observe. The TTL value itself was **not** changed by this work; see Data Retention below.

## Architecture

### Backend

All data is stored in **Redis** via Popoto ORM models. SQLite was removed as of 2026-02-24.

### Models

**`TelegramMessage`** (`models/telegram.py`) - Source of truth for all Telegram messages
- `msg_id` - Auto-generated key
- `chat_id` - Telegram chat ID (KeyField, used for filtering)
- `message_id` - Telegram message ID (KeyField(null=True), enables O(1) indexed filter for reverse lookup)
- `direction` - "in" or "out" (KeyField)
- `sender` - Message sender name (KeyField)
- `content` - Full message content (up to 50,000 chars, no truncation)
- `timestamp` - Unix timestamp (SortedField, partitioned by chat_id)
- `message_type` - text, photo, voice, response, etc. (KeyField)
- TTL: 90 days (cleaned by the `redis-ttl-cleanup` reflection)

**`Link`** (`models/link.py`) - URLs shared in chats
- `link_id` - Auto-generated key
- `url` - The URL (KeyField)
- `chat_id` - Where it was shared (KeyField)
- `domain` - Extracted domain (KeyField)
- `sender` - Who shared it (KeyField)
- `status` - unread, read, or archived (KeyField)
- `timestamp` - Unix timestamp (SortedField)
- `tags` - ListField for categorization
- `ai_summary` - AI-generated summary (up to 50,000 chars)
- TTL: 90 days (cleaned by the `redis-ttl-cleanup` reflection)

**`Chat`** (`models/chat.py`) - Chat ID to name mapping
- `chat_id` - Telegram chat ID (UniqueKeyField)
- `chat_name` - Human-readable name (KeyField)
- `chat_type` - private, group, supergroup, channel (KeyField)
- `updated_at` - Unix timestamp (SortedField)
- TTL: 90 days (cleaned by the `redis-ttl-cleanup` reflection)

### Data Retention

- Redis models: 90-day TTL, cleaned by the `redis-ttl-cleanup` reflection (`reflections.maintenance.run_redis_ttl_cleanup`) — **unchanged** by the storage-gating work in #2020; only the volume of chats eligible for storage changed
- No SQLite backup after 2026-02-24 migration

## Configuration

Add to `.env`:

```bash
# Users whose forwarded links are automatically saved
TELEGRAM_LINK_COLLECTORS=tomcounsell
```

## API Reference

### Message Functions

```python
from tools.telegram_history import (
    AmbiguousChatError,
    ChatCandidate,
    store_message,
    search_history,
    get_recent_messages,
    get_chat_stats,
    register_chat,
    list_chats,
    resolve_chat_candidates,
    resolve_chat_id,
    resolve_chats_by_project,
    search_all_chats,
)

# Store a message (writes directly to Redis, no SQLite)
store_message(
    chat_id="12345",
    content="Hello world",
    sender="Tom",
    message_id=100,
    timestamp=datetime.now(),
    message_type="text",
)

# Search messages in a specific chat
results = search_history(
    query="python",
    chat_id="12345",
    max_results=10,
    max_age_days=30,
)

# Get recent messages
recent = get_recent_messages(
    chat_id="12345",
    limit=20,
)

# Get chat statistics
stats = get_chat_stats(chat_id="12345")

# Register a chat (called by bridge on every message)
register_chat(chat_id="12345", chat_name="Dev: Valor", chat_type="group")

# List all known chats with message counts
chats = list_chats()

# Resolve chat name to candidate list (issue #1163)
# Runs a 3-stage cascade (exact → case-insensitive exact → normalized
# substring), collects ALL matches per stage, returns them as
# ChatCandidate(chat_id, chat_name, last_activity_ts) sorted by
# last_activity_ts desc (None sorts last).
candidates = resolve_chat_candidates("Dev: Valor")
# candidates -> [ChatCandidate(chat_id="-100...", chat_name="Dev: Valor", last_activity_ts=...)]

# Resolve chat name to a single chat ID.
#
# Default (strict=False, Q2 = pick-most-recent-with-warning):
#   - Zero match → returns None.
#   - Single match → returns that chat_id.
#   - Multiple matches → returns the most-recent candidate's chat_id and
#     emits a logger.warning listing all candidates. No exception raised.
chat_id = resolve_chat_id("Dev: Valor")

# Strict mode: opt into AmbiguousChatError on >1 candidate. Used by the
# CLI when --strict is on the command line; scripted callers that need a
# non-zero exit code on ambiguity also use this path.
try:
    chat_id = resolve_chat_id("Dev: Valor", strict=True)
except AmbiguousChatError as e:
    # e.candidates is list[ChatCandidate]; render to the user.
    ...

# Defensive invariant (both paths): if the selection logic ever returns
# a candidate that is NOT the one with the maximum last_activity_ts,
# resolve_chat_id raises AmbiguousChatError unconditionally regardless
# of strict. This is a fail-loud guard against a broken sort or race.

# Narrow exception handling: resolve_chat_candidates catches only
# redis.RedisError / popoto.ModelException / popoto.QueryException,
# logs a warning, and returns []. It does NOT swallow arbitrary exceptions.

# Resolve a project_key to its set of chats (issue #1169).
# Scans Chat.query.all() and filters by Chat.project_key == project_key
# (project_key is a plain Field, not a KeyField — no indexed lookup).
# Returns list[ChatCandidate] sorted by last_activity_ts desc with
# chat_id ascending tiebreak. Chats with project_key=None are NEVER
# returned. Empty/whitespace project_key returns []. Failure returns []
# and logs a warning. Used by `valor-telegram read --project KEY` to
# union messages across every chat in the project.
project_chats = resolve_chats_by_project("psyoptimal")
# project_chats -> [ChatCandidate(...), ChatCandidate(...), ...]

# Search across all chats
results = search_all_chats(query="python", max_results=20)
```

### Link Functions

```python
from tools.telegram_history import (
    store_link,
    search_links,
    list_links,
    update_link,
    get_link_stats,
    get_link_by_url,
)

# Store a link (get-or-create by url+chat_id)
store_link(
    url="https://example.com/article",
    sender="Tom",
    chat_id="12345",
    message_id=100,
    title="Example Article",
    description="An article about examples",
    tags=["example", "tutorial"],
)

# Search links
results = search_links(
    query="python",        # Text search in URL, title, description
    domain="github.com",   # Filter by domain (exact match)
    sender="Tom",          # Filter by sender
    status="unread",       # Filter by status
    limit=20,
)

# List recent links with pagination
links = list_links(
    limit=20,
    offset=0,
    status="unread",
)

# Update a link (link_id is the link's auto-generated key)
update_link(
    link_id="<link_id>",
    status="read",
    tags=["important", "tutorial"],
    notes="Great article for reference",
)

# Get link statistics
stats = get_link_stats()

# Get link by URL (for caching AI summaries)
link = get_link_by_url("https://example.com", max_age_hours=24)
```

## Bridge Integration

The Telegram bridge (`bridge/telegram_bridge.py`) automatically:

1. **Stores messages from owned chats (plus registered bots)** - Gated by `should_store_inbound()`; unowned chats are read-through from the Telegram API instead of stored (see Cache-not-archive model above)
2. **Extracts and stores links** - URLs from users in `TELEGRAM_LINK_COLLECTORS` are automatically saved
3. **Registers chats** - `register_chat()` called on each stored message to maintain the chat registry
4. **Stores full responses** - Valor's responses stored with no character cap

## Testing

Run the tests:

```bash
pytest tests/tools/test_telegram_history.py -v
```

Tests use Redis db=1 (isolated via the `redis_test_db` autouse fixture) and cover:
- Message storage and retrieval
- Link storage with duplicate handling (get-or-create)
- Search functionality for both messages and links
- Filtering by domain, sender, and status
- Pagination
- Statistics
- Chat registration and resolution (single-chat and project-level)

## Future Enhancements (Not Yet Implemented)

- RediSearch / full-text search indexing (see plan for decision rationale)
- Web UI for browsing links
- Export to Notion/bookmarks
- Automatic categorization with AI
- Link health checking (detect dead links)
- ~~Session tagging automation~~ — shipped, see [Session Tagging](session-tagging.md)
