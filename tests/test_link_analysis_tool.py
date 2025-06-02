#!/usr/bin/env python3
"""
Simplified unit tests for the link analysis tool.
Tests URL extraction, validation, analysis, and storage functionality.
"""

import os
import sqlite3
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from tools.link_analysis_tool import (
    extract_urls,
    validate_url,
    is_url_only_message,
    analyze_url_content,
    store_link_with_analysis,
    search_stored_links
)


class TestLinkAnalysisUtilities(unittest.TestCase):
    """Test utility functions for URL processing."""

    def test_extract_urls_basic(self):
        """Test basic URL extraction from text."""
        text = "Visit https://example.com for more info"
        urls = extract_urls(text)
        self.assertEqual(urls, ["https://example.com"])

    def test_extract_urls_multiple(self):
        """Test extraction of multiple URLs from text."""
        text = "Check https://example.com and http://github.com/test"
        urls = extract_urls(text)
        self.assertEqual(len(urls), 2)
        self.assertIn("https://example.com", urls)
        self.assertIn("http://github.com/test", urls)

    def test_validate_url_valid_cases(self):
        """Test URL validation for valid URLs."""
        valid_urls = [
            "https://example.com",
            "http://example.com",
            "https://subdomain.example.com/path",
        ]
        
        for url in valid_urls:
            with self.subTest(url=url):
                self.assertTrue(validate_url(url), f"Should be valid: {url}")

    def test_validate_url_invalid_cases(self):
        """Test URL validation for invalid URLs."""
        invalid_urls = [
            "not-a-url",
            "https://",  # No domain
            "example.com",  # No scheme
            "",  # Empty string
        ]
        
        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(validate_url(url), f"Should be invalid: {url}")

    def test_is_url_only_message_true_cases(self):
        """Test detection of messages containing only URLs."""
        self.assertTrue(is_url_only_message("https://example.com"))
        self.assertTrue(is_url_only_message("  https://example.com  "))

    def test_is_url_only_message_false_cases(self):
        """Test detection of messages with text and URLs."""
        self.assertFalse(is_url_only_message("Check out https://example.com"))
        self.assertFalse(is_url_only_message("No URLs here"))


class TestLinkAnalysisAPI(unittest.TestCase):
    """Test API integration for URL content analysis."""

    @patch('tools.link_analysis_tool.OpenAI')
    def test_analyze_url_content_success(self, mock_openai):
        """Test successful URL content analysis."""
        # Mock successful API response
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = """
TITLE: Example Article Title
MAIN_TOPIC: This is a comprehensive article about testing.
REASONS_TO_CARE: 
• Provides valuable testing insights
• Shows best practices for API testing
"""
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        # Mock environment variable
        with patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test-key'}):
            result = analyze_url_content("https://example.com")

        self.assertNotIn('error', result)
        self.assertEqual(result['title'], 'Example Article Title')
        self.assertEqual(result['main_topic'], 'This is a comprehensive article about testing.')

    def test_analyze_url_content_invalid_url(self):
        """Test analysis with invalid URL."""
        result = analyze_url_content("not-a-url")
        self.assertIn('error', result)
        self.assertIn('Invalid URL format', result['error'])

    def test_analyze_url_content_missing_api_key(self):
        """Test analysis with missing API key."""
        with patch.dict(os.environ, {}, clear=True):
            result = analyze_url_content("https://example.com")
        
        self.assertIn('error', result)
        self.assertIn('Missing PERPLEXITY_API_KEY', result['error'])


class TestLinkStorage(unittest.TestCase):
    """Test link storage and retrieval functionality."""

    @patch('tools.link_analysis_tool.analyze_url_content')
    def test_store_link_with_analysis_success(self, mock_analyze):
        """Test successful link storage with analysis."""
        # Mock successful analysis - this is the only thing we need to mock
        mock_analyze.return_value = {
            'title': 'Test Article',
            'main_topic': 'Testing best practices',
            'reasons_to_care': 'Valuable for developers'
        }

        # Use real database functions
        result = store_link_with_analysis("https://test-example-success.com")
        self.assertTrue(result)
        
        # Verify by searching
        search_result = search_stored_links("test-example-success")
        self.assertIn("test-example-success", search_result)
        self.assertIn("Test Article", search_result)

    @patch('tools.link_analysis_tool.analyze_url_content')
    def test_store_link_with_analysis_error(self, mock_analyze):
        """Test link storage with analysis error."""
        # Mock analysis error
        mock_analyze.return_value = {'error': 'API failed'}

        # Use real database functions
        result = store_link_with_analysis("https://test-example-error.com")
        self.assertTrue(result)  # Should still store with error status
        
        # Verify error was recorded by searching
        search_result = search_stored_links("test-example-error")
        self.assertIn("test-example-error", search_result)
        self.assertIn("❌", search_result)  # Error icon should be present

    def test_store_link_invalid_url(self):
        """Test storage with invalid URL."""
        result = store_link_with_analysis("not-a-url")
        self.assertFalse(result)

    def test_search_stored_links_not_found(self):
        """Test searching with no matches."""
        result = search_stored_links("totally-nonexistent-domain-12345")
        self.assertIn("No links found", result)


class TestAgentIntegration(unittest.TestCase):
    """Test agent tool integration."""

    def test_agent_tools_import(self):
        """Test that agent tools can be imported successfully."""
        try:
            from agents.valor.agent import save_link_for_later, search_saved_links
            self.assertTrue(callable(save_link_for_later))
            self.assertTrue(callable(search_saved_links))
        except ImportError as e:
            self.fail(f"Failed to import agent tools: {e}")

    @patch('agents.valor.agent.store_link_with_analysis')
    @patch('agents.valor.agent.extract_urls')
    def test_save_link_for_later_agent_tool(self, mock_extract, mock_store):
        """Test save_link_for_later agent tool functionality."""
        try:
            from agents.valor.agent import save_link_for_later
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock dependencies
        mock_extract.return_value = ["https://example.com"]
        mock_store.return_value = True

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()

        result = save_link_for_later(mock_ctx, "https://example.com")
        
        self.assertIn("Link saved successfully", result)
        self.assertIn("https://example.com", result)
        mock_store.assert_called_once_with("https://example.com")


if __name__ == '__main__':
    unittest.main()