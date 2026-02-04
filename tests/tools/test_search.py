"""Tests for the web search tool."""

import os

import pytest

from tools.search import search, search_with_context


class TestSearchValidation:
    """Test input validation."""

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
        # Temporarily remove API key
        original_key = os.environ.pop("PERPLEXITY_API_KEY", None)
        try:
            result = search("test query")
            assert "error" in result
            assert "PERPLEXITY_API_KEY" in result["error"]
        finally:
            if original_key:
                os.environ["PERPLEXITY_API_KEY"] = original_key


class TestSearchExecution:
    """Test actual search execution with real API."""

    def test_basic_search_query(self, perplexity_api_key):
        """Test basic search query."""
        result = search("What is the capital of France?")
        assert "error" not in result
        assert result.get("summary")
        assert "paris" in result["summary"].lower()

    def test_search_returns_query(self, perplexity_api_key):
        """Test that search returns the original query."""
        query = "Python programming language"
        result = search(query)
        assert result.get("query") == query

    def test_search_conversational_type(self, perplexity_api_key):
        """Test conversational search type."""
        result = search("How do I make coffee?", search_type="conversational")
        assert "error" not in result
        assert result.get("summary")

    def test_search_factual_type(self, perplexity_api_key):
        """Test factual search type."""
        result = search("population of Japan 2024", search_type="factual")
        assert "error" not in result
        assert result.get("summary")

    def test_search_citations_type(self, perplexity_api_key):
        """Test citations search type."""
        result = search("climate change effects", search_type="citations")
        assert "error" not in result
        assert result.get("summary")


class TestSearchWithContext:
    """Test search with context."""

    def test_search_with_context(self, perplexity_api_key):
        """Test search with additional context."""
        result = search_with_context(
            query="best practices",
            context="I am developing a Python REST API",
            search_type="conversational",
        )
        assert "error" not in result
        assert result.get("summary")


class TestSearchParameters:
    """Test search parameters."""

    def test_max_results_clamping(self, perplexity_api_key):
        """Test max_results is clamped to valid range."""
        # max_results=100 should be clamped to 50
        result = search("test", max_results=100)
        # Should not error due to clamping
        assert "error" not in result or "max_results" not in result.get("error", "")

    def test_domain_filter(self, perplexity_api_key):
        """Test domain filtering."""
        result = search("Python tutorials", domain_filter=["python.org"])
        assert "error" not in result
        assert result.get("summary")

    def test_time_filter_week(self, perplexity_api_key):
        """Test time filter for recent results."""
        result = search("latest tech news", time_filter="week")
        assert "error" not in result
        assert result.get("summary")


class TestSearchEdgeCases:
    """Test edge cases."""

    def test_special_characters_in_query(self, perplexity_api_key):
        """Test query with special characters."""
        result = search("What is C++ & C#?")
        assert "error" not in result

    def test_unicode_query(self, perplexity_api_key):
        """Test query with unicode characters."""
        result = search("日本語とは何ですか")  # "What is Japanese?"
        assert "error" not in result

    def test_long_query(self, perplexity_api_key):
        """Test with a longer query."""
        query = "Explain the process of photosynthesis in plants including the light-dependent and light-independent reactions"
        result = search(query)
        assert "error" not in result
        assert result.get("summary")
