# Search Tool

Web search using Perplexity API with intelligent result ranking and summarization.

## Overview

This tool provides web search capabilities through the Perplexity API, offering conversational, factual, and citation-based search modes.

## Installation

No additional installation required. Ensure you have the Perplexity API key configured:

```bash
export PERPLEXITY_API_KEY=your_api_key
```

## Quick Start

```python
from tools.search import search

# Basic search
result = search("What is Python?")
print(result["summary"])

# Search with citations
result = search("Python history", search_type="citations")
print(result["citations"])
```

## API Reference

### search()

```python
def search(
    query: str,
    search_type: Literal["conversational", "factual", "citations"] = "conversational",
    max_results: int = 10,
    time_filter: Literal["day", "week", "month", "year"] | None = None,
    domain_filter: list[str] | None = None,
    language: str = "en",
) -> dict
```

**Parameters:**
- `query`: Search query (required)
- `search_type`: Type of search
  - `conversational`: Natural language response
  - `factual`: Precise, verifiable facts
  - `citations`: Includes source URLs
- `max_results`: Maximum results (1-50)
- `time_filter`: Filter by time period
- `domain_filter`: List of domains to search within
- `language`: ISO 639-1 language code

**Returns:**
```python
{
    "query": str,
    "search_type": str,
    "summary": str,           # AI-generated summary
    "results": list[dict],    # Search results
    "citations": list[str],   # Source URLs (if citations mode)
    "suggested_refinements": list[str]  # Related questions
}
```

### search_with_context()

```python
def search_with_context(
    query: str,
    context: str,
    search_type: Literal["conversational", "factual", "citations"] = "conversational",
) -> dict
```

Enhanced search with additional context for more relevant results.

## Workflows

### Basic Search
```python
result = search("latest Python release")
print(result["summary"])
```

### Domain-Filtered Search
```python
result = search(
    "Django tutorials",
    domain_filter=["docs.djangoproject.com", "realpython.com"]
)
```

### Time-Filtered Search
```python
result = search(
    "Python news",
    time_filter="week"
)
```

## Error Handling

All functions return a dict. Check for errors:

```python
result = search("query")
if "error" in result:
    print(f"Search failed: {result['error']}")
else:
    print(result["summary"])
```

## Troubleshooting

### API Key Not Set
```
Error: PERPLEXITY_API_KEY environment variable not set
```
Set your API key in the environment or `.env` file.

### Request Timeout
```
Error: Search request timed out
```
The Perplexity API may be slow. Retry or simplify your query.

### Rate Limiting
Check your Perplexity API usage limits if you receive 429 errors.
