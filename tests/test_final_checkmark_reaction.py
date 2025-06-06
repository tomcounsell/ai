#!/usr/bin/env python3
"""
Test script to verify final checkmark reaction and action summaries work correctly.
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram.handlers import MessageHandler
from integrations.telegram.reaction_manager import TelegramReactionManager, ReactionStatus


class TestFinalCheckmarkReaction(unittest.TestCase):
    """Test that final checkmark reactions and action summaries work correctly."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.mock_client.send_reaction = AsyncMock()
        self.mock_client.send_message = AsyncMock()
        self.mock_client.send_photo = AsyncMock()
        self.mock_client.get_me = AsyncMock(return_value=Mock(username="testbot", id=123456))
        
        self.mock_chat_history = Mock()
        self.mock_chat_history.add_message = Mock()
        self.mock_chat_history.get_context = Mock(return_value=[])
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history,
            notion_scout=None,
            bot_start_time=0
        )
        
        self.reaction_manager = TelegramReactionManager()

    async def test_completion_reaction_emoji(self):
        """Test that completion reaction uses green checkmark emoji."""
        # Verify the completion reaction is set to green checkmark
        self.assertEqual(
            self.reaction_manager.status_reactions[ReactionStatus.COMPLETED],
            "✅"
        )
        
        # Verify green checkmark is in valid emojis list
        self.assertIn("✅", self.reaction_manager.valid_telegram_emojis)

    async def test_final_reaction_sent_with_text_response(self):
        """Test that final checkmark reaction is sent after text response."""
        # Mock message
        message = Mock()
        message.id = 123
        message.chat = Mock()
        message.chat.id = -1001234567890
        
        # Process a text response
        await self.handler._process_agent_response(
            message=message,
            chat_id=-1001234567890,
            answer="This is a test response"
        )
        
        # Verify response was sent
        self.mock_client.send_message.assert_called_once()
        
        # Verify completion reaction was sent
        self.mock_client.send_reaction.assert_called_with(-1001234567890, 123, "✅")

    async def test_final_reaction_sent_with_image_response(self):
        """Test that final checkmark reaction is sent after image response."""
        # Mock message
        message = Mock()
        message.id = 456
        message.chat = Mock()
        message.chat.id = -1001234567890
        
        # Create a mock image file
        with patch('pathlib.Path.exists', return_value=True):
            with patch('os.remove'):
                # Process an image response
                await self.handler._process_agent_response(
                    message=message,
                    chat_id=-1001234567890,
                    answer="TELEGRAM_IMAGE_GENERATED|/tmp/test.png|Test image caption"
                )
        
        # Verify image was sent
        self.mock_client.send_photo.assert_called_once()
        
        # Verify completion reaction was sent
        self.mock_client.send_reaction.assert_called_with(-1001234567890, 456, "✅")

    async def test_action_summary_in_response(self):
        """Test that action summaries are added to responses."""
        from agents.valor.handlers import handle_telegram_message
        from agents.valor.agent import ValorContext
        
        # Mock the agent result with tool usage
        mock_result = Mock()
        mock_result.output = "Here's the current weather information."
        
        # Mock message parts to simulate tool usage
        mock_message = Mock()
        mock_part = Mock()
        mock_part.tool_name = "search_current_info"
        mock_message.parts = [mock_part]
        mock_result.messages = [mock_message]
        
        # Patch the agent run to return our mock result
        with patch('agents.valor.agent.valor_agent.run', return_value=mock_result):
            response = await handle_telegram_message(
                message="What's the weather?",
                chat_id=12345,
                username="testuser",
                is_group_chat=False,
                chat_history_obj=self.mock_chat_history
            )
            
            # Verify action summary was added
            self.assertIn("Actions: 🔍 Web Search", response)
            self.assertIn("Here's the current weather information.", response)


def run_tests():
    """Run the test suite."""
    # Create test suite
    suite = unittest.TestLoader().loadTestsFromTestCase(TestFinalCheckmarkReaction)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Return success/failure
    return result.wasSuccessful()


if __name__ == "__main__":
    # Run async tests
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Convert async tests to sync for unittest
    for attr_name in dir(TestFinalCheckmarkReaction):
        attr = getattr(TestFinalCheckmarkReaction, attr_name)
        if asyncio.iscoroutinefunction(attr) and attr_name.startswith('test_'):
            setattr(TestFinalCheckmarkReaction, attr_name, 
                   lambda self, coro=attr: loop.run_until_complete(coro(self)))
    
    success = run_tests()
    exit(0 if success else 1)