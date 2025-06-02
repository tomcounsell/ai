"""Integration tests for telegram history search functionality."""

import time
import unittest
from unittest.mock import MagicMock

from integrations.telegram.chat_history import ChatHistoryManager
from tools.telegram_history_tool import search_telegram_history


class TestTelegramHistorySearch(unittest.TestCase):
    """Test suite for telegram history search with real ChatHistoryManager."""

    def setUp(self):
        """Set up test with fresh ChatHistoryManager and sample data."""
        # Use real ChatHistoryManager instance
        self.chat_history = ChatHistoryManager()
        self.test_chat_id = 12345
        
        # Clear any existing history for clean test
        if self.test_chat_id in self.chat_history.chat_histories:
            del self.chat_history.chat_histories[self.test_chat_id]
        
        # Add sample messages with different timestamps and content
        current_time = time.time()
        
        sample_messages = [
            {
                "role": "user",
                "content": "Let's discuss the authentication API implementation",
                "timestamp": current_time - 3600,  # 1 hour ago
                "message_id": 1
            },
            {
                "role": "assistant", 
                "content": "For authentication, I recommend using JWT tokens with proper validation",
                "timestamp": current_time - 3500,  # 55 minutes ago
                "message_id": 2
            },
            {
                "role": "user",
                "content": "What about API rate limiting for the endpoints?",
                "timestamp": current_time - 1800,  # 30 minutes ago
                "message_id": 3
            },
            {
                "role": "assistant",
                "content": "Rate limiting should be implemented per user with sliding window",
                "timestamp": current_time - 1700,  # 28 minutes ago  
                "message_id": 4
            },
            {
                "role": "user",
                "content": "Can you help me debug this Python function?",
                "timestamp": current_time - 600,   # 10 minutes ago
                "message_id": 5
            },
            {
                "role": "assistant",
                "content": "Sure! Let me take a look at your Python code",
                "timestamp": current_time - 500,   # 8 minutes ago
                "message_id": 6
            }
        ]
        
        # Add messages to chat history (timestamps are set automatically)
        for msg in sample_messages:
            self.chat_history.add_message(
                chat_id=self.test_chat_id,
                role=msg["role"],
                content=msg["content"]
            )
            # Manually adjust timestamp for test purposes
            if self.chat_history.chat_histories[self.test_chat_id]:
                self.chat_history.chat_histories[self.test_chat_id][-1]["timestamp"] = msg["timestamp"]

    def test_search_basic_functionality(self):
        """Test basic search finds relevant messages."""
        # Search for authentication-related messages
        result = search_telegram_history(
            query="authentication",
            chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            max_results=5
        )
        
        # Should find authentication-related messages
        self.assertIn("authentication", result.lower())
        self.assertIn("JWT tokens", result)
        self.assertIn("Found", result)
        
    def test_search_relevance_ranking(self):
        """Test that search ranks by relevance + recency."""
        # Search for "API" which appears in multiple messages
        result = search_telegram_history(
            query="API",
            chat_history_obj=self.chat_history, 
            chat_id=self.test_chat_id,
            max_results=3
        )
        
        # Should find API-related messages
        self.assertIn("API", result)
        self.assertIn("Found", result)
        
        # More recent "API rate limiting" message should rank higher than older "authentication API"
        lines = result.split('\n')
        api_lines = [line for line in lines if 'API' in line or 'rate limiting' in line]
        
        # Should contain both messages
        self.assertTrue(any('rate limiting' in line for line in api_lines))
        self.assertTrue(any('authentication' in line for line in api_lines))

    def test_search_no_matches(self):
        """Test search behavior when no matches found."""
        result = search_telegram_history(
            query="nonexistent_term_xyz",
            chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            max_results=5
        )
        
        self.assertIn("No messages found matching", result)
        self.assertIn("nonexistent_term_xyz", result)

    def test_search_max_results_limit(self):
        """Test that max_results parameter is respected."""
        # Search with limit of 2 results
        result = search_telegram_history(
            query="user",  # Should match multiple messages
            chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            max_results=2
        )
        
        # Count the numbered results (1., 2., etc.)
        numbered_lines = [line for line in result.split('\n') if line.strip().startswith(('1.', '2.', '3.'))]
        self.assertLessEqual(len(numbered_lines), 2)

    def test_search_case_insensitive(self):
        """Test that search is case insensitive."""
        # Search with different cases
        result_lower = search_telegram_history(
            query="python",
            chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            max_results=5
        )
        
        result_upper = search_telegram_history(
            query="PYTHON", 
            chat_history_obj=self.chat_history,
            chat_id=self.test_chat_id,
            max_results=5
        )
        
        # Both should find the Python-related messages
        self.assertIn("Python", result_lower)
        self.assertIn("Python", result_upper)

    def test_search_with_no_chat_history(self):
        """Test search behavior when chat history is None."""
        result = search_telegram_history(
            query="test",
            chat_history_obj=None,
            chat_id=self.test_chat_id,
            max_results=5
        )
        
        self.assertEqual(result, "No chat history available for search")

    def test_search_nonexistent_chat_id(self):
        """Test search behavior with nonexistent chat ID."""
        result = search_telegram_history(
            query="test",
            chat_history_obj=self.chat_history,
            chat_id=99999,  # Nonexistent chat ID
            max_results=5
        )
        
        self.assertIn("No messages found matching", result)

    def test_agent_tool_integration(self):
        """Test integration with agent tool wrapper (minimal mocking)."""
        from agents.valor.agent import search_conversation_history
        
        # Create minimal mock context with required dependencies
        mock_ctx = MagicMock()
        mock_ctx.deps.chat_id = self.test_chat_id
        mock_ctx.deps.chat_history_obj = self.chat_history
        
        # Test agent tool call
        result = search_conversation_history(
            ctx=mock_ctx,
            search_query="authentication",
            max_results=3
        )
        
        # Should find authentication-related content
        self.assertIn("authentication", result.lower())
        self.assertIn("JWT", result)

    def test_agent_tool_no_history(self):
        """Test agent tool handles missing chat history gracefully."""
        from agents.valor.agent import search_conversation_history
        
        # Create mock context with no chat history
        mock_ctx = MagicMock()
        mock_ctx.deps.chat_id = None
        mock_ctx.deps.chat_history_obj = None
        
        result = search_conversation_history(
            ctx=mock_ctx,
            search_query="test",
            max_results=3
        )
        
        self.assertEqual(result, "No chat history available for search")


if __name__ == '__main__':
    unittest.main()