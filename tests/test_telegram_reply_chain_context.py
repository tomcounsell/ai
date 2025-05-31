"""
Test suite for Telegram reply chain context functionality.

Tests the enhanced context management that prioritizes reply chains
over temporal message order for better conversation context.
"""

import pytest
import time
from unittest.mock import Mock, AsyncMock, patch
from pyrogram.enums import ChatType

from integrations.telegram.handlers import MessageHandler
from integrations.telegram.chat_history import ChatHistoryManager


class TestChatHistoryReplyChain:
    """Test suite for ChatHistoryManager reply chain functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.chat_history = ChatHistoryManager(max_messages=20)
        self.chat_id = 12345

    def test_add_message_with_reply_id(self):
        """Test adding messages with reply_to_message_id tracking."""
        # Add original message
        self.chat_history.add_message(self.chat_id, "user", "Original question", None)
        
        # Add reply to original message
        self.chat_history.add_message(self.chat_id, "assistant", "Response to original", 1)
        
        # Add reply to the response
        self.chat_history.add_message(self.chat_id, "user", "Follow-up question", 2)
        
        # Check message structure
        messages = self.chat_history.chat_histories[self.chat_id]
        assert len(messages) == 3
        assert messages[0]["content"] == "Original question"
        assert "reply_to_message_id" not in messages[0]
        assert messages[1]["reply_to_message_id"] == 1
        assert messages[2]["reply_to_message_id"] == 2

    def test_get_reply_chain_simple(self):
        """Test getting a simple reply chain."""
        # Build conversation: A -> B -> C
        self.chat_history.add_message(self.chat_id, "user", "What is Python?", None)  # msg 1
        self.chat_history.add_message(self.chat_id, "assistant", "Python is a programming language", 1)  # msg 2
        self.chat_history.add_message(self.chat_id, "user", "Can you give examples?", 2)  # msg 3
        
        # Get reply chain for message 3 (should include 1 -> 2 -> 3)
        chain = self.chat_history.get_reply_chain(self.chat_id, 3)
        
        assert len(chain) == 3
        assert chain[0]["content"] == "What is Python?"
        assert chain[1]["content"] == "Python is a programming language" 
        assert chain[2]["content"] == "Can you give examples?"

    def test_get_reply_chain_partial(self):
        """Test getting reply chain when some messages in chain are missing."""
        # Add messages with gaps in the chain
        self.chat_history.add_message(self.chat_id, "user", "Question 1", None)  # msg 1
        self.chat_history.add_message(self.chat_id, "user", "Question 2", None)  # msg 2
        self.chat_history.add_message(self.chat_id, "user", "Reply to msg 5", 5)  # msg 3 (refers to non-existent msg 5)
        
        # Should only return the single message since reply target doesn't exist
        chain = self.chat_history.get_reply_chain(self.chat_id, 3)
        assert len(chain) == 1
        assert chain[0]["content"] == "Reply to msg 5"

    def test_get_reply_chain_max_depth(self):
        """Test reply chain respects max_depth limit."""
        # Create long chain: 1 -> 2 -> 3 -> 4 -> 5 -> 6
        self.chat_history.add_message(self.chat_id, "user", "Message 1", None)  # msg 1
        self.chat_history.add_message(self.chat_id, "assistant", "Message 2", 1)  # msg 2
        self.chat_history.add_message(self.chat_id, "user", "Message 3", 2)  # msg 3
        self.chat_history.add_message(self.chat_id, "assistant", "Message 4", 3)  # msg 4
        self.chat_history.add_message(self.chat_id, "user", "Message 5", 4)  # msg 5
        self.chat_history.add_message(self.chat_id, "assistant", "Message 6", 5)  # msg 6
        
        # Get chain with max_depth=3
        chain = self.chat_history.get_reply_chain(self.chat_id, 6, max_depth=3)
        
        assert len(chain) == 3
        assert chain[0]["content"] == "Message 4"  # Should start from msg 4
        assert chain[1]["content"] == "Message 5"
        assert chain[2]["content"] == "Message 6"

    def test_context_with_reply_priority_simple(self):
        """Test context building with reply priority."""
        # Create conversation with mixed temporal and reply context
        self.chat_history.add_message(self.chat_id, "user", "Random message 1", None)  # msg 1
        self.chat_history.add_message(self.chat_id, "user", "Random message 2", None)  # msg 2
        self.chat_history.add_message(self.chat_id, "user", "Start of important topic", None)  # msg 3
        self.chat_history.add_message(self.chat_id, "assistant", "Response to important topic", 3)  # msg 4
        self.chat_history.add_message(self.chat_id, "user", "Random message 3", None)  # msg 5
        self.chat_history.add_message(self.chat_id, "user", "Follow-up on important topic", 4)  # msg 6
        
        # Get context prioritizing the reply chain for message 6
        context = self.chat_history.get_context_with_reply_priority(
            self.chat_id, current_message_reply_to_id=4, max_context_messages=5
        )
        
        # Should prioritize the reply chain: msg 3 -> msg 4 -> msg 6
        # And fill remaining slots with recent temporal context
        assert len(context) <= 5
        
        # Check that important topic messages are included
        content_texts = [msg["content"] for msg in context]
        assert "Start of important topic" in content_texts
        assert "Response to important topic" in content_texts
        assert "Follow-up on important topic" in content_texts

    def test_context_with_reply_priority_no_reply(self):
        """Test context building falls back to temporal when no reply chain."""
        # Add some messages
        self.chat_history.add_message(self.chat_id, "user", "Message 1", None)
        self.chat_history.add_message(self.chat_id, "assistant", "Response 1", 1)
        self.chat_history.add_message(self.chat_id, "user", "Message 2", None)
        
        # Get context without reply priority (should be same as regular context)
        context_with_reply = self.chat_history.get_context_with_reply_priority(
            self.chat_id, current_message_reply_to_id=None, max_context_messages=5
        )
        context_regular = self.chat_history.get_context(self.chat_id, max_context_messages=5)
        
        assert len(context_with_reply) == len(context_regular)
        assert context_with_reply == context_regular

    def test_context_with_reply_priority_avoids_duplicates(self):
        """Test that reply chain context doesn't include duplicates."""
        # Create scenario where reply chain might overlap with temporal context
        self.chat_history.add_message(self.chat_id, "user", "Question A", None)  # msg 1
        self.chat_history.add_message(self.chat_id, "assistant", "Answer A", 1)  # msg 2
        self.chat_history.add_message(self.chat_id, "user", "Follow-up A", 2)  # msg 3 (recent + in reply chain)
        
        context = self.chat_history.get_context_with_reply_priority(
            self.chat_id, current_message_reply_to_id=2, max_context_messages=5
        )
        
        # Should not have duplicate messages
        content_texts = [msg["content"] for msg in context]
        assert len(content_texts) == len(set(content_texts))  # No duplicates


