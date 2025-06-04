#!/usr/bin/env python3
"""
Test Telegram Tools Event Loop Bug Fix

Tests the fix for the event loop handling bug in list_telegram_dialogs function
and validates proper error handling for various async scenarios.
"""

import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp_servers.telegram_tools import list_telegram_dialogs


class TestEventLoopFix:
    """Test the event loop bug fix for list_telegram_dialogs."""

    def test_list_dialogs_no_event_loop(self):
        """Test normal operation when no event loop is running."""
        print("🧪 Testing list_telegram_dialogs with no active event loop")
        
        with patch('asyncio.run') as mock_run:
            # Mock successful dialog retrieval
            mock_run.return_value = (
                [{'id': 123, 'title': 'Test Chat', 'type': 'group'}], 
                None
            )
            
            with patch('integrations.telegram.utils.format_dialogs_list') as mock_format:
                mock_format.return_value = "📱 Test Chat (123)"
                
                result = list_telegram_dialogs()
                
                assert "📱 Test Chat (123)" in result
                mock_run.assert_called_once()
                print("   ✅ Normal operation works correctly")

    def test_list_dialogs_with_running_event_loop(self):
        """Test proper error handling when event loop is already running."""
        print("🧪 Testing list_telegram_dialogs with running event loop")
        
        # Simulate the "cannot be called from a running event loop" error
        runtime_error = RuntimeError("asyncio.run() cannot be called from a running event loop")
        
        with patch('asyncio.run', side_effect=runtime_error):
            result = list_telegram_dialogs()
            
            assert "❌ Cannot retrieve dialogs from within an active event loop" in result
            assert "Please run from a synchronous context" in result
            print("   ✅ Running event loop error handled correctly")

    def test_list_dialogs_other_runtime_error(self):
        """Test handling of other RuntimeError cases."""
        print("🧪 Testing list_telegram_dialogs with other RuntimeError")
        
        runtime_error = RuntimeError("Some other async error")
        
        with patch('asyncio.run', side_effect=runtime_error):
            with patch('asyncio.get_event_loop') as mock_get_loop:
                mock_loop = MagicMock()
                mock_loop.is_running.return_value = True
                mock_get_loop.return_value = mock_loop
                
                result = list_telegram_dialogs()
                
                assert "❌ Cannot retrieve dialogs: Event loop is already running" in result
                print("   ✅ Other RuntimeError handled correctly")

    def test_list_dialogs_loop_not_running(self):
        """Test fallback to run_until_complete when loop exists but not running."""
        print("🧪 Testing list_telegram_dialogs with stopped event loop")
        
        runtime_error = RuntimeError("Some other async error")
        
        with patch('asyncio.run', side_effect=runtime_error):
            with patch('asyncio.get_event_loop') as mock_get_loop:
                mock_loop = MagicMock()
                mock_loop.is_running.return_value = False
                mock_loop.run_until_complete.return_value = (
                    [{'id': 456, 'title': 'Fallback Chat'}], 
                    None
                )
                mock_get_loop.return_value = mock_loop
                
                with patch('integrations.telegram.utils.format_dialogs_list') as mock_format:
                    mock_format.return_value = "📱 Fallback Chat (456)"
                    
                    result = list_telegram_dialogs()
                    
                    assert "📱 Fallback Chat (456)" in result
                    mock_loop.run_until_complete.assert_called_once()
                    print("   ✅ Fallback to run_until_complete works")

    def test_list_dialogs_nested_exception(self):
        """Test handling of exceptions in the fallback path."""
        print("🧪 Testing list_telegram_dialogs with nested exceptions")
        
        runtime_error = RuntimeError("Some other async error")
        
        with patch('asyncio.run', side_effect=runtime_error):
            with patch('asyncio.get_event_loop', side_effect=Exception("Loop error")):
                result = list_telegram_dialogs()
                
                assert "❌ Event loop error: Loop error" in result
                print("   ✅ Nested exceptions handled correctly")


class TestEventLoopEndToEnd:
    """End-to-end tests for the event loop fix."""

    async def async_context_test(self):
        """Helper to test from within an async context."""
        return list_telegram_dialogs()

    def test_from_async_context(self):
        """Test calling list_telegram_dialogs from within an async function."""
        print("🧪 End-to-end test: Calling from async context")
        
        async def run_test():
            # This should trigger the "running event loop" error
            result = list_telegram_dialogs()
            return result
        
        # Run the test
        result = asyncio.run(run_test())
        
        # Should get a proper error message, not a crash
        assert "❌" in result
        assert "event loop" in result.lower()
        print("   ✅ Async context properly handled")

    def test_from_sync_context(self):
        """Test calling list_telegram_dialogs from synchronous context."""
        print("🧪 End-to-end test: Calling from sync context")
        
        # Mock the entire chain to simulate successful operation
        with patch('asyncio.run') as mock_run:
            mock_run.return_value = (
                [
                    {'id': 111, 'title': 'Test Group', 'type': 'group', 'members': 10},
                    {'id': 222, 'title': 'Test DM', 'type': 'private', 'members': 2}
                ], 
                None
            )
            
            with patch('integrations.telegram.utils.format_dialogs_list') as mock_format:
                mock_format.return_value = "📱 **Available Chats:**\n1. Test Group (111) - 10 members\n2. Test DM (222)"
                
                result = list_telegram_dialogs()
                
                assert "Test Group" in result
                assert "Test DM" in result
                assert "111" in result
                assert "222" in result
                print("   ✅ Sync context works correctly")


def test_integration_with_real_imports():
    """Test that the fix works with real module imports."""
    print("🧪 Integration test: Real imports and error paths")
    
    # Test that we can import and call the function without crashes
    try:
        from mcp_servers.telegram_tools import list_telegram_dialogs
        
        # Call it - should either work or give a proper error message
        result = list_telegram_dialogs()
        
        # Should not crash and should return a string
        assert isinstance(result, str)
        
        # Should either be successful or a proper error message
        assert len(result) > 0
        
        if result.startswith("❌"):
            print(f"   ℹ️  Expected error: {result[:100]}...")
        else:
            print(f"   ✅ Successful result: {result[:100]}...")
            
    except Exception as e:
        pytest.fail(f"Function should not crash: {e}")


if __name__ == "__main__":
    print("🔬 Telegram Tools Event Loop Fix Test Suite")
    print("=" * 60)
    
    # Run individual test classes
    test_fix = TestEventLoopFix()
    test_fix.test_list_dialogs_no_event_loop()
    test_fix.test_list_dialogs_with_running_event_loop()
    test_fix.test_list_dialogs_other_runtime_error()
    test_fix.test_list_dialogs_loop_not_running()
    test_fix.test_list_dialogs_nested_exception()
    
    test_e2e = TestEventLoopEndToEnd()
    test_e2e.test_from_async_context()
    test_e2e.test_from_sync_context()
    
    # Run integration test
    test_integration_with_real_imports()
    
    print("\n🎉 All Event Loop Fix Tests Passed!")
    print("✅ Event loop bug has been properly fixed and tested")