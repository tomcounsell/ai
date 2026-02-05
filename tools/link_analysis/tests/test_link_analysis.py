"""
Integration tests for link-analysis tool.

Run with: pytest tools/link-analysis/tests/ -v
"""

import os

import pytest

from tools.link_analysis import (
    analyze_text_links,
    analyze_url,
    extract_urls,
    get_metadata,
    validate_url,
)


class TestLinkAnalysisInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.link_analysis import extract_urls

        assert callable(extract_urls)


class TestExtractUrls:
    """Test URL extraction."""

    def test_single_url(self):
        """Extracts single URL."""
        result = extract_urls("Visit https://example.com for more info")

        assert result["count"] == 1
        assert "https://example.com" in result["urls"]

    def test_multiple_urls(self):
        """Extracts multiple URLs."""
        text = "Check https://example.com and https://test.com"
        result = extract_urls(text)

        assert result["count"] == 2

    def test_no_urls(self):
        """Returns empty for text without URLs."""
        result = extract_urls("No links here")

        assert result["count"] == 0
        assert result["urls"] == []

    def test_empty_text(self):
        """Handles empty text."""
        result = extract_urls("")

        assert result["count"] == 0

    def test_duplicate_urls(self):
        """Removes duplicate URLs."""
        text = "https://example.com and https://example.com again"
        result = extract_urls(text)

        assert result["count"] == 1

    def test_url_with_path(self):
        """Extracts URLs with paths."""
        text = "See https://example.com/path/to/page?query=1"
        result = extract_urls(text)

        assert result["count"] == 1
        assert "path/to/page" in result["urls"][0]


class TestValidateUrl:
    """Test URL validation."""

    def test_valid_url(self):
        """Validates accessible URL."""
        result = validate_url("https://httpbin.org/status/200")

        assert result["valid"] is True
        assert result["status_code"] == 200

    def test_invalid_format(self):
        """Rejects invalid URL format."""
        result = validate_url("not-a-url")

        assert result["valid"] is False

    def test_empty_url(self):
        """Rejects empty URL."""
        result = validate_url("")

        assert result["valid"] is False


class TestGetMetadata:
    """Test metadata extraction."""

    def test_get_metadata(self):
        """Gets metadata from URL."""
        result = get_metadata("https://httpbin.org/html")

        assert "url" in result
        assert "content_type" in result


@pytest.mark.skipif(
    not os.environ.get("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set"
)
class TestAnalyzeUrl:
    """Test URL content analysis."""

    def test_analyze_url(self):
        """Analyzes URL content."""
        result = analyze_url("https://example.com")

        assert "error" not in result or "URL not accessible" not in result.get(
            "error", ""
        )

    def test_analyze_without_content(self):
        """Analysis without content fetch."""
        result = analyze_url("https://example.com", analyze_content=False)

        assert "validation" in result


class TestAnalyzeTextLinks:
    """Test text link analysis."""

    def test_analyze_text_links(self):
        """Analyzes links in text."""
        text = "Check https://httpbin.org/status/200"
        result = analyze_text_links(text, validate_links=True, analyze_content=False)

        assert result["urls_found"] == 1
        assert len(result["results"]) == 1
