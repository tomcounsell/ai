# Link Collection Feature - Build Plan

## Overview

Store links forwarded by Tom (and others) via Telegram in a local SQLite database for later retrieval and search.

## Current State

**Existing Tools:**
- `tools/link_analysis/` - URL extraction, validation, metadata fetching, AI analysis
- `tools/telegram_history/` - Message storage in SQLite at `~/.valor/telegram_history.db`

**What's Missing:**
- Dedicated links table with proper schema
- Bridge integration to detect and store links automatically
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

### Phase 1: Core Storage (Day 1)

1. **Add links table to telegram_history**
   - Update `tools/telegram_history/__init__.py`
   - Add `store_link()` function
   - Add `search_links()` function
   - Add `list_links()` function (recent, by domain, by sender)
   - Add `update_link()` function (status, tags, notes)

2. **Unit tests**
   - Test CRUD operations
   - Test search functionality
   - Test duplicate handling

### Phase 2: Bridge Integration (Day 1)

3. **Detect links in incoming messages**
   - Use `link_analysis.extract_urls()` in bridge handler
   - Filter: only store links from whitelisted senders (Tom)
   - Auto-fetch metadata (title, description)

4. **Store links automatically**
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
# After processing the message, check for links
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

- [ ] Links from Tom are automatically stored when forwarded
- [ ] Can search links by keyword, domain, or sender
- [ ] Metadata (title, description) is fetched automatically
- [ ] No duplicate links stored
- [ ] Links persist across bridge restarts

## Future Enhancements (Not in scope)

- Web UI for browsing links
- Export to Notion/bookmarks
- Automatic categorization with AI
- Link health checking (detect dead links)
- RSS feed generation
