#!/usr/bin/env python3
"""
Comprehensive Message Lifecycle Testing

Tests the complete message flow through integrations/telegram/handlers.py 
and agents/valor/handlers.py, covering all critical steps and integration points.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the modules we're testing
from integrations.telegram.handlers import MessageHandler
from agents.valor.handlers import (
    handle_telegram_message, 
    handle_telegram_message_with_intent,
    _detect_mixed_content
)


class MockMessage:
    """Mock Telegram message object for testing."""
    
    def __init__(self, text="test message", chat_id=12345, message_id=1, 
                 chat_type="PRIVATE", username="testuser", has_photo=False,
                 caption=None, reply_to_message=None):
        self.text = text
        self.caption = caption
        self.id = message_id
        self.date = Mock(timestamp=Mock(return_value=datetime.now().timestamp()))
        
        # Chat object
        from pyrogram.enums import ChatType as PyrogramChatType
        self.chat = Mock()
        self.chat.id = chat_id
        if chat_type == "PRIVATE":
            self.chat.type = PyrogramChatType.PRIVATE
        elif chat_type == "GROUP":
            self.chat.type = PyrogramChatType.GROUP
        else:
            self.chat.type = Mock()
            self.chat.type.name = chat_type
        
        # User object
        self.from_user = Mock()
        self.from_user.username = username
        self.from_user.id = 999
        
        # Media attributes
        self.photo = Mock() if has_photo else None
        self.document = None
        self.voice = None
        self.audio = None
        self.video = None
        self.video_note = None
        
        # Reply context
        self.reply_to_message = reply_to_message
        
        # Entities for mention processing
        self.entities = None
        self.caption_entities = None
        
        # Mock methods
        self.reply = AsyncMock()
        self.download = AsyncMock(return_value="/tmp/test_image.jpg")


class MockChatHistory:
    """Mock chat history manager for testing."""
    
    def __init__(self):
        self.messages = {}
        self.chat_histories = {}
    
    def add_message(self, chat_id, role, content, reply_to=None, msg_id=None, is_telegram_id=False):
        if chat_id not in self.messages:
            self.messages[chat_id] = []
        self.messages[chat_id].append({
            'role': role,
            'content': content,
            'reply_to': reply_to,
            'msg_id': msg_id,
            'timestamp': datetime.now().isoformat()
        })
    
    def get_context(self, chat_id, max_context_messages=10, max_age_hours=24, always_include_last=0):
        return self.messages.get(chat_id, [])[-max_context_messages:]
    
    def get_context_with_reply_priority(self, chat_id, reply_id, max_context_messages=10):
        return self.get_context(chat_id, max_context_messages)
    
    def get_internal_message_id(self, chat_id, telegram_id):
        return telegram_id  # Simplified for testing


class TestMessageLifecycleStage1(unittest.TestCase):
    """Test Stage 1: Message Reception and Initial Processing."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_client = AsyncMock()
        self.mock_chat_history = MockChatHistory()
        self.mock_notion_scout = Mock()
        
        # Mock environment for chat filtering
        self.env_patcher = patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '12345,67890',
            'TELEGRAM_ALLOW_DMS': 'true',
            'TELEGRAM_ALLOWED_USERS': 'testuser'  # Allow specific user for DMs
        })
        self.env_patcher.start()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history,
            notion_scout=self.mock_notion_scout,
            bot_start_time=datetime.now()
        )
    
    def tearDown(self):
        """Clean up test environment."""
        self.env_patcher.stop()
    
    async def test_basic_message_flow(self):
        """Test basic text message processing flow."""
        message = MockMessage(text="Hello bot", chat_id=12345)
        
        # Mock bot info
        bot_me = Mock()
        bot_me.username = "testbot"
        bot_me.id = 888
        self.mock_client.get_me = AsyncMock(return_value=bot_me)
        
        # Mock chat history read
        self.mock_client.read_chat_history = AsyncMock()
        
        # Mock reaction manager
        with patch('integrations.telegram.reaction_manager.add_message_received_reaction', new_callable=AsyncMock):
            with patch.object(self.handler, '_route_message_with_intent', new_callable=AsyncMock) as mock_route:
                await self.handler.handle_message(self.mock_client, message)
        
        # Verify message was stored in chat history
        self.assertEqual(len(self.mock_chat_history.messages[12345]), 1)
        self.assertEqual(self.mock_chat_history.messages[12345][0]['content'], "Hello bot")
        self.assertEqual(self.mock_chat_history.messages[12345][0]['role'], "user")
        
        # Verify routing was called
        mock_route.assert_called_once()
    
    async def test_security_whitelist_rejection(self):
        """Test security whitelist rejection."""
        # Message from non-whitelisted group
        message = MockMessage(text="Hello", chat_id=99999, chat_type="GROUP")
        
        # Should reject without processing
        with patch.object(self.handler, '_route_message_with_intent', new_callable=AsyncMock) as mock_route:
            await self.handler.handle_message(self.mock_client, message)
        
        # Verify routing was NOT called
        mock_route.assert_not_called()
        
        # Verify no message stored
        self.assertEqual(len(self.mock_chat_history.messages), 0)
    
    async def test_mention_processing(self):
        """Test @mention detection in groups."""
        message = MockMessage(text="@testbot hello", chat_id=12345, chat_type="GROUP")
        
        # Mock bot info
        bot_me = Mock()
        bot_me.username = "testbot"
        bot_me.id = 888
        
        is_mentioned, processed_text = self.handler._process_mentions(
            message, "testbot", 888, False
        )
        
        self.assertTrue(is_mentioned)
        self.assertEqual(processed_text, "hello")
    
    def test_old_message_filtering(self):
        """Test filtering of old messages (missed message collection)."""
        from integrations.telegram.utils import is_message_too_old
        
        # Recent message should not be filtered
        recent_timestamp = datetime.now().timestamp()
        self.assertFalse(is_message_too_old(recent_timestamp))
        
        # Old message should be filtered
        old_timestamp = (datetime.now() - timedelta(hours=2)).timestamp()
        self.assertTrue(is_message_too_old(old_timestamp))


