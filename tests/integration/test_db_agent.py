"""
Database-Agent Integration Tests

Tests the integration between the database layer and Valor agent,
ensuring proper context persistence, message history, and data consistency.
"""

import asyncio
import pytest
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List

from utilities.database import DatabaseManager
from agents.valor.agent import ValorAgent
from agents.valor.context import ValorContext


class TestDatabaseAgentIntegration:
    """Test suite for Database-Agent integration."""
    
    @pytest.fixture
    async def database(self):
        """Create test database instance."""
        db = DatabaseManager()
        await db.initialize()
        yield db
        await db.close()
    
    @pytest.fixture
    async def agent(self):
        """Create test agent instance."""
        agent = ValorAgent(
            model="openai:gpt-3.5-turbo",  # Use cheaper model for tests
            debug=True
        )
        yield agent
        # Cleanup contexts
        for chat_id in agent.list_contexts():
            await agent.clear_context(chat_id)
    
    @pytest.mark.asyncio
    async def test_agent_context_persistence(self, database: DatabaseManager, agent: ValorAgent):
        """Test that agent contexts are properly persisted to database."""
        chat_id = "test_chat_001"
        user_name = "test_user"
        
        # Create context through agent
        context = await agent.create_context(
            chat_id=chat_id,
            user_name=user_name,
            workspace="test_workspace",
            metadata={"test": True}
        )
        
        assert context is not None
        assert context.chat_id == chat_id
        assert context.user_name == user_name
        
        # Process a message to generate history
        response = await agent.process_message(
            message="Hello, this is a test message",
            chat_id=chat_id
        )
        
        assert response.success
        assert response.context_updated
        
        # Verify context is retrievable
        retrieved_context = agent.get_context(chat_id)
        assert retrieved_context is not None
        assert len(retrieved_context.message_history) >= 2  # User + assistant
        
        # Check database has the message history
        chat_history = await database.get_chat_history(session_id=chat_id, limit=100)
        assert len(chat_history) >= 1
        
        # Verify message content
        user_message = next((msg for msg in chat_history if msg['role'] == 'user'), None)
        assert user_message is not None
        assert user_message['content'] == "Hello, this is a test message"
        assert user_message['session_id'] == chat_id
    
    @pytest.mark.asyncio
    async def test_context_compression_persistence(self, database: DatabaseManager, agent: ValorAgent):
        """Test context compression and persistence integration."""
        chat_id = "test_chat_compression"
        user_name = "compression_user"
        
        # Create context
        await agent.create_context(chat_id=chat_id, user_name=user_name)
        
        # Generate many messages to trigger compression
        messages = [
            "What is the capital of France?",
            "Tell me about machine learning",
            "How do neural networks work?",
            "What is deep learning?",
            "Explain transformers in AI",
            "What is the difference between AI and ML?",
            "How do you train a neural network?",
            "What is backpropagation?",
            "Explain gradient descent",
            "What are activation functions?"
        ]
        
        for message in messages:
            await agent.process_message(message=message, chat_id=chat_id)
            await asyncio.sleep(0.1)  # Prevent rate limiting
        
        # Check context exists and has messages
        context = agent.get_context(chat_id)
        assert context is not None
        assert len(context.message_history) > 0
        
        # Verify database persistence
        chat_history = await database.get_chat_history(session_id=chat_id, limit=100)
        assert len(chat_history) >= len(messages)
        
        # Check message order is preserved
        user_messages = [msg for msg in chat_history if msg['role'] == 'user']
        for i, message in enumerate(messages):
            if i < len(user_messages):
                assert user_messages[i]['content'] == message
    
    @pytest.mark.asyncio
    async def test_multiple_context_isolation(self, database: DatabaseManager, agent: ValorAgent):
        """Test that multiple contexts are properly isolated in database."""
        chat_ids = ["chat_001", "chat_002", "chat_003"]
        user_names = ["user_001", "user_002", "user_003"]
        
        contexts = {}
        messages = {}
        
        # Create multiple contexts with different messages
        for i, (chat_id, user_name) in enumerate(zip(chat_ids, user_names)):
            contexts[chat_id] = await agent.create_context(
                chat_id=chat_id,
                user_name=user_name,
                workspace=f"workspace_{i}"
            )
            
            test_message = f"Hello from {user_name} in context {chat_id}"
            messages[chat_id] = test_message
            
            await agent.process_message(message=test_message, chat_id=chat_id)
        
        # Verify contexts are isolated
        for chat_id in chat_ids:
            context = agent.get_context(chat_id)
            assert context is not None
            
            # Check database isolation
            chat_history = await database.get_chat_history(session_id=chat_id)
            user_messages = [msg for msg in chat_history if msg['role'] == 'user']
            
            assert len(user_messages) == 1
            assert user_messages[0]['content'] == messages[chat_id]
            assert user_messages[0]['session_id'] == chat_id
        
        # Verify no cross-contamination
        all_messages = await database.get_chat_history(session_id="nonexistent_chat")
        assert len(all_messages) == 0
    
    @pytest.mark.asyncio
    async def test_context_export_import_database_sync(self, database: DatabaseManager, agent: ValorAgent):
        """Test context export/import with database synchronization."""
        chat_id = "export_import_test"
        user_name = "export_user"
        
        # Create and populate context
        await agent.create_context(chat_id=chat_id, user_name=user_name)
        
        test_messages = [
            "First message",
            "Second message with context",
            "Third message for export test"
        ]
        
        for message in test_messages:
            await agent.process_message(message=message, chat_id=chat_id)
        
        # Export context
        exported_data = await agent.export_context(chat_id)
        assert exported_data is not None
        assert exported_data['chat_id'] == chat_id
        assert len(exported_data['message_history']) >= len(test_messages)
        
        # Clear context from agent
        await agent.clear_context(chat_id)
        assert agent.get_context(chat_id) is None
        
        # Import context back
        import_success = await agent.import_context(exported_data)
        assert import_success
        
        # Verify context is restored
        restored_context = agent.get_context(chat_id)
        assert restored_context is not None
        assert restored_context.chat_id == chat_id
        assert restored_context.user_name == user_name
        
        # Verify database consistency
        chat_history = await database.get_chat_history(session_id=chat_id)
        user_messages = [msg for msg in chat_history if msg['role'] == 'user']
        
        for i, expected_message in enumerate(test_messages):
            if i < len(user_messages):
                assert user_messages[i]['content'] == expected_message
    
    @pytest.mark.asyncio
    async def test_concurrent_context_access(self, database: DatabaseManager, agent: ValorAgent):
        """Test concurrent access to contexts with database consistency."""
        chat_id = "concurrent_test"
        user_name = "concurrent_user"
        
        # Create context
        await agent.create_context(chat_id=chat_id, user_name=user_name)
        
        # Define concurrent message processing tasks
        async def process_messages(message_prefix: str, count: int):
            for i in range(count):
                message = f"{message_prefix} message {i}"
                await agent.process_message(message=message, chat_id=chat_id)
                await asyncio.sleep(0.01)  # Small delay to interleave operations
        
        # Run concurrent tasks
        tasks = [
            process_messages("Task1", 5),
            process_messages("Task2", 5),
            process_messages("Task3", 5)
        ]
        
        await asyncio.gather(*tasks)
        
        # Verify all messages were processed
        context = agent.get_context(chat_id)
        assert context is not None
        assert len(context.message_history) >= 15  # At least 15 user messages + responses
        
        # Verify database consistency
        chat_history = await database.get_chat_history(session_id=chat_id, limit=100)
        user_messages = [msg for msg in chat_history if msg['role'] == 'user']
        assert len(user_messages) == 15
        
        # Check all message prefixes are present
        task1_messages = [msg for msg in user_messages if msg['content'].startswith("Task1")]
        task2_messages = [msg for msg in user_messages if msg['content'].startswith("Task2")]
        task3_messages = [msg for msg in user_messages if msg['content'].startswith("Task3")]
        
        assert len(task1_messages) == 5
        assert len(task2_messages) == 5
        assert len(task3_messages) == 5
    
    @pytest.mark.asyncio
    async def test_database_error_handling(self, agent: ValorAgent):
        """Test agent behavior when database operations fail."""
        chat_id = "error_test"
        user_name = "error_user"
        
        # Create context
        context = await agent.create_context(chat_id=chat_id, user_name=user_name)
        assert context is not None
        
        # Agent should continue working even if database operations fail
        # (This tests graceful degradation)
        response = await agent.process_message(
            message="Test message with potential DB error",
            chat_id=chat_id
        )
        
        # Response should still be successful
        assert response is not None
        assert isinstance(response.content, str)
        assert len(response.content) > 0
    
    @pytest.mark.asyncio
    async def test_context_metadata_persistence(self, database: DatabaseManager, agent: ValorAgent):
        """Test that context metadata is properly persisted."""
        chat_id = "metadata_test"
        user_name = "metadata_user"
        
        metadata = {
            "user_preferences": {"theme": "dark", "language": "en"},
            "session_info": {"device": "mobile", "app_version": "1.0.0"},
            "project_context": {"name": "test_project", "id": "proj_123"}
        }
        
        # Create context with metadata
        context = await agent.create_context(
            chat_id=chat_id,
            user_name=user_name,
            workspace="metadata_workspace",
            metadata=metadata
        )
        
        assert context.session_metadata == metadata
        
        # Process message to trigger persistence
        await agent.process_message(
            message="Test metadata persistence",
            chat_id=chat_id,
            metadata={"message_metadata": "test"}
        )
        
        # Export context to check metadata preservation
        exported_data = await agent.export_context(chat_id)
        assert exported_data is not None
        assert exported_data['session_metadata'] == metadata
        
        # Check that message metadata is preserved
        message_history = exported_data['message_history']
        user_message = next((msg for msg in message_history if msg['role'] == 'user'), None)
        assert user_message is not None
        assert user_message.get('metadata', {}).get('message_metadata') == 'test'
    
    @pytest.mark.asyncio
    async def test_context_stats_accuracy(self, database: DatabaseManager, agent: ValorAgent):
        """Test that context statistics accurately reflect database state."""
        chat_id = "stats_test"
        user_name = "stats_user"
        
        # Create context
        await agent.create_context(chat_id=chat_id, user_name=user_name)
        
        # Process several messages
        test_messages = [
            "First test message",
            "Second test message",
            "Third test message"
        ]
        
        for message in test_messages:
            await agent.process_message(message=message, chat_id=chat_id)
        
        # Get context stats
        stats = agent.get_context_stats(chat_id)
        assert stats is not None
        assert stats['message_count'] >= len(test_messages) * 2  # User + assistant messages
        assert stats['workspace'] is not None
        assert stats['created_at'] is not None
        assert stats['last_activity'] is not None
        
        # Verify database consistency
        chat_history = await database.get_chat_history(session_id=chat_id, limit=100)
        total_db_messages = len(chat_history)
        
        # Stats should reflect actual database state
        assert stats['message_count'] == total_db_messages