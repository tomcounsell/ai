"""Firecrawl fetch/scrape provider."""

import os

import httpx

from tools.web.types import FetchResult

name = "firecrawl"

FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1"


async def fetch(url: str, **kwargs) -> FetchResult | None:
    """Fetch content from URL using Firecrawl API.

    Args:
        url: URL to scrape
        **kwargs: Additional parameters (formats, includeTags, excludeTags)

    Returns:
        FetchResult on success, None on failure
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        return None

    if not url or not url.strip():
        return None

    # Extract kwargs
    formats = kwargs.get("formats", ["markdown", "html"])
    include_tags = kwargs.get("includeTags", [])
    exclude_tags = kwargs.get("excludeTags", ["nav", "footer"])

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{FIRECRAWL_API_URL}/scrape",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": formats,
                    "onlyMainContent": True,
                    "includeTags": include_tags,
                    "excludeTags": exclude_tags,
                },
            )

            response.raise_for_status()
            result = response.json()

            # Extract content
            data = result.get("data", {})
            if not data:
                return None

            # Prefer markdown, fallback to html
            content = data.get("markdown", "") or data.get("html", "")
            if not content:
                return None

            # Extract metadata
            metadata = data.get("metadata", {})
            title = metadata.get("title")

            return FetchResult(content=content, title=title, url=url, provider=name)

    except Exception:
        # Any error returns None to trigger fallback
        return None