class TestMessageLifecycleStage2(unittest.TestCase):
    """Test Stage 2: Intent Classification."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_client = AsyncMock()
        self.mock_chat_history = MockChatHistory()
        
        # Mock environment
        self.env_patcher = patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '12345',
            'TELEGRAM_ALLOW_DMS': 'true'
        })
        self.env_patcher.start()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history
        )
    
    def tearDown(self):
        """Clean up test environment."""
        self.env_patcher.stop()
    
    async def test_intent_classification_success(self):
        """Test successful intent classification."""
        message = MockMessage(text="What's the weather?", chat_id=12345)
        
        # Mock intent classification result
        mock_intent_result = Mock()
        mock_intent_result.intent = Mock()
        mock_intent_result.intent.value = "QUESTION"
        mock_intent_result.confidence = 0.9
        mock_intent_result.reasoning = "User asking about weather"
        mock_intent_result.suggested_emoji = "❓"
        
        with patch('integrations.telegram.handlers.classify_message_intent', 
                   new_callable=AsyncMock, return_value=mock_intent_result) as mock_classify:
            result = await self.handler._classify_message_intent("What's the weather?", message, 12345)
        
        self.assertEqual(result.intent.value, "QUESTION")
        self.assertEqual(result.confidence, 0.9)
        mock_classify.assert_called_once()
    
    async def test_intent_classification_fallback(self):
        """Test intent classification fallback on error."""
        message = MockMessage(text="Hello", chat_id=12345)
        
        with patch('integrations.telegram.handlers.classify_message_intent', 
                   new_callable=AsyncMock, side_effect=Exception("Classification failed")):
            result = await self.handler._classify_message_intent("Hello", message, 12345)
        
        # Should fallback to UNCLEAR
        self.assertEqual(result.intent.value, "UNCLEAR")
        self.assertEqual(result.confidence, 0.5)


class TestMessageLifecycleStage3(unittest.TestCase):
    """Test Stage 3: Message Routing with Intent."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_client = AsyncMock()
        self.mock_chat_history = MockChatHistory()
        
        # Mock environment
        self.env_patcher = patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '12345',
            'TELEGRAM_ALLOW_DMS': 'true'
        })
        self.env_patcher.start()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history
        )
    
    def tearDown(self):
        """Clean up test environment."""
        self.env_patcher.stop()
    
    async def test_ping_command_bypass(self):
        """Test ping command bypasses intent classification."""
        message = MockMessage(text="ping", chat_id=12345)
        
        with patch.object(self.handler, '_handle_ping', new_callable=AsyncMock) as mock_ping:
            with patch.object(self.handler, '_classify_message_intent', new_callable=AsyncMock) as mock_classify:
                await self.handler._route_message_with_intent(self.mock_client, message, 12345, "ping")
        
        # Ping handler should be called
        mock_ping.assert_called_once()
        
        # Intent classification should be skipped
        mock_classify.assert_not_called()
    
    async def test_intent_based_routing(self):
        """Test intent-based message routing."""
        message = MockMessage(text="Generate an image of a sunset", chat_id=12345)
        
        # Mock intent result
        mock_intent_result = Mock()
        mock_intent_result.intent = Mock()
        mock_intent_result.intent.value = "IMAGE_GENERATION"
        mock_intent_result.confidence = 0.95
        
        with patch.object(self.handler, '_classify_message_intent', 
                         new_callable=AsyncMock, return_value=mock_intent_result):
            with patch.object(self.handler, '_handle_with_valor_agent_intent', 
                             new_callable=AsyncMock) as mock_agent:
                with patch('integrations.telegram.reaction_manager.add_intent_based_reaction', new_callable=AsyncMock):
                    with patch('integrations.telegram.reaction_manager.complete_reaction_sequence', new_callable=AsyncMock):
                        await self.handler._route_message_with_intent(
                            self.mock_client, message, 12345, "Generate an image of a sunset"
                        )
        
        # Valor agent should be called with intent
        mock_agent.assert_called_once()
        call_args = mock_agent.call_args
        self.assertEqual(call_args[1]['intent_result'], mock_intent_result)


