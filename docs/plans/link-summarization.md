# Link Content Summarization

**Status**: Planned
**Priority**: P2
**Created**: 2026-01-20

## Problem

When users share links, Valor stores the URL but doesn't understand the content. The link database has fields for `title`, `description`, and `ai_summary` but they're not being populated.

## Current Flow

```
User sends: "Read this https://example.com/article"
    ↓
Bridge extracts URL
    ↓
Stores in links table (url, sender, chat_id, timestamp only)
    ↓
Claude receives raw URL, has to guess what it contains
```

## Proposed Solution

Automatically fetch and summarize link content using Perplexity API (already imported) or web fetching + Claude.

### Option A: Perplexity API (Recommended)

- Already have API key configured
- Good at web content understanding
- Returns concise summaries

### Option B: Fetch + Claude

- Fetch page content with requests/BeautifulSoup
- Use Claude to summarize
- More control but more complex

### Option C: Fetch + Ollama

- Free, local processing
- May be slower for complex content

## Implementation

### 1. Enhanced Link Storage

```python
async def store_link_with_summary(
    url: str,
    sender: str,
    chat_id: str,
    message_id: int,
    timestamp: datetime,
) -> dict:
    """Store a link with fetched metadata and AI summary."""

    # Get basic metadata (title, description)
    metadata = get_metadata(url)  # Already exists in link_analysis

    # Get AI summary using Perplexity
    summary = await summarize_url_content(url)

    return store_link(
        url=url,
        sender=sender,
        chat_id=chat_id,
        message_id=message_id,
        timestamp=timestamp,
        title=metadata.get("title"),
        description=metadata.get("description"),
        final_url=metadata.get("final_url"),
        ai_summary=summary,
    )
```

### 2. Perplexity Summarization

```python
async def summarize_url_content(url: str) -> str | None:
    """Use Perplexity to summarize URL content."""
    import httpx

    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        return None

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "llama-3.1-sonar-small-128k-online",
                "messages": [{
                    "role": "user",
                    "content": f"Summarize the main points of this URL in 2-3 sentences: {url}"
                }]
            },
            timeout=30.0
        )

        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]

    return None
```

### 3. Include Summary in Message Context

When a user sends a link, include the summary:

```python
# In message handler
if urls:
    for url in urls:
        summary = await summarize_url_content(url)
        if summary:
            clean_text += f"\n[Link summary: {summary}]"
```

## New Flow

```
User sends: "Read this https://example.com/article"
    ↓
Bridge extracts URL
    ↓
Fetches metadata (title, description)
    ↓
Gets AI summary via Perplexity
    ↓
Stores complete record in links table
    ↓
Enriches message: "Read this https://...
  [Link summary: This article discusses...]"
    ↓
Claude can discuss article content immediately
```

## Files to Modify

- `bridge/telegram_bridge.py`: Add summary fetching in link processing
- `tools/link_analysis/__init__.py`: Add `summarize_url_content()` function
- `tools/telegram_history/__init__.py`: Update `store_link()` calls

## Link Database Schema (Already Exists)

```sql
CREATE TABLE links (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    final_url TEXT,           -- After redirects
    title TEXT,               -- Page title
    description TEXT,         -- Meta description
    domain TEXT,              -- Extracted domain
    sender TEXT,
    chat_id TEXT,
    message_id INTEGER,
    timestamp DATETIME,
    tags TEXT,                -- JSON array
    notes TEXT,               -- User notes
    status TEXT DEFAULT 'unread',
    ai_summary TEXT           -- <-- This is where summary goes
);
```

## Caching Strategy

- Check if URL already has ai_summary before fetching
- Update existing record if re-shared (refresh summary)
- Consider TTL for stale summaries (e.g., 7 days)

## Rate Limiting

- Don't summarize more than 5 links per message
- Don't summarize same URL twice in 24 hours
- Queue summarization if too many requests

## Testing

1. Share news article → Should get title + summary
2. Share GitHub repo → Should describe what it is
3. Share Twitter/X link → Should summarize the post
4. Share PDF link → Should attempt to describe
5. Share broken link → Should gracefully handle

## Estimated Effort

2-3 hours for basic implementation
