---
status: Planning
appetite: Medium: 3-5 days
owner: Valor
created: 2026-02-02
tracking:
---

# Unified Web Search & Fetch Tools

## Problem

The agent currently has fragmented web capabilities spread across multiple tools with leaky abstractions:

- `tools/search/` — Perplexity-only, returns AI summaries but no raw results or source content
- `tools/link_analysis/` — fetches metadata via raw `requests.get()` + regex HTML parsing, uses Perplexity for URL summarization
- `tools/browser/` — full Playwright browser, overkill for "read this page"
- Claude Code's built-in `WebFetch` / `WebSearch` — available but limited, can't be customized or improved

**Current behavior:**
The agent must know *which* tool to use for *which* kind of web task, and none of them are great:
- Search gives summaries but no way to read the actual source pages
- Fetching a page means regex HTML parsing or spinning up a headless browser
- No fallback chain — if Perplexity is down or rate-limited, search fails entirely
- Firecrawl API key exists in `.env` but is unused
- Tavily API key exists in `.env` but is unused

**Desired outcome:**
Two simple functions the agent can call without knowing or caring what's under the hood:

```python
# Search the web — returns structured results with summaries and sources
results = web_search("latest Python 3.13 features")

# Fetch and read a URL — returns clean markdown content
content = fetch("https://docs.python.org/3.13/whatsnew/3.13.html")
```

The internals handle provider selection, fallbacks, rate limiting, and content extraction automatically.

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

This replaces/consolidates existing tools rather than building from scratch. The Perplexity search tool and link_analysis fetch logic already work — this is about wrapping them with Firecrawl/Tavily, adding fallback chains, and presenting a clean interface.

## Solution

### Key Elements

- **`web_search(query)`**: Single function that searches the web and returns structured results (answer, sources, citations). Uses a provider chain internally.
- **`fetch(url)`**: Single function that fetches a URL and returns clean, readable markdown content. Uses a provider chain internally.
- **Provider chain with automatic fallback**: Each function has an ordered list of backends. If the primary fails (error, rate limit, timeout), it falls through to the next.
- **CLI entry points**: Both installable as `valor-search` and `valor-fetch` for use from shell/hooks.

### Flow

**web_search flow:**
```
Agent calls web_search("query")
    → Try Perplexity sonar (best for AI-summarized answers with citations)
    → Fallback: Tavily search (structured results, good source extraction)
    → Fallback: Return error with what we know
    → Return: { answer, sources[], citations[], provider_used }
```

**fetch flow:**
```
Agent calls fetch("https://example.com/page")
    → Try Firecrawl scrape (best: returns clean markdown, handles JS-rendered pages)
    → Fallback: httpx + html2text (fast, no JS, works for most static pages)
    → Fallback: Tavily extract (can read page content as part of search)
    → Return: { content (markdown), title, url, provider_used }
```

### Technical Approach

**Directory structure:**
```
tools/web/
├── __init__.py          # Exports web_search() and fetch()
├── search.py            # web_search implementation + provider chain
├── fetch.py             # fetch implementation + provider chain
├── providers/
│   ├── __init__.py
│   ├── perplexity.py    # Perplexity sonar search
│   ├── tavily.py        # Tavily search + extract
│   ├── firecrawl.py     # Firecrawl scrape/crawl
│   └── httpx_fallback.py # Raw httpx + html2text (no API key needed)
├── manifest.json
├── README.md
└── tests/
    ├── __init__.py
    ├── test_search.py
    └── test_fetch.py
```

**Provider interface:**
```python
class SearchProvider(Protocol):
    name: str
    async def search(self, query: str, **kwargs) -> SearchResult | None: ...

class FetchProvider(Protocol):
    name: str
    async def fetch(self, url: str, **kwargs) -> FetchResult | None: ...
```

Each provider returns `None` on failure (triggering fallback) or a result dataclass on success. Providers check their own API key availability at init and mark themselves as unavailable if missing.

**Result types:**
```python
@dataclass
class SearchResult:
    answer: str                    # AI-generated summary/answer
    sources: list[Source]          # URLs with titles and snippets
    citations: list[str]           # Direct citation URLs
    query: str                     # Original query
    provider: str                  # Which provider answered

@dataclass
class FetchResult:
    content: str                   # Clean markdown content
    title: str | None              # Page title
    url: str                       # Final URL (after redirects)
    provider: str                  # Which provider fetched

@dataclass
class Source:
    url: str
    title: str | None
    snippet: str | None
```