class TestMessageHandlerReplyIntegration:
    """Test suite for MessageHandler integration with reply chain context."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.mock_chat_history = Mock()
        self.mock_notion_scout = Mock()
        
        # Mock the new reply-aware methods
        self.mock_chat_history.get_context_with_reply_priority = Mock(return_value=[])
        self.mock_chat_history.get_context = Mock(return_value=[])
        self.mock_chat_history.add_message = Mock()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history,
            notion_scout=self.mock_notion_scout
        )

    def test_extract_reply_to_message_id(self):
        """Test extraction of reply_to_message_id from Telegram message."""
        # Mock message with reply
        message = Mock()
        message.text = "This is a reply"
        message.chat.id = 12345
        message.chat.type = ChatType.PRIVATE
        message.from_user.username = "testuser"
        message.photo = None
        message.document = None
        message.voice = None
        message.audio = None
        message.video = None
        message.video_note = None
        message.date.timestamp.return_value = time.time()
        
        # Mock reply_to_message
        reply_msg = Mock()
        reply_msg.id = 98765
        message.reply_to_message = reply_msg
        
        # Mock client
        self.mock_client.get_me = AsyncMock()
        self.mock_client.get_me.return_value.username = "testbot"
        self.mock_client.get_me.return_value.id = 123456
        self.mock_client.read_chat_history = AsyncMock()
        self.mock_client.send_reaction = AsyncMock()
        
        # Mock the unified integration to avoid import errors
        with patch('agents.unified_integration.process_message_unified') as mock_unified:
            mock_unified.return_value = "Test response"
            
            # Process the message
            import asyncio
            asyncio.run(self.handler.handle_message(self.mock_client, message))
        
        # Verify add_message was called with reply_to_message_id
        calls = self.mock_chat_history.add_message.call_args_list
        assert len(calls) >= 1
        
        # Find the call that should include reply_to_message_id
        found_reply_call = False
        for call in calls:
            args, kwargs = call
            if len(args) >= 4:  # chat_id, role, content, reply_to_message_id
                found_reply_call = True
                assert args[3] == 98765  # reply_to_message_id
                break
        
        assert found_reply_call, "add_message should have been called with reply_to_message_id"

    @pytest.mark.asyncio
    async def test_reply_aware_context_used_for_text_messages(self):
        """Test that reply-aware context is used when message is a reply."""
        # Mock message with reply
        message = Mock()
        message.text = "This is a reply to previous message"
        message.chat.id = 12345
        message.chat.type = ChatType.PRIVATE
        message.from_user.username = "testuser"
        message.photo = None
        message.document = None
        message.voice = None
        message.audio = None
        message.video = None
        message.video_note = None
        message.date.timestamp.return_value = time.time()
        
        # Mock reply_to_message
        reply_msg = Mock()
        reply_msg.id = 54321
        message.reply_to_message = reply_msg
        
        # Mock client
        self.mock_client.get_me = AsyncMock()
        self.mock_client.get_me.return_value.username = "testbot"
        self.mock_client.get_me.return_value.id = 123456
        self.mock_client.read_chat_history = AsyncMock()
        self.mock_client.send_reaction = AsyncMock()
        
        # Mock the unified integration
        with patch('agents.unified_integration.process_message_unified') as mock_unified:
            mock_unified.return_value = "Test response"
            
            # Process the message
            await self.handler.handle_message(self.mock_client, message)
        
        # Verify reply-aware context method was called
        self.mock_chat_history.get_context_with_reply_priority.assert_called_once()
        args, kwargs = self.mock_chat_history.get_context_with_reply_priority.call_args
        assert args[1] == 54321  # reply_to_message_id
        
        # Verify regular context method was NOT called in this case
        self.mock_chat_history.get_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_to_regular_context_when_no_reply(self):
        """Test that regular context is used when message is not a reply."""
        # Mock message without reply
        message = Mock()
        message.text = "This is a regular message"
        message.chat.id = 12345
        message.chat.type = ChatType.PRIVATE
        message.from_user.username = "testuser"
        message.photo = None
        message.document = None
        message.voice = None
        message.audio = None
        message.video = None
        message.video_note = None
        message.date.timestamp.return_value = time.time()
        message.reply_to_message = None
        
        # Mock client
        self.mock_client.get_me = AsyncMock()
        self.mock_client.get_me.return_value.username = "testbot"
        self.mock_client.get_me.return_value.id = 123456
        self.mock_client.read_chat_history = AsyncMock()
        self.mock_client.send_reaction = AsyncMock()
        
        # Mock the unified integration
        with patch('agents.unified_integration.process_message_unified') as mock_unified:
            mock_unified.return_value = "Test response"
            
            # Process the message
            await self.handler.handle_message(self.mock_client, message)
        
        # Verify regular context method was called
        self.mock_chat_history.get_context.assert_called_once()
        
        # Verify reply-aware context method was NOT called
        self.mock_chat_history.get_context_with_reply_priority.assert_not_called()

    @pytest.mark.asyncio
    async def test_reply_context_in_photo_messages(self):
        """Test that reply context works for photo messages too."""
        # Mock photo message with reply
        message = Mock()
        message.photo = Mock()  # Indicates this is a photo
        message.caption = "Look at this image related to our discussion"
        message.chat.id = 12345
        message.chat.type = ChatType.PRIVATE
        message.from_user.username = "testuser"
        message.download = AsyncMock(return_value="/tmp/test_image.jpg")
        message.date.timestamp.return_value = time.time()
        
        # Mock reply_to_message
        reply_msg = Mock()
        reply_msg.id = 11111
        message.reply_to_message = reply_msg
        
        # Mock client
        self.mock_client.get_me = AsyncMock()
        self.mock_client.get_me.return_value.username = "testbot"
        self.mock_client.get_me.return_value.id = 123456
        self.mock_client.read_chat_history = AsyncMock()
        self.mock_client.send_reaction = AsyncMock()
        
        # Mock the unified integration for images
        with patch('agents.unified_integration.process_image_unified') as mock_image_unified:
            mock_image_unified.return_value = "Image analysis response"
            
            # Process the photo message
            await self.handler.handle_message(self.mock_client, message)
        
        # Verify reply-aware context was used for image processing
        self.mock_chat_history.get_context_with_reply_priority.assert_called()
        args, kwargs = self.mock_chat_history.get_context_with_reply_priority.call_args
        assert args[1] == 11111  # reply_to_message_id


class TestReplyChainEdgeCases:
    """Test edge cases and error conditions for reply chain functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.chat_history = ChatHistoryManager(max_messages=10)
        self.chat_id = 99999

    def test_reply_chain_with_circular_reference(self):
        """Test handling of circular references in reply chains."""
        # This shouldn't happen in real Telegram, but test robustness
        self.chat_history.add_message(self.chat_id, "user", "Message A", None)  # msg 1
        self.chat_history.add_message(self.chat_id, "user", "Message B", 3)  # msg 2 -> refers to msg 3
        self.chat_history.add_message(self.chat_id, "user", "Message C", 2)  # msg 3 -> refers to msg 2
        
        # Should not infinite loop, should handle gracefully
        chain = self.chat_history.get_reply_chain(self.chat_id, 2, max_depth=10)
        
        # Should include at least the requested message and stop when no valid parent found
        assert len(chain) >= 1
        assert len(chain) <= 10  # Should respect max_depth

    def test_reply_to_nonexistent_message(self):
        """Test reply to message ID that doesn't exist."""
        self.chat_history.add_message(self.chat_id, "user", "Reply to ghost", 999999)  # msg 1
        
        chain = self.chat_history.get_reply_chain(self.chat_id, 1)
        
        assert len(chain) == 1
        assert chain[0]["content"] == "Reply to ghost"

    def test_context_with_reply_priority_empty_history(self):
        """Test reply priority context with empty chat history."""
        context = self.chat_history.get_context_with_reply_priority(
            self.chat_id, current_message_reply_to_id=123, max_context_messages=5
        )
        
        assert len(context) == 0

    def test_message_id_assignment_consistency(self):
        """Test that message IDs are assigned consistently."""
        # Add messages and verify ID assignment
        self.chat_history.add_message(self.chat_id, "user", "First", None)
        self.chat_history.add_message(self.chat_id, "user", "Second", None)
        self.chat_history.add_message(self.chat_id, "user", "Third", None)
        
        messages = self.chat_history.chat_histories[self.chat_id]
        
        assert messages[0]["message_id"] == 1
        assert messages[1]["message_id"] == 2
        assert messages[2]["message_id"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])