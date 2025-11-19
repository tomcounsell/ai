"""
Comprehensive tests for the database layer.

Tests cover DatabaseManager, ConnectionPool, migrations, and all database operations.
"""

import asyncio
import json
import pytest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# Import the database modules
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utilities.database import (
    DatabaseManager, ConnectionPool, DatabaseConnection, DatabaseError,
    ConnectionPoolError, MigrationError, BackupError
)
from utilities.migrations import (
    MigrationManager, Migration, CreateInitialTables, AddUserPreferences,
    get_migration_manager
)
from config.settings import settings


class TestDatabaseConnection:
    """Test DatabaseConnection class."""
    
    @pytest.fixture
    async def mock_connection(self):
        """Create a mock aiosqlite connection."""
        connection = AsyncMock()
        return connection
    
    def test_database_connection_creation(self, mock_connection):
        """Test DatabaseConnection initialization."""
        created_at = datetime.now(timezone.utc)
        db_conn = DatabaseConnection(mock_connection, created_at)
        
        assert db_conn.connection == mock_connection
        assert db_conn.created_at == created_at
        assert db_conn.last_used == created_at
        assert not db_conn.in_use
        assert db_conn.transaction_level == 0


class TestConnectionPool:
    """Test ConnectionPool class."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        if db_path.exists():
            db_path.unlink()
    
    @pytest.fixture
    async def connection_pool(self, temp_db_path):
        """Create a connection pool for testing."""
        pool = ConnectionPool(temp_db_path, max_connections=3)
        yield pool
        await pool.close()
    
    @pytest.mark.asyncio
    async def test_connection_pool_creation(self, temp_db_path):
        """Test connection pool initialization."""
        pool = ConnectionPool(temp_db_path, max_connections=5)
        
        assert pool.db_path == temp_db_path
        assert pool.max_connections == 5
        assert len(pool._pool) == 0
        assert not pool._closed
        
        await pool.close()
    
    @pytest.mark.asyncio
    async def test_connection_acquire_and_release(self, connection_pool):
        """Test acquiring and releasing connections."""
        # Acquire a connection
        db_conn = await connection_pool.acquire()
        
        assert db_conn.in_use
        assert len(connection_pool._pool) == 1
        
        # Release the connection
        await connection_pool.release(db_conn)
        
        assert not db_conn.in_use
    
    @pytest.mark.asyncio
    async def test_connection_reuse(self, connection_pool):
        """Test connection reuse."""
        # Acquire and release a connection
        db_conn1 = await connection_pool.acquire()
        await connection_pool.release(db_conn1)
        
        # Acquire again - should reuse the same connection
        db_conn2 = await connection_pool.acquire()
        
        assert db_conn1 == db_conn2
        assert db_conn2.in_use
        
        await connection_pool.release(db_conn2)
    
    @pytest.mark.asyncio
    async def test_max_connections_limit(self, connection_pool):
        """Test that connection pool respects max connections limit."""
        connections = []
        
        # Acquire maximum connections
        for i in range(connection_pool.max_connections):
            conn = await connection_pool.acquire()
            connections.append(conn)
        
        assert len(connection_pool._pool) == connection_pool.max_connections
        
        # Try to acquire one more - should block (we'll timeout quickly)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(connection_pool.acquire(), timeout=0.1)
        
        # Release all connections
        for conn in connections:
            await connection_pool.release(conn)
    
    @pytest.mark.asyncio
    async def test_pool_close(self, connection_pool):
        """Test pool closing."""
        # Acquire some connections
        conn1 = await connection_pool.acquire()
        conn2 = await connection_pool.acquire()
        
        await connection_pool.close()
        
        assert connection_pool._closed
        assert len(connection_pool._pool) == 0
        
        # Should not be able to acquire after closing
        with pytest.raises(ConnectionPoolError):
            await connection_pool.acquire()


class TestDatabaseManager:
    """Test DatabaseManager class."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        if db_path.exists():
            db_path.unlink()
    
    @pytest.fixture
    async def db_manager(self, temp_db_path):
        """Create a database manager for testing."""
        manager = DatabaseManager(temp_db_path)
        await manager.initialize()
        yield manager
        await manager.close()
    
    @pytest.mark.asyncio
    async def test_database_manager_initialization(self, temp_db_path):
        """Test database manager initialization."""
        manager = DatabaseManager(temp_db_path)
        
        assert manager.db_path == temp_db_path
        assert not manager._initialized
        
        await manager.initialize()
        
        assert manager._initialized
        assert temp_db_path.exists()
        
        await manager.close()
    
    @pytest.mark.asyncio
    async def test_connection_context_manager(self, db_manager):
        """Test connection context manager."""
        async with db_manager.get_connection() as conn:
            assert conn is not None
            # Test a simple query
            await conn.execute("SELECT 1")
    
    @pytest.mark.asyncio
    async def test_transaction_context_manager(self, db_manager):
        """Test transaction context manager."""
        async with db_manager.transaction() as conn:
            await conn.execute("SELECT 1")
    
    @pytest.mark.asyncio
    async def test_transaction_rollback_on_error(self, db_manager):
        """Test transaction rollback on error."""
        with pytest.raises(Exception):
            async with db_manager.transaction() as conn:
                await conn.execute("SELECT 1")
                raise Exception("Test error")
    
    @pytest.mark.asyncio
    async def test_project_operations(self, db_manager):
        """Test project CRUD operations."""
        # Create project
        project_id = await db_manager.create_project(
            name="Test Project",
            path="/test/path",
            description="Test description",
            metadata={"key": "value"}
        )
        
        assert project_id is not None
        
        # Get project
        project = await db_manager.get_project(project_id)
        
        assert project is not None
        assert project['name'] == "Test Project"
        assert project['path'] == "/test/path"
        assert project['description'] == "Test description"
        assert project['metadata'] == {"key": "value"}
        assert project['status'] == 'active'
        
        # Update project
        success = await db_manager.update_project(
            project_id,
            name="Updated Project",
            status="inactive"
        )
        
        assert success
        
        updated_project = await db_manager.get_project(project_id)
        assert updated_project['name'] == "Updated Project"
        assert updated_project['status'] == "inactive"
        
        # List projects
        projects = await db_manager.list_projects()
        assert len(projects) == 1
        assert projects[0]['id'] == project_id
        
        # List projects by status
        active_projects = await db_manager.list_projects(status="active")
        assert len(active_projects) == 0
        
        inactive_projects = await db_manager.list_projects(status="inactive")
        assert len(inactive_projects) == 1
        
        # Delete project
        success = await db_manager.delete_project(project_id)
        assert success
        
        deleted_project = await db_manager.get_project(project_id)
        assert deleted_project is None
    
    @pytest.mark.asyncio
    async def test_chat_history_operations(self, db_manager):
        """Test chat history operations."""
        # Create a project first
        project_id = await db_manager.create_project("Chat Test", "/test/chat")
        session_id = "test-session-123"
        
        # Add chat messages
        message1_id = await db_manager.add_chat_message(
            project_id=project_id,
            session_id=session_id,
            role="user",
            content="Hello, AI!",
            metadata={"test": True},
            token_count=3
        )
        
        message2_id = await db_manager.add_chat_message(
            project_id=project_id,
            session_id=session_id,
            role="assistant",
            content="Hello! How can I help you?",
            token_count=7
        )
        
        # Get chat history
        history = await db_manager.get_chat_history(session_id, project_id)
        
        assert len(history) == 2
        assert history[0]['role'] == "user"
        assert history[0]['content'] == "Hello, AI!"
        assert history[0]['token_count'] == 3
        assert history[0]['metadata'] == {"test": True}
        
        assert history[1]['role'] == "assistant"
        assert history[1]['content'] == "Hello! How can I help you?"
        assert history[1]['token_count'] == 7
        
        # Test pagination
        limited_history = await db_manager.get_chat_history(session_id, project_id, limit=1)
        assert len(limited_history) == 1
        
        offset_history = await db_manager.get_chat_history(session_id, project_id, limit=1, offset=1)
        assert len(offset_history) == 1
        assert offset_history[0]['id'] != limited_history[0]['id']
    
    @pytest.mark.asyncio
    async def test_promise_operations(self, db_manager):
        """Test promise CRUD operations."""
        # Create a project
        project_id = await db_manager.create_project("Promise Test", "/test/promise")
        
        # Create promise
        promise_id = await db_manager.create_promise(
            project_id=project_id,
            title="Test Promise",
            description="Test promise description",
            priority="high",
            assigned_to="ai-agent",
            completion_criteria="Must pass all tests",
            metadata={"category": "feature"}
        )
        
        assert promise_id is not None
        
        # Get promise
        promise = await db_manager.get_promise(promise_id)
        
        assert promise is not None
        assert promise['title'] == "Test Promise"
        assert promise['priority'] == "high"
        assert promise['status'] == "pending"
        assert promise['metadata'] == {"category": "feature"}
        
        # Update promise status
        success = await db_manager.update_promise(
            promise_id,
            status="completed",
            metadata={"category": "feature", "completed_by": "test"}
        )
        
        assert success
        
        updated_promise = await db_manager.get_promise(promise_id)
        assert updated_promise['status'] == "completed"
        assert updated_promise['completed_at'] is not None
        assert updated_promise['metadata']['completed_by'] == "test"
        
        # List promises
        promises = await db_manager.list_promises()
        assert len(promises) == 1
        
        # List by project
        project_promises = await db_manager.list_promises(project_id=project_id)
        assert len(project_promises) == 1
        
        # List by status
        completed_promises = await db_manager.list_promises(status="completed")
        assert len(completed_promises) == 1
        
        pending_promises = await db_manager.list_promises(status="pending")
        assert len(pending_promises) == 0
    
    @pytest.mark.asyncio
    async def test_workspace_operations(self, db_manager):
        """Test workspace CRUD operations."""
        # Create workspace
        workspace_id = await db_manager.create_workspace(
            name="Test Workspace",
            description="Test workspace description",
            config={"theme": "dark", "auto_save": True}
        )
        
        assert workspace_id is not None
        
        # Get workspace
        workspace = await db_manager.get_workspace(workspace_id)
        
        assert workspace is not None
        assert workspace['name'] == "Test Workspace"
        assert workspace['description'] == "Test workspace description"
        assert workspace['config'] == {"theme": "dark", "auto_save": True}
        assert workspace['active'] is True
        
        # Update workspace
        success = await db_manager.update_workspace(
            workspace_id,
            config={"theme": "light", "auto_save": False},
            active=False
        )
        
        assert success
        
        updated_workspace = await db_manager.get_workspace(workspace_id)
        assert updated_workspace['config']['theme'] == "light"
        assert updated_workspace['active'] is False
        
        # List workspaces
        all_workspaces = await db_manager.list_workspaces(active_only=False)
        assert len(all_workspaces) == 1
        
        active_workspaces = await db_manager.list_workspaces(active_only=True)
        assert len(active_workspaces) == 0
        
        # Delete workspace
        success = await db_manager.delete_workspace(workspace_id)
        assert success
        
        deleted_workspace = await db_manager.get_workspace(workspace_id)
        assert deleted_workspace is None
    
    @pytest.mark.asyncio
    async def test_tool_metrics_operations(self, db_manager):
        """Test tool metrics operations."""
        # Record metrics
        metric1_id = await db_manager.record_tool_metric(
            tool_name="test_tool",
            operation="test_op",
            execution_time_ms=100,
            success=True,
            metadata={"version": "1.0"}
        )
        
        metric2_id = await db_manager.record_tool_metric(
            tool_name="test_tool",
            operation="test_op2",
            execution_time_ms=200,
            success=False,
            error_message="Test error",
            metadata={"version": "1.0"}
        )
        
        # Get all metrics
        all_metrics = await db_manager.get_tool_metrics()
        assert len(all_metrics) == 2
        
        # Get by tool name
        tool_metrics = await db_manager.get_tool_metrics(tool_name="test_tool")
        assert len(tool_metrics) == 2
        
        # Get by success status
        successful_metrics = await db_manager.get_tool_metrics(success=True)
        assert len(successful_metrics) == 1
        assert successful_metrics[0]['error_message'] is None
        
        failed_metrics = await db_manager.get_tool_metrics(success=False)
        assert len(failed_metrics) == 1
        assert failed_metrics[0]['error_message'] == "Test error"
        
        # Get by operation
        op_metrics = await db_manager.get_tool_metrics(operation="test_op")
        assert len(op_metrics) == 1
    
    @pytest.mark.asyncio
    async def test_backup_operations(self, db_manager):
        """Test backup and restore operations."""
        # Create some data
        project_id = await db_manager.create_project("Backup Test", "/test/backup")
        
        # Create backup
        backup_path = await db_manager.create_backup()
        
        assert backup_path.exists()
        assert backup_path.stat().st_size > 0
        
        # Modify data
        await db_manager.update_project(project_id, name="Modified Project")
        
        # Verify modification
        modified_project = await db_manager.get_project(project_id)
        assert modified_project['name'] == "Modified Project"
        
        # Restore backup
        await db_manager.restore_backup(backup_path)
        
        # Verify restoration
        restored_project = await db_manager.get_project(project_id)
        assert restored_project['name'] == "Backup Test"
        
        # Clean up backup file
        backup_path.unlink()
    
    @pytest.mark.asyncio
    async def test_database_info(self, db_manager):
        """Test database info retrieval."""
        # Create some test data
        await db_manager.create_project("Info Test", "/test/info")
        await db_manager.create_workspace("Test Workspace")
        
        info = await db_manager.get_database_info()
        
        assert info['database_path'] == str(db_manager.db_path)
        assert info['size_bytes'] > 0
        assert info['size_mb'] > 0
        assert 'tables' in info
        assert info['tables']['projects'] >= 1
        assert info['tables']['workspaces'] >= 1
        assert info['total_records'] >= 2
    
    @pytest.mark.asyncio
    async def test_utility_operations(self, db_manager):
        """Test database utility operations."""
        # Test vacuum
        await db_manager.vacuum()
        
        # Test analyze
        await db_manager.analyze()
        
        # These operations should complete without error


