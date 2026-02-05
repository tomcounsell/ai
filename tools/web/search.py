"""Unified web search with provider fallback chain."""

import asyncio

from tools.web.providers import perplexity, tavily
from tools.web.types import SearchResult


async def web_search(query: str, **kwargs) -> SearchResult | None:
    """Search the web using provider fallback chain.

    Tries providers in order: Perplexity → Tavily
    Returns the first successful result or None if all fail.

    Args:
        query: Search query
        **kwargs: Additional parameters passed to providers:
            - search_type: "conversational" | "factual" | "citations" (Perplexity)
            - max_results: Number of results to return (default 10)
            - time_filter: "day" | "week" | "month" | "year"
            - domain_filter: List of domains to search within
            - search_depth: "basic" | "advanced" (Tavily)
            - include_domains: List of domains to include (Tavily)
            - exclude_domains: List of domains to exclude (Tavily)

    Returns:
        SearchResult on success, None if all providers fail
    """
    # Provider chain: Perplexity → Tavily
    providers = [perplexity, tavily]

    for provider in providers:
        try:
            result = await provider.search(query, **kwargs)
            if result is not None:
                return result
        except Exception:
            # Continue to next provider on any error
            continue

    # All providers failed
    return None


def web_search_sync(query: str, **kwargs) -> SearchResult | None:
    """Synchronous wrapper for web_search().

    Use this for non-async callers. Internally runs the async function.

    Args:
        query: Search query
        **kwargs: Additional parameters (see web_search)

    Returns:
        SearchResult on success, None if all providers fail
    """
    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is already running, create a new task
            import threading

            result = None
            exception = None

            def run_in_thread():
                nonlocal result, exception
                try:
                    result = asyncio.run(web_search(query, **kwargs))
                except Exception as e:
                    exception = e

            thread = threading.Thread(target=run_in_thread)
            thread.start()
            thread.join()

            if exception:
                raise exception
            return result
        else:
            # No loop running, use asyncio.run
            return loop.run_until_complete(web_search(query, **kwargs))
    except RuntimeError:
        # No event loop exists, create one
        return asyncio.run(web_search(query, **kwargs))
