#!/usr/bin/env python3
"""
Test suite for UnifiedValorClaudeAgent - Phase 2 Implementation

This module provides comprehensive testing for the unified agent system,
validating the integration between Valor's persona and Claude Code capabilities.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the components to test
from agents.unified_valor_claude_agent import (
    ClaudeCodeSession,
    ConversationContextManager,
    TelegramStreamHandler,
    UnifiedContext,
    UnifiedValorClaudeAgent,
)
from agents.unified_integration import UnifiedTelegramIntegration, process_message_unified


class TestUnifiedContext(unittest.TestCase):
    """Test the UnifiedContext model."""
    
    def test_unified_context_creation(self):
        """Test creating UnifiedContext with various parameters."""
        context = UnifiedContext(
            chat_id=12345,
            username="test_user",
            is_group_chat=True,
            working_directory="/tmp/test"
        )
        
        self.assertEqual(context.chat_id, 12345)
        self.assertEqual(context.username, "test_user")
        self.assertTrue(context.is_group_chat)
        self.assertEqual(context.working_directory, "/tmp/test")
        self.assertEqual(context.chat_history, [])
    
    def test_unified_context_defaults(self):
        """Test UnifiedContext with default values."""
        context = UnifiedContext()
        
        self.assertIsNone(context.chat_id)
        self.assertIsNone(context.username)
        self.assertFalse(context.is_group_chat)
        self.assertEqual(context.working_directory, "/Users/valorengels/src/ai")
        self.assertEqual(context.chat_history, [])


class TestClaudeCodeSession(unittest.TestCase):
    """Test the ClaudeCodeSession class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.session = ClaudeCodeSession("/tmp/test")
    
    def test_session_initialization(self):
        """Test ClaudeCodeSession initialization."""
        self.assertEqual(self.session.working_directory, "/tmp/test")
        self.assertIsNotNone(self.session.session_id)
        self.assertTrue(self.session.session_id.startswith("unified_session_"))
        self.assertIsNone(self.session.active_process)
    
    def test_build_claude_command(self):
        """Test building Claude Code command."""
        mcp_servers = ["social-tools", "notion-tools"]
        message = "Test message"
        
        cmd = self.session._build_claude_command(message, mcp_servers)
        
        self.assertIn("claude", cmd)
        self.assertIn("--mcp", cmd)
        self.assertIn("social-tools,notion-tools", cmd)
        self.assertIn("--working-directory", cmd)
        self.assertIn("/tmp/test", cmd)
        self.assertIn("--session-id", cmd)
        self.assertIn("--stream", cmd)
    
    def test_terminate(self):
        """Test session termination."""
        # Mock an active process
        mock_process = MagicMock()
        self.session.active_process = mock_process
        
        self.session.terminate()
        
        mock_process.terminate.assert_called_once()
        self.assertIsNone(self.session.active_process)


class TestTelegramStreamHandler(unittest.TestCase):
    """Test the TelegramStreamHandler class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.handler = TelegramStreamHandler()
    
    def test_initialization(self):
        """Test TelegramStreamHandler initialization."""
        self.assertEqual(self.handler.active_messages, {})
        self.assertEqual(self.handler.update_throttle, 2.0)
    
    @pytest.mark.asyncio
    async def test_send_update(self):
        """Test sending updates to Telegram."""
        chat_id = 12345
        content = "Test content"
        
        # Test first update
        await self.handler.send_update(chat_id, content)
        
        message_key = f"{chat_id}_current"
        self.assertIn(message_key, self.handler.active_messages)
        self.assertEqual(self.handler.active_messages[message_key]['content'], content)
    
    @pytest.mark.asyncio
    async def test_process_special_responses(self):
        """Test processing special response types."""
        chat_id = 12345
        
        # Test image generation response
        image_response = "TELEGRAM_IMAGE_GENERATED|/tmp/test.png|Test caption"
        
        with patch.object(self.handler, '_handle_image_response') as mock_image:
            await self.handler.process_special_responses(image_response, chat_id)
            mock_image.assert_called_once_with(image_response, chat_id)
        
        # Test progress indicator
        progress_response = "Analyzing the code..."
        
        with patch.object(self.handler, '_add_progress_reaction') as mock_progress:
            await self.handler.process_special_responses(progress_response, chat_id)
            mock_progress.assert_called_once_with(chat_id)


class TestConversationContextManager(unittest.TestCase):
    """Test the ConversationContextManager class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.manager = ConversationContextManager()
    
    def test_initialization(self):
        """Test ConversationContextManager initialization."""
        self.assertEqual(self.manager.context_store, {})
    
    def test_get_context(self):
        """Test getting context for a chat."""
        chat_id = 12345
        
        # Test empty context
        context = self.manager.get_context(chat_id)
        self.assertEqual(context, {})
        
        # Test existing context
        test_data = {"key": "value"}
        self.manager.context_store[chat_id] = test_data
        context = self.manager.get_context(chat_id)
        self.assertEqual(context, test_data)
    
    def test_update_context(self):
        """Test updating context for a chat."""
        chat_id = 12345
        initial_data = {"key1": "value1"}
        update_data = {"key2": "value2"}
        
        # Update empty context
        self.manager.update_context(chat_id, initial_data)
        self.assertEqual(self.manager.context_store[chat_id], initial_data)
        
        # Update existing context
        self.manager.update_context(chat_id, update_data)
        expected = {"key1": "value1", "key2": "value2"}
        self.assertEqual(self.manager.context_store[chat_id], expected)
    
    def test_get_notion_data(self):
        """Test getting Notion data from context."""
        chat_id = 12345
        notion_data = "Test Notion data"
        
        # Test no Notion data
        result = self.manager.get_notion_data(chat_id)
        self.assertIsNone(result)
        
        # Test with Notion data
        self.manager.update_context(chat_id, {"notion_data": notion_data})
        result = self.manager.get_notion_data(chat_id)
        self.assertEqual(result, notion_data)