class TestMigrations:
    """Test migration system."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        if db_path.exists():
            db_path.unlink()
    
    @pytest.fixture
    async def migration_manager(self, temp_db_path):
        """Create a migration manager for testing."""
        db_manager = DatabaseManager(temp_db_path)
        await db_manager.initialize()
        manager = MigrationManager(db_manager)
        yield manager
        await db_manager.close()
    
    def test_migration_creation(self):
        """Test migration class creation."""
        migration = CreateInitialTables()
        
        assert migration.version == "001"
        assert "initial" in migration.description.lower()
        assert migration.applied_at is None
    
    @pytest.mark.asyncio
    async def test_migration_manager_initialization(self, migration_manager):
        """Test migration manager initialization."""
        assert len(migration_manager.migrations) > 0
        assert migration_manager.migrations[0].version == "001"
        
        # Check migrations are sorted by version
        versions = [m.version for m in migration_manager.migrations]
        assert versions == sorted(versions)
    
    @pytest.mark.asyncio
    async def test_migration_status(self, migration_manager):
        """Test migration status retrieval."""
        status = await migration_manager.get_migration_status()
        
        assert 'current_version' in status
        assert 'latest_available_version' in status
        assert 'applied_migrations' in status
        assert 'pending_migrations' in status
        assert 'total_migrations' in status
        assert 'applied_list' in status
        assert 'pending_list' in status
        
        # Initially, no migrations should be applied
        assert status['current_version'] is None
        assert status['applied_migrations'] == 0
        assert status['pending_migrations'] == len(migration_manager.migrations)
    
    @pytest.mark.asyncio
    async def test_apply_migrations(self, migration_manager):
        """Test applying migrations."""
        # Get first migration
        first_migration = migration_manager.migrations[0]
        
        # Apply it
        await migration_manager.apply_migration(first_migration)
        
        # Check status
        status = await migration_manager.get_migration_status()
        assert status['applied_migrations'] == 1
        assert status['current_version'] == first_migration.version
        
        # Check applied migrations list
        applied = await migration_manager.get_applied_migrations()
        assert len(applied) == 1
        assert applied[0][0] == first_migration.version
    
    @pytest.mark.asyncio
    async def test_migrate_to_latest(self, migration_manager):
        """Test migrating to latest version."""
        await migration_manager.migrate_to_latest()
        
        status = await migration_manager.get_migration_status()
        assert status['pending_migrations'] == 0
        assert status['applied_migrations'] == len(migration_manager.migrations)
        assert status['current_version'] == migration_manager.migrations[-1].version
    
    @pytest.mark.asyncio
    async def test_migration_validation(self, migration_manager):
        """Test migration validation."""
        validation = await migration_manager.validate_migrations()
        
        assert 'valid' in validation
        assert 'issues' in validation
        assert validation['valid'] is True  # Should be valid for our test migrations
        assert len(validation['issues']) == 0


class TestIntegration:
    """Integration tests for the complete database system."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        if db_path.exists():
            db_path.unlink()
    
    @pytest.mark.asyncio
    async def test_complete_workflow(self, temp_db_path):
        """Test a complete workflow from initialization to data operations."""
        # Initialize database manager
        db_manager = DatabaseManager(temp_db_path)
        await db_manager.initialize()
        
        try:
            # Apply migrations
            migration_manager = MigrationManager(db_manager)
            await migration_manager.migrate_to_latest()
            
            # Create a project
            project_id = await db_manager.create_project(
                name="Integration Test Project",
                path="/integration/test"
            )
            
            # Create a workspace
            workspace_id = await db_manager.create_workspace(
                name="Integration Test Workspace",
                config={"test": True}
            )
            
            # Add chat history
            session_id = "integration-session"
            await db_manager.add_chat_message(
                project_id, session_id, "user", "Test message"
            )
            
            # Create a promise
            promise_id = await db_manager.create_promise(
                project_id, "Integration test promise"
            )
            
            # Record tool metrics
            await db_manager.record_tool_metric(
                "integration_tool", "test", 100, True
            )
            
            # Verify all data exists
            project = await db_manager.get_project(project_id)
            workspace = await db_manager.get_workspace(workspace_id)
            history = await db_manager.get_chat_history(session_id)
            promise = await db_manager.get_promise(promise_id)
            metrics = await db_manager.get_tool_metrics()
            
            assert project is not None
            assert workspace is not None
            assert len(history) == 1
            assert promise is not None
            assert len(metrics) == 1
            
            # Test backup and restore
            backup_path = await db_manager.create_backup()
            
            # Modify data
            await db_manager.update_project(project_id, name="Modified Name")
            
            # Restore backup
            await db_manager.restore_backup(backup_path)
            
            # Verify restoration
            restored_project = await db_manager.get_project(project_id)
            assert restored_project['name'] == "Integration Test Project"
            
            # Clean up
            backup_path.unlink()
            
        finally:
            await db_manager.close()
    
    @pytest.mark.asyncio
    async def test_concurrent_operations(self, temp_db_path):
        """Test concurrent database operations."""
        db_manager = DatabaseManager(temp_db_path)
        await db_manager.initialize()
        
        try:
            # Create multiple projects concurrently
            tasks = []
            for i in range(10):
                task = db_manager.create_project(
                    f"Concurrent Project {i}",
                    f"/concurrent/{i}"
                )
                tasks.append(task)
            
            project_ids = await asyncio.gather(*tasks)
            assert len(project_ids) == 10
            assert len(set(project_ids)) == 10  # All should be unique
            
            # Verify all projects were created
            projects = await db_manager.list_projects()
            assert len(projects) == 10
            
        finally:
            await db_manager.close()


# Test utilities
@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])