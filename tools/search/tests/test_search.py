"""
Integration tests for search tool.

Run with: pytest tools/search/tests/ -v
"""

import os

import pytest

from tools.search import search, search_with_context


class TestSearchInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.search import search

        assert callable(search)

    def test_api_key_required(self):
        """Tool returns error when API key missing."""
        # Temporarily remove API key
        original = os.environ.get("PERPLEXITY_API_KEY")
        if "PERPLEXITY_API_KEY" in os.environ:
            del os.environ["PERPLEXITY_API_KEY"]

        try:
            result = search("test query")
            assert "error" in result
            assert "PERPLEXITY_API_KEY" in result["error"]
        finally:
            if original:
                os.environ["PERPLEXITY_API_KEY"] = original


@pytest.mark.skipif(
    not os.environ.get("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set"
)
class TestSearchCore:
    """Test core search functionality."""

    def test_basic_search(self):
        """Basic search returns results."""
        result = search("What is Python programming language?")

        assert "error" not in result, f"Search failed: {result.get('error')}"
        assert "summary" in result
        assert len(result["summary"]) > 0

    def test_citations_search(self):
        """Citations search includes sources."""
        result = search("Python programming language history", search_type="citations")

        assert "error" not in result, f"Search failed: {result.get('error')}"
        assert "summary" in result

    def test_factual_search(self):
        """Factual search returns precise information."""
        result = search("Python 3.12 release date", search_type="factual")

        assert "error" not in result, f"Search failed: {result.get('error')}"
        assert "summary" in result


class TestSearchValidation:
    """Test input validation."""

    def test_empty_query(self):
        """Empty query returns error."""
        result = search("")
        assert "error" in result

    def test_whitespace_query(self):
        """Whitespace-only query returns error."""
        result = search("   ")
        assert "error" in result


@pytest.mark.skipif(
    not os.environ.get("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set"
)
class TestSearchWithContext:
    """Test context-enhanced search."""

    def test_search_with_context(self):
        """Search with context returns relevant results."""
        result = search_with_context(
            query="best practices",
            context="I'm working on a Python web application using FastAPI",
            search_type="conversational",
        )

        assert "error" not in result, f"Search failed: {result.get('error')}"
        assert "summary" in result
