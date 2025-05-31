"""Test Telegram integration for handling messages with both text and images."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
from pyrogram.enums import ChatType

from integrations.telegram.handlers import MessageHandler
from integrations.telegram.chat_history import ChatHistoryManager


class TestTelegramImageTextHandling(unittest.IsolatedAsyncioTestCase):
    """Test cases for Telegram message handling with images and text combinations."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = AsyncMock()
        self.chat_history = ChatHistoryManager()
        self.handler = MessageHandler(
            client=self.client,
            chat_history=self.chat_history,
            notion_scout=None,
            bot_start_time=None
        )
        # Mock environment to allow all chats
        self.handler.allow_dms = True
        self.handler.allowed_groups = set()

    def create_mock_message(self, text=None, caption=None, photo=None, chat_type=ChatType.PRIVATE):
        """Create a mock message object with specified properties."""
        import time
        message = MagicMock()
        message.text = text
        message.caption = caption
        message.photo = photo
        message.id = 123
        message.date.timestamp.return_value = time.time()  # Use current time to avoid "too old" issues
        
        # Mock chat
        message.chat = MagicMock()
        message.chat.id = -123456
        message.chat.type = chat_type
        
        # Mock user
        message.from_user = MagicMock()
        message.from_user.username = "testuser"
        message.from_user.id = 456
        
        # Mock other message types as False
        message.document = None
        message.voice = None
        message.audio = None
        message.video = None
        message.video_note = None
        
        # Mock reply functionality
        message.reply = AsyncMock()
        
        return message

    def test_text_only_message_detection(self):
        """Test detection of text-only messages."""
        message = self.create_mock_message(text="Hello world")
        
        # Test _process_mentions method
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, True  # is_private_chat=True
        )
        
        self.assertEqual(processed_text, "Hello world")
        self.assertFalse(is_mentioned)  # No mention in private chat

    def test_photo_only_message_detection(self):
        """Test detection of photo-only messages (no caption)."""
        message = self.create_mock_message(photo=True)
        
        # Test _process_mentions method
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, True  # is_private_chat=True
        )
        
        self.assertEqual(processed_text, "")  # No text content

    def test_photo_with_caption_message_detection(self):
        """Test detection of photo messages with captions."""
        message = self.create_mock_message(caption="Check out this photo!", photo=True)
        
        # Test _process_mentions method
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, True  # is_private_chat=True
        )
        
        self.assertEqual(processed_text, "Check out this photo!")

    def test_photo_with_mention_in_caption(self):
        """Test photo with bot mention in caption (group chat)."""
        message = self.create_mock_message(
            caption="@botname check this out!", 
            photo=True, 
            chat_type=ChatType.GROUP
        )
        
        # Test _process_mentions method
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, False  # is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "check this out!")

    async def test_message_routing_text_only(self):
        """Test that text-only messages are routed correctly."""
        message = self.create_mock_message(text="Hello world")
        
        # Mock client.get_me() for bot info
        me_mock = MagicMock()
        me_mock.username = "testbot"
        me_mock.id = 789
        self.client.get_me.return_value = me_mock
        
        # Mock read chat history and send reaction
        self.client.read_chat_history = AsyncMock()
        self.client.send_reaction = AsyncMock()
        
        # The message should be processed as text, not as photo
        with patch.object(self.handler, '_handle_with_valor_agent') as mock_handle:
            mock_handle.return_value = None
            
            await self.handler.handle_message(self.client, message)
            
            # Should call _handle_with_valor_agent for text processing
            mock_handle.assert_called_once()

    async def test_message_routing_photo_only(self):
        """Test that photo-only messages are routed correctly."""
        message = self.create_mock_message(photo=True)
        
        # Mock client.get_me() for bot info
        me_mock = MagicMock()
        me_mock.username = "testbot"
        me_mock.id = 789
        self.client.get_me.return_value = me_mock
        
        # Mock read chat history and send reaction
        self.client.read_chat_history = AsyncMock()
        self.client.send_reaction = AsyncMock()
        
        # The message should be processed as photo
        with patch.object(self.handler, '_handle_photo_message') as mock_handle:
            mock_handle.return_value = None
            
            await self.handler.handle_message(self.client, message)
            
            # Should call _handle_photo_message
            mock_handle.assert_called_once()

    async def test_message_routing_photo_with_caption(self):
        """Test that photo messages with captions are routed correctly."""
        message = self.create_mock_message(caption="Look at this!", photo=True)
        
        # Mock client.get_me() for bot info
        me_mock = MagicMock()
        me_mock.username = "testbot"
        me_mock.id = 789
        self.client.get_me.return_value = me_mock
        
        # Mock read chat history and send reaction
        self.client.read_chat_history = AsyncMock()
        self.client.send_reaction = AsyncMock()
        
        # The message should be processed as photo (not text)
        with patch.object(self.handler, '_handle_photo_message') as mock_handle:
            mock_handle.return_value = None
            
            await self.handler.handle_message(self.client, message)
            
            # Should call _handle_photo_message, not text handler
            mock_handle.assert_called_once()

    def test_process_mentions_handles_both_text_and_caption(self):
        """Test that _process_mentions correctly handles both text and caption sources."""
        # Test message with text only
        message_text = self.create_mock_message(text="Hello @botname")
        is_mentioned, processed = self.handler._process_mentions(
            message_text, "botname", 789, False
        )
        self.assertTrue(is_mentioned)
        self.assertEqual(processed, "Hello")
        
        # Test message with caption only  
        message_caption = self.create_mock_message(caption="Hello @botname", photo=True)
        is_mentioned, processed = self.handler._process_mentions(
            message_caption, "botname", 789, False
        )
        self.assertTrue(is_mentioned)
        self.assertEqual(processed, "Hello")
        
        # Test message with both (should prefer text)
        message_both = self.create_mock_message(text="Text content", caption="Caption content")
        is_mentioned, processed = self.handler._process_mentions(
            message_both, "botname", 789, False
        )
        self.assertEqual(processed, "Text content")  # Should use text, not caption

    def test_edge_case_empty_caption(self):
        """Test handling of photos with empty captions."""
        message = self.create_mock_message(caption="", photo=True)
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, True
        )
        
        self.assertEqual(processed_text, "")

    def test_edge_case_none_caption(self):
        """Test handling of photos with None captions."""
        message = self.create_mock_message(caption=None, photo=True)
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, True
        )
        
        self.assertEqual(processed_text, "")

    def test_caption_entities_processing(self):
        """Test that caption entities are processed correctly."""
        message = self.create_mock_message(caption="Hello @botname check this", photo=True, chat_type=ChatType.GROUP)
        
        # Mock caption entities
        entity_mock = MagicMock()
        entity_mock.type = "mention"
        entity_mock.offset = 6  # Position of @botname
        entity_mock.length = 8  # Length of @botname
        
        message.entities = None  # No regular entities
        message.caption_entities = [entity_mock]  # Caption entities present
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, False  # Group chat
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "Hello  check this")  # @botname removed

    def test_text_mention_in_caption_entities(self):
        """Test that text_mention entities in captions are processed correctly."""
        message = self.create_mock_message(caption="Hello check this", photo=True, chat_type=ChatType.GROUP)
        
        # Mock text_mention entity
        entity_mock = MagicMock()
        entity_mock.type = "text_mention"
        entity_mock.offset = 0
        entity_mock.length = 5  # "Hello"
        entity_mock.user = MagicMock()
        entity_mock.user.id = 789  # Bot ID
        
        message.entities = None
        message.caption_entities = [entity_mock]
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, False  # Group chat
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "check this")  # First part removed, leading space stripped

    def test_both_entities_and_caption_entities(self):
        """Test that both regular entities and caption entities are processed."""
        message = self.create_mock_message(text="Text @botname", caption="Caption content", photo=True, chat_type=ChatType.GROUP)
        
        # Mock regular entity in text
        text_entity = MagicMock()
        text_entity.type = "mention"
        text_entity.offset = 5  # Position of @botname in text
        text_entity.length = 8
        
        # Mock caption entity  
        caption_entity = MagicMock()
        caption_entity.type = "mention"
        caption_entity.offset = 0  # Different position
        caption_entity.length = 7
        
        message.entities = [text_entity]
        message.caption_entities = [caption_entity]
        
        # Since _process_mentions prioritizes text over caption, should process text entities
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "botname", 789, False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "Text")  # @botname removed from text


if __name__ == "__main__":
    unittest.main()