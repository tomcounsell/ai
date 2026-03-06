"""Free fallback provider using httpx + html2text."""

import httpx
from html2text import HTML2Text

from tools.web.types import FetchResult

name = "httpx_fallback"


async def fetch(url: str, **kwargs) -> FetchResult | None:
    """Fetch content from URL using httpx + html2text.

    This is a free fallback that works for most static pages.
    No API key required.

    Args:
        url: URL to fetch
        **kwargs: Additional parameters (timeout)

    Returns:
        FetchResult on success, None on failure
    """
    if not url or not url.strip():
        return None

    # Extract kwargs
    timeout = kwargs.get("timeout", 30.0)

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            # Get final URL after redirects
            final_url = str(response.url)

            # Extract title from HTML
            title = None
            html_content = response.text
            if "<title>" in html_content:
                title_start = html_content.find("<title>") + 7
                title_end = html_content.find("</title>", title_start)
                if title_end > title_start:
                    title = html_content[title_start:title_end].strip()

            # Convert HTML to markdown
            h = HTML2Text()
            h.ignore_links = False
            h.ignore_images = False
            h.ignore_emphasis = False
            h.body_width = 0  # Don't wrap lines
            h.unicode_snob = True  # Use unicode
            h.skip_internal_links = True

            content = h.handle(html_content)

            if not content or len(content.strip()) < 50:
                # Content too short, likely error page
                return None

            return FetchResult(
                content=content, title=title, url=final_url, provider=name
            )

    except Exception:
        # Any error returns None to trigger fallback
        return None
