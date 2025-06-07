"""
Comprehensive tests for the promise queue system.

Tests cover:
- Promise creation and lifecycle
- Task execution and completion
- Dependency management
- Message queue functionality
- Error handling and recovery
"""
import pytest
import json
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from utilities.promise_manager_huey import HueyPromiseManager
from utilities.database import (
    init_database, get_promise, get_database_connection,
    queue_missed_message, get_pending_messages, update_message_queue_status
)
from tasks.promise_tasks import (
    execute_coding_task, execute_search_task, execute_analysis_task,
    check_promise_dependencies, execute_promise_by_type
)


class TestPromiseCreation:
    """Test promise creation and basic lifecycle."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Initialize test database before each test."""
        init_database()
        yield
        # Cleanup after test
        with get_database_connection() as conn:
            conn.execute("DELETE FROM promises")
            conn.execute("DELETE FROM message_queue")
            conn.commit()
    
    def test_create_simple_promise(self):
        """Test creating a basic promise."""
        manager = HueyPromiseManager()
        
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67890,
            task_description="Test task",
            task_type="code"
        )
        
        assert promise_id is not None
        
        # Verify promise was created
        promise = get_promise(promise_id)
        assert promise is not None
        assert promise['chat_id'] == 12345
        assert promise['task_description'] == "Test task"
        assert promise['status'] == 'pending'
    
    def test_create_promise_with_metadata(self):
        """Test creating a promise with metadata."""
        manager = HueyPromiseManager()
        
        metadata = {
            'target_directory': '/tmp/test',
            'instructions': 'Do something special'
        }
        
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67890,
            task_description="Complex task",
            task_type="code",
            metadata=metadata
        )
        
        promise = get_promise(promise_id)
        assert promise is not None
        stored_metadata = json.loads(promise['metadata'])
        assert stored_metadata['target_directory'] == '/tmp/test'
    
    def test_create_parallel_promises(self):
        """Test creating multiple parallel promises."""
        manager = HueyPromiseManager()
        
        tasks = [
            {'description': 'Task 1', 'type': 'code'},
            {'description': 'Task 2', 'type': 'search'},
            {'description': 'Task 3', 'type': 'analysis'}
        ]
        
        promise_ids = manager.create_parallel_promises(
            chat_id=12345,
            message_id=67890,
            tasks=tasks
        )
        
        assert len(promise_ids) == 3
        
        # Verify all promises were created
        for i, promise_id in enumerate(promise_ids):
            promise = get_promise(promise_id)
            assert promise['task_description'] == f'Task {i+1}'
            assert promise['status'] == 'pending'
    
    def test_invalid_task_type(self):
        """Test that invalid task types are rejected."""
        manager = HueyPromiseManager()
        
        with pytest.raises(ValueError) as excinfo:
            manager.create_promise(
                chat_id=12345,
                message_id=67890,
                task_description="Invalid task",
                task_type="invalid_type"
            )
        
        assert "Invalid task type" in str(excinfo.value)


class TestPromiseExecution:
    """Test promise execution and task handling."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Initialize test database and enable immediate mode."""
        init_database()
        # Enable immediate mode for synchronous testing
        from tasks.huey_config import huey
        huey.immediate = True
        yield
        huey.immediate = False
        # Cleanup
        with get_database_connection() as conn:
            conn.execute("DELETE FROM promises")
            conn.commit()
    
    @patch('tools.valor_delegation_tool.spawn_valor_session')
    def test_execute_coding_task(self, mock_spawn):
        """Test execution of a coding task."""
        mock_spawn.return_value = "Task completed successfully"
        
        # Create a promise
        manager = HueyPromiseManager()
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67890,
            task_description="Fix bug in auth.py",
            task_type="code"
        )
        
        # In immediate mode, task should execute synchronously
        time.sleep(0.1)  # Small delay to ensure execution
        
        # Check promise was completed
        promise = get_promise(promise_id)
        assert promise['status'] == 'completed'
        assert promise['result_summary'] == "Task completed successfully"
    
    @patch('tools.search_tool.search_web')
    def test_execute_search_task(self, mock_search):
        """Test execution of a search task."""
        mock_search.return_value = "Search results: Found 3 items"
        
        manager = HueyPromiseManager()
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67890,
            task_description="Search for Python tutorials",
            task_type="search"
        )
        
        time.sleep(0.1)
        
        promise = get_promise(promise_id)
        assert promise['status'] == 'completed'
        assert "Search results" in promise['result_summary']
    
    def test_task_failure_handling(self):
        """Test handling of task failures."""
        with patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
            mock_spawn.side_effect = Exception("Task failed!")
            
            manager = HueyPromiseManager()
            promise_id = manager.create_promise(
                chat_id=12345,
                message_id=67890,
                task_description="Failing task",
                task_type="code"
            )
            
            time.sleep(0.1)
            
            promise = get_promise(promise_id)
            assert promise['status'] == 'failed'
            assert "Task failed!" in promise['error_message']


class TestPromiseDependencies:
    """Test promise dependency management."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Initialize test database."""
        init_database()
        yield
        with get_database_connection() as conn:
            conn.execute("DELETE FROM promises")
            conn.commit()
    
    def test_dependent_promises(self):
        """Test creating promises with dependencies."""
        manager = HueyPromiseManager()
        
        tasks = [
            {'name': 'setup', 'description': 'Set up environment', 'type': 'code'},
            {'name': 'test', 'description': 'Run tests', 'type': 'code'},
            {'name': 'deploy', 'description': 'Deploy to production', 'type': 'code'}
        ]
        
        dependency_map = {
            'test': ['setup'],      # test depends on setup
            'deploy': ['test']      # deploy depends on test
        }
        
        name_to_id = manager.create_dependent_promises(
            chat_id=12345,
            message_id=67890,
            tasks=tasks,
            dependency_map=dependency_map
        )
        
        # Check that all promises were created
        assert len(name_to_id) == 3
        
        # Check dependency ordering
        setup_promise = get_promise(name_to_id['setup'])
        test_promise = get_promise(name_to_id['test'])
        deploy_promise = get_promise(name_to_id['deploy'])
        
        # Setup should be pending (no dependencies)
        assert setup_promise['status'] == 'pending'
        
        # Test and deploy should be waiting
        assert test_promise['status'] == 'waiting'
        assert deploy_promise['status'] == 'waiting'
        
        # Check dependencies are stored correctly
        test_metadata = json.loads(test_promise['metadata'])
        assert name_to_id['setup'] in test_metadata.get('parent_promise_ids', [])
    
    def test_topological_sort(self):
        """Test dependency sorting algorithm."""
        manager = HueyPromiseManager()
        
        nodes = ['a', 'b', 'c', 'd']
        dependencies = {
            'b': ['a'],      # b depends on a
            'c': ['a', 'b'], # c depends on a and b
            'd': ['c']       # d depends on c
        }
        
        sorted_nodes = manager._topological_sort(nodes, dependencies)
        
        # 'a' should come first
        assert sorted_nodes[0] == 'a'
        # 'd' should come last
        assert sorted_nodes[-1] == 'd'
        # 'b' should come before 'c'
        assert sorted_nodes.index('b') < sorted_nodes.index('c')


class TestMessageQueue:
    """Test missed message queue functionality."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Initialize test database."""
        init_database()
        yield
        with get_database_connection() as conn:
            conn.execute("DELETE FROM message_queue")
            conn.commit()
    
    def test_queue_missed_message(self):
        """Test queuing a missed message."""
        message_id = queue_missed_message(
            chat_id=12345,
            message_text="Hello, I missed you!",
            sender_username="testuser",
            original_timestamp=datetime.utcnow().isoformat()
        )
        
        assert message_id is not None
        
        # Verify message was queued
        messages = get_pending_messages()
        assert len(messages) == 1
        assert messages[0]['message_text'] == "Hello, I missed you!"
        assert messages[0]['status'] == 'pending'
    
    def test_update_message_status(self):
        """Test updating message queue status."""
        message_id = queue_missed_message(
            chat_id=12345,
            message_text="Test message"
        )
        
        # Update to processing
        update_message_queue_status(message_id, 'processing')
        
        with get_database_connection() as conn:
            cursor = conn.cursor()
            status = cursor.execute(
                "SELECT status FROM message_queue WHERE id = ?",
                (message_id,)
            ).fetchone()[0]
        
        assert status == 'processing'
        
        # Update to completed
        update_message_queue_status(message_id, 'completed')
        
        with get_database_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT status, processed_at FROM message_queue WHERE id = ?",
                (message_id,)
            ).fetchone()
        
        assert row[0] == 'completed'
        assert row[1] is not None  # processed_at should be set
    
    def test_get_pending_messages_limit(self):
        """Test retrieving pending messages with limit."""
        # Queue multiple messages
        for i in range(15):
            queue_missed_message(
                chat_id=12345,
                message_text=f"Message {i}"
            )
        
        # Get with limit
        messages = get_pending_messages(limit=5)
        assert len(messages) == 5
        
        # Should be ordered by creation time
        assert messages[0]['message_text'] == "Message 0"
        assert messages[4]['message_text'] == "Message 4"


class TestPromiseManagerFeatures:
    """Test additional promise manager features."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Initialize test database."""
        init_database()
        yield
        with get_database_connection() as conn:
            conn.execute("DELETE FROM promises")
            conn.commit()
    
    def test_get_promise_status(self):
        """Test retrieving promise status."""
        manager = HueyPromiseManager()
        
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67890,
            task_description="Status test"
        )
        
        status = manager.get_promise_status(promise_id)
        assert status is not None
        assert status['id'] == promise_id
        assert status['task_description'] == "Status test"
        assert status['status'] == 'pending'
    
    def test_cancel_promise(self):
        """Test cancelling a promise."""
        manager = HueyPromiseManager()
        
        promise_id = manager.create_promise(
            chat_id=12345,
            message_id=67890,
            task_description="Cancel me"
        )
        
        # Cancel the promise
        success = manager.cancel_promise(promise_id)
        assert success is True
        
        # Check status
        promise = get_promise(promise_id)
        assert promise['status'] == 'cancelled'
        assert promise['error_message'] == 'Cancelled by user'
        
        # Try to cancel again (should fail)
        success = manager.cancel_promise(promise_id)
        assert success is False
    
    def test_get_user_promises(self):
        """Test retrieving promises for a specific user."""
        manager = HueyPromiseManager()
        
        # Create promises for different chats
        for i in range(3):
            manager.create_promise(
                chat_id=12345,
                message_id=i,
                task_description=f"Task for user 1 - {i}"
            )
        
        for i in range(2):
            manager.create_promise(
                chat_id=67890,
                message_id=i,
                task_description=f"Task for user 2 - {i}"
            )
        
        # Get promises for first chat
        user1_promises = manager.get_user_promises(12345)
        assert len(user1_promises) == 3
        
        # Get promises for second chat
        user2_promises = manager.get_user_promises(67890)
        assert len(user2_promises) == 2
    
    def test_resume_pending_promises(self):
        """Test resuming pending promises after restart."""
        manager = HueyPromiseManager()
        
        # Create some promises
        pending_ids = []
        for i in range(3):
            promise_id = manager.create_promise(
                chat_id=12345,
                message_id=i,
                task_description=f"Pending task {i}"
            )
            pending_ids.append(promise_id)
        
        # Mark one as completed
        with get_database_connection() as conn:
            conn.execute(
                "UPDATE promises SET status = 'completed' WHERE id = ?",
                (pending_ids[0],)
            )
            conn.commit()
        
        # Resume pending promises
        resumed_count = manager.resume_pending_promises()
        
        # Should resume 2 promises (not the completed one)
        assert resumed_count == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])