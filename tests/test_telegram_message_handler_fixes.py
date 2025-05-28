"""
Test suite for Telegram message handler bug fixes.

Tests the comprehensive solution for:
1. Bot responding to untagged image messages in groups
2. NoneType errors in image processing
3. Enhanced robustness and error handling
"""

import pytest
from unittest.mock import Mock, AsyncMock
from pyrogram.enums import ChatType

from integrations.telegram.handlers import MessageHandler


class TestMessageHandlerFixes:
    """Test suite for message handler bug fixes."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.mock_chat_history = Mock()
        self.mock_notion_scout = Mock()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history,
            notion_scout=self.mock_notion_scout
        )

    def test_process_mentions_with_photo_caption(self):
        """Test _process_mentions correctly handles photo captions."""
        # Mock message with caption (photo message)
        message = Mock()
        message.text = None
        message.caption = "@testbot analyze this image"
        message.reply_to_message = None
        message.entities = None
        
        bot_username = "testbot"
        bot_id = 123456
        is_private_chat = False
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, bot_username, bot_id, is_private_chat
        )
        
        assert is_mentioned is True
        assert processed_text == "analyze this image"

    def test_process_mentions_with_none_text_and_caption(self):
        """Test _process_mentions handles None text and caption gracefully."""
        # Mock message with no text or caption
        message = Mock()
        message.text = None
        message.caption = None
        message.reply_to_message = None
        message.entities = None
        
        bot_username = "testbot"
        bot_id = 123456
        is_private_chat = False
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, bot_username, bot_id, is_private_chat
        )
        
        assert is_mentioned is False
        assert processed_text == ""

    def test_process_mentions_with_missing_attributes(self):
        """Test _process_mentions handles messages missing attributes."""
        # Mock message with missing attributes
        message = Mock()
        # Don't set text or caption attributes at all
        if hasattr(message, 'text'):
            delattr(message, 'text')
        if hasattr(message, 'caption'):
            delattr(message, 'caption')
        
        bot_username = "testbot"
        bot_id = 123456
        is_private_chat = False
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, bot_username, bot_id, is_private_chat
        )
        
        assert is_mentioned is False
        assert processed_text == ""

    def test_process_mentions_with_invalid_bot_params(self):
        """Test _process_mentions handles invalid bot parameters."""
        message = Mock()
        message.text = "@testbot hello"
        message.reply_to_message = None
        message.entities = None
        
        # Test with None bot_username
        is_mentioned, processed_text = self.handler._process_mentions(
            message, None, 123456, False
        )
        assert is_mentioned is False
        assert processed_text == "@testbot hello"
        
        # Test with invalid bot_id
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "testbot", "invalid", False
        )
        assert is_mentioned is False
        assert processed_text == "@testbot hello"

    def test_process_mentions_with_reply_to_message_none_user(self):
        """Test _process_mentions handles reply_to_message with None from_user."""
        message = Mock()
        message.text = "hello"
        message.caption = None
        message.reply_to_message = Mock()
        message.reply_to_message.from_user = None
        message.entities = None
        
        bot_username = "testbot"
        bot_id = 123456
        is_private_chat = False
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, bot_username, bot_id, is_private_chat
        )
        
        assert is_mentioned is False
        assert processed_text == "hello"

    def test_process_mentions_with_malformed_entities(self):
        """Test _process_mentions handles malformed entities gracefully."""
        message = Mock()
        message.text = "@testbot hello"
        message.caption = None
        message.reply_to_message = None
        
        # Create malformed entity
        malformed_entity = Mock()
        malformed_entity.type = "mention"
        malformed_entity.offset = 100  # Beyond text length
        malformed_entity.length = 50   # Beyond text length
        
        message.entities = [malformed_entity]
        
        bot_username = "testbot"
        bot_id = 123456
        is_private_chat = False
        
        # Should not raise exception and should fallback gracefully
        is_mentioned, processed_text = self.handler._process_mentions(
            message, bot_username, bot_id, is_private_chat
        )
        
        # Should still work due to string matching fallback
        assert is_mentioned is True
        assert processed_text == "hello"

    def test_process_mentions_entity_without_user_attribute(self):
        """Test _process_mentions handles text_mention entities without user."""
        message = Mock()
        message.text = "Hello there"
        message.caption = None
        message.reply_to_message = None
        
        # Create text_mention entity without user attribute
        entity = Mock()
        entity.type = "text_mention"
        entity.offset = 0
        entity.length = 5
        # Don't set user attribute
        
        message.entities = [entity]
        
        bot_username = "testbot"
        bot_id = 123456
        is_private_chat = False
        
        # Should not raise exception
        is_mentioned, processed_text = self.handler._process_mentions(
            message, bot_username, bot_id, is_private_chat
        )
        
        assert is_mentioned is False
        assert processed_text == "Hello there"

    @pytest.mark.asyncio
    async def test_handle_message_with_error_in_process_mentions(self):
        """Test handle_message handles _process_mentions errors gracefully."""
        # Mock message
        message = Mock()
        message.photo = None
        message.document = None
        message.voice = None
        message.audio = None
        message.video = None
        message.video_note = None
        message.text = "hello"
        message.date.timestamp.return_value = 1000000000  # Not too old
        message.chat.id = 12345
        message.chat.type = ChatType.PRIVATE
        message.from_user.username = "testuser"
        
        # Mock client.get_me to raise exception
        self.mock_client.get_me = AsyncMock(side_effect=Exception("Network error"))
        
        # Should not raise exception, should handle gracefully
        await self.handler.handle_message(self.mock_client, message)
        
        # Should still add message to chat history as fallback
        self.mock_chat_history.add_message.assert_called()

    @pytest.mark.asyncio
    async def test_handle_photo_message_with_error_in_process_mentions(self):
        """Test _handle_photo_message handles _process_mentions errors gracefully."""
        # Mock message
        message = Mock()
        message.chat.id = 12345
        message.chat.type = ChatType.GROUP
        message.caption = "@testbot analyze this"
        message.from_user.username = "testuser"
        
        # Mock client.get_me to raise exception
        self.mock_client.get_me = AsyncMock(side_effect=Exception("Network error"))
        
        # Should not raise exception, should handle gracefully
        await self.handler._handle_photo_message(self.mock_client, message, 12345)
        
        # Should still add message to chat history as fallback
        self.mock_chat_history.add_message.assert_called()


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.mock_chat_history = Mock()
        self.handler = MessageHandler(self.mock_client, self.mock_chat_history)

    def test_empty_text_content_with_entities(self):
        """Test handling of empty text with entities."""
        message = Mock()
        message.text = ""
        message.caption = None
        message.reply_to_message = None
        
        entity = Mock()
        entity.type = "mention"
        entity.offset = 0
        entity.length = 0
        message.entities = [entity]
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "testbot", 123456, False
        )
        
        assert is_mentioned is False
        assert processed_text == ""

    def test_very_long_text_with_mention_at_end(self):
        """Test handling of very long text with mention at the end."""
        long_text = "a" * 4000 + " @testbot"
        message = Mock()
        message.text = long_text
        message.caption = None
        message.reply_to_message = None
        message.entities = None
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "testbot", 123456, False
        )
        
        assert is_mentioned is True
        assert processed_text == "a" * 4000

    def test_multiple_mentions_of_same_bot(self):
        """Test handling of multiple mentions of the same bot."""
        message = Mock()
        message.text = "@testbot hello @testbot how are you @testbot"
        message.caption = None
        message.reply_to_message = None
        message.entities = None
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "testbot", 123456, False
        )
        
        assert is_mentioned is True
        # Should remove all instances
        assert "@testbot" not in processed_text
        assert "hello  how are you" in processed_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])