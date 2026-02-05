"""Tavily search and extract provider."""

import os

import httpx

from tools.web.types import FetchResult, SearchResult, Source

name = "tavily"

TAVILY_API_URL = "https://api.tavily.com"


async def search(query: str, **kwargs) -> SearchResult | None:
    """Search using Tavily API.

    Args:
        query: Search query
        **kwargs: Additional parameters (max_results, search_depth, include_domains, exclude_domains)

    Returns:
        SearchResult on success, None on failure
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return None

    if not query or not query.strip():
        return None

    # Extract kwargs
    max_results = max(1, min(20, kwargs.get("max_results", 10)))
    search_depth = kwargs.get("search_depth", "basic")  # basic or advanced
    include_domains = kwargs.get("include_domains", [])
    exclude_domains = kwargs.get("exclude_domains", [])

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{TAVILY_API_URL}/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": search_depth,
                    "max_results": max_results,
                    "include_answer": True,
                    "include_domains": include_domains,
                    "exclude_domains": exclude_domains,
                },
            )

            response.raise_for_status()
            result = response.json()

            # Extract answer
            answer = result.get("answer", "")
            if not answer:
                # If no answer, concatenate first few result snippets
                results_list = result.get("results", [])
                if results_list:
                    answer = "\n\n".join(
                        r.get("content", "")
                        for r in results_list[:3]
                        if r.get("content")
                    )

            if not answer:
                return None

            # Extract sources
            sources = []
            citations = []
            for item in result.get("results", [])[:max_results]:
                url = item.get("url", "")
                title = item.get("title")
                snippet = item.get("content")

                if url:
                    sources.append(Source(url=url, title=title, snippet=snippet))
                    citations.append(url)

            return SearchResult(
                answer=answer,
                sources=sources,
                citations=citations,
                query=query,
                provider=name,
            )

    except Exception:
        # Any error returns None to trigger fallback
        return None


async def fetch(url: str, **kwargs) -> FetchResult | None:
    """Extract content from URL using Tavily extract API.

    Args:
        url: URL to extract content from
        **kwargs: Additional parameters

    Returns:
        FetchResult on success, None on failure
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return None

    if not url or not url.strip():
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{TAVILY_API_URL}/extract",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": api_key,
                    "urls": [url],
                },
            )

            response.raise_for_status()
            result = response.json()

            # Extract results
            results = result.get("results", [])
            if not results:
                return None

            # Get first result
            first_result = results[0]
            content = first_result.get("raw_content", "")
            if not content:
                return None

            title = first_result.get("title")

            return FetchResult(content=content, title=title, url=url, provider=name)

    except Exception:
        # Any error returns None to trigger fallback
        return None
