"""
End-to-end tests for the promise queue system.

These tests verify the complete flow from Telegram message handling
through promise creation, task execution, and completion notification.
"""
import pytest
import asyncio
import json
import time
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from utilities.database import init_database, get_database_connection, get_promise
from utilities.promise_manager_huey import HueyPromiseManager
from integrations.telegram.handlers import TelegramHandler
from tasks.huey_config import huey


class TestPromiseE2E:
    """End-to-end tests for promise queue functionality."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test environment."""
        # Initialize database
        init_database()
        
        # Enable immediate mode for testing
        huey.immediate = True
        
        yield
        
        # Cleanup
        huey.immediate = False
        with get_database_connection() as conn:
            conn.execute("DELETE FROM promises")
            conn.execute("DELETE FROM message_queue")
            conn.commit()
    
    @pytest.mark.asyncio
    async def test_async_promise_detection_and_execution(self):
        """Test that ASYNC_PROMISE markers trigger background execution."""
        # Mock the valor agent to return an ASYNC_PROMISE response
        mock_agent_response = Mock()
        mock_agent_response.data = "ASYNC_PROMISE|I'll analyze your code in the background"
        
        with patch('agents.valor.agent.valor_agent.run') as mock_agent:
            mock_agent.return_value = mock_agent_response
            
            # Mock the delegation tool to complete the task
            with patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
                mock_spawn.return_value = "Code analysis complete: 5 issues found"
                
                # Create mock message and handler
                handler = TelegramHandler()
                mock_message = Mock()
                mock_message.id = 12345
                mock_message.text = "Analyze the authentication code"
                mock_message.from_user = Mock(username="testuser")
                mock_message.chat = Mock(id=67890, type="private")
                
                # Mock Telegram client for sending completion notification
                mock_client = Mock()
                mock_send_message = AsyncMock()
                mock_client.send_message = mock_send_message
                
                # Process message (this should create a promise)
                with patch('integrations.telegram.client.get_telegram_client') as mock_get_client:
                    mock_get_client.return_value = Mock(client=mock_client)
                    
                    # Process the message
                    await handler._process_message_text(mock_message, 67890, "Analyze the authentication code")
                
                # Verify promise was created
                with get_database_connection() as conn:
                    cursor = conn.cursor()
                    promise = cursor.execute(
                        "SELECT * FROM promises WHERE chat_id = ?",
                        (67890,)
                    ).fetchone()
                
                assert promise is not None
                assert promise[3] == "I'll analyze your code in the background"  # task_description
                assert promise[5] == "completed"  # status (immediate mode)
                
                # In immediate mode, the task executes synchronously
                # Verify the delegation tool was called
                mock_spawn.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_search_promise_execution(self):
        """Test search task execution through promises."""
        manager = HueyPromiseManager()
        
        # Mock search tool
        with patch('tools.search_tool.search_web') as mock_search:
            mock_search.return_value = "🔍 **Python tutorials**\n\nFound 3 excellent tutorials..."
            
            # Create search promise
            promise_id = manager.create_promise(
                chat_id=12345,
                message_id=67890,
                task_description="Find Python async/await tutorials",
                task_type="search",
                metadata={'query': 'Python async await tutorials'}
            )
            
            # In immediate mode, task executes synchronously
            time.sleep(0.1)
            
            # Check promise completed
            promise = get_promise(promise_id)
            assert promise['status'] == 'completed'
            assert 'Found 3 excellent tutorials' in promise['result_summary']
            
            # Verify search was called with correct query
            mock_search.assert_called_with('Python async await tutorials', max_results=3)
    
    @pytest.mark.asyncio
    async def test_missed_message_queue_processing(self):
        """Test that missed messages are queued and processed."""
        from utilities.database import queue_missed_message, get_pending_messages
        from tasks.telegram_tasks import process_missed_message
        
        # Queue a missed message
        message_id = queue_missed_message(
            chat_id=12345,
            message_text="What's the project status?",
            sender_username="testuser",
            original_timestamp=datetime.utcnow().isoformat(),
            metadata={'is_group_chat': False}
        )
        
        # Verify it's queued
        pending = get_pending_messages()
        assert len(pending) == 1
        assert pending[0]['message_text'] == "What's the project status?"
        
        # Mock agent response
        mock_response = Mock()
        mock_response.data = "Here's the current project status..."
        
        with patch('agents.valor.agent.valor_agent.run') as mock_agent:
            mock_agent.return_value = mock_response
            
            # Mock Telegram client
            mock_client = Mock()
            mock_send = AsyncMock()
            mock_client.send_message = mock_send
            
            with patch('integrations.telegram.client.get_telegram_client') as mock_get_client:
                mock_get_client.return_value = Mock(client=mock_client)
                
                # Process the missed message
                process_missed_message(message_id)
                
                # Verify message was processed
                with get_database_connection() as conn:
                    cursor = conn.cursor()
                    processed = cursor.execute(
                        "SELECT status FROM message_queue WHERE id = ?",
                        (message_id,)
                    ).fetchone()
                
                assert processed[0] == 'completed'
    
    @pytest.mark.asyncio
    async def test_promise_cancellation(self):
        """Test that promises can be cancelled before execution."""
        manager = HueyPromiseManager()
        
        # Disable immediate mode to prevent execution
        huey.immediate = False
        
        try:
            # Create a promise
            promise_id = manager.create_promise(
                chat_id=12345,
                message_id=67890,
                task_description="Long running task",
                task_type="code"
            )
            
            # Cancel it immediately
            success = manager.cancel_promise(promise_id)
            assert success is True
            
            # Verify status
            promise = get_promise(promise_id)
            assert promise['status'] == 'cancelled'
            assert 'Cancelled by user' in promise['error_message']
            
        finally:
            huey.immediate = True
    
    @pytest.mark.asyncio
    async def test_task_failure_handling(self):
        """Test that task failures are properly handled."""
        manager = HueyPromiseManager()
        
        # Mock a failing task
        with patch('tools.valor_delegation_tool.spawn_valor_session') as mock_spawn:
            mock_spawn.side_effect = Exception("Network error: Unable to reach API")
            
            # Create promise
            promise_id = manager.create_promise(
                chat_id=12345,
                message_id=67890,
                task_description="Deploy to production",
                task_type="code"
            )
            
            # In immediate mode, failure happens synchronously
            time.sleep(0.1)
            
            # Check promise failed
            promise = get_promise(promise_id)
            assert promise['status'] == 'failed'
            assert 'Network error' in promise['error_message']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])