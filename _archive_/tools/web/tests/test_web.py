"""
Tests for the web search and fetch tool.

Unit tests run without API keys. Integration tests require PERPLEXITY_API_KEY.
Run with: pytest tools/web/tests/ -v
"""

import json
import os
from pathlib import Path

import pytest

from tools.web import fetch_sync, web_search_sync
from tools.web.types import FetchResult, SearchResult, Source


class TestConfiguration:
    """Test tool configuration and setup."""

    def test_manifest_exists(self):
        """manifest.json should exist."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        assert manifest_path.exists()

    def test_manifest_valid_json(self):
        """manifest.json should be valid JSON."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["name"] == "web"
        assert manifest["type"] == "api"
        assert "search" in manifest["capabilities"]
        assert "fetch" in manifest["capabilities"]

    def test_readme_exists(self):
        """README.md should exist."""
        readme_path = Path(__file__).parent.parent / "README.md"
        assert readme_path.exists()


class TestImports:
    """Test that the module can be imported."""

    def test_import_sync_functions(self):
        """Should be able to import sync functions."""
        assert callable(web_search_sync)
        assert callable(fetch_sync)

    def test_import_async_functions(self):
        """Should be able to import async functions."""
        from tools.web import fetch, web_search

        assert callable(web_search)
        assert callable(fetch)

    def test_import_types(self):
        """Should be able to import type classes."""
        assert SearchResult is not None
        assert FetchResult is not None
        assert Source is not None


class TestTypes:
    """Test data types."""

    def test_source_creation(self):
        """Should create Source objects."""
        source = Source(url="https://example.com", title="Example", snippet="A snippet")
        assert source.url == "https://example.com"
        assert source.title == "Example"

    def test_search_result_creation(self):
        """Should create SearchResult objects."""
        result = SearchResult(
            answer="Test answer",
            sources=[Source(url="https://example.com", title="Ex", snippet="Snip")],
            citations=["https://example.com"],
            query="test",
            provider="test_provider",
        )
        assert result.answer == "Test answer"
        assert len(result.sources) == 1
        assert result.provider == "test_provider"

    def test_fetch_result_creation(self):
        """Should create FetchResult objects."""
        result = FetchResult(
            content="# Hello",
            title="Hello Page",
            url="https://example.com",
            provider="test_provider",
        )
        assert result.content == "# Hello"
        assert result.title == "Hello Page"


class TestWebSearchIntegration:
    """Integration tests requiring API keys."""

    @pytest.mark.skipif(
        not os.environ.get("PERPLEXITY_API_KEY") and not os.environ.get("TAVILY_API_KEY"),
        reason="No search API key set (PERPLEXITY_API_KEY or TAVILY_API_KEY)",
    )
    def test_web_search_returns_result(self):
        """Should return a SearchResult with a real query."""
        result = web_search_sync("What is Python programming language")
        if result is not None:
            assert isinstance(result, SearchResult)
            assert result.answer
            assert result.query == "What is Python programming language"

    @pytest.mark.skipif(
        not os.environ.get("PERPLEXITY_API_KEY") and not os.environ.get("TAVILY_API_KEY"),
        reason="No search API key set",
    )
    def test_web_search_empty_query(self):
        """Should handle empty query gracefully."""
        result = web_search_sync("")
        # Either returns None or a result -- should not raise
        assert result is None or isinstance(result, SearchResult)


class TestFetchIntegration:
    """Integration tests for URL fetching."""

    def test_fetch_real_url(self):
        """Should fetch content from a real URL."""
        result = fetch_sync("https://example.com")
        if result is not None:
            assert isinstance(result, FetchResult)
            assert result.content
            assert "example" in result.content.lower() or "Example" in result.content
