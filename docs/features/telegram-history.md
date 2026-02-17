# Telegram History & Link Collection

**Status**: Implemented
**Created**: 2026-01-19
**Implemented**: 2026-01-20

---

## Overview

The Telegram History & Link Collection feature provides:

1. **Full message history** - All incoming Telegram messages are stored locally in SQLite
2. **Link collection** - URLs from whitelisted users are automatically extracted and stored with metadata

## Architecture

### Database Location

`~/.valor/telegram_history.db` - SQLite database containing both messages and links.

### Tables

**messages** - Stores all incoming Telegram messages
- `id` - Primary key
- `chat_id` - Telegram chat ID
- `message_id` - Telegram message ID
- `sender` - Message sender name
- `content` - Message text content
- `timestamp` - When the message was sent
- `message_type` - Type of message (text, photo, voice, etc.)

**links** - Stores URLs shared by whitelisted users
- `id` - Primary key
- `url` - The original URL
- `final_url` - URL after redirects (if different)
- `title` - Page title
- `description` - Page meta description
- `domain` - Extracted domain for filtering
- `sender` - Who shared the link
- `chat_id` - Where it was shared
- `message_id` - Original message ID
- `timestamp` - When it was shared
- `tags` - JSON array of tags
- `notes` - User notes
- `status` - unread, read, or archived
- `ai_summary` - Optional AI-generated summary

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
)

# Store a message
store_message(
    chat_id="12345",
    content="Hello world",
    sender="Tom",
    message_id=100,
    timestamp=datetime.now(),
    message_type="text",
)

# Search messages
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
```

### Link Functions

```python
from tools.telegram_history import (
    store_link,
    search_links,
    list_links,
    update_link,
    get_link_stats,
)

# Store a link
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
    domain="github.com",   # Filter by domain
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

# Update a link
update_link(
    link_id=1,
    status="read",
    tags=["important", "tutorial"],
    notes="Great article for reference",
)

# Get link statistics
stats = get_link_stats()
```

## Bridge Integration

The Telegram bridge (`bridge/telegram_bridge.py`) automatically:

1. **Stores all incoming messages** - Every message the bridge receives is stored for history
2. **Extracts and stores links** - URLs from users in `TELEGRAM_LINK_COLLECTORS` are automatically saved

### How It Works

```python
# In the message handler:

# 1. Store ALL incoming messages
store_message(
    chat_id=str(event.chat_id),
    content=text,
    sender=sender_name,
    message_id=message.id,
    timestamp=message.date,
    message_type="text" if not message.media else media_type,
)

# 2. For whitelisted senders, extract and store links
if sender_username.lower() in LINK_COLLECTORS:
    urls = extract_urls(text)
    for url in urls['urls']:
        store_link(
            url=url,
            sender=sender_name,
            chat_id=str(event.chat_id),
            message_id=message.id,
        )
```

## Testing

Run the tests:

```bash
pytest tests/tools/test_telegram_history.py -v
```

The test suite covers:
- Message storage and retrieval
- Link storage with duplicate handling
- Search functionality for both messages and links
- Filtering by domain, sender, and status
- Pagination
- Statistics

## Future Enhancements (Not Yet Implemented)

- Web UI for browsing links
- Export to Notion/bookmarks
- Automatic categorization with AI
- Link health checking (detect dead links)
- RSS feed generation
- MCP tool for link access
- AI summary on save using Perplexity