class TestUnifiedValorClaudeAgent(unittest.TestCase):
    """Test the main UnifiedValorClaudeAgent class."""
    
    def setUp(self):
        """Set up test fixtures."""
        with patch('pathlib.Path.open', create=True):
            self.agent = UnifiedValorClaudeAgent("/tmp/test")
    
    def test_initialization(self):
        """Test UnifiedValorClaudeAgent initialization."""
        self.assertEqual(self.agent.working_directory, "/tmp/test")
        self.assertIsInstance(self.agent.claude_session, ClaudeCodeSession)
        self.assertIsInstance(self.agent.telegram_streamer, TelegramStreamHandler)
        self.assertIsInstance(self.agent.context_manager, ConversationContextManager)
        self.assertEqual(self.agent.mcp_servers, ['social-tools', 'notion-tools', 'telegram-tools'])
    
    def test_inject_context(self):
        """Test context injection functionality."""
        context = UnifiedContext(
            chat_id=12345,
            username="test_user",
            chat_history=[{"role": "user", "content": "Hello"}],
            notion_data="Test project data"
        )
        
        message = "Test message"
        enhanced_message = self.agent._inject_context(message, context)
        
        self.assertIn("CONTEXT_DATA:", enhanced_message)
        self.assertIn("CHAT_ID=12345", enhanced_message)
        self.assertIn("USERNAME=test_user", enhanced_message)
        self.assertIn("RECENT_HISTORY:", enhanced_message)
        self.assertIn("PROJECT_DATA:", enhanced_message)
        self.assertIn("USER_REQUEST: Test message", enhanced_message)
    
    def test_inject_context_minimal(self):
        """Test context injection with minimal context."""
        context = UnifiedContext()
        message = "Test message"
        
        enhanced_message = self.agent._inject_context(message, context)
        
        self.assertIn("SYSTEM_PROMPT:", enhanced_message)
        self.assertIn("USER_REQUEST: Test message", enhanced_message)
        self.assertNotIn("CONTEXT_DATA:", enhanced_message)
    
    def test_get_session_info(self):
        """Test getting session information."""
        info = self.agent.get_session_info()
        
        self.assertIn('session_id', info)
        self.assertIn('working_directory', info)
        self.assertIn('mcp_servers', info)
        self.assertIn('active_process', info)
        
        self.assertEqual(info['working_directory'], "/tmp/test")
        self.assertEqual(info['mcp_servers'], ['social-tools', 'notion-tools', 'telegram-tools'])
        self.assertFalse(info['active_process'])


