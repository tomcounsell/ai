"""
Web Search Tool (Legacy Wrapper)

DEPRECATED: This module is maintained for backward compatibility only.
New code should use tools.web instead.

This wrapper delegates to the unified tools.web module which provides:
- Multi-provider fallback (Perplexity → Tavily)
- Unified interface for search and fetch
- Better error handling and resilience
"""

from typing import Literal

from tools.web import web_search_sync


class SearchError(Exception):
    """Search operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def search(
    query: str,
    search_type: Literal["conversational", "factual", "citations"] = "conversational",
    max_results: int = 10,
    time_filter: Literal["day", "week", "month", "year"] | None = None,
    domain_filter: list[str] | None = None,
    language: str = "en",
) -> dict:
    """
    Search the web (legacy interface).

    This function delegates to tools.web.web_search_sync() for backward compatibility.

    Args:
        query: Search query
        search_type: Type of search (conversational, factual, citations)
        max_results: Maximum number of results (1-50)
        time_filter: Filter by time period
        domain_filter: List of domains to include
        language: ISO 639-1 language code (currently ignored)

    Returns:
        dict with keys:
            - results: List of search results
            - summary: AI-generated summary
            - citations: Source citations (if search_type='citations')
            - error: Error message (if failed)
    """
    import os

    # Validate input
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    # For backward compatibility, check for API keys and return specific error
    # The legacy implementation expected specific API key errors
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    tavily_key = os.environ.get("TAVILY_API_KEY")

    if not api_key and not tavily_key:
        return {"error": "PERPLEXITY_API_KEY environment variable not set"}

    # Call the new unified web_search_sync
    try:
        result = web_search_sync(
            query,
            search_type=search_type,
            max_results=max_results,
            time_filter=time_filter,
            domain_filter=domain_filter,
        )

        if result is None:
            # Return legacy-compatible error message
            if not api_key:
                return {
                    "error": "PERPLEXITY_API_KEY environment variable not set",
                    "query": query,
                }
            return {"error": "All search providers failed", "query": query}

        # Convert new format to legacy format
        legacy_result = {
            "query": result.query,
            "search_type": search_type,
            "summary": result.answer,
            "results": [
                {"url": source.url, "title": source.title or source.url.split("/")[-1]}
                for source in result.sources[:max_results]
            ],
        }

        if result.citations:
            legacy_result["citations"] = result.citations

        return legacy_result

    except Exception as e:
        return {"error": f"Search failed: {str(e)}", "query": query}


def search_with_context(
    query: str,
    context: str,
    search_type: Literal["conversational", "factual", "citations"] = "conversational",
) -> dict:
    """
    Search with additional context for more relevant results.

    Args:
        query: Search query
        context: Additional context to guide the search
        search_type: Type of search

    Returns:
        dict with search results
    """
    enhanced_query = f"{query}\n\nContext: {context}"
    return search(enhanced_query, search_type=search_type)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.search 'your search query'")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f"Searching for: {query}")

    result = search(query)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"\nSummary:\n{result['summary']}")
        if result.get("citations"):
            print("\nSources:")
            for c in result["citations"]:
                print(f"  - {c}")
