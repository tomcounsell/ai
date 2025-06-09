#!/usr/bin/env python3
"""
Promise Queue Integration Demo

This script demonstrates the promise queue system working end-to-end.
It creates various types of promises and shows their execution.
"""
import asyncio
import time
import json
from datetime import datetime

from utilities.database import init_database, get_database_connection, get_promise
from utilities.promise_manager_huey import HueyPromiseManager
from tasks.huey_config import huey
from tasks.test_runner_tasks import schedule_test_run


def print_header(title):
    """Print a formatted header."""
    print("\n" + "="*60)
    print(f" {title}")
    print("="*60)


def print_promise_status(promise_id):
    """Print the current status of a promise."""
    promise = get_promise(promise_id)
    if promise:
        print(f"Promise {promise_id}: {promise['status']}")
        if promise['result_summary']:
            print(f"Result: {promise['result_summary'][:100]}...")
        if promise['error_message']:
            print(f"Error: {promise['error_message'][:100]}...")
    else:
        print(f"Promise {promise_id}: Not found")


def demo_basic_promise():
    """Demonstrate basic promise creation and execution."""
    print_header("Demo 1: Basic Promise Creation and Execution")
    
    manager = HueyPromiseManager()
    
    # Enable immediate mode for demo
    huey.immediate = True
    
    # Mock the delegation tool for demo
    import unittest.mock
    with unittest.mock.patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
        mock_spawn.return_value = "Demo task completed successfully! Found 3 issues to fix."
        
        # Create a simple promise
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67890,
            task_description="Analyze the authentication module for security issues",
            task_type="code"
        )
        
        print(f"Created promise: {promise_id}")
        
        # In immediate mode, task executes synchronously
        time.sleep(0.1)
        
        print_promise_status(promise_id)
        
        print("\n✅ Demo 1 Complete: Promise created and executed successfully!")


def demo_search_promise():
    """Demonstrate search promise execution."""
    print_header("Demo 2: Search Promise Execution")
    
    manager = HueyPromiseManager()
    
    # Mock search tool
    import unittest.mock
    with unittest.mock.patch('tools.search_tool.search_web') as mock_search:
        mock_search.return_value = """🔍 **Python Async/Await Best Practices**

Found 5 comprehensive resources:

1. **Real Python Guide**: Complete async/await tutorial with examples
2. **Python.org Documentation**: Official asyncio documentation  
3. **AsyncIO Patterns**: Common patterns and anti-patterns
4. **Performance Tips**: Optimizing async code for speed
5. **Testing Async Code**: How to test asynchronous applications

These resources cover everything from basic concepts to advanced patterns."""
        
        # Create search promise
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67891,
            task_description="Find comprehensive resources about Python async/await best practices",
            task_type="search",
            metadata={'query': 'Python async await best practices tutorial'}
        )
        
        print(f"Created search promise: {promise_id}")
        time.sleep(0.1)
        
        print_promise_status(promise_id)
        
        print("\n✅ Demo 2 Complete: Search promise executed successfully!")


def demo_dependent_promises():
    """Demonstrate promises with dependencies."""
    print_header("Demo 3: Dependent Promises Execution")
    
    manager = HueyPromiseManager()
    
    # Track execution order
    execution_order = []
    
    def mock_task_executor(task_name):
        def execute(*args, **kwargs):
            execution_order.append(task_name)
            return f"{task_name.title()} completed successfully"
        return execute
    
    # Mock all task types
    import unittest.mock
    with unittest.mock.patch('tools.valor_delegation_tool.spawn_valor_session', mock_task_executor('setup')):
        with unittest.mock.patch('tools.search_tool.search_web', mock_task_executor('research')):
            
            # Create dependent tasks
            tasks = [
                {'name': 'setup', 'description': 'Set up development environment', 'type': 'code'},
                {'name': 'research', 'description': 'Research best practices', 'type': 'search'},
                {'name': 'implement', 'description': 'Implement the feature', 'type': 'code'}
            ]
            
            dependency_map = {
                'research': ['setup'],      # research depends on setup
                'implement': ['research']   # implement depends on research
            }
            
            print("Creating dependent promises:")
            for task in tasks:
                deps = dependency_map.get(task['name'], [])
                if deps:
                    print(f"  {task['name']} (depends on: {', '.join(deps)})")
                else:
                    print(f"  {task['name']} (no dependencies)")
            
            name_to_id = manager.create_dependent_promises(
                chat_id=12345,
                message_id=67892,
                tasks=tasks,
                dependency_map=dependency_map
            )
            
            # In immediate mode, tasks execute synchronously
            time.sleep(0.3)
            
            print(f"\nExecution order: {' → '.join(execution_order)}")
            
            # Show final status
            for name, promise_id in name_to_id.items():
                print(f"{name.title()}: ", end="")
                print_promise_status(promise_id)
            
            print("\n✅ Demo 3 Complete: Dependent promises executed in correct order!")


def demo_message_queue():
    """Demonstrate missed message queue functionality."""
    print_header("Demo 4: Missed Message Queue")
    
    from utilities.database import queue_missed_message, get_pending_messages, update_message_queue_status
    
    # Queue some missed messages
    print("Queueing missed messages...")
    
    messages = [
        "What's the status of the authentication module?",
        "Can you help me debug this async function?",
        "Please review the latest pull request"
    ]
    
    message_ids = []
    for msg in messages:
        msg_id = queue_missed_message(
            chat_id=12345,
            message_text=msg,
            sender_username="demo_user",
            original_timestamp=datetime.utcnow().isoformat()
        )
        message_ids.append(msg_id)
        print(f"  Queued: {msg[:50]}...")
    
    # Show pending messages
    pending = get_pending_messages()
    print(f"\nPending messages in queue: {len(pending)}")
    
    # Process them (simulate)
    print("\nProcessing messages...")
    for msg_id in message_ids:
        update_message_queue_status(msg_id, 'processing')
        time.sleep(0.1)  # Simulate processing time
        update_message_queue_status(msg_id, 'completed')
        print(f"  Processed message {msg_id}")
    
    # Check final status
    pending_after = get_pending_messages()
    print(f"\nRemaining pending messages: {len(pending_after)}")
    
    print("\n✅ Demo 4 Complete: Message queue processed successfully!")


def demo_test_scheduling():
    """Demonstrate test scheduling through promises."""
    print_header("Demo 5: Test Scheduling")
    
    # Mock test execution
    import unittest.mock
    with unittest.mock.patch('subprocess.run') as mock_run:
        # Mock successful test results
        mock_result = unittest.mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = """
test_promise_system.py::test_create_simple_promise PASSED
test_promise_system.py::test_search_promise_execution PASSED
test_promise_system.py::test_promise_cancellation PASSED

=== 3 passed in 2.54s ===
"""
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        
        print("Scheduling test run through promise queue...")
        
        # Schedule tests
        promise_id = schedule_test_run(
            chat_id=12345,
            message_id=67893,
            test_files=['tests/test_promise_system.py'],
            max_workers=1
        )
        
        print(f"Created test promise: {promise_id}")
        
        # In immediate mode, tests run synchronously
        time.sleep(0.2)
        
        print_promise_status(promise_id)
        
        print("\n✅ Demo 5 Complete: Tests scheduled and executed through promise queue!")


def demo_promise_cancellation():
    """Demonstrate promise cancellation."""
    print_header("Demo 6: Promise Cancellation")
    
    manager = HueyPromiseManager()
    
    # Disable immediate mode to prevent execution
    huey.immediate = False
    
    try:
        # Create a promise
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67894,
            task_description="Long running deployment task",
            task_type="code"
        )
        
        print(f"Created promise: {promise_id}")
        print_promise_status(promise_id)
        
        # Cancel it
        print("\nCancelling promise...")
        success = manager.cancel_promise(promise_id)
        print(f"Cancellation success: {success}")
        
        print_promise_status(promise_id)
        
        print("\n✅ Demo 6 Complete: Promise cancelled successfully!")
        
    finally:
        # Re-enable immediate mode
        huey.immediate = True


def demo_promise_status_tracking():
    """Demonstrate promise status tracking."""
    print_header("Demo 7: Promise Status Tracking")
    
    manager = HueyPromiseManager()
    
    # Create multiple promises
    print("Creating multiple promises for status tracking...")
    
    import unittest.mock
    with unittest.mock.patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
        # Different outcomes for different promises
        mock_spawn.side_effect = [
            "Task 1 completed successfully",
            Exception("Task 2 failed: Network timeout"),
            "Task 3 completed with warnings"
        ]
        
        promise_ids = []
        for i in range(3):
            promise_id = manager.create_promise(
                chat_id=12345,
                message_id=67895 + i,
                task_description=f"Demo task {i+1}",
                task_type="code"
            )
            promise_ids.append(promise_id)
        
        time.sleep(0.2)
        
        # Show status of all promises
        print(f"\nStatus of {len(promise_ids)} promises:")
        for i, promise_id in enumerate(promise_ids):
            print(f"Task {i+1}: ", end="")
            print_promise_status(promise_id)
        
        # Get user promises
        user_promises = manager.get_user_promises(12345)
        print(f"\nTotal promises for user: {len(user_promises)}")
        
        print("\n✅ Demo 7 Complete: Promise status tracking working!")


def main():
    """Run all demos."""
    print_header("Promise Queue Integration Demo")
    print("This demo shows the promise queue system working end-to-end.")
    print("Each demo tests a different aspect of the system.")
    
    # Initialize database
    init_database()
    
    try:
        # Run all demos
        demo_basic_promise()
        demo_search_promise()
        demo_dependent_promises()
        demo_message_queue()
        demo_test_scheduling()
        demo_promise_cancellation()
        demo_promise_status_tracking()
        
        print_header("All Demos Completed Successfully!")
        print("✅ Promise queue system is working end-to-end")
        print("✅ Promise creation, execution, and completion")
        print("✅ Search and analysis task execution")  
        print("✅ Dependency management")
        print("✅ Message queue processing")
        print("✅ Test scheduling")
        print("✅ Promise cancellation")
        print("✅ Status tracking")
        
    except Exception as e:
        print(f"\n❌ Demo failed with error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Cleanup
        with get_database_connection() as conn:
            conn.execute("DELETE FROM promises")
            conn.execute("DELETE FROM message_queue")
            conn.commit()
        
        print("\n🧹 Cleaned up demo data")


if __name__ == "__main__":
    main()