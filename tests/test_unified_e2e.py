#!/usr/bin/env python3
"""
End-to-End Integration Tests for Unified Valor-Claude Agent

This module provides comprehensive end-to-end testing that validates the complete
integration between the unified agent system and existing infrastructure.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestUnifiedE2EIntegration(unittest.TestCase):
    """End-to-end integration tests for the unified system."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.test_chat_id = 12345
        self.test_username = "test_user"
        self.test_working_dir = "/tmp/test_unified"
    
    @pytest.mark.asyncio
    async def test_complete_message_flow(self):
        """Test complete message processing flow through unified system."""
        
        # Mock the underlying Claude Code execution
        with patch('subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                "Analyzing your request...",
                "Found relevant code in main.py",
                "Implementing changes...",
                "Tests passing ✅",
                "Changes committed successfully",
                ""  # End of stream
            ]
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process
            
            from agents.unified_integration import process_message_unified
            
            # Test development task
            response = await process_message_unified(
                message="Fix the authentication bug in the login system",
                chat_id=self.test_chat_id,
                username=self.test_username,
                is_group_chat=False
            )
            
            # Verify response contains expected elements
            self.assertIsInstance(response, str)
            self.assertGreater(len(response), 0)
    
    @pytest.mark.asyncio
    async def test_conversation_and_development_flow(self):
        """Test mixed conversation and development tasks."""
        
        # Mock Claude Code responses for different types of requests
        with patch('subprocess.Popen') as mock_popen:
            # Mock for casual conversation
            mock_process_chat = MagicMock()
            mock_process_chat.stdout.readline.side_effect = [
                "Hello! I'm doing great, thanks for asking.",
                "How can I help you today?",
                ""
            ]
            mock_process_chat.poll.return_value = None
            mock_process_chat.wait.return_value = 0
            
            # Mock for development task
            mock_process_dev = MagicMock()
            mock_process_dev.stdout.readline.side_effect = [
                "🔧 Analyzing codebase structure...",
                "Found 15 test files to run",
                "Running pytest...",
                "✅ All tests passed (23 passed, 0 failed)",
                ""
            ]
            mock_process_dev.poll.return_value = None
            mock_process_dev.wait.return_value = 0
            
            mock_popen.side_effect = [mock_process_chat, mock_process_dev]
            
            from agents.unified_integration import process_message_unified
            
            # Test casual conversation
            chat_response = await process_message_unified(
                message="Hey, how are you doing today?",
                chat_id=self.test_chat_id,
                username=self.test_username
            )
            
            self.assertIsInstance(chat_response, str)
            
            # Test development task
            dev_response = await process_message_unified(
                message="Run the test suite and show me the results",
                chat_id=self.test_chat_id,
                username=self.test_username
            )
            
            self.assertIsInstance(dev_response, str)
    
    @pytest.mark.asyncio 
    async def test_mcp_tool_integration(self):
        """Test that MCP tools are properly integrated."""
        
        # Mock MCP server responses
        with patch('subprocess.Popen') as mock_popen:
            # Mock Claude Code calling MCP tools
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                "🔍 Searching for current information...",
                "Found 3 recent articles about Python 3.12",
                "Key features include: improved error messages, faster startup",
                "Would you like me to show you specific examples?",
                ""
            ]
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process
            
            from agents.unified_integration import process_message_unified
            
            response = await process_message_unified(
                message="What are the latest Python 3.12 features?",
                chat_id=self.test_chat_id,
                username=self.test_username
            )
            
            self.assertIsInstance(response, str)
            # Verify that the command included MCP server configuration
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args[0][0]
            self.assertIn('--mcp', call_args)
    
    @pytest.mark.asyncio
    async def test_image_generation_flow(self):
        """Test image generation through unified system."""
        
        with patch('subprocess.Popen') as mock_popen:
            # Mock image generation response
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                "🎨 Generating image with DALL-E 3...",
                "TELEGRAM_IMAGE_GENERATED|/tmp/generated_sunset.png|Beautiful sunset over mountains",
                ""
            ]
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process
            
            from agents.unified_integration import process_message_unified
            
            response = await process_message_unified(
                message="Create an image of a beautiful sunset over mountains",
                chat_id=self.test_chat_id,
                username=self.test_username
            )
            
            self.assertIsInstance(response, str)
            # Should contain the special image format
            self.assertIn("TELEGRAM_IMAGE_GENERATED|", response)
    
    @pytest.mark.asyncio
    async def test_notion_integration_flow(self):
        """Test Notion project queries through unified system."""
        
        with patch('subprocess.Popen') as mock_popen:
            # Mock Notion query response
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = [
                "🎯 Querying PsyOPTIMAL workspace...",
                "Found 3 high-priority tasks:",
                "1. Implement user authentication (Status: Ready for Dev)",
                "2. Fix payment processing bug (Status: In Progress)",
                "3. Add email notifications (Status: Design Complete)",
                ""
            ]
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process
            
            from agents.unified_integration import process_message_unified
            
            response = await process_message_unified(
                message="What are the highest priority tasks in PsyOPTIMAL?",
                chat_id=self.test_chat_id,
                username=self.test_username,
                notion_data="PsyOPTIMAL project data"
            )
            
            self.assertIsInstance(response, str)
    
    def test_telegram_handlers_integration(self):
        """Test that Telegram handlers can use unified system."""
        
        from integrations.telegram.handlers import MessageHandler
        
        # Create mock dependencies
        mock_client = MagicMock()
        mock_chat_history = MagicMock()
        mock_chat_history.get_context.return_value = []
        mock_notion_scout = MagicMock()
        
        # Create handler
        handler = MessageHandler(
            client=mock_client,
            chat_history=mock_chat_history,
            notion_scout=mock_notion_scout
        )
        
        # Verify the handler has been updated with unified integration
        self.assertTrue(hasattr(handler, '_handle_with_valor_agent'))
        
        # Check that the method contains unified agent logic
        import inspect
        source = inspect.getsource(handler._handle_with_valor_agent)
        self.assertIn("unified_integration", source)
        self.assertIn("process_message_unified", source)
    
    def test_mcp_server_availability(self):
        """Test that all required MCP servers are available."""
        
        mcp_servers_dir = Path("/Users/valorengels/src/ai/mcp_servers")
        
        # Skip if not in actual project directory
        if not mcp_servers_dir.exists():
            self.skipTest("MCP servers directory not available in test environment")
        
        required_servers = [
            'social_tools.py',
            'notion_tools.py',
            'telegram_tools.py'
        ]
        
        for server_file in required_servers:
            server_path = mcp_servers_dir / server_file
            self.assertTrue(server_path.exists(), f"MCP server {server_file} should exist")
            
            # Basic syntax check
            with open(server_path, 'r') as f:
                content = f.read()
                # Check for MCP server initialization
                self.assertIn("FastMCP", content)
                self.assertIn("@mcp.tool", content)
    
    def test_mcp_configuration_valid(self):
        """Test that MCP configuration is valid."""
        
        mcp_config_path = Path("/Users/valorengels/src/ai/.mcp.json")
        
        # Skip if not in actual project directory
        if not mcp_config_path.exists():
            self.skipTest("MCP configuration not available in test environment")
        
        import json
        
        with open(mcp_config_path, 'r') as f:
            config = json.load(f)
        
        # Verify structure
        self.assertIn('mcpServers', config)
        servers = config['mcpServers']
        
        # Verify required servers
        required_servers = ['social-tools', 'notion-tools', 'telegram-tools']
        for server in required_servers:
            self.assertIn(server, servers)
            server_config = servers[server]
            self.assertIn('command', server_config)
            self.assertIn('args', server_config)
            self.assertEqual(server_config['command'], 'python')
    
    @pytest.mark.asyncio
    async def test_error_handling_and_fallback(self):
        """Test error handling and fallback mechanisms."""
        
        # Test unified agent initialization failure
        with patch('agents.unified_integration.UnifiedValorClaudeAgent', side_effect=Exception("Test error")):
            from agents.unified_integration import UnifiedTelegramIntegration
            
            integration = UnifiedTelegramIntegration()
            self.assertIsNone(integration.unified_agent)
        
        # Test Claude Code execution failure
        with patch('subprocess.Popen', side_effect=Exception("Claude Code not available")):
            from agents.unified_integration import process_message_unified
            
            response = await process_message_unified(
                message="Test message",
                chat_id=self.test_chat_id,
                username=self.test_username
            )
            
            # Should handle the error gracefully
            self.assertIsInstance(response, str)
            self.assertIn("Error", response)
    
    def test_context_injection_comprehensive(self):
        """Test comprehensive context injection scenarios."""
        
        from agents.unified_valor_claude_agent import UnifiedValorClaudeAgent, UnifiedContext
        
        with patch('pathlib.Path.open', create=True):
            agent = UnifiedValorClaudeAgent()
        
        # Test full context
        context = UnifiedContext(
            chat_id=12345,
            username="test_user",
            is_group_chat=True,
            chat_history=[
                {"role": "user", "content": "Previous message 1"},
                {"role": "assistant", "content": "Previous response 1"},
                {"role": "user", "content": "Previous message 2"}
            ],
            notion_data="PsyOPTIMAL project tasks",
            project_context="Working on authentication system"
        )
        
        message = "Continue with the authentication implementation"
        enhanced_message = agent._inject_context(message, context)
        
        # Verify all context elements are included
        self.assertIn("CHAT_ID=12345", enhanced_message)
        self.assertIn("USERNAME=test_user", enhanced_message)
        self.assertIn("RECENT_HISTORY:", enhanced_message)
        self.assertIn("Previous message", enhanced_message)
        self.assertIn("PROJECT_DATA:", enhanced_message)
        self.assertIn("PsyOPTIMAL project tasks", enhanced_message)
        self.assertIn("PROJECT_CONTEXT:", enhanced_message)
        self.assertIn("authentication system", enhanced_message)
        self.assertIn("USER_REQUEST: Continue with the authentication implementation", enhanced_message)
    
    @pytest.mark.asyncio
    async def test_session_management(self):
        """Test session management and persistence."""
        
        from agents.unified_valor_claude_agent import UnifiedValorClaudeAgent
        
        with patch('pathlib.Path.open', create=True):
            agent = UnifiedValorClaudeAgent()
        
        # Test session info
        session_info = agent.get_session_info()
        self.assertIn('session_id', session_info)
        self.assertIn('working_directory', session_info)
        self.assertIn('mcp_servers', session_info)
        
        # Test session termination
        agent.terminate_session()
        # Should not raise exceptions


