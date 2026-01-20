# Search Tool SOP

**Version**: 1.0.0
**Last Updated**: 2026-01-20
**Owner**: Valor AI System
**Status**: Active

## Overview

This SOP defines the standard procedure for executing web searches using the Perplexity API. It covers query processing, result handling, and caching strategies.

## Prerequisites

- PERPLEXITY_API_KEY environment variable configured
- Network access to Perplexity API

## Parameters

### Required
- **query** (string): Search query
  - Min length: 1 character
  - Max length: 2000 characters
  - Description: The search query to execute

### Optional
- **search_type** (string): Type of search result
  - Values: `conversational` | `factual` | `citations`
  - Default: `conversational`

- **max_results** (integer): Maximum results to return
  - Range: 1-50
  - Default: 10

- **time_filter** (string): Filter by recency
  - Values: `day` | `week` | `month` | `year`
  - Default: None (all time)

- **domain_filter** (array): Domains to include
  - Example: `["python.org", "github.com"]`
  - Default: None (all domains)

- **language** (string): Result language
  - Format: ISO 639-1 code
  - Default: `en`

## Steps

### 1. Validate and Sanitize Query

**Purpose**: Ensure query is safe and well-formed.

**Actions**:
- MUST check query is not empty
- MUST trim whitespace
- MUST validate length limits
- SHOULD sanitize special characters
- MAY expand abbreviations

**Validation**:
- Query length within limits
- No injection attempts

**Error Handling**:
- If empty: Return error "Query cannot be empty"
- If too long: Truncate to max length

### 2. Check Cache

**Purpose**: Return cached results for identical recent queries.

**Actions**:
- SHOULD check cache for identical query
- SHOULD use cache if result is < 15 minutes old
- MAY use stale cache on API failure

**Cache Key**:
```python
cache_key = hash(f"{query}:{search_type}:{domain_filter}")
```

**Validation**:
- Cache entry exists
- Cache entry not expired

### 3. Build API Request

**Purpose**: Construct the Perplexity API request.

**Actions**:
- MUST include query and model parameters
- MUST set appropriate system prompt for search type
- SHOULD include domain filter in query if specified
- SHOULD set reasonable timeout (60s)

**Request Format**:
```python
{
    "model": "llama-3.1-sonar-small-128k-online",
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": search_query}
    ],
    "max_tokens": 1024,
    "return_citations": search_type == "citations",
    "return_related_questions": True
}
```

### 4. Execute Search

**Purpose**: Call Perplexity API and handle response.

**Actions**:
- MUST send request with authorization header
- MUST handle rate limits gracefully
- MUST respect timeout settings
- SHOULD retry on transient failures

**Error Handling**:
- If timeout: Return error with query
- If rate limited: Wait and retry (max 3 times)
- If API error: Return error message from API

### 5. Process Results

**Purpose**: Parse and structure the API response.

**Actions**:
- MUST extract summary from response
- MUST parse citations if available
- SHOULD extract related questions
- SHOULD structure results consistently

**Output Format**:
```python
{
    "query": original_query,
    "search_type": search_type,
    "summary": content,
    "results": [{"url": url, "title": title}],
    "citations": [urls],
    "suggested_refinements": [questions]
}
```

### 6. Cache Results

**Purpose**: Store results for future identical queries.

**Actions**:
- SHOULD cache successful results
- MUST set appropriate TTL (15 minutes)
- MAY cache partial results on error

## Success Criteria

- Search returns relevant summary
- Citations included (if requested)
- Response time < 5 seconds
- No API errors

## Error Recovery

| Error Type | Recovery Procedure |
|------------|-------------------|
| Empty query | Return validation error |
| API timeout | Retry once, then return timeout error |
| Rate limited | Wait 60s, retry up to 3 times |
| API error | Return error message, check API key |
| Network error | Retry with exponential backoff |

## Examples

### Example 1: Basic Search

```
Input:
  query: "What is the capital of France?"
  search_type: conversational

Output:
  query: "What is the capital of France?"
  search_type: conversational
  summary: "Paris is the capital of France..."
  results: []
  suggested_refinements: ["History of Paris", "French government"]
```

### Example 2: Search with Citations

```
Input:
  query: "climate change effects 2024"
  search_type: citations
  time_filter: year

Output:
  query: "climate change effects 2024"
  search_type: citations
  summary: "Recent studies show..."
  citations:
    - "https://nature.com/article/..."
    - "https://science.org/doi/..."
  results:
    - url: "https://nature.com/article/..."
      title: "Climate Impact Study"
```

## Related SOPs

- [Knowledge Search](knowledge-search.sop.md)
- [Link Analysis](../subagents/notion/knowledge-search.sop.md)

## Version History

- v1.0.0 (2026-01-20): Initial version
