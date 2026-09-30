# Web Search and Fetch

## Overview

Unified web search and URL content fetching with multi-provider fallback chains. If the primary provider fails, the next provider in the chain is tried automatically.

**Capabilities:** `search`, `fetch`

## Quick Start

```python
from tools.web import web_search_sync, fetch_sync

# Search the web
result = web_search_sync("latest Python release")
print(result.answer)
for source in result.sources:
    print(f"  - {source.title}: {source.url}")

# Fetch a URL
page = fetch_sync("https://example.com")
print(page.content)  # Clean markdown
```

## CLI

```bash
# Search
valor-search "Claude API pricing 2026"

# Fetch URL content
valor-fetch "https://example.com"
```

## API Reference

### `web_search_sync(query, **kwargs) -> SearchResult | None`

Synchronous web search. Returns a `SearchResult` with:
- `answer` -- AI-generated summary
- `sources` -- List of `Source(url, title, snippet)`
- `citations` -- Direct citation URLs
- `query` -- Original query
- `provider` -- Which provider answered

### `fetch_sync(url, **kwargs) -> FetchResult | None`

Synchronous URL fetch. Returns a `FetchResult` with:
- `content` -- Clean markdown content
- `title` -- Page title
- `url` -- Final URL (after redirects)
- `provider` -- Which provider fetched

### Async variants

`web_search()` and `fetch()` are the async equivalents of the sync functions above.

## Providers

### Search Providers
| Provider | Env Var | Notes |
|----------|---------|-------|
| Perplexity | `PERPLEXITY_API_KEY` | Primary, AI-summarized results |
| Tavily | `TAVILY_API_KEY` | Fallback search provider |

### Fetch Providers
| Provider | Env Var | Notes |
|----------|---------|-------|
| Firecrawl | `FIRECRAWL_API_KEY` | Primary, clean markdown extraction |
| httpx fallback | None | Built-in, uses html2text conversion |

## Architecture

The provider fallback chain tries each provider in order. If a provider returns `None` (failure), the next provider is tried. This provides resilience without requiring all API keys to be configured.
