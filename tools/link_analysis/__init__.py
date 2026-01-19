"""
Link Analysis Tool

URL extraction, validation, and content analysis.
"""

import os
import re
from urllib.parse import urlparse

import requests

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "llama-3.1-sonar-small-128k-online"

# URL regex pattern
URL_PATTERN = re.compile(
    r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\-.~:/?#[\]@!$&\'()*+,;=%]*',
    re.IGNORECASE
)


class LinkAnalysisError(Exception):
    """Link analysis operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def extract_urls(text: str) -> dict:
    """
    Extract URLs from text.

    Args:
        text: Text containing URLs

    Returns:
        dict with:
            - urls: List of extracted URLs
            - count: Number of URLs found
    """
    if not text:
        return {"urls": [], "count": 0}

    urls = URL_PATTERN.findall(text)
    unique_urls = list(dict.fromkeys(urls))  # Preserve order, remove duplicates

    return {
        "urls": unique_urls,
        "count": len(unique_urls),
    }


def validate_url(url: str, timeout: int = 10) -> dict:
    """
    Validate a URL by checking if it's accessible.

    Args:
        url: URL to validate
        timeout: Request timeout in seconds

    Returns:
        dict with validation result
    """
    if not url:
        return {"url": url, "valid": False, "error": "URL cannot be empty"}

    # Check URL format
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return {"url": url, "valid": False, "error": "Invalid URL format"}
    except Exception as e:
        return {"url": url, "valid": False, "error": str(e)}

    # Check accessibility
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        return {
            "url": url,
            "valid": True,
            "status_code": response.status_code,
            "final_url": response.url,
            "redirected": response.url != url,
        }
    except requests.exceptions.Timeout:
        return {"url": url, "valid": False, "error": "Request timed out"}
    except requests.exceptions.RequestException as e:
        return {"url": url, "valid": False, "error": str(e)}


def get_metadata(url: str, timeout: int = 10) -> dict:
    """
    Get metadata from a URL (title, description, etc.).

    Args:
        url: URL to fetch metadata from
        timeout: Request timeout

    Returns:
        dict with metadata
    """
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        content = response.text

        metadata = {
            "url": url,
            "title": None,
            "description": None,
            "content_type": response.headers.get("content-type"),
        }

        # Extract title
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
        if title_match:
            metadata["title"] = title_match.group(1).strip()

        # Extract meta description
        desc_match = re.search(
            r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
            content,
            re.IGNORECASE
        )
        if not desc_match:
            desc_match = re.search(
                r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']description["\']',
                content,
                re.IGNORECASE
            )
        if desc_match:
            metadata["description"] = desc_match.group(1).strip()

        return metadata

    except requests.exceptions.RequestException as e:
        return {"url": url, "error": str(e)}


def analyze_url(
    url: str,
    analyze_content: bool = True,
) -> dict:
    """
    Analyze a URL's content using AI.

    Args:
        url: URL to analyze
        analyze_content: Whether to analyze page content

    Returns:
        dict with analysis
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return {"error": "PERPLEXITY_API_KEY environment variable not set"}

    # Get basic validation and metadata
    validation = validate_url(url)
    if not validation.get("valid"):
        return {
            "url": url,
            "validation": validation,
            "error": f"URL not accessible: {validation.get('error')}",
        }

    metadata = get_metadata(url)

    if not analyze_content:
        return {
            "url": url,
            "validation": validation,
            "metadata": metadata,
        }

    # Use Perplexity to analyze the content
    try:
        response = requests.post(
            PERPLEXITY_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "Summarize the content of the given URL concisely.",
                    },
                    {
                        "role": "user",
                        "content": f"Analyze and summarize this URL: {url}",
                    },
                ],
                "max_tokens": 512,
            },
            timeout=60,
        )

        response.raise_for_status()
        result = response.json()

        summary = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        return {
            "url": url,
            "validation": validation,
            "metadata": metadata,
            "analysis": {
                "summary": summary,
            },
        }

    except requests.exceptions.RequestException as e:
        return {
            "url": url,
            "validation": validation,
            "metadata": metadata,
            "error": f"Analysis failed: {str(e)}",
        }


def analyze_text_links(
    text: str,
    analyze_content: bool = False,
    validate_links: bool = True,
) -> dict:
    """
    Extract and analyze all links in text.

    Args:
        text: Text containing URLs
        analyze_content: Analyze page content for each URL
        validate_links: Validate each URL

    Returns:
        dict with all extracted and analyzed links
    """
    extracted = extract_urls(text)
    urls = extracted["urls"]

    results = []
    for url in urls:
        result = {"url": url}

        if validate_links:
            result["validation"] = validate_url(url)

        if analyze_content:
            analysis = analyze_url(url, analyze_content=True)
            result.update(analysis)

        results.append(result)

    return {
        "text_length": len(text),
        "urls_found": len(urls),
        "results": results,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.link_analysis 'https://example.com' or 'text with urls'")
        sys.exit(1)

    arg = " ".join(sys.argv[1:])

    if arg.startswith(("http://", "https://")):
        print(f"Analyzing URL: {arg}")
        result = analyze_url(arg)
    else:
        print(f"Extracting URLs from text")
        result = extract_urls(arg)

    import json

    print(json.dumps(result, indent=2))
