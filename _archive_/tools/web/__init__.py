"""Unified web search and fetch tools."""

import sys

from tools.web.fetch import fetch, fetch_sync
from tools.web.search import web_search, web_search_sync

# Public API exports
__all__ = [
    "web_search",
    "web_search_sync",
    "fetch",
    "fetch_sync",
    "cli_search",
    "cli_fetch",
]


def cli_search():
    """CLI entry point for web search."""
    if len(sys.argv) < 2:
        print("Usage: valor-search <query>", file=sys.stderr)
        print("\nExample: valor-search 'Claude AI model pricing 2026'", file=sys.stderr)
        sys.exit(1)

    query = " ".join(sys.argv[1:])

    try:
        result = web_search_sync(query)
        if result:
            if result.answer:
                print(result.answer)
            if result.sources:
                print("\nSources:")
                for source in result.sources:
                    print(f"  - {source.title or 'Untitled'}: {source.url}")
            # If no answer or sources, still consider it a success if we got a result
            if not result.answer and not result.sources:
                print("Search completed but returned no content", file=sys.stderr)
                sys.exit(1)
        else:
            print("Search returned no results", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Search failed: {e}", file=sys.stderr)
        sys.exit(1)


def cli_fetch():
    """CLI entry point for web fetch."""
    if len(sys.argv) < 2:
        print("Usage: valor-fetch <url>", file=sys.stderr)
        print("\nExample: valor-fetch 'https://example.com'", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]

    try:
        result = fetch_sync(url)
        if result and result.content:
            print(result.content)
        else:
            print("Fetch returned no content", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
