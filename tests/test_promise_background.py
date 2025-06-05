"""Test background promise execution with real Claude Code."""

import asyncio
from unittest.mock import AsyncMock, Mock
from integrations.telegram.handlers import MessageHandler
import time

async def test_background_execution():
    """Test actual background execution of a promise."""
    
    # Create mock Telegram components
    mock_client = AsyncMock()
    mock_chat_history = Mock()
    mock_chat_history.add_message = Mock()
    
    # Create handler
    handler = MessageHandler(mock_client, mock_chat_history)
    
    # Create mock message
    mock_message = AsyncMock()
    mock_message.id = 999
    mock_message.reply = AsyncMock()
    
    # Test parameters
    chat_id = 12345
    promise_id = 2
    task_description = "echo 'Hello from background task'"
    
    print(f"🧪 Testing background execution of: {task_description}")
    print(f"   Promise ID: {promise_id}")
    print(f"   Chat ID: {chat_id}")
    
    # Execute the background task
    start_time = time.time()
    try:
        await handler._execute_promise_background(
            mock_message,
            chat_id,
            promise_id,
            task_description
        )
        execution_time = time.time() - start_time
        print(f"\n✅ Background execution completed in {execution_time:.1f}s")
        
        # Check if reply was called
        if mock_message.reply.called:
            reply_text = mock_message.reply.call_args[0][0]
            print(f"\n📤 Reply sent:")
            print(f"{reply_text[:500]}...")
            
            # Verify the reply contains expected elements
            assert "✅ **Task Complete!**" in reply_text
            assert task_description in reply_text
            print(f"\n✅ Reply contains expected elements")
        else:
            print(f"\n❌ No reply was sent")
            
    except Exception as e:
        print(f"\n❌ Background execution failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Initialize database first
    from utilities.database import init_database
    print("Initializing database...")
    init_database()
    
    # Run the async test
    print("\nRunning background execution test...")
    asyncio.run(test_background_execution())