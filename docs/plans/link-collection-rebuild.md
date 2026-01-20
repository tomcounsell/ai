# Telegram History & Link Collection - Build Plan

**Status**: 📋 Plan (Not Yet Implemented)
**Created**: 2026-01-19

The tools exist (`tools/telegram_history/`, `tools/link_analysis/`) but bridge integration is not yet complete.

---

## Overview

1. **Store ALL Telegram messages locally** - Full searchable history of DMs and group chats
2. **Store links with rich metadata** - Dedicated table for URLs with title, description, AI summary

## Current State

**Existing Tools:**
- `tools/link_analysis/` - URL extraction, validation, metadata fetching, AI analysis
- `tools/telegram_history/` - Message storage in SQLite at `~/.valor/telegram_history.db`
  - Has `store_message()`, `search_history()`, `get_recent_messages()` functions
  - **NOT currently wired up** - bridge doesn't call these functions
  - Database doesn't exist yet

**What's Missing:**
- Bridge integration to store messages automatically
- Dedicated links table with rich metadata
- Search/retrieval functions for saved links

## Proposed Architecture

### Option A: Extend telegram_history tool (Recommended)
Add a `links` table to the existing `~/.valor/telegram_history.db`:
- Simpler, single database
- Reuses existing connection code
- Links are contextually related to messages

### Option B: Separate links database
Create `~/.valor/links.db`:
- Clean separation of concerns
- Could be used independently of Telegram

**Recommendation:** Option A - keep it simple, one database.

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    final_url TEXT,                    -- After redirects
    title TEXT,
    description TEXT,
    domain TEXT,                       -- Extracted for filtering
    sender TEXT,                       -- Who sent it
    chat_id TEXT,                      -- Where it came from
    message_id INTEGER,                -- Original message
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    tags TEXT,                         -- JSON array of tags
    notes TEXT,                        -- User notes
    status TEXT DEFAULT 'unread',      -- unread, read, archived
    ai_summary TEXT,                   -- Optional AI analysis
    UNIQUE(url, chat_id, message_id)   -- Prevent duplicates
);

CREATE INDEX idx_links_domain ON links(domain);
CREATE INDEX idx_links_sender ON links(sender);
CREATE INDEX idx_links_timestamp ON links(timestamp);
CREATE INDEX idx_links_status ON links(status);
```

## Implementation Tasks

### Phase 1: Wire Up Message Storage (Day 1)

1. **Integrate telegram_history into bridge**
   - Import `store_message` in `bridge/telegram_bridge.py`
   - Store EVERY incoming message (text, media descriptions)
   - Include: chat_id, message_id, sender, content, timestamp, message_type
   - This gives us full local searchable history

2. **Test message storage**
   - Verify messages are being stored
   - Test search functionality works

### Phase 2: Add Links Table (Day 1)

3. **Add links table to telegram_history**
   - Update `tools/telegram_history/__init__.py`
   - Add `store_link()` function
   - Add `search_links()` function
   - Add `list_links()` function (recent, by domain, by sender)
   - Add `update_link()` function (status, tags, notes)

4. **Unit tests for links**
   - Test CRUD operations
   - Test search functionality
   - Test duplicate handling

### Phase 3: Bridge Link Detection (Day 1)

5. **Detect and store links in messages**
   - Use `link_analysis.extract_urls()` in bridge handler
   - Filter: only store links from whitelisted senders (Tom)
   - Auto-fetch metadata (title, description)

6. **Store links automatically**
   - Call `store_link()` for each detected URL
   - Log storage for debugging

### Phase 3: Retrieval Interface (Day 2)

5. **Add Clawdbot skill for link access**
   - `/links` - List recent saved links
   - `/links search <query>` - Search links
   - `/links domain <domain>` - Filter by domain
   - Natural language: "find that article about X Tom sent"

6. **Optional: AI summary on save**
   - Use Perplexity to summarize link content
   - Store in `ai_summary` field
   - Can be done async/background

## API Design

```python
# Store a link
store_link(
    url: str,
    sender: str,
    chat_id: str,
    message_id: int | None = None,
    fetch_metadata: bool = True,
) -> dict

# Search links
search_links(
    query: str | None = None,
    domain: str | None = None,
    sender: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict

# List recent links
list_links(
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
) -> dict

# Update link
update_link(
    link_id: int,
    status: str | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
) -> dict
```

## Bridge Integration

In `bridge/telegram_bridge.py`, add to message handler:

```python
from tools.telegram_history import store_message, store_link
from tools.link_analysis import extract_urls

# Store EVERY message for local history
store_message(
    chat_id=str(event.chat_id),
    content=clean_text,  # or full text with media description
    sender=sender_name,
    message_id=message.id,
    timestamp=message.date,
    message_type="text" if not message.media else media_type,
)

# For whitelisted senders, also extract and store links
if sender_username in LINK_COLLECTORS:  # e.g., ['tomcounsell']
    urls = extract_urls(text)
    for url in urls['urls']:
        store_link(
            url=url,
            sender=sender_name,
            chat_id=str(event.chat_id),
            message_id=message.id,
            fetch_metadata=True,
        )
        logger.info(f"Stored link from {sender_name}: {url[:50]}...")
```

## Configuration

Add to `.env`:
```bash
# Users whose forwarded links are automatically saved
TELEGRAM_LINK_COLLECTORS=tomcounsell
```

## Success Criteria

**Message History:**
- [ ] ALL incoming messages are stored in SQLite
- [ ] Messages include: chat_id, sender, content, timestamp, message_type
- [ ] Can search message history with `search_history()`
- [ ] Can retrieve recent messages with `get_recent_messages()`

**Link Collection:**
- [ ] Links from Tom are automatically stored when forwarded
- [ ] Can search links by keyword, domain, or sender
- [ ] Metadata (title, description) is fetched automatically
- [ ] No duplicate links stored
- [ ] Links persist across bridge restarts

**Completion Checklist:**
- [ ] Code implemented and working
- [ ] Unit tests passing
- [ ] Bridge restarted and tested end-to-end
- [ ] This plan moved to `docs/features/` as feature documentation

## Future Enhancements (Not in scope)

- Web UI for browsing links
- Export to Notion/bookmarks
- Automatic categorization with AI
- Link health checking (detect dead links)
- RSS feed generation