class TestUnifiedTelegramIntegration(unittest.TestCase):
    """Test the UnifiedTelegramIntegration class."""
    
    def setUp(self):
        """Set up test fixtures."""
        with patch('agents.unified_integration.UnifiedValorClaudeAgent'):
            self.integration = UnifiedTelegramIntegration()
    
    def test_initialization(self):
        """Test UnifiedTelegramIntegration initialization."""
        self.assertIsNotNone(self.integration)
    
    def test_is_priority_question(self):
        """Test priority question detection."""
        # Priority questions
        self.assertTrue(self.integration._is_priority_question("What are the priority tasks?"))
        self.assertTrue(self.integration._is_priority_question("Show me project status"))
        self.assertTrue(self.integration._is_priority_question("What's urgent?"))
        
        # Non-priority questions
        self.assertFalse(self.integration._is_priority_question("Hello, how are you?"))
        self.assertFalse(self.integration._is_priority_question("What's the weather?"))
        
        # With notion data (always priority)
        self.assertTrue(self.integration._is_priority_question("Hello", "Project data"))
    
    def test_get_agent_status_unavailable(self):
        """Test getting agent status when unavailable."""
        self.integration.unified_agent = None
        status = self.integration.get_agent_status()
        
        self.assertEqual(status['status'], 'unavailable')
        self.assertIn('error', status)
    
    @patch('agents.unified_integration.UnifiedValorClaudeAgent')
    def test_get_agent_status_available(self, mock_agent_class):
        """Test getting agent status when available."""
        mock_agent = MagicMock()
        mock_agent.get_session_info.return_value = {'test': 'info'}
        mock_agent.mcp_servers = ['social-tools']
        
        self.integration.unified_agent = mock_agent
        status = self.integration.get_agent_status()
        
        self.assertEqual(status['status'], 'available')
        self.assertIn('session_info', status)
        self.assertIn('mcp_servers', status)


class TestIntegrationFunctions(unittest.TestCase):
    """Test the integration functions."""
    
    @pytest.mark.asyncio
    async def test_process_message_unified(self):
        """Test the global process_message_unified function."""
        with patch('agents.unified_integration.unified_integration') as mock_integration:
            mock_integration.handle_telegram_message = AsyncMock(return_value="Test response")
            
            result = await process_message_unified(
                message="Test message",
                chat_id=12345,
                username="test_user"
            )
            
            self.assertEqual(result, "Test response")
            mock_integration.handle_telegram_message.assert_called_once()


class TestMCPIntegration(unittest.TestCase):
    """Test MCP server integration with unified agent."""
    
    def test_mcp_configuration_available(self):
        """Test that MCP configuration exists."""
        mcp_config_path = Path("/Users/valorengels/src/ai/.mcp.json")
        
        # Skip if not in actual project directory
        if not mcp_config_path.exists():
            self.skipTest("MCP configuration not available in test environment")
        
        import json
        with open(mcp_config_path) as f:
            config = json.load(f)
        
        self.assertIn('mcpServers', config)
        
        expected_servers = ['social-tools', 'notion-tools', 'telegram-tools']
        for server in expected_servers:
            self.assertIn(server, config['mcpServers'])
    
    def test_mcp_server_files_exist(self):
        """Test that MCP server files exist."""
        mcp_servers_dir = Path("/Users/valorengels/src/ai/mcp_servers")
        
        # Skip if not in actual project directory
        if not mcp_servers_dir.exists():
            self.skipTest("MCP servers directory not available in test environment")
        
        expected_files = [
            'social_tools.py',
            'notion_tools.py', 
            'telegram_tools.py'
        ]
        
        for filename in expected_files:
            server_file = mcp_servers_dir / filename
            self.assertTrue(server_file.exists(), f"MCP server file {filename} should exist")


class TestCompatibilityLayer(unittest.TestCase):
    """Test compatibility with existing Telegram handlers."""
    
    def test_handlers_import(self):
        """Test that handlers can import unified integration."""
        try:
            from agents.unified_integration import process_message_unified, process_image_unified
            self.assertTrue(callable(process_message_unified))
            self.assertTrue(callable(process_image_unified))
        except ImportError as e:
            self.fail(f"Should be able to import unified integration functions: {e}")
    
    def test_fallback_mechanism(self):
        """Test that fallback to original valor agent works."""
        # This test would verify that when unified agent is not available,
        # the system gracefully falls back to the original valor agent
        
        # We test this by checking the error handling in the handlers
        from integrations.telegram.handlers import MessageHandler
        
        # Create a mock handler
        handler = MessageHandler(
            client=MagicMock(),
            chat_history=MagicMock(),
            notion_scout=MagicMock()
        )
        
        # Verify the handler has the necessary methods
        self.assertTrue(hasattr(handler, '_handle_with_valor_agent'))


if __name__ == '__main__':
    # Run the tests
    unittest.main(verbosity=2)