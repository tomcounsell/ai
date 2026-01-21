# Link Content Summarization

**Status**: Implemented
**Implemented**: 2026-01-20

## Overview

When users share links in messages, Valor automatically fetches metadata and generates AI summaries using the Perplexity API. This allows Claude to understand and discuss linked content immediately without requiring users to paste article text.

## Features

### URL Detection

Extracts URLs from message text using regex pattern matching:
- Supports HTTP and HTTPS URLs
- Handles complex URLs with paths, query parameters, and fragments
- Deduplicates repeated URLs in the same message

### Metadata Extraction

Fetches basic page metadata:
- Page title (from `<title>` tag)
- Meta description
- Content type
- Final URL after redirects

### AI Summarization

Uses Perplexity API (`sonar` model) to generate concise 2-3 sentence summaries of linked content. Perplexity is ideal because it can browse and understand web content directly.

### Caching

- Checks if URL already has a summary in the database
- Avoids re-summarizing the same URL within 24 hours
- Rate limits to max 5 links per message

## Message Flow

```
User sends: "Check out this article https://example.com/article"
    |
    v
Bridge extracts URLs from message
    |
    v
For each URL (up to 5):
    |
    v
Check cache - already summarized recently?
    |
    +-- Yes --> Use cached summary
    |
    +-- No --> Fetch metadata (title, description)
               |
               v
               Call Perplexity API for AI summary
               |
               v
               Store in links table with ai_summary
    |
    v
Format summaries for message context:
  "[Link: Example Article - This article discusses the latest
   developments in AI technology, covering three key trends...]"
    |
    v
Enriched message passed to clawdbot
    |
    v
Claude can discuss article content immediately
```

## Implementation Files

### Core Functions

- `tools/link_analysis/__init__.py`:
  - `extract_urls()` - Extract URLs from text
  - `validate_url()` - Check URL accessibility
  - `get_metadata()` - Fetch page title and description
  - `summarize_url_content()` - Call Perplexity API for summary

- `bridge/telegram_bridge.py`:
  - `get_link_summaries()` - Orchestrates URL processing with caching/rate limiting
  - `format_link_summaries()` - Formats summaries for message context
  - Integration in `handle_message()` - Enriches incoming messages

### Database Schema

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
    ai_summary TEXT           -- Perplexity-generated summary
);
```

## Dependencies

### API Keys
- `PERPLEXITY_API_KEY` - Required for AI summarization

### Python Packages
- `httpx` - Async HTTP client
- `requests` - Sync HTTP for metadata fetching

## Configuration

### Constants (in link_analysis)

```python
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar"  # Current Perplexity model
```

### Rate Limiting

- Maximum 5 links summarized per message
- 24-hour cache window for repeated URLs
- 30-second timeout per summarization request

## Edge Case Handling

| Case | Behavior |
|------|----------|
| No API key | Logs warning, skips summarization |
| Invalid URL | Validation fails, skipped gracefully |
| Timeout | Logs warning, continues with other URLs |
| API error | Logs error, returns None for that URL |
| Cached URL | Uses existing summary from database |
| >5 URLs | Only first 5 are summarized |

## Testing

25 tests covering:
- URL extraction (single, multiple, complex, deduplication)
- URL validation (valid, invalid, redirects)
- Metadata extraction
- AI summarization (basic, news articles, missing key, timeout)

Run tests:
```bash
pytest tests/tools/test_link_analysis.py -v
```

## Example Interactions

**User shares news article:**
```
User: "Thoughts on this? https://techcrunch.com/2026/01/20/ai-breakthrough"

[Link: AI Breakthrough Announced - TechCrunch reports that researchers
have achieved a significant milestone in AI reasoning capabilities,
demonstrating improved performance on complex multi-step tasks.]

Valor can now discuss the specific article content.
```

**User shares GitHub repo:**
```
User: "Have you seen https://github.com/anthropics/claude-code"

[Link: anthropics/claude-code - This repository contains Claude Code,
Anthropic's official CLI tool for AI-assisted software development
with support for multiple programming languages and IDE integrations.]

Valor can discuss the repo's purpose and features.
```
