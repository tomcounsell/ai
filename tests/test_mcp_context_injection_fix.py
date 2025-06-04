#!/usr/bin/env python3
"""
Test MCP Context Injection Fix

Tests the fix for the context injection bug where MCP tools expected
chat_id and other context but couldn't receive it when called from Claude Code.
"""

import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp_servers.context_manager import (
    MCPContextManager,
    get_context_manager,
    set_mcp_context,
    get_mcp_context,
    inject_context_for_tool
)


class TestMCPContextManager:
    """Test the MCP Context Manager functionality."""

    def setUp(self):
        """Set up test environment."""
        # Create a temporary context file for testing
        self.temp_dir = tempfile.mkdtemp()
        self.original_cache_file = None
        
    def tearDown(self):
        """Clean up test environment."""
        # Reset context manager state
        manager = get_context_manager()
        manager.clear_context()
        
    def test_singleton_behavior(self):
        """Test that context manager is a singleton."""
        print("🧪 Testing singleton behavior")
        
        manager1 = MCPContextManager()
        manager2 = MCPContextManager()
        manager3 = get_context_manager()
        
        assert manager1 is manager2
        assert manager2 is manager3
        print("   ✅ Context manager is properly singleton")

    def test_set_and_get_context(self):
        """Test basic context setting and retrieval."""
        print("🧪 Testing context set and get")
        
        manager = get_context_manager()
        manager.clear_context()
        
        # Set context
        manager.set_context(
            chat_id="12345",
            username="testuser",
            workspace="test-workspace"
        )
        
        # Get individual values
        assert manager.get_context('chat_id') == "12345"
        assert manager.get_context('username') == "testuser"
        assert manager.get_context('workspace') == "test-workspace"
        
        # Get all context
        all_context = manager.get_context()
        assert all_context['chat_id'] == "12345"
        assert all_context['username'] == "testuser"
        assert all_context['workspace'] == "test-workspace"
        
        print("   ✅ Context setting and retrieval works")

    def test_convenience_functions(self):
        """Test convenience functions for context management."""
        print("🧪 Testing convenience functions")
        
        # Clear any existing context
        manager = get_context_manager()
        manager.clear_context()
        
        # Test convenience set function
        set_mcp_context(chat_id="67890", username="convenience_user")
        
        # Test convenience get function
        assert get_mcp_context('chat_id') == "67890"
        assert get_mcp_context('username') == "convenience_user"
        
        print("   ✅ Convenience functions work correctly")

    def test_inject_context_for_tool(self):
        """Test the inject_context_for_tool function."""
        print("🧪 Testing inject_context_for_tool")
        
        manager = get_context_manager()
        manager.clear_context()
        
        # Set stored context
        manager.set_context(chat_id="stored_chat", username="stored_user")
        
        # Test with no parameters (should use stored)
        chat_id, username = inject_context_for_tool("", "")
        assert chat_id == "stored_chat"
        assert username == "stored_user"
        
        # Test with provided parameters (should use provided)
        chat_id, username = inject_context_for_tool("provided_chat", "provided_user")
        assert chat_id == "provided_chat"
        assert username == "provided_user"
        
        # Test with mixed parameters
        chat_id, username = inject_context_for_tool("provided_chat", "")
        assert chat_id == "provided_chat"
        assert username == "stored_user"
        
        print("   ✅ Context injection for tools works correctly")

    def test_environment_fallback(self):
        """Test environment variable fallback."""
        print("🧪 Testing environment variable fallback")
        
        manager = get_context_manager()
        manager.clear_context()
        
        with patch.dict(os.environ, {'CURRENT_CHAT_ID': 'env_chat', 'CURRENT_USERNAME': 'env_user'}):
            chat_id = manager.get_chat_id()
            username = manager.get_username()
            
            assert chat_id == "env_chat"
            assert username == "env_user"
            
            # Should now be stored in context
            assert manager.get_context('chat_id') == "env_chat"
            assert manager.get_context('username') == "env_user"
        
        print("   ✅ Environment fallback works correctly")


class TestMCPToolsWithContext:
    """Test MCP tools with context injection."""

    def setUp(self):
        """Set up test environment."""
        manager = get_context_manager()
        manager.clear_context()

    def test_telegram_tools_context_injection(self):
        """Test that Telegram tools use context injection."""
        print("🧪 Testing Telegram tools context injection")
        
        # Set context (use numeric chat_id for Telegram)
        set_mcp_context(chat_id="12345", username="telegram_user")
        
        # Import and test telegram tools
        from mcp_servers.telegram_tools import search_conversation_history
        
        # Mock the chat history manager to avoid database dependencies
        with patch('integrations.telegram.chat_history.ChatHistoryManager') as mock_history:
            mock_instance = MagicMock()
            mock_history.return_value = mock_instance
            
            # Mock the search function to return a test result
            with patch('mcp_servers.telegram_tools.search_telegram_history') as mock_search:
                mock_search.return_value = "Found 2 messages about Python"
                
                # Call without providing chat_id (should use injected context)
                result = search_conversation_history("Python")
                
                # Should not get "No chat ID" error
                print(f"   📝 Search result: {result}")
                assert "❌ No chat ID" not in result
                # The result should either contain the query or the mocked response
                assert "Python" in result or "Found" in result or "📂" in result
                
                # Verify the injected chat_id was used
                mock_search.assert_called_once()
                call_args = mock_search.call_args
                assert call_args[0][2] == int("12345")  # chat_id_int parameter
                
        print("   ✅ Telegram tools use context injection correctly")

    def test_social_tools_context_injection(self):
        """Test that Social tools use context injection."""
        print("🧪 Testing Social tools context injection")
        
        # Set context (use numeric chat_id)
        set_mcp_context(chat_id="67890", username="social_user")
        
        # Import and test social tools
        from mcp_servers.social_tools import create_image, save_link
        
        # Test create_image with context injection
        with patch('mcp_servers.social_tools.generate_image') as mock_generate:
            mock_generate.return_value = "/tmp/test_image.png"
            
            # Call without providing chat_id (should use injected context)
            result = create_image("test prompt")
            
            # Should include chat_id in response for Telegram formatting
            assert "TELEGRAM_IMAGE_GENERATED" in result
            assert "67890" in result
            
        # Test save_link with context injection
        with patch('mcp_servers.social_tools.store_link_with_analysis') as mock_store:
            mock_store.return_value = True
            
            # Call without providing chat_id or username (should use injected context)
            result = save_link("https://example.com")
            
            # Should not get an error
            assert "❌" not in result
            assert "Successfully stored" in result
            
            # Verify the injected context was used
            mock_store.assert_called_once()
            call_args = mock_store.call_args
            # chat_id_int should be converted from string
            assert call_args[0][1] == int("67890")
            assert call_args[0][3] == "social_user"  # username parameter
            
        print("   ✅ Social tools use context injection correctly")


