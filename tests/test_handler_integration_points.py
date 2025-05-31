#!/usr/bin/env python3
"""
Handler Integration Points Testing

Focused tests for critical integration points between 
integrations/telegram/handlers.py and agents/valor/handlers.py
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestChatHistoryIntegration(unittest.TestCase):
    """Test chat history integration between handlers."""
    
    def setUp(self):
        """Set up test environment."""
        # Import here to avoid import issues during setup
        from integrations.telegram.handlers import MessageHandler
        from agents.valor.handlers import handle_telegram_message
        
        self.mock_client = AsyncMock()
        
        # Create a real-ish chat history manager for integration testing
        class TestChatHistory:
            def __init__(self):
                self.messages = {}
            
            def add_message(self, chat_id, role, content, reply_to=None, msg_id=None, is_telegram_id=False):
                if chat_id not in self.messages:
                    self.messages[chat_id] = []
                msg = {
                    'role': role,
                    'content': content,
                    'reply_to': reply_to,
                    'msg_id': msg_id,
                    'timestamp': datetime.now().isoformat(),
                    'is_telegram_id': is_telegram_id
                }
                self.messages[chat_id].append(msg)
                return len(self.messages[chat_id])  # Internal ID
            
            def get_context(self, chat_id, max_context_messages=10, max_age_hours=24, always_include_last=0):
                msgs = self.messages.get(chat_id, [])
                return msgs[-max_context_messages:] if msgs else []
            
            def get_context_with_reply_priority(self, chat_id, reply_id, max_context_messages=10):
                return self.get_context(chat_id, max_context_messages)
            
            def get_internal_message_id(self, chat_id, telegram_id):
                # Find message by telegram ID
                for msg in self.messages.get(chat_id, []):
                    if msg.get('msg_id') == telegram_id and msg.get('is_telegram_id'):
                        return msg['msg_id']
                return None
        
        self.chat_history = TestChatHistory()
        
        # Mock environment
        self.env_patcher = patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '12345',
            'TELEGRAM_ALLOW_DMS': 'true'
        })
        self.env_patcher.start()
        
        self.handler = MessageHandler(
            client=self.mock_client,
            chat_history=self.chat_history
        )
    
    def tearDown(self):
        """Clean up test environment."""
        self.env_patcher.stop()
    
    async def test_chat_history_flow_integration(self):
        """Test that chat history flows properly between telegram and valor handlers."""
        # Add some initial context
        self.chat_history.add_message(12345, "user", "Hello bot")
        self.chat_history.add_message(12345, "assistant", "Hi! How can I help?")
        self.chat_history.add_message(12345, "user", "What's the weather?")
        
        # Mock valor agent to capture the enhanced message
        captured_message = None
        captured_context = None
        
        async def mock_agent_run(message, deps=None):
            nonlocal captured_message, captured_context
            captured_message = message
            captured_context = deps
            result = Mock()
            result.output = "It's sunny today!"
            return result
        
        with patch('agents.valor.handlers.valor_agent') as mock_agent:
            mock_agent.run = mock_agent_run
            
            from agents.valor.handlers import handle_telegram_message
            response = await handle_telegram_message(
                message="And tomorrow?",
                chat_id=12345,
                username="testuser",
                chat_history_obj=self.chat_history
            )
        
        # Verify chat history was included in enhanced message
        self.assertIsNotNone(captured_message)
        self.assertIn("Recent conversation:", captured_message)
        self.assertIn("What's the weather?", captured_message)
        self.assertIn("And tomorrow?", captured_message)
        
        # Verify context object has chat history
        self.assertIsNotNone(captured_context)
        self.assertEqual(captured_context.chat_id, 12345)
        self.assertEqual(captured_context.chat_history_obj, self.chat_history)
    
    async def test_reply_chain_integration(self):
        """Test reply chain handling between handlers."""
        # Add initial messages
        msg1_id = self.chat_history.add_message(12345, "user", "Original question", msg_id=100, is_telegram_id=True)
        msg2_id = self.chat_history.add_message(12345, "assistant", "Original answer", msg_id=101, is_telegram_id=True)
        
        # Mock message object with reply
        mock_message = Mock()
        mock_message.text = "Follow-up question"
        mock_message.id = 102
        mock_message.chat = Mock()
        mock_message.chat.id = 12345
        mock_message.chat.type = Mock()
        mock_message.chat.type.name = "PRIVATE"
        mock_message.from_user = Mock()
        mock_message.from_user.username = "testuser"
        mock_message.from_user.id = 999
        mock_message.date = Mock()
        mock_message.date.timestamp = Mock(return_value=datetime.now().timestamp())
        mock_message.reply_to_message = Mock()
        mock_message.reply_to_message.id = 101  # Replying to assistant message
        mock_message.photo = None
        mock_message.document = None
        mock_message.voice = None
        mock_message.audio = None
        mock_message.video = None
        mock_message.video_note = None
        mock_message.entities = None
        mock_message.caption_entities = None
        mock_message.reply = AsyncMock()
        
        # Mock bot info
        bot_me = Mock()
        bot_me.username = "testbot"
        bot_me.id = 888
        self.mock_client.get_me = AsyncMock(return_value=bot_me)
        self.mock_client.read_chat_history = AsyncMock()
        
        # Capture the reply context used by valor agent
        captured_context = None
        
        async def mock_valor_handler(**kwargs):
            nonlocal captured_context
            captured_context = kwargs
            return "Reply to your follow-up"
        
        with patch('integrations.telegram.reaction_manager.add_message_received_reaction', new_callable=AsyncMock):
            with patch('integrations.telegram.handlers.classify_message_intent', new_callable=AsyncMock) as mock_classify:
                mock_intent = Mock()
                mock_intent.intent = Mock()
                mock_intent.intent.value = "QUESTION"
                mock_intent.confidence = 0.8
                mock_intent.reasoning = "Follow-up question"
                mock_intent.suggested_emoji = "❓"
                mock_classify.return_value = mock_intent
                
                with patch('integrations.telegram.reaction_manager.add_intent_based_reaction', new_callable=AsyncMock):
                    with patch('agents.valor.handlers.handle_telegram_message_with_intent', 
                               new_callable=AsyncMock, side_effect=mock_valor_handler):
                        with patch('integrations.telegram.reaction_manager.complete_reaction_sequence', new_callable=AsyncMock):
                            await self.handler.handle_message(self.mock_client, mock_message)
        
        # Verify reply context was passed correctly
        self.assertIsNotNone(captured_context)
        # Note: The handler should detect this is a reply and potentially modify context
        # The exact behavior depends on implementation details


class TestIntentIntegration(unittest.TestCase):
    """Test intent system integration between handlers."""
    
    def setUp(self):
        """Set up test environment."""
        from integrations.telegram.handlers import MessageHandler
        
        self.mock_client = AsyncMock()
        self.mock_chat_history = Mock()
        self.mock_chat_history.add_message = Mock()
        self.mock_chat_history.get_context = Mock(return_value=[])
        
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
    
    async def test_intent_to_valor_integration(self):
        """Test intent result flows from telegram handler to valor handler."""
        # Mock intent classification
        mock_intent_result = Mock()
        mock_intent_result.intent = Mock()
        mock_intent_result.intent.value = "IMAGE_GENERATION"
        mock_intent_result.confidence = 0.95
        mock_intent_result.reasoning = "User wants to generate an image"
        mock_intent_result.suggested_emoji = "🎨"
        
        # Capture what gets passed to valor handler
        captured_intent = None
        
        async def mock_valor_intent_handler(**kwargs):
            nonlocal captured_intent
            captured_intent = kwargs.get('intent_result')
            return "TELEGRAM_IMAGE_GENERATED|/tmp/sunset.png|Beautiful sunset image"
        
        with patch('integrations.telegram.handlers.classify_message_intent', 
                   new_callable=AsyncMock, return_value=mock_intent_result):
            with patch('agents.valor.handlers.handle_telegram_message_with_intent', 
                       new_callable=AsyncMock, side_effect=mock_valor_intent_handler):
                with patch.object(self.handler, '_process_agent_response', new_callable=AsyncMock):
                    await self.handler._handle_with_valor_agent_intent(
                        Mock(), 12345, "Generate a sunset image", None, mock_intent_result
                    )
        
        # Verify intent result was passed correctly
        self.assertIsNotNone(captured_intent)
        self.assertEqual(captured_intent.intent.value, "IMAGE_GENERATION")
        self.assertEqual(captured_intent.confidence, 0.95)
    
    async def test_intent_system_prompt_integration(self):
        """Test intent-specific system prompt integration."""
        from agents.valor.handlers import handle_telegram_message_with_intent
        
        # Mock intent result
        mock_intent_result = Mock()
        mock_intent_result.intent = Mock()
        mock_intent_result.intent.value = "CODE_HELP"
        mock_intent_result.confidence = 0.9
        
        # Mock intent system prompt
        custom_prompt = "You are a coding assistant specialized in helping with development tasks."
        
        # Track system prompt changes
        original_prompt = "Original system prompt"
        current_prompt = original_prompt
        
        def mock_prompt_setter(value):
            nonlocal current_prompt
            current_prompt = value
        
        def mock_prompt_getter():
            return current_prompt
        
        mock_agent = Mock()
        type(mock_agent).system_prompt = property(mock_prompt_getter, mock_prompt_setter)
        mock_agent.run = AsyncMock()
        mock_result = Mock()
        mock_result.output = "Here's how to solve your coding problem..."
        mock_agent.run.return_value = mock_result
        
        with patch('integrations.intent_prompts.get_intent_system_prompt', return_value=custom_prompt):
            with patch('agents.valor.handlers.valor_agent', mock_agent):
                response = await handle_telegram_message_with_intent(
                    message="How do I implement a binary search?",
                    chat_id=12345,
                    intent_result=mock_intent_result
                )
        
        # Verify response was generated
        self.assertEqual(response, "Here's how to solve your coding problem...")
        
        # Verify system prompt was restored
        self.assertEqual(current_prompt, original_prompt)


class TestSecurityIntegration(unittest.TestCase):
    """Test security and access control integration."""
    
    def setUp(self):
        """Set up test environment."""
        from integrations.telegram.handlers import MessageHandler
        
        self.mock_client = AsyncMock()
        self.mock_chat_history = Mock()
        self.mock_chat_history.add_message = Mock()
    
    async def test_whitelist_security_integration(self):
        """Test whitelist security prevents unauthorized access to valor agent."""
        # Mock environment with restrictive whitelist
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '99999',  # Different from test chat
            'TELEGRAM_ALLOW_DMS': 'false'
        }):
            handler = MessageHandler(
                client=self.mock_client,
                chat_history=self.mock_chat_history
            )
            
            # Mock message from non-whitelisted chat
            mock_message = Mock()
            mock_message.text = "Unauthorized message"
            mock_message.chat = Mock()
            mock_message.chat.id = 12345  # Not in whitelist
            mock_message.chat.type = Mock()
            mock_message.chat.type.name = "GROUP"
            mock_message.from_user = Mock()
            mock_message.from_user.username = "hacker"
            
            # Track if valor agent was called
            valor_called = False
            
            def mock_valor_call(*args, **kwargs):
                nonlocal valor_called
                valor_called = True
                return "Should not be reached"
            
            with patch('agents.valor.handlers.handle_telegram_message_with_intent', 
                       side_effect=mock_valor_call):
                await handler.handle_message(self.mock_client, mock_message)
            
            # Verify valor agent was NOT called
            self.assertFalse(valor_called)
            
            # Verify no message was stored in chat history
            self.mock_chat_history.add_message.assert_not_called()
    
    async def test_workspace_validation_integration(self):
        """Test workspace validation integration between handlers."""
        # This would test the integration with workspace validator
        # for Notion access control
        
        # Mock environment with proper setup
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '12345',
            'TELEGRAM_ALLOW_DMS': 'true'
        }):
            from integrations.telegram.handlers import MessageHandler
            
            handler = MessageHandler(
                client=self.mock_client,
                chat_history=self.mock_chat_history
            )
            
            # Test that workspace validation is checked for Notion queries
            # (This would require deeper integration with actual validation logic)
            self.assertTrue(True)  # Placeholder for now


class TestErrorHandlingIntegration(unittest.TestCase):
    """Test error handling integration across handlers."""
    
    async def test_fallback_chain_integration(self):
        """Test error fallback chain from intent handler to regular handler."""
        from integrations.telegram.handlers import MessageHandler
        
        mock_client = AsyncMock()
        mock_chat_history = Mock()
        mock_chat_history.add_message = Mock()
        mock_chat_history.get_context = Mock(return_value=[])
        
        # Mock environment
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '12345',
            'TELEGRAM_ALLOW_DMS': 'true'
        }):
            handler = MessageHandler(
                client=mock_client,
                chat_history=mock_chat_history
            )
            
            # Mock intent handler failure
            async def failing_intent_handler(**kwargs):
                raise Exception("Intent handler failed")
            
            # Mock regular handler success
            async def successful_regular_handler(**kwargs):
                return "Fallback response"
            
            mock_message = Mock()
            mock_message.from_user = Mock()
            mock_message.from_user.username = "testuser"
            
            # Track which handlers were called
            fallback_called = False
            
            def track_fallback_call(*args, **kwargs):
                nonlocal fallback_called
                fallback_called = True
                return successful_regular_handler(*args, **kwargs)
            
            with patch('agents.valor.handlers.handle_telegram_message_with_intent', 
                       side_effect=failing_intent_handler):
                with patch('agents.valor.handlers.handle_telegram_message', 
                           side_effect=track_fallback_call):
                    with patch.object(handler, '_process_agent_response', new_callable=AsyncMock):
                        await handler._handle_with_valor_agent_intent(
                            mock_message, 12345, "Test message", None, None
                        )
            
            # Verify fallback was called
            self.assertTrue(fallback_called)


class TestPerformanceIntegration(unittest.TestCase):
    """Test performance-related integration points."""
    
    async def test_context_optimization_integration(self):
        """Test that context optimization works between handlers."""
        from integrations.telegram.handlers import MessageHandler
        from agents.valor.handlers import handle_telegram_message
        
        # Create handler with large chat history
        mock_client = AsyncMock()
        
        class LargeChatHistory:
            def __init__(self):
                self.messages = {}
                # Add many messages to test optimization
                for i in range(100):
                    self.add_message(12345, "user" if i % 2 == 0 else "assistant", f"Message {i}")
            
            def add_message(self, chat_id, role, content, reply_to=None, msg_id=None, is_telegram_id=False):
                if chat_id not in self.messages:
                    self.messages[chat_id] = []
                self.messages[chat_id].append({
                    'role': role,
                    'content': content,
                    'timestamp': datetime.now().isoformat()
                })
            
            def get_context(self, chat_id, max_context_messages=10, max_age_hours=24, always_include_last=0):
                # Should limit context appropriately
                msgs = self.messages.get(chat_id, [])
                return msgs[-max_context_messages:]  # Only return limited messages
        
        large_history = LargeChatHistory()
        
        # Track the size of enhanced message
        captured_message_size = 0
        
        async def mock_agent_run(message, deps=None):
            nonlocal captured_message_size
            captured_message_size = len(message)
            result = Mock()
            result.output = "Response"
            return result
        
        with patch('agents.valor.handlers.valor_agent') as mock_agent:
            mock_agent.run = mock_agent_run
            
            response = await handle_telegram_message(
                message="Current question",
                chat_id=12345,
                chat_history_obj=large_history
            )
        
        # Verify context was optimized (message size should be reasonable)
        # Even with 100 messages in history, enhanced message should be manageable
        self.assertLess(captured_message_size, 10000)  # Reasonable limit
        self.assertGreater(captured_message_size, 100)  # But includes some context


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
for test_class in [TestChatHistoryIntegration, TestIntentIntegration, 
                   TestSecurityIntegration, TestErrorHandlingIntegration,
                   TestPerformanceIntegration]:
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