**Key design decisions:**
- **Async-first** with sync wrappers — the bridge is async, Claude Code can call sync
- **No caching in v1** — keep it simple, caching is a v2 concern
- **API keys from environment** — consistent with all other tools (`PERPLEXITY_API_KEY`, `FIRECRAWL_API_KEY`, `TAVILY_API_KEY`)
- **httpx everywhere** — replace `requests` with `httpx` for consistency and async support
- **html2text for the free fallback** — pip dependency, converts HTML to readable markdown without an API

**CLI entry points (pyproject.toml):**
```toml
[project.scripts]
valor-search = "tools.web:cli_search"
valor-fetch = "tools.web:cli_fetch"
```

**Migration:**
- The old `tools/search/` becomes a thin wrapper that imports from `tools/web/` (backward compat for any code using it)
- `tools/link_analysis/` URL summarization delegates to `fetch()` internally
- No breaking changes to existing callers

## Rabbit Holes & Risks

### Risk 1: Provider API instability
**Impact:** If Firecrawl or Tavily change their API, fetch/search breaks silently
**Mitigation:** Each provider is isolated. Fallback chain means one provider breaking doesn't kill the tool. Integration tests hit real APIs.

### Risk 2: Rate limiting across providers
**Impact:** Heavy use exhausts one provider's quota
**Mitigation:** The fallback chain naturally distributes load. v1 doesn't need smart routing — if primary is rate-limited it returns None, fallback kicks in. v2 could add token bucket tracking.

### Risk 3: html2text output quality varies
**Impact:** The free fallback produces messy markdown on complex pages
**Mitigation:** It's the *last* fallback, not the primary. For JS-heavy pages, Firecrawl is primary. html2text handles the "good enough for most static pages" case.

### Risk 4: Scope creep into crawling/spidering
**Impact:** `fetch()` is for single pages. Multi-page crawling is a different beast.
**Mitigation:** Explicitly out of scope. `fetch()` takes one URL, returns one page.

## No-Gos (Out of Scope)

- **Multi-page crawling/spidering** — fetch() is single-URL only
- **Caching layer** — no result caching in v1
- **Smart provider routing** — no cost optimization or load balancing, just ordered fallback
- **Browser rendering fallback** — Playwright is too heavy for a fallback; if Firecrawl can't render it, we accept degraded output from html2text
- **YouTube/media processing** — stays in `link_analysis`, not part of this tool
- **Replacing Claude Code's WebSearch/WebFetch** — these tools complement them for use in the Telegram bridge agent path; Claude Code sessions keep their built-in tools

## Success Criteria

- [ ] `web_search("Python 3.13 features")` returns a coherent answer with source URLs
- [ ] `fetch("https://docs.python.org/3.13/whatsnew/3.13.html")` returns readable markdown
- [ ] Fallback works: disabling Perplexity key makes search fall through to Tavily
- [ ] Fallback works: disabling Firecrawl key makes fetch fall through to httpx+html2text
- [ ] `valor-search` and `valor-fetch` CLI commands work
- [ ] Integration tests pass with real API calls (skipped when keys missing)
- [ ] Old `tools/search` import path still works (backward compat)
- [ ] All providers that have API keys configured are tested and functional

---

## Open Questions

1. **Provider priority for search**: I've defaulted to Perplexity first (best AI summaries) then Tavily (good structured results). Does that match your preference, or should Tavily be primary?

2. **Firecrawl plan/tier**: What Firecrawl plan are we on? The free tier has low limits. This affects whether Firecrawl should be the primary fetch provider or a secondary behind httpx+html2text for cost reasons.

3. **Should fetch() handle PDFs?** Firecrawl can extract text from PDFs. Should we support `fetch("https://example.com/paper.pdf")` returning markdown text, or is that out of scope for v1?

4. **Deprecation timeline for old tools**: Should `tools/search/` and `tools/link_analysis/` URL fetching be fully replaced (deleted) once `tools/web/` is stable, or keep them indefinitely as thin wrappers?