class TestMessageLifecycleStage4(unittest.TestCase):
    """Test Stage 4: Valor Agent Integration."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_client = AsyncMock()
        self.mock_chat_history = MockChatHistory()
        self.mock_notion_scout = Mock()
        
        # Mock environment
        self.env_patcher = patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '12345',
            'TELEGRAM_ALLOW_DMS': 'true'
        })
        self.env_patcher.start()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history,
            notion_scout=self.mock_notion_scout
        )
    
    def tearDown(self):
        """Clean up test environment."""
        self.env_patcher.stop()
    
    async def test_valor_agent_invocation(self):
        """Test Valor agent invocation with context."""
        message = MockMessage(text="What's on my todo list?", chat_id=12345)
        
        # Mock intent result
        mock_intent_result = Mock()
        mock_intent_result.intent = Mock()
        mock_intent_result.intent.value = "WORK_QUESTION"
        
        # Add some chat history
        self.mock_chat_history.add_message(12345, "user", "Previous message")
        self.mock_chat_history.add_message(12345, "assistant", "Previous response")
        
        with patch('agents.valor.handlers.handle_telegram_message_with_intent', 
                   new_callable=AsyncMock, return_value="Your todos are...") as mock_agent:
            with patch.object(self.handler, '_process_agent_response', new_callable=AsyncMock):
                await self.handler._handle_with_valor_agent_intent(
                    message, 12345, "What's on my todo list?", None, mock_intent_result
                )
        
        # Verify agent was called with correct parameters
        mock_agent.assert_called_once()
        call_args = mock_agent.call_args
        
        self.assertEqual(call_args[1]['message'], "What's on my todo list?")
        self.assertEqual(call_args[1]['chat_id'], 12345)
        self.assertEqual(call_args[1]['username'], "testuser")
        self.assertEqual(call_args[1]['intent_result'], mock_intent_result)
    
    async def test_notion_context_integration(self):
        """Test Notion context integration for priority questions."""
        message = MockMessage(text="What should I work on?", chat_id=12345)
        
        # Mock priority question detection
        with patch('integrations.telegram.utils.is_user_priority_question', return_value=True):
            with patch.object(self.handler, '_get_notion_context', 
                             new_callable=AsyncMock, return_value="Project: TestProject"):
                with patch('agents.valor.handlers.handle_telegram_message_with_intent', 
                           new_callable=AsyncMock, return_value="Work on feature X") as mock_agent:
                    with patch.object(self.handler, '_process_agent_response', new_callable=AsyncMock):
                        await self.handler._handle_with_valor_agent_intent(
                            message, 12345, "What should I work on?", None, None
                        )
        
        # Verify Notion data was passed to agent
        call_args = mock_agent.call_args
        self.assertEqual(call_args[1]['notion_data'], "Project: TestProject")
        self.assertTrue(call_args[1]['is_priority_question'])


class TestMessageLifecycleStage5(unittest.TestCase):
    """Test Stage 5: Valor Agent Processing."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_chat_history = MockChatHistory()
        
        # Add some test chat history
        self.mock_chat_history.add_message(12345, "user", "Previous question")
        self.mock_chat_history.add_message(12345, "assistant", "Previous answer")
    
    def test_mixed_content_detection(self):
        """Test mixed content detection for image+text messages."""
        # Test mixed content indicators
        mixed_message = "[Image+Text] User sent: Hello with an image"
        self.assertTrue(_detect_mixed_content(mixed_message))
        
        # Test non-mixed content
        text_message = "Just a regular text message"
        self.assertFalse(_detect_mixed_content(text_message))
        
        # Test image-only message
        image_message = "[Image]"
        self.assertFalse(_detect_mixed_content(image_message))
        
        # Test enhanced mixed content marker
        enhanced_mixed = "🖼️📝 MIXED CONTENT MESSAGE: text and image"
        self.assertTrue(_detect_mixed_content(enhanced_mixed))
    
    async def test_context_building(self):
        """Test context building for agent messages."""
        # Mock agent run
        with patch('agents.valor.handlers.valor_agent') as mock_agent:
            mock_result = Mock()
            mock_result.output = "Test response"
            mock_agent.run = AsyncMock(return_value=mock_result)
            
            response = await handle_telegram_message(
                message="What's the weather?",
                chat_id=12345,
                username="testuser",
                is_group_chat=False,
                chat_history_obj=self.mock_chat_history,
                notion_data="Project data",
                is_priority_question=True
            )
        
        # Verify agent was called
        mock_agent.run.assert_called_once()
        
        # Check that enhanced message includes context
        call_args = mock_agent.run.call_args[0]
        enhanced_message = call_args[0]
        
        self.assertIn("Recent conversation:", enhanced_message)
        self.assertIn("Current project data:", enhanced_message)
        self.assertIn("What's the weather?", enhanced_message)
    
    async def test_intent_system_prompt_modification(self):
        """Test intent-specific system prompt modification."""
        # Mock intent result
        mock_intent_result = Mock()
        mock_intent_result.intent = Mock()
        mock_intent_result.intent.value = "IMAGE_GENERATION"
        mock_intent_result.confidence = 0.9
        
        # Mock intent system prompt
        with patch('integrations.intent_prompts.get_intent_system_prompt', 
                   return_value="Custom prompt for image generation"):
            with patch('agents.valor.handlers.valor_agent') as mock_agent:
                mock_result = Mock()
                mock_result.output = "Generated image response"
                mock_agent.run = AsyncMock(return_value=mock_result)
                mock_agent.system_prompt = "Original prompt"
                
                response = await handle_telegram_message_with_intent(
                    message="Generate a sunset image",
                    chat_id=12345,
                    intent_result=mock_intent_result
                )
        
        # Verify agent was called
        mock_agent.run.assert_called_once()
        
        # Verify system prompt was restored (would be in finally block)
        self.assertEqual(mock_agent.system_prompt, "Original prompt")


