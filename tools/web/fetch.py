"""URL fetching with provider fallback chain."""

import asyncio

from tools.web.providers import firecrawl, httpx_fallback, tavily
from tools.web.types import FetchResult


async def fetch(url: str, **kwargs) -> FetchResult | None:
    """Fetch and read content from a URL using provider fallback chain.

    Tries providers in order until one succeeds:
    1. httpx + html2text (free, fast, works for most static pages)
    2. Firecrawl (handles JS-rendered pages, requires API key)
    3. Tavily extract (last resort, requires API key)

    Args:
        url: URL to fetch
        **kwargs: Additional parameters passed to providers (timeout, formats, etc.)

    Returns:
        FetchResult with content, title, final URL, and provider name on success.
        None if all providers fail.

    Example:
        >>> result = await fetch("https://example.com")
        >>> if result:
        ...     print(f"Content: {result.content}")
        ...     print(f"Provider: {result.provider}")
    """
    if not url or not url.strip():
        return None

    # Provider chain: httpx → Firecrawl → Tavily
    providers = [
        httpx_fallback,
        firecrawl,
        tavily,
    ]

    for provider in providers:
        try:
            result = await provider.fetch(url, **kwargs)
            if result:
                return result
        except Exception:
            # Provider failed, try next
            continue

    # All providers failed
    return None


def fetch_sync(url: str, **kwargs) -> FetchResult | None:
    """Synchronous wrapper for fetch().

    For non-async callers. Creates an event loop and runs fetch().

    Args:
        url: URL to fetch
        **kwargs: Additional parameters passed to fetch()

    Returns:
        FetchResult on success, None on failure

    Example:
        >>> result = fetch_sync("https://example.com")
        >>> if result:
        ...     print(result.content)
    """
    try:
        return asyncio.run(fetch(url, **kwargs))
    except Exception:
        return None
