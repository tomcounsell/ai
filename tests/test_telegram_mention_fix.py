"""Test the mention processing fix for photo/caption handling."""

import unittest
from unittest.mock import MagicMock, Mock
from integrations.telegram.handlers import MessageHandler


class MockMessage:
    """Mock Telegram message for testing."""
    
    def __init__(self, text=None, caption=None, entities=None, reply_to_message=None):
        self.text = text
        self.caption = caption
        self.entities = entities or []
        self.reply_to_message = reply_to_message
        self.chat = Mock()
        self.chat.type = "group"  # Default to group
        self.from_user = Mock()
        self.from_user.id = 12345


class MockEntity:
    """Mock Telegram entity for testing."""
    
    def __init__(self, type_name, offset, length, user=None):
        self.type = type_name
        self.offset = offset
        self.length = length
        self.user = user


class TestMentionProcessing(unittest.TestCase):
    """Test mention processing for both text messages and photo captions."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.handler = MessageHandler(
            client=Mock(),
            chat_history=Mock(),
            notion_scout=None,
            bot_start_time=None
        )
        self.bot_username = "test_bot"
        self.bot_id = 98765
    
    def test_text_message_with_mention(self):
        """Test normal text message with @mention."""
        message = MockMessage(text="Hey @test_bot, what's up?")
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "Hey , what's up?")
    
    def test_photo_with_caption_mention(self):
        """Test photo message with @mention in caption."""
        message = MockMessage(text=None, caption="@test_bot analyze this image please")
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "analyze this image please")
    
    def test_photo_without_caption(self):
        """Test photo message with no caption."""
        message = MockMessage(text=None, caption=None)
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertFalse(is_mentioned)
        self.assertEqual(processed_text, "")
    
    def test_photo_with_empty_caption(self):
        """Test photo message with empty caption."""
        message = MockMessage(text=None, caption="")
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertFalse(is_mentioned)
        self.assertEqual(processed_text, "")
    
    def test_photo_caption_no_mention(self):
        """Test photo with caption but no mention."""
        message = MockMessage(text=None, caption="Just a nice photo")
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertFalse(is_mentioned)
        self.assertEqual(processed_text, "Just a nice photo")
    
    def test_text_message_no_mention(self):
        """Test normal text message without mention."""
        message = MockMessage(text="Hello everyone!")
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertFalse(is_mentioned)
        self.assertEqual(processed_text, "Hello everyone!")
    
    def test_private_chat_always_mentioned(self):
        """Test that private chats don't need mentions."""
        message = MockMessage(text="Hello bot")
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=True
        )
        
        # Private chats don't check mentions, but function should still work
        self.assertFalse(is_mentioned)  # Logic doesn't set mentioned for private chats
        self.assertEqual(processed_text, "Hello bot")
    
    def test_reply_to_bot_message(self):
        """Test mention detection via reply to bot message."""
        # Create a mock reply message from the bot
        bot_message = Mock()
        bot_message.from_user.id = self.bot_id
        
        message = MockMessage(text="Thanks for the info!", reply_to_message=bot_message)
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "Thanks for the info!")
    
    def test_photo_reply_to_bot(self):
        """Test photo caption with reply to bot message."""
        bot_message = Mock()
        bot_message.from_user.id = self.bot_id
        
        message = MockMessage(
            text=None, 
            caption="Here's the image you requested", 
            reply_to_message=bot_message
        )
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "Here's the image you requested")
    
    def test_entity_mention_in_text(self):
        """Test mention via entities in text message."""
        entity = MockEntity("mention", 0, 9)  # "@test_bot" at beginning
        message = MockMessage(text="@test_bot hello", entities=[entity])
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "hello")
    
    def test_entity_mention_in_caption(self):
        """Test mention via entities in photo caption."""
        entity = MockEntity("mention", 0, 9)  # "@test_bot" at beginning
        message = MockMessage(
            text=None, 
            caption="@test_bot look at this", 
            entities=[entity]
        )
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "look at this")
    
    def test_text_mention_entity_in_caption(self):
        """Test text_mention entity in photo caption."""
        bot_user = Mock()
        bot_user.id = self.bot_id
        
        entity = MockEntity("text_mention", 0, 8, user=bot_user)  # "Test Bot" at beginning
        message = MockMessage(
            text=None,
            caption="Test Bot please help",
            entities=[entity]
        )
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "please help")
    
    def test_edge_case_none_values(self):
        """Test edge cases with None values."""
        # Message with no text or caption
        message = MockMessage(text=None, caption=None)
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertFalse(is_mentioned)
        self.assertEqual(processed_text, "")
    
    def test_mixed_content_text_takes_precedence(self):
        """Test that text takes precedence over caption when both exist."""
        message = MockMessage(
            text="@test_bot text content", 
            caption="@test_bot caption content"
        )
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, self.bot_username, self.bot_id, is_private_chat=False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "text content")


def run_mention_tests():
    """Run the mention processing tests."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMentionProcessing)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n✅ All mention processing tests passed!")
        return True
    else:
        print(f"\n❌ {len(result.failures)} tests failed, {len(result.errors)} errors")
        return False


if __name__ == "__main__":
    run_mention_tests()