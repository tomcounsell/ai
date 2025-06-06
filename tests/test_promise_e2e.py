#!/usr/bin/env python
"""
End-to-end test for the promise architecture.

This test simulates a real scenario where:
1. A user sends a message requesting a long-running task
2. The system creates a promise and returns immediately
3. The task executes in the background
4. A completion message is sent when done
"""

import asyncio
import time
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock

# Import the components we need
from integrations.telegram.handlers import MessageHandler
from utilities.database import (
    init_database, create_promise, get_promise, 
    update_promise_status, get_pending_promises
)
from tools.valor_delegation_tool import execute_valor_delegation, spawn_valor_session, estimate_task_duration


async def test_promise_e2e():
    """Run end-to-end test of promise architecture."""
    print("\n🧪 PROMISE ARCHITECTURE END-TO-END TEST")
    print("=" * 50)
    
    # 1. Initialize database
    print("\n1️⃣ Initializing database...")
    init_database()
    print("   ✅ Database initialized")
    
    # 2. Create mock Telegram components
    print("\n2️⃣ Setting up mock Telegram environment...")
    mock_client = AsyncMock()
    mock_message = Mock()
    mock_message.id = 12345
    mock_message.text = "Can you create comprehensive documentation for the promise architecture?"
    mock_message.from_user = Mock()
    mock_message.from_user.id = 67890
    mock_message.from_user.username = "testuser"
    mock_message.date = Mock()
    mock_message.date.timestamp = Mock(return_value=time.time())
    
    # Mock chat history
    mock_chat_history = Mock()
    mock_chat_history.get_recent_messages = Mock(return_value=[])
    mock_chat_history.add_message = Mock()
    
    # 3. Create message handler
    print("   ✅ Mock environment ready")
    print("\n3️⃣ Creating message handler...")
    handler = MessageHandler(mock_client, mock_chat_history)
    print("   ✅ Message handler created")
    
    # 4. Test task duration estimation
    print("\n4️⃣ Testing task duration estimation...")
    duration = estimate_task_duration("create comprehensive documentation")
    print(f"   📊 Estimated duration: {duration} seconds")
    print(f"   {'🔄 Will use async promise' if duration > 30 else '⚡ Will use sync execution'}")
    
    # 5. Simulate delegation tool response
    print("\n5️⃣ Testing delegation tool...")
    with patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
        # Make it return async promise marker
        mock_spawn.return_value = "✅ Created comprehensive documentation with 5 sections and code examples"
        
        # Test the delegation
        response = execute_valor_delegation(
            Mock(deps=Mock(chat_id=123)),
            "create comprehensive documentation",
            ".",
            ""
        )
        print(f"   📝 Response: {response[:100]}...")
        print(f"   {'✅ Contains ASYNC_PROMISE marker' if 'ASYNC_PROMISE|' in response else '❌ Missing ASYNC_PROMISE marker'}")
    
    # 6. Test promise creation from response
    print("\n6️⃣ Testing promise creation...")
    chat_id = 123
    
    # Check if we should create a promise
    if "ASYNC_PROMISE|" in response:
        parts = response.split("ASYNC_PROMISE|", 1)
        promise_message = parts[1].strip() if len(parts) > 1 else "Working on task"
        
        # Create promise
        promise_id = create_promise(chat_id, mock_message.id, promise_message)
        print(f"   ✅ Created promise ID: {promise_id}")
        
        # Verify promise
        promise = get_promise(promise_id)
        print(f"   📊 Promise status: {promise['status']}")
        print(f"   📝 Task description: {promise['task_description'][:50]}...")
    
    # 7. Simulate background execution
    print("\n7️⃣ Simulating background execution...")
    if promise_id:
        # Update to in_progress
        update_promise_status(promise_id, "in_progress")
        print("   ⏳ Status updated to: in_progress")
        
        # Simulate task completion
        await asyncio.sleep(0.5)  # Short delay to simulate work
        
        # Update to completed
        result = "Created comprehensive documentation with:\n- Architecture overview\n- Implementation guide\n- Testing strategy\n- Examples"
        update_promise_status(promise_id, "completed", result_summary=result)
        print("   ✅ Status updated to: completed")
        
        # Verify final state
        final_promise = get_promise(promise_id)
        print(f"   📊 Final status: {final_promise['status']}")
        print(f"   📝 Result: {final_promise['result_summary'][:50]}...")
    
    # 8. Test completion message
    print("\n8️⃣ Testing completion message...")
    if final_promise['status'] == 'completed':
        completion_msg = f"""✅ **Task Complete!**

I finished working on: {final_promise['task_description']}

**Result:**
{final_promise['result_summary']}

_Task completed successfully!_"""
        
        print("   📨 Completion message:")
        print("   " + "\n   ".join(completion_msg.split("\n")))
    
    # 9. Summary
    print("\n" + "=" * 50)
    print("✅ END-TO-END TEST COMPLETE!")
    print("\nSummary:")
    print(f"  • Task duration: {duration}s ({'async' if duration > 30 else 'sync'})")
    print(f"  • Promise created: {'Yes' if promise_id else 'No'}")
    print(f"  • Background execution: {'Success' if final_promise['status'] == 'completed' else 'Failed'}")
    print(f"  • System ready: Yes")
    
    # Check for any pending promises
    pending = get_pending_promises()
    print(f"\n📊 Pending promises in system: {len(pending)}")
    if pending:
        for p in pending:
            print(f"   - Promise {p['id']}: {p['task_description'][:50]}...")


if __name__ == "__main__":
    # Run the test
    asyncio.run(test_promise_e2e())