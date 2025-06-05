"""
Comprehensive test suite for the minimal promise architecture.

Tests cover:
1. Database operations (promise CRUD)
2. Task duration estimation
3. Promise detection and creation
4. Background execution flow
5. Completion message delivery
6. Error handling scenarios
7. Edge cases and concurrency
"""

import asyncio
import sqlite3
import pytest
import os
import time
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime

# Import components to test
from utilities.database import (
    init_database, create_promise, update_promise_status, 
    get_promise, get_pending_promises
)
from tools.valor_delegation_tool import estimate_task_duration, spawn_valor_session
from integrations.telegram.handlers import MessageHandler


class TestDatabaseOperations:
    """Test promise database CRUD operations."""
    
    @pytest.fixture
    def test_db(self, tmp_path):
        """Create a temporary test database."""
        db_path = tmp_path / "test_promises.db"
        os.environ["DATABASE_PATH"] = str(db_path)
        
        # Initialize database with promise table
        with patch('utilities.database.get_database_path', return_value=db_path):
            init_database()
        
        yield db_path
        
        # Cleanup
        if db_path.exists():
            db_path.unlink()
    
    def test_create_promise(self, test_db):
        """Test creating a new promise."""
        with patch('utilities.database.get_database_path', return_value=test_db):
            promise_id = create_promise(
                chat_id=12345,
                message_id=67890,
                task_description="Fix authentication bug"
            )
            
            assert isinstance(promise_id, int)
            assert promise_id > 0
            
            # Verify promise was created
            promise = get_promise(promise_id)
            assert promise is not None
            assert promise['chat_id'] == 12345
            assert promise['message_id'] == 67890
            assert promise['task_description'] == "Fix authentication bug"
            assert promise['status'] == 'pending'
    
    def test_update_promise_status(self, test_db):
        """Test updating promise status through lifecycle."""
        with patch('utilities.database.get_database_path', return_value=test_db):
            # Create promise
            promise_id = create_promise(12345, 67890, "Test task")
            
            # Update to in_progress
            update_promise_status(promise_id, "in_progress")
            promise = get_promise(promise_id)
            assert promise['status'] == 'in_progress'
            assert promise['completed_at'] is None
            
            # Update to completed with result
            update_promise_status(
                promise_id, 
                "completed", 
                result_summary="Fixed the bug successfully"
            )
            promise = get_promise(promise_id)
            assert promise['status'] == 'completed'
            assert promise['result_summary'] == "Fixed the bug successfully"
            assert promise['completed_at'] is not None
    
    def test_get_pending_promises(self, test_db):
        """Test retrieving pending promises."""
        with patch('utilities.database.get_database_path', return_value=test_db):
            # Create multiple promises
            p1 = create_promise(12345, 1, "Task 1")
            p2 = create_promise(12345, 2, "Task 2")
            p3 = create_promise(67890, 3, "Task 3")
            
            # Mark one as completed
            update_promise_status(p2, "completed")
            
            # Get all pending
            pending = get_pending_promises()
            assert len(pending) == 2
            assert pending[0]['id'] == p1
            assert pending[1]['id'] == p3
            
            # Get pending for specific chat
            chat_pending = get_pending_promises(chat_id=12345)
            assert len(chat_pending) == 1
            assert chat_pending[0]['id'] == p1
    
    def test_concurrent_access(self, test_db):
        """Test concurrent promise operations."""
        with patch('utilities.database.get_database_path', return_value=test_db):
            # Create multiple promises concurrently
            promises = []
            for i in range(5):
                promise_id = create_promise(12345, i, f"Concurrent task {i}")
                promises.append(promise_id)
            
            # Verify all were created
            all_promises = get_pending_promises(chat_id=12345)
            assert len(all_promises) == 5


