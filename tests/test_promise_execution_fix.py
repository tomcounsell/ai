"""
Test the promise execution fix to ensure background tasks complete properly.
"""

import asyncio
import time
from unittest.mock import Mock, AsyncMock, patch

from integrations.telegram.handlers import MessageHandler
from tools.valor_delegation_tool import spawn_valor_session


def test_promise_generation():
    """Test that ASYNC_PROMISE is generated for long tasks."""
    # Test short task - should execute directly
    result = spawn_valor_session(
        "list files",
        ".",
        force_sync=False
    )
    assert "ASYNC_PROMISE|" not in result
    
    # Test long task - should return promise
    result = spawn_valor_session(
        "analyze all code and create comprehensive documentation",
        ".",
        force_sync=False
    )
    assert "ASYNC_PROMISE|" in result
    assert "I'll work on this task in the background" in result
    
    print("✅ Promise generation test passed")


def test_force_sync_execution():
    """Test that force_sync=True executes tasks synchronously."""
    # This simulates what happens in the background task
    result = spawn_valor_session(
        "echo hello world",
        ".",
        force_sync=True
    )
    
    # Should NOT contain ASYNC_PROMISE marker
    assert "ASYNC_PROMISE|" not in result
    print(f"✅ Force sync test passed. Result preview: {result[:100]}...")


async def test_background_execution():
    """Test the complete background execution flow."""
    # Create mock objects
    mock_client = AsyncMock()
    mock_chat_history = Mock()
    mock_chat_history.add_message = Mock()
    
    # Create handler
    handler = MessageHandler(mock_client, mock_chat_history)
    
    # Create mock message
    mock_message = AsyncMock()
    mock_message.id = 123
    mock_message.reply = AsyncMock()
    
    # Test promise execution
    chat_id = 12345
    promise_id = 1
    task_description = "test task"
    
    print("🧪 Testing background execution...")
    
    with patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
        # Simulate successful execution
        mock_spawn.return_value = "Task completed successfully!"
        
        # Execute the background task
        await handler._execute_promise_background(
            mock_message,
            chat_id,
            promise_id,
            task_description
        )
        
        # Verify spawn_valor_session was called with force_sync=True
        mock_spawn.assert_called_once()
        call_kwargs = mock_spawn.call_args[1]
        assert call_kwargs['force_sync'] is True
        
        # Verify message was sent
        assert mock_message.reply.called
        reply_text = mock_message.reply.call_args[0][0]
        assert "✅ **Task Complete!**" in reply_text
        assert "Task completed successfully!" in reply_text
        
        print("✅ Background execution test passed")


async def test_error_handling():
    """Test error handling in background execution."""
    # Create mock objects
    mock_client = AsyncMock()
    mock_chat_history = Mock()
    handler = MessageHandler(mock_client, mock_chat_history)
    
    # Create mock message that fails to send reply
    mock_message = AsyncMock()
    mock_message.id = 123
    mock_message.reply = AsyncMock(side_effect=Exception("Network error"))
    
    chat_id = 12345
    promise_id = 2
    task_description = "failing task"
    
    print("🧪 Testing error handling...")
    
    with patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
        # Simulate execution error
        mock_spawn.side_effect = Exception("Execution failed")
        
        # Execute the background task
        await handler._execute_promise_background(
            mock_message,
            chat_id,
            promise_id,
            task_description
        )
        
        # Verify error message send was attempted
        mock_message.reply.assert_called()
        error_text = mock_message.reply.call_args[0][0]
        assert "❌ **Task Failed**" in error_text
        assert "Execution failed" in error_text
        
        # Verify fallback to client.send_message was attempted
        if mock_client.send_message.called:
            fallback_text = mock_client.send_message.call_args[0][1]
            assert "❌ **Task Failed**" in fallback_text
        
        print("✅ Error handling test passed")


if __name__ == "__main__":
    # Run sync tests
    test_promise_generation()
    test_force_sync_execution()
    
    # Run async tests
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_background_execution())
    loop.run_until_complete(test_error_handling())
    
    print("\n✅ All promise execution tests passed!")