class TestEndToEndContextFlow:
    """End-to-end tests for the complete context flow."""

    def test_complete_context_flow(self):
        """Test the complete context flow from setting to tool usage."""
        print("🧪 End-to-end context flow test")
        
        # Clear any existing context
        manager = get_context_manager()
        manager.clear_context()
        
        # Step 1: Set context (simulating what would happen when a message comes in)
        set_mcp_context(
            chat_id="11111",
            username="e2e_user",
            workspace="e2e_workspace"
        )
        print("   📝 Step 1: Context set")
        
        # Step 2: Verify context is stored
        assert get_mcp_context('chat_id') == "11111"
        assert get_mcp_context('username') == "e2e_user"
        assert get_mcp_context('workspace') == "e2e_workspace"
        print("   ✅ Step 2: Context stored correctly")
        
        # Step 3: Test tool usage without explicit parameters
        from mcp_servers.telegram_tools import get_recent_history
        
        with patch('integrations.telegram.chat_history.ChatHistoryManager') as mock_history:
            mock_instance = MagicMock()
            mock_instance.get_context.return_value = [
                {'timestamp': '2024-01-01', 'role': 'user', 'content': 'Test message'}
            ]
            mock_history.return_value = mock_instance
            
            # Call tool without providing chat_id
            result = get_recent_history()
            
            # Should work without errors
            assert "❌ No chat ID" not in result
            assert "Recent Messages" in result
            assert "Test message" in result
            
        print("   ✅ Step 3: Tool works with injected context")
        
        # Step 4: Test that explicit parameters override context
        with patch('integrations.telegram.chat_history.ChatHistoryManager') as mock_history:
            mock_instance = MagicMock()
            mock_instance.get_context.return_value = []
            mock_history.return_value = mock_instance
            
            # Call with explicit chat_id
            result = get_recent_history(chat_id="22222")
            
            # Should use explicit chat_id, not stored context
            mock_instance.get_context.assert_called_once()
            call_args = mock_instance.get_context.call_args
            assert call_args[1]['chat_id'] == int("22222")
            
        print("   ✅ Step 4: Explicit parameters override context")
        
        print("   🎯 End-to-end context flow works correctly!")

    def test_context_persistence(self):
        """Test that context persists across different tool calls."""
        print("🧪 Testing context persistence")
        
        # Set context once
        set_mcp_context(chat_id="33333", username="persist_user")
        
        # Call multiple different tools without providing context
        from mcp_servers.social_tools import create_image
        from mcp_servers.telegram_tools import search_conversation_history
        
        # Mock dependencies
        with patch('mcp_servers.social_tools.generate_image') as mock_generate:
            mock_generate.return_value = "/tmp/persist_image.png"
            
            result1 = create_image("test image")
            assert "33333" in result1
            
        with patch('integrations.telegram.chat_history.ChatHistoryManager') as mock_history:
            mock_instance = MagicMock()
            mock_history.return_value = mock_instance
            
            with patch('mcp_servers.telegram_tools.search_telegram_history') as mock_search:
                mock_search.return_value = "Found messages"
                
                result2 = search_conversation_history("test query")
                assert "❌ No chat ID" not in result2
                
                # Verify context was used
                mock_search.assert_called_once()
                call_args = mock_search.call_args
                assert call_args[0][2] == int("33333")
        
        print("   ✅ Context persists across different tool calls")


def run_all_tests():
    """Run all context injection tests."""
    print("🔬 MCP Context Injection Fix Test Suite")
    print("=" * 60)
    
    # Test context manager
    test_manager = TestMCPContextManager()
    test_manager.setUp()
    test_manager.test_singleton_behavior()
    test_manager.test_set_and_get_context()
    test_manager.test_convenience_functions()
    test_manager.test_inject_context_for_tool()
    test_manager.test_environment_fallback()
    test_manager.tearDown()
    
    # Test MCP tools with context
    test_tools = TestMCPToolsWithContext()
    test_tools.setUp()
    test_tools.test_telegram_tools_context_injection()
    test_tools.test_social_tools_context_injection()
    
    # Test end-to-end flow
    test_e2e = TestEndToEndContextFlow()
    test_e2e.test_complete_context_flow()
    test_e2e.test_context_persistence()
    
    print("\n🎉 All Context Injection Fix Tests Passed!")
    print("✅ Context injection bugs have been properly fixed")
    print("✅ MCP tools now work without requiring explicit context parameters")
    print("✅ Context persists and can be injected automatically")


if __name__ == "__main__":
    run_all_tests()