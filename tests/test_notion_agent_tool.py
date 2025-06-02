#!/usr/bin/env python3
"""
Test suite for the query_notion_projects agent tool.
Tests agent tool functionality, error handling, and integration patterns.
"""

import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))


class TestNotionAgentTool(unittest.TestCase):
    """Test suite for query_notion_projects agent tool."""

    def test_agent_tool_import(self):
        """Test that query_notion_projects agent tool can be imported successfully."""
        try:
            from agents.valor.agent import query_notion_projects
            self.assertTrue(callable(query_notion_projects))
        except ImportError as e:
            self.fail(f"Failed to import query_notion_projects agent tool: {e}")

    @patch('agents.valor.agent.query_psyoptimal_workspace')
    def test_query_notion_projects_success(self, mock_query):
        """Test successful notion project query."""
        try:
            from agents.valor.agent import query_notion_projects
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock successful response
        mock_query.return_value = "🎯 **PsyOPTIMAL Status**\n\nFound 3 tasks ready for development..."

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()

        result = query_notion_projects(mock_ctx, "What tasks are ready for dev?")
        
        self.assertIn("PsyOPTIMAL Status", result)
        self.assertIn("3 tasks ready", result)
        mock_query.assert_called_once_with("What tasks are ready for dev?")

    @patch('agents.valor.agent.query_psyoptimal_workspace')
    def test_query_notion_projects_connection_error(self, mock_query):
        """Test connection error handling in notion project query."""
        try:
            from agents.valor.agent import query_notion_projects
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock connection error
        mock_query.side_effect = Exception("Connection refused")

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()

        result = query_notion_projects(mock_ctx, "What tasks are ready?")
        
        self.assertIn("❌ Connection error", result)
        self.assertIn("Cannot reach Notion API", result)

    @patch('agents.valor.agent.query_psyoptimal_workspace')
    def test_query_notion_projects_timeout_error(self, mock_query):
        """Test timeout error handling in notion project query."""
        try:
            from agents.valor.agent import query_notion_projects
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock timeout error
        mock_query.side_effect = Exception("Request timed out")

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()

        result = query_notion_projects(mock_ctx, "What tasks are ready?")
        
        self.assertIn("❌ Timeout error", result)
        self.assertIn("took too long", result)

    @patch('agents.valor.agent.query_psyoptimal_workspace')
    def test_query_notion_projects_api_key_error(self, mock_query):
        """Test API key error handling in notion project query."""
        try:
            from agents.valor.agent import query_notion_projects
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock API key error
        mock_query.side_effect = Exception("NOTION_API_KEY not configured")

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()

        result = query_notion_projects(mock_ctx, "What tasks are ready?")
        
        self.assertIn("❌ Authentication error", result)
        self.assertIn("NOTION_API_KEY", result)

    @patch('agents.valor.agent.query_psyoptimal_workspace')
    def test_query_notion_projects_anthropic_error(self, mock_query):
        """Test Anthropic API error handling in notion project query."""
        try:
            from agents.valor.agent import query_notion_projects
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock Anthropic error
        mock_query.side_effect = Exception("ANTHROPIC_API_KEY invalid")

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()

        result = query_notion_projects(mock_ctx, "What tasks are ready?")
        
        self.assertIn("❌ AI Analysis error", result)
        self.assertIn("ANTHROPIC_API_KEY", result)

    @patch('agents.valor.agent.query_psyoptimal_workspace')
    def test_query_notion_projects_workspace_error(self, mock_query):
        """Test workspace error handling in notion project query."""
        try:
            from agents.valor.agent import query_notion_projects
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock workspace error
        mock_query.side_effect = Exception("Unknown workspace: TestWorkspace")

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()

        result = query_notion_projects(mock_ctx, "What tasks are ready?")
        
        self.assertIn("❌ Workspace error", result)
        self.assertIn("Unknown workspace", result)

    @patch('logging.getLogger')
    @patch('agents.valor.agent.query_psyoptimal_workspace')
    def test_query_notion_projects_unexpected_error(self, mock_query, mock_logger):
        """Test unexpected error handling with logging in notion project query."""
        try:
            from agents.valor.agent import query_notion_projects
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock unexpected error
        mock_query.side_effect = Exception("Unexpected database error")
        mock_logger_instance = MagicMock()
        mock_logger.return_value = mock_logger_instance

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()

        result = query_notion_projects(mock_ctx, "What tasks are ready?")
        
        self.assertIn("❌ Error querying PsyOPTIMAL", result)
        self.assertIn("Unexpected database error", result)
        self.assertIn("Notion API integration", result)
        
        # Verify logging was called
        mock_logger_instance.error.assert_called_once()

    @patch('agents.valor.agent.query_psyoptimal_workspace')
    def test_query_notion_projects_context_handling(self, mock_query):
        """Test that query_notion_projects properly handles context parameter."""
        try:
            from agents.valor.agent import query_notion_projects
        except ImportError:
            self.skipTest("Agent dependencies not available")

        # Mock an error to ensure error handling works
        mock_query.side_effect = Exception("Test error")

        # Test that the function accepts context parameter without error
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()
        
        # This should not raise an error even if backend fails
        result = query_notion_projects(mock_ctx, "test question")
        # Should return some error message, not raise an exception
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("❌"))


if __name__ == '__main__':
    unittest.main()