class TestMessageLifecycleStage6(unittest.TestCase):
    """Test Stage 6: Agent Tool Execution."""
    
    async def test_tool_execution_context(self):
        """Test that tools receive proper context."""
        # This test would require mocking the PydanticAI agent execution
        # and verifying that tools receive the ValorContext properly
        
        # Mock ValorContext
        mock_context = Mock()
        mock_context.chat_id = 12345
        mock_context.username = "testuser"
        mock_context.chat_history_obj = MockChatHistory()
        
        with patch('agents.valor.handlers.valor_agent') as mock_agent:
            mock_result = Mock()
            mock_result.output = "Tool execution result"
            mock_agent.run = AsyncMock(return_value=mock_result)
            
            response = await handle_telegram_message(
                message="Search for Python tutorials",
                chat_id=12345,
                username="testuser",
                chat_history_obj=mock_context.chat_history_obj
            )
        
        # Verify agent was called with proper dependencies
        call_args = mock_agent.run.call_args
        deps = call_args[1]['deps']
        
        self.assertEqual(deps.chat_id, 12345)
        self.assertEqual(deps.username, "testuser")


class TestMessageLifecycleStage7(unittest.TestCase):
    """Test Stage 7: Response Processing."""
    
    def setUp(self):
        """Set up test environment."""
        self.mock_client = AsyncMock()
        self.mock_chat_history = MockChatHistory()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history
        )
    
    async def test_text_response_processing(self):
        """Test normal text response processing."""
        message = MockMessage(text="Hello", chat_id=12345)
        
        # Test normal response
        await self.handler._process_agent_response(
            message, 12345, "Hello! How can I help you today?"
        )
        
        # Verify response was stored in chat history
        self.assertEqual(len(self.mock_chat_history.messages[12345]), 1)
        self.assertEqual(
            self.mock_chat_history.messages[12345][0]['content'],
            "Hello! How can I help you today?"
        )
        self.assertEqual(self.mock_chat_history.messages[12345][0]['role'], "assistant")
    
    async def test_image_response_processing(self):
        """Test image response processing."""
        message = MockMessage(text="Generate image", chat_id=12345)
        
        # Mock image file existence
        with patch('pathlib.Path.exists', return_value=True):
            with patch('os.remove') as mock_remove:
                result = await self.handler._process_agent_response(
                    message, 12345, "TELEGRAM_IMAGE_GENERATED|/tmp/test.png|Generated sunset image"
                )
        
        # Should return True for image processing
        self.assertTrue(result)
        
        # Verify image was "sent"
        self.mock_client.send_photo.assert_called_once()
        
        # Verify cleanup was attempted
        mock_remove.assert_called_once_with("/tmp/test.png")
    
    async def test_long_response_splitting(self):
        """Test long response splitting."""
        message = MockMessage(text="Long question", chat_id=12345)
        
        # Create a response longer than 4000 characters
        long_response = "A" * 5000
        
        await self.handler._process_agent_response(message, 12345, long_response)
        
        # Should have been split into multiple replies
        self.assertGreater(message.reply.call_count, 1)
    
    def test_message_content_validation(self):
        """Test message content validation."""
        # Test empty content
        result = self.handler._validate_message_content("", "Fallback")
        self.assertEqual(result, "Fallback")
        
        # Test whitespace-only content
        result = self.handler._validate_message_content("   \n\t  ", "Fallback")
        self.assertEqual(result, "Fallback")
        
        # Test normal content
        result = self.handler._validate_message_content("Hello world", "Fallback")
        self.assertEqual(result, "Hello world")
        
        # Test content too long
        long_content = "A" * 5000
        result = self.handler._validate_message_content(long_content, "Fallback")
        self.assertEqual(len(result), 4000)  # Should be truncated
        self.assertTrue(result.endswith("..."))


class TestMessageLifecycleIntegration(unittest.TestCase):
    """Test end-to-end message lifecycle integration."""
    
    def setUp(self):
        """Set up integration test environment."""
        self.mock_client = AsyncMock()
        self.mock_chat_history = MockChatHistory()
        self.mock_notion_scout = Mock()
        
        # Mock environment
        self.env_patcher = patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '12345',
            'TELEGRAM_ALLOW_DMS': 'true'
        })
        self.env_patcher.start()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.mock_chat_history,
            notion_scout=self.mock_notion_scout
        )
    
    def tearDown(self):
        """Clean up integration test environment."""
        self.env_patcher.stop()
    
    async def test_complete_message_flow(self):
        """Test complete message flow from reception to response."""
        message = MockMessage(text="What's the weather like today?", chat_id=12345)
        
        # Mock all external dependencies
        bot_me = Mock()
        bot_me.username = "testbot"
        bot_me.id = 888
        self.mock_client.get_me = AsyncMock(return_value=bot_me)
        self.mock_client.read_chat_history = AsyncMock()
        
        # Mock intent classification
        mock_intent_result = Mock()
        mock_intent_result.intent = Mock()
        mock_intent_result.intent.value = "QUESTION"
        mock_intent_result.confidence = 0.9
        mock_intent_result.reasoning = "Weather question"
        mock_intent_result.suggested_emoji = "❓"
        
        # Mock Valor agent response
        with patch('integrations.telegram.reaction_manager.add_message_received_reaction', new_callable=AsyncMock):
            with patch('integrations.telegram.handlers.classify_message_intent', 
                       new_callable=AsyncMock, return_value=mock_intent_result):
                with patch('integrations.telegram.reaction_manager.add_intent_based_reaction', new_callable=AsyncMock):
                    with patch('agents.valor.handlers.handle_telegram_message_with_intent', 
                               new_callable=AsyncMock, return_value="It's sunny and 75°F today!") as mock_agent:
                        with patch('integrations.telegram.reaction_manager.complete_reaction_sequence', new_callable=AsyncMock):
                            await self.handler.handle_message(self.mock_client, message)
        
        # Verify complete flow
        # 1. Message stored in history
        self.assertEqual(len(self.mock_chat_history.messages[12345]), 2)  # User + assistant
        self.assertEqual(self.mock_chat_history.messages[12345][0]['role'], "user")
        self.assertEqual(self.mock_chat_history.messages[12345][1]['role'], "assistant")
        
        # 2. Agent was called
        mock_agent.assert_called_once()
        
        # 3. Response was sent
        message.reply.assert_called_once()
    
    async def test_error_recovery_flow(self):
        """Test error recovery throughout the message flow."""
        message = MockMessage(text="Test error handling", chat_id=12345)
        
        # Mock bot info
        bot_me = Mock()
        bot_me.username = "testbot"
        bot_me.id = 888
        self.mock_client.get_me = AsyncMock(return_value=bot_me)
        self.mock_client.read_chat_history = AsyncMock()
        
        # Mock intent classification failure
        with patch('integrations.telegram.reaction_manager.add_message_received_reaction', new_callable=AsyncMock):
            with patch('integrations.telegram.handlers.classify_message_intent', 
                       new_callable=AsyncMock, side_effect=Exception("Classification failed")):
                with patch('integrations.telegram.reaction_manager.add_intent_based_reaction', new_callable=AsyncMock):
                    with patch('agents.valor.handlers.handle_telegram_message_with_intent', 
                               new_callable=AsyncMock, side_effect=Exception("Agent failed")):
                        with patch('agents.valor.handlers.handle_telegram_message', 
                                   new_callable=AsyncMock, return_value="Fallback response"):
                            with patch('integrations.telegram.reaction_manager.complete_reaction_sequence', new_callable=AsyncMock):
                                await self.handler.handle_message(self.mock_client, message)
        
        # Should still have processed message and stored response
        self.assertEqual(len(self.mock_chat_history.messages[12345]), 2)
        message.reply.assert_called()


# Test runner for async tests
def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# Add async test methods to test classes
for test_class in [TestMessageLifecycleStage1, TestMessageLifecycleStage2, 
                   TestMessageLifecycleStage3, TestMessageLifecycleStage4,
                   TestMessageLifecycleStage5, TestMessageLifecycleStage6,
                   TestMessageLifecycleStage7, TestMessageLifecycleIntegration]:
    for method_name in dir(test_class):
        if method_name.startswith('test_') and asyncio.iscoroutinefunction(getattr(test_class, method_name)):
            # Wrap async test methods
            def make_sync_test(async_method):
                def sync_test(self):
                    return run_async_test(async_method(self))
                return sync_test
            
            original_method = getattr(test_class, method_name)
            setattr(test_class, method_name, make_sync_test(original_method))


if __name__ == "__main__":
    # Run all tests
    unittest.main(verbosity=2)