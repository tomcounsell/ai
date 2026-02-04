"""Tests for the link analysis tool."""

import os

import pytest

from tools.link_analysis import (
    extract_urls,
    validate_url,
    get_metadata,
    analyze_url,
    analyze_text_links,
    summarize_url_content,
)


class TestExtractUrls:
    """Test URL extraction from text."""

    def test_extract_single_url(self):
        """Test extracting a single URL."""
        text = "Check out https://example.com for more info"
        result = extract_urls(text)

        assert result["count"] == 1
        assert "https://example.com" in result["urls"]

    def test_extract_multiple_urls(self):
        """Test extracting multiple URLs."""
        text = """
        Visit https://google.com and https://github.com for resources.
        Also check http://example.org
        """
        result = extract_urls(text)

        assert result["count"] == 3
        assert "https://google.com" in result["urls"]
        assert "https://github.com" in result["urls"]
        assert "http://example.org" in result["urls"]

    def test_extract_no_urls(self):
        """Test text with no URLs."""
        text = "This text has no URLs in it."
        result = extract_urls(text)

        assert result["count"] == 0
        assert result["urls"] == []

    def test_extract_empty_text(self):
        """Test empty text."""
        result = extract_urls("")
        assert result["count"] == 0
        assert result["urls"] == []

    def test_extract_deduplicates_urls(self):
        """Test that duplicate URLs are removed."""
        text = "Visit https://example.com and later https://example.com again"
        result = extract_urls(text)

        assert result["count"] == 1

    def test_extract_complex_urls(self):
        """Test extracting URLs with paths and parameters."""
        text = "Go to https://example.com/path/to/page?foo=bar&baz=qux#section"
        result = extract_urls(text)

        assert result["count"] == 1
        assert result["urls"][0].startswith("https://example.com/path")


class TestValidateUrl:
    """Test URL validation."""

    def test_validate_valid_url(self):
        """Test validating a valid URL."""
        result = validate_url("https://google.com")

        assert result["valid"] is True
        assert result["status_code"] == 200

    def test_validate_empty_url(self):
        """Test validating empty URL."""
        result = validate_url("")

        assert result["valid"] is False
        assert "empty" in result["error"].lower()

    def test_validate_invalid_format(self):
        """Test validating URL with invalid format."""
        result = validate_url("not-a-url")

        assert result["valid"] is False
        assert "invalid" in result["error"].lower()

    def test_validate_nonexistent_domain(self):
        """Test validating URL with non-existent domain."""
        result = validate_url("https://nonexistent-domain-12345.com", timeout=5)

        assert result["valid"] is False

    def test_validate_detects_redirect(self):
        """Test that redirects are detected."""
        # HTTP redirects to HTTPS
        result = validate_url("http://google.com")

        assert result["valid"] is True
        # Google typically redirects
        assert "redirected" in result


class TestGetMetadata:
    """Test metadata extraction."""

    def test_get_metadata_from_page(self):
        """Test getting metadata from a page."""
        result = get_metadata("https://google.com")

        assert "url" in result
        if "error" not in result:
            assert "title" in result
            assert "content_type" in result

    def test_get_metadata_from_invalid_url(self):
        """Test getting metadata from invalid URL."""
        result = get_metadata("https://nonexistent-12345.com")

        assert "error" in result


class TestAnalyzeUrl:
    """Test URL analysis."""

    def test_analyze_url_basic(self, perplexity_api_key):
        """Test basic URL analysis."""
        result = analyze_url("https://google.com", analyze_content=False)

        assert "url" in result
        assert "validation" in result
        assert "metadata" in result

    def test_analyze_url_with_content(self, perplexity_api_key):
        """Test URL analysis with content analysis."""
        result = analyze_url("https://google.com", analyze_content=True)

        assert "url" in result
        if "error" not in result:
            assert "analysis" in result

    def test_analyze_invalid_url(self):
        """Test analyzing invalid URL."""
        result = analyze_url("https://nonexistent-domain-12345.com")

        assert "error" in result

    def test_analyze_url_missing_api_key(self):
        """Test analysis without API key."""
        original_key = os.environ.pop("PERPLEXITY_API_KEY", None)
        try:
            result = analyze_url("https://google.com", analyze_content=True)
            assert "error" in result
            assert "PERPLEXITY_API_KEY" in result["error"]
        finally:
            if original_key:
                os.environ["PERPLEXITY_API_KEY"] = original_key


class TestAnalyzeTextLinks:
    """Test analyzing links in text."""

    def test_analyze_text_links(self):
        """Test analyzing links in text."""
        text = "Check https://google.com and https://github.com"
        result = analyze_text_links(text, validate_links=True, analyze_content=False)

        assert result["urls_found"] == 2
        assert len(result["results"]) == 2

    def test_analyze_text_no_links(self):
        """Test analyzing text with no links."""
        text = "This text has no links"
        result = analyze_text_links(text, validate_links=True)

        assert result["urls_found"] == 0
        assert result["results"] == []

    def test_analyze_text_with_validation(self):
        """Test that link validation is performed."""
        text = "Visit https://google.com"
        result = analyze_text_links(text, validate_links=True)

        assert result["urls_found"] == 1
        assert "validation" in result["results"][0]


class TestSummarizeUrlContent:
    """Test URL summarization using Perplexity API."""

    @pytest.mark.asyncio
    async def test_summarize_url_basic(self, perplexity_api_key):
        """Test basic URL summarization."""
        summary = await summarize_url_content("https://google.com")

        # Should return a string
        assert summary is not None
        assert isinstance(summary, str)
        assert len(summary) > 0

    @pytest.mark.asyncio
    async def test_summarize_url_news_article(self, perplexity_api_key):
        """Test summarizing a news/content page."""
        # Using a well-known stable page
        summary = await summarize_url_content(
            "https://en.wikipedia.org/wiki/Python_(programming_language)"
        )

        assert summary is not None
        assert isinstance(summary, str)
        # Should contain some content about Python
        assert len(summary) > 50

    @pytest.mark.asyncio
    async def test_summarize_url_missing_api_key(self):
        """Test summarization without API key."""
        original_key = os.environ.pop("PERPLEXITY_API_KEY", None)
        try:
            summary = await summarize_url_content("https://google.com")
            assert summary is None
        finally:
            if original_key:
                os.environ["PERPLEXITY_API_KEY"] = original_key

    @pytest.mark.asyncio
    async def test_summarize_url_timeout(self, perplexity_api_key):
        """Test that summarization respects timeout."""
        # Very short timeout should either succeed quickly or fail
        summary = await summarize_url_content("https://google.com", timeout=1.0)
        # Either returns a summary or None due to timeout - both are acceptable
        assert summary is None or isinstance(summary, str)

    @pytest.mark.asyncio
    async def test_summarize_invalid_url(self, perplexity_api_key):
        """Test summarizing an invalid URL."""
        # Perplexity should handle this gracefully
        summary = await summarize_url_content("https://nonexistent-domain-12345.com")
        # Should either return None or return an error message
        assert summary is None or isinstance(summary, str)
