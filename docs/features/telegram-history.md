# Telegram History & Link Collection

**Status**: Implemented
**Created**: 2026-01-19
**Implemented**: 2026-01-20
**Backend migrated to Redis**: 2026-02-24

---

## Overview

The Telegram History & Link Collection feature provides:

1. **Full message history** - All incoming Telegram messages are stored in Redis via Popoto
2. **Link collection** - URLs from whitelisted users are automatically extracted and stored with metadata
3. **Chat registry** - Chat ID to name mappings maintained in Redis

## Architecture

### Backend

All data is stored in **Redis** via Popoto ORM models. SQLite was removed as of 2026-02-24.

### Models

**`TelegramMessage`** (`models/telegram.py`) - Source of truth for all Telegram messages
- `msg_id` - Auto-generated key
- `chat_id` - Telegram chat ID (KeyField, used for filtering)
- `message_id` - Telegram message ID
- `direction` - "in" or "out" (KeyField)
- `sender` - Message sender name (KeyField)
- `content` - Full message content (up to 50,000 chars, no truncation)
- `timestamp` - Unix timestamp (SortedField, partitioned by chat_id)
- `message_type` - text, photo, voice, response, etc. (KeyField)
- TTL: 90 days (cleaned by daydream step 13)

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
- TTL: 90 days (cleaned by daydream step 13)

**`Chat`** (`models/chat.py`) - Chat ID to name mapping
- `chat_id` - Telegram chat ID (UniqueKeyField)
- `chat_name` - Human-readable name (KeyField)
- `chat_type` - private, group, supergroup, channel (KeyField)
- `updated_at` - Unix timestamp (SortedField)
- TTL: 90 days (cleaned by daydream step 13)

### Data Retention

- Redis models: 90-day TTL, cleaned by daydream `step_redis_cleanup()` (step 13)
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
    store_message,
    search_history,
    get_recent_messages,
    get_chat_stats,
    register_chat,
    list_chats,
    resolve_chat_id,
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

# Resolve chat name to ID
chat_id = resolve_chat_id("Dev: Valor")  # supports partial match

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

1. **Stores all incoming messages** - Every message the bridge receives is stored directly in Redis
2. **Extracts and stores links** - URLs from users in `TELEGRAM_LINK_COLLECTORS` are automatically saved
3. **Registers chats** - `register_chat()` called on each message to maintain the chat registry
4. **Stores full responses** - Valor's responses stored with no character cap

## Migration from SQLite (2026-02-24)

The SQLite backend was replaced with Redis/Popoto. To migrate existing data:

```bash
python scripts/migrate_sqlite_to_redis.py --dry-run  # preview
python scripts/migrate_sqlite_to_redis.py            # migrate
python scripts/migrate_sqlite_to_redis.py --verify   # verify counts
```

The SQLite database at `~/.valor/telegram_history.db` can be deleted after 30 days post-migration.

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
- Chat registration and resolution

## Future Enhancements (Not Yet Implemented)

- RediSearch / full-text search indexing (see plan for decision rationale)
- Web UI for browsing links
- Export to Notion/bookmarks
- Automatic categorization with AI
- Link health checking (detect dead links)
- Session tagging automation (tags field exists, automation is future work)
