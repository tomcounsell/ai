"""Tests for the web search tool."""

import os

import pytest

from tools.search import search, search_with_context


@pytest.fixture
def working_search_api(perplexity_api_key):
    """Skip if search API is not working (expired key, network issues, etc.).

    Performs a quick probe search to verify the API is functional.
    """
    result = search("test")
    if "error" in result:
        pytest.skip(f"Search API not available: {result['error']}")
    return perplexity_api_key


class TestSearchValidation:
    """Test input validation (no API required)."""

    def test_empty_query_returns_error(self):
        """Test that empty query returns error."""
        result = search("")
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_whitespace_query_returns_error(self):
        """Test that whitespace-only query returns error."""
        result = search("   \t\n  ")
        assert "error" in result

    def test_missing_api_key_returns_error(self):
        """Test that missing API key returns error."""
        original_key = os.environ.pop("PERPLEXITY_API_KEY", None)
        original_tavily = os.environ.pop("TAVILY_API_KEY", None)
        try:
            result = search("test query")
            assert "error" in result
            assert "PERPLEXITY_API_KEY" in result["error"]
        finally:
            if original_key:
                os.environ["PERPLEXITY_API_KEY"] = original_key
            if original_tavily:
                os.environ["TAVILY_API_KEY"] = original_tavily


class TestSearchExecution:
    """Test actual search execution with real API.

    These tests require a working search API (Perplexity or Tavily).
    They are skipped automatically when the API is unavailable.
    """

    def test_basic_search_query(self, working_search_api):
        """Test basic search query returns a summary."""
        result = search("What is the capital of France?")
        assert "error" not in result
        assert result.get("summary")
        assert "paris" in result["summary"].lower()

    def test_search_returns_query(self, working_search_api):
        """Test that search returns the original query."""
        query = "Python programming language"
        result = search(query)
        assert result.get("query") == query

    def test_search_type_parameter(self, working_search_api):
        """Test that different search types work."""
        for search_type in ("conversational", "factual", "citations"):
            result = search("test query", search_type=search_type)
            assert "error" not in result
            assert result.get("summary")


class TestSearchWithContext:
    """Test search with context (requires working API)."""

    def test_search_with_context(self, working_search_api):
        """Test search with additional context."""
        result = search_with_context(
            query="best practices",
            context="I am developing a Python REST API",
            search_type="conversational",
        )
        assert "error" not in result
        assert result.get("summary")


class TestSearchParameters:
    """Test search parameters (requires working API)."""

    def test_max_results_clamping(self, working_search_api):
        """Test max_results=100 is clamped and doesn't cause errors."""
        result = search("test", max_results=100)
        assert "error" not in result or "max_results" not in result.get("error", "")

    def test_domain_filter(self, working_search_api):
        """Test domain filtering."""
        result = search("Python tutorials", domain_filter=["python.org"])
        assert "error" not in result
        assert result.get("summary")

    def test_time_filter(self, working_search_api):
        """Test time filter for recent results."""
        result = search("latest tech news", time_filter="week")
        assert "error" not in result
        assert result.get("summary")


class TestSearchEdgeCases:
    """Test edge cases (requires working API)."""

    @pytest.mark.parametrize(
        "query",
        [
            "What is C++ & C#?",
            "\u65e5\u672c\u8a9e\u3068\u306f\u4f55\u3067\u3059\u304b",  # "What is Japanese?"
            (
                "Explain the process of photosynthesis in plants including the "
                "light-dependent and light-independent reactions"
            ),
        ],
        ids=["special_chars", "unicode", "long_query"],
    )
    def test_query_variations(self, working_search_api, query):
        """Test that various query formats are handled without errors."""
        result = search(query)
        assert "error" not in result