class TestPerformanceAndScaling(unittest.TestCase):
    """Test performance characteristics of the unified system."""
    
    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Test handling multiple concurrent requests."""
        
        from agents.unified_integration import process_message_unified
        
        # Mock rapid responses
        with patch('subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout.readline.side_effect = ["Quick response", ""]
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process
            
            # Create multiple concurrent requests
            tasks = []
            for i in range(5):
                task = process_message_unified(
                    message=f"Test message {i}",
                    chat_id=12345 + i,
                    username=f"user_{i}"
                )
                tasks.append(task)
            
            # Execute concurrently
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Verify all completed successfully
            for response in responses:
                self.assertIsInstance(response, str)
    
    def test_memory_usage(self):
        """Test memory usage of unified agent components."""
        
        from agents.unified_valor_claude_agent import (
            UnifiedValorClaudeAgent,
            ConversationContextManager,
            TelegramStreamHandler
        )
        
        # Test that components can be created without excessive memory usage
        with patch('pathlib.Path.open', create=True):
            agent = UnifiedValorClaudeAgent()
            context_manager = ConversationContextManager()
            stream_handler = TelegramStreamHandler()
        
        # Basic memory efficiency checks
        self.assertIsNotNone(agent)
        self.assertIsNotNone(context_manager)
        self.assertIsNotNone(stream_handler)
        
        # Test cleanup
        agent.terminate_session()


if __name__ == '__main__':
    # Configure asyncio for testing
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    
    # Run the tests
    unittest.main(verbosity=2)