class TestTaskEstimation:
    """Test task duration estimation logic."""
    
    def test_quick_task_estimation(self):
        """Test estimation for quick tasks."""
        # Quick task keywords
        assert estimate_task_duration("fix typo in README") == 15
        assert estimate_task_duration("update version number") == 15
        assert estimate_task_duration("add comment to function") == 15
        assert estimate_task_duration("rename variable") == 15
    
    def test_long_task_estimation(self):
        """Test estimation for complex tasks."""
        # Long task keywords
        assert estimate_task_duration("refactor authentication system") == 60
        assert estimate_task_duration("implement new feature") == 60
        assert estimate_task_duration("create comprehensive test suite") == 60
        assert estimate_task_duration("build entire application") == 60
    
    def test_default_estimation(self):
        """Test default estimation for ambiguous tasks."""
        assert estimate_task_duration("work on the project") == 30
        assert estimate_task_duration("help with code") == 30
        assert estimate_task_duration("") == 30
    
    def test_mixed_keywords(self):
        """Test tasks with both quick and long keywords."""
        # More long keywords should win
        assert estimate_task_duration("fix bug in entire authentication system refactor") == 60
        # More quick keywords should win  
        assert estimate_task_duration("quick fix update change small typo") == 15


class TestPromiseDetection:
    """Test ASYNC_PROMISE detection in delegation tool."""
    
    @patch('tools.valor_delegation_tool.execute_valor_delegation')
    def test_async_promise_trigger(self, mock_execute):
        """Test that long tasks return ASYNC_PROMISE marker."""
        mock_execute.return_value = "Task completed successfully"
        
        # Task estimated >30 seconds should return promise
        result = spawn_valor_session(
            task_description="Refactor entire authentication system",
            target_directory="/tmp"
        )
        
        assert "ASYNC_PROMISE|" in result
        assert "I'll work on this task in the background" in result
        assert "Refactor entire authentication system" in result
        
        # Should not actually execute
        mock_execute.assert_not_called()
    
    @patch('tools.valor_delegation_tool.execute_valor_delegation')
    def test_sync_execution(self, mock_execute):
        """Test that quick tasks execute synchronously."""
        mock_execute.return_value = "Fixed typo successfully"
        
        # Task estimated <30 seconds should execute normally
        result = spawn_valor_session(
            task_description="Fix typo in README",
            target_directory="/tmp"
        )
        
        assert "ASYNC_PROMISE|" not in result
        assert result == "Fixed typo successfully"
        mock_execute.assert_called_once()


class TestBackgroundExecution:
    """Test background promise execution in message handler."""
    
    @pytest.fixture
    def mock_message(self):
        """Create a mock Telegram message."""
        message = Mock()
        message.id = 12345
        message.reply = AsyncMock()
        message.from_user = Mock()
        message.from_user.username = "testuser"
        message.chat = Mock()
        message.chat.type = "private"
        message.chat.id = 67890
        return message
    
    @pytest.fixture
    def handler(self):
        """Create message handler instance."""
        handler = MessageHandler(Mock(), Mock())
        handler.chat_history = Mock()
        handler.chat_history.add_message = Mock()
        handler.client = Mock()
        handler.client.send_message = AsyncMock()
        handler._safe_reply = AsyncMock()
        return handler
    
    @pytest.mark.asyncio
    async def test_promise_detection_in_response(self, handler, mock_message):
        """Test that ASYNC_PROMISE in response triggers background execution."""
        # Mock database operations
        with patch('utilities.database.create_promise', return_value=1):
            # Mock the background task creation
            with patch('asyncio.create_task') as mock_create_task:
                # Process response with ASYNC_PROMISE marker
                answer = "ASYNC_PROMISE|I'll work on this task in the background: Fix authentication bug"
                
                result = await handler._process_agent_response(
                    mock_message, 67890, answer
                )
            
            # Should return True (handled)
            assert result is True
            
            # Should send immediate response
            handler._safe_reply.assert_called_once()
            call_args = handler._safe_reply.call_args[0]
            assert "I'll work on this task in the background" in call_args[1]
            
            # Should create background task
            mock_create_task.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_background_execution_success(self, handler, mock_message):
        """Test successful background task execution."""
        with patch('utilities.database.create_promise', return_value=1):
            with patch('utilities.database.update_promise_status') as mock_update:
                with patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
                    mock_spawn.return_value = "Successfully fixed the authentication bug"
                    
                    # Execute background task
                    await handler._execute_promise_background(
                        mock_message, 67890, 1, "Fix authentication bug"
                    )
                    
                    # Should update status to in_progress
                    assert mock_update.call_args_list[0][0][1] == "in_progress"
                    
                    # Should execute task
                    mock_spawn.assert_called_once()
                    
                    # Should send completion message
                    mock_message.reply.assert_called_once()
                    completion_msg = mock_message.reply.call_args[0][0]
                    assert "Task Complete!" in completion_msg
                    assert "Fix authentication bug" in completion_msg
                    assert "Successfully fixed" in completion_msg
                    
                    # Should update status to completed
                    assert mock_update.call_args_list[1][0][1] == "completed"
    
    @pytest.mark.asyncio
    async def test_background_execution_failure(self, handler, mock_message):
        """Test failed background task execution."""
        with patch('utilities.database.update_promise_status') as mock_update:
            with patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
                mock_spawn.side_effect = Exception("Claude Code failed")
                
                # Execute background task
                await handler._execute_promise_background(
                    mock_message, 67890, 1, "Complex refactoring"
                )
                
                # Should send error message
                mock_message.reply.assert_called_once()
                error_msg = mock_message.reply.call_args[0][0]
                assert "Task Failed" in error_msg
                assert "Complex refactoring" in error_msg
                assert "Claude Code failed" in error_msg
                
                # Should update status to failed
                final_update = mock_update.call_args_list[-1]
                assert final_update[0][1] == "failed"
                assert "Claude Code failed" in final_update[1]['error_message']


class TestEdgeCases:
    """Test edge cases and error scenarios."""
    
    def test_empty_task_description(self):
        """Test handling of empty task descriptions."""
        # Should use default estimation
        assert estimate_task_duration("") == 30
        assert estimate_task_duration(None) == 30
    
    @pytest.mark.asyncio
    async def test_telegram_timeout_handling(self):
        """Test handling when Telegram message sending times out."""
        handler = MessageHandler(Mock(), Mock())
        handler.chat_history = Mock()
        handler.chat_history.add_message = Mock()
        
        message = Mock()
        message.reply = AsyncMock(side_effect=asyncio.TimeoutError())
        handler.client = Mock()
        handler.client.send_message = AsyncMock()
        
        with patch('utilities.database.update_promise_status'):
            with patch('tools.valor_delegation_tool.spawn_valor_session', return_value="Result"):
                # Should handle timeout gracefully
                await handler._execute_promise_background(
                    message, 12345, 1, "Test task"
                )
                
                # Should try alternative send method
                handler.client.send_message.assert_called_once()
    
    def test_database_lock_scenario(self):
        """Test handling of database locks."""
        # This would require more complex setup with actual SQLite locks
        # For now, verify that operations use short transactions
        pass
    
    @pytest.mark.asyncio
    async def test_server_restart_recovery(self):
        """Test recovery of pending promises after restart."""
        # This would be tested in integration/production
        # Verify that get_pending_promises() returns unfinished work
        pass


class TestUserExperience:
    """Test user-facing aspects of the promise system."""
    
    @pytest.mark.asyncio
    async def test_response_time_sync_tasks(self):
        """Test that sync tasks respond quickly."""
        start = time.time()
        
        with patch('tools.valor_delegation_tool.execute_valor_delegation', return_value="Done"):
            result = spawn_valor_session(
                task_description="Fix typo",
                target_directory="/tmp"
            )
        
        elapsed = time.time() - start
        
        # Should complete very quickly (not go async)
        assert elapsed < 0.1
        assert "ASYNC_PROMISE" not in result
    
    def test_promise_message_clarity(self):
        """Test that promise messages are clear to users."""
        result = spawn_valor_session(
            task_description="Implement user authentication system",
            target_directory="/tmp"
        )
        
        # Should contain clear promise message
        assert "I'll work on this task in the background" in result
        assert "Implement user authentication system" in result
    
    @pytest.mark.asyncio  
    async def test_completion_message_formatting(self):
        """Test completion message formatting."""
        handler = MessageHandler(Mock(), Mock())
        mock_message = Mock()
        mock_message.reply = AsyncMock()
        
        with patch('utilities.database.update_promise_status'):
            with patch('tools.valor_delegation_tool.spawn_valor_session', return_value="Created auth.py\nAdded login function\nAll tests pass"):
                with patch('time.time', side_effect=[0, 45.5]):  # 45.5 second execution
                    await handler._execute_promise_background(
                        mock_message, 12345, 1, "Create authentication"
                    )
        
        # Check completion message format
        call_args = mock_message.reply.call_args[0][0]
        assert "✅" in call_args  # Success emoji
        assert "Task Complete!" in call_args
        assert "45.5 seconds" in call_args  # Execution time
        assert "Created auth.py" in call_args  # Result details


if __name__ == "__main__":
    pytest.main([__file__, "-v"])