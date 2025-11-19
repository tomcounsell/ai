"""
Database Management System for AI Rebuild

This module provides comprehensive database management using SQLite with async support,
connection pooling, thread-safe operations, and automated backup/restore utilities.
"""

import asyncio
import json
import logging
import shutil
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple
from uuid import uuid4

import aiosqlite
from pydantic import BaseModel, Field

from config.settings import settings

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


class ConnectionPoolError(DatabaseError):
    """Exception for connection pool operations."""
    pass


class MigrationError(DatabaseError):
    """Exception for migration operations."""
    pass


class BackupError(DatabaseError):
    """Exception for backup operations."""
    pass


class DatabaseConnection:
    """Represents a database connection with metadata."""
    
    def __init__(self, connection: aiosqlite.Connection, created_at: datetime):
        self.connection = connection
        self.created_at = created_at
        self.last_used = created_at
        self.in_use = False
        self.transaction_level = 0


class ConnectionPool:
    """Thread-safe connection pool for SQLite database."""
    
    def __init__(self, db_path: Path, max_connections: int = 20):
        self.db_path = db_path
        self.max_connections = max_connections
        self._pool: List[DatabaseConnection] = []
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_connections)
        self._closed = False
        
    async def _create_connection(self) -> DatabaseConnection:
        """Create a new database connection with optimal settings."""
        connection = await aiosqlite.connect(
            self.db_path,
            timeout=30.0,
            check_same_thread=False
        )
        
        # Enable WAL mode for concurrent access
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA synchronous=NORMAL")
        await connection.execute("PRAGMA cache_size=10000")
        await connection.execute("PRAGMA temp_store=memory")
        await connection.execute("PRAGMA mmap_size=268435456")  # 256MB
        await connection.execute("PRAGMA optimize")
        
        return DatabaseConnection(connection, datetime.now(timezone.utc))
    
    async def acquire(self) -> DatabaseConnection:
        """Acquire a connection from the pool."""
        if self._closed:
            raise ConnectionPoolError("Connection pool is closed")
            
        await self._semaphore.acquire()
        
        try:
            async with self._lock:
                # Try to reuse an existing connection
                for db_conn in self._pool:
                    if not db_conn.in_use:
                        db_conn.in_use = True
                        db_conn.last_used = datetime.now(timezone.utc)
                        return db_conn
                
                # Create a new connection if pool not full
                if len(self._pool) < self.max_connections:
                    db_conn = await self._create_connection()
                    db_conn.in_use = True
                    self._pool.append(db_conn)
                    return db_conn
                
                # This shouldn't happen due to semaphore, but just in case
                raise ConnectionPoolError("No connections available")
                
        except Exception:
            self._semaphore.release()
            raise
    
    async def release(self, db_conn: DatabaseConnection) -> None:
        """Release a connection back to the pool."""
        async with self._lock:
            db_conn.in_use = False
            db_conn.last_used = datetime.now(timezone.utc)
        
        self._semaphore.release()
    
    async def close(self) -> None:
        """Close all connections in the pool."""
        async with self._lock:
            self._closed = True
            for db_conn in self._pool:
                try:
                    await db_conn.connection.close()
                except Exception as e:
                    logger.error(f"Error closing connection: {e}")
            self._pool.clear()


class DatabaseManager:
    """
    Thread-safe database manager with connection pooling and comprehensive utilities.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.database.path
        self.pool_size = settings.database.pool_size
        self._pool: Optional[ConnectionPool] = None
        self._init_lock = threading.Lock()
        self._initialized = False
        
        # Ensure database directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    @property
    def pool(self) -> ConnectionPool:
        """Get or create the connection pool."""
        if self._pool is None:
            with self._init_lock:
                if self._pool is None:
                    self._pool = ConnectionPool(self.db_path, self.pool_size)
        return self._pool
    
    @asynccontextmanager
    async def get_connection(self):
        """Get a database connection from the pool."""
        db_conn = await self.pool.acquire()
        try:
            yield db_conn.connection
        finally:
            await self.pool.release(db_conn)
    
    @asynccontextmanager
    async def transaction(self):
        """Execute operations within a database transaction."""
        async with self.get_connection() as conn:
            try:
                await conn.execute("BEGIN")
                yield conn
                await conn.execute("COMMIT")
            except Exception as e:
                await conn.execute("ROLLBACK")
                logger.error(f"Transaction failed, rolled back: {e}")
                raise
    
    async def initialize(self) -> None:
        """Initialize the database with all required tables and indexes."""
        if self._initialized:
            return
            
        async with self.transaction() as conn:
            # Create all tables
            await self._create_projects_table(conn)
            await self._create_chat_history_table(conn)
            await self._create_promises_table(conn)
            await self._create_workspaces_table(conn)
            await self._create_tool_metrics_table(conn)
            await self._create_migrations_table(conn)
            
            # Create all indexes
            await self._create_indexes(conn)
            
            logger.info("Database initialized successfully")
        
        self._initialized = True
    
    async def _create_projects_table(self, conn: aiosqlite.Connection) -> None:
        """Create the projects table."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                path TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                metadata JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(path)
            )
        """)
    
    async def _create_chat_history_table(self, conn: aiosqlite.Connection) -> None:
        """Create the chat history table."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                metadata JSON DEFAULT '{}',
                token_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
            )
        """)
    
    async def _create_promises_table(self, conn: aiosqlite.Connection) -> None:
        """Create the promises table for AI commitments and task tracking."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS promises (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'cancelled')),
                priority TEXT DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
                assigned_to TEXT,
                due_date TIMESTAMP,
                completion_criteria TEXT,
                metadata JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
            )
        """)
    
    async def _create_workspaces_table(self, conn: aiosqlite.Connection) -> None:
        """Create the workspaces table."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                config JSON DEFAULT '{}',
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name)
            )
        """)
    
    async def _create_tool_metrics_table(self, conn: aiosqlite.Connection) -> None:
        """Create the tool metrics table for performance tracking."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_metrics (
                id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                operation TEXT NOT NULL,
                execution_time_ms INTEGER NOT NULL,
                success BOOLEAN NOT NULL,
                error_message TEXT,
                metadata JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    async def _create_migrations_table(self, conn: aiosqlite.Connection) -> None:
        """Create the migrations table for schema versioning."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL UNIQUE,
                description TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    async def _create_indexes(self, conn: aiosqlite.Connection) -> None:
        """Create all performance indexes."""
        indexes = [
            # Projects indexes
            "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects (status)",
            "CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects (updated_at)",
            
            # Chat history indexes
            "CREATE INDEX IF NOT EXISTS idx_chat_history_project_id ON chat_history (project_id)",
            "CREATE INDEX IF NOT EXISTS idx_chat_history_session_id ON chat_history (session_id)",
            "CREATE INDEX IF NOT EXISTS idx_chat_history_role ON chat_history (role)",
            "CREATE INDEX IF NOT EXISTS idx_chat_history_created_at ON chat_history (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_chat_history_project_session ON chat_history (project_id, session_id)",
            
            # Promises indexes
            "CREATE INDEX IF NOT EXISTS idx_promises_project_id ON promises (project_id)",
            "CREATE INDEX IF NOT EXISTS idx_promises_status ON promises (status)",
            "CREATE INDEX IF NOT EXISTS idx_promises_priority ON promises (priority)",
            "CREATE INDEX IF NOT EXISTS idx_promises_assigned_to ON promises (assigned_to)",
            "CREATE INDEX IF NOT EXISTS idx_promises_due_date ON promises (due_date)",
            "CREATE INDEX IF NOT EXISTS idx_promises_created_at ON promises (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_promises_updated_at ON promises (updated_at)",
            
            # Workspaces indexes
            "CREATE INDEX IF NOT EXISTS idx_workspaces_active ON workspaces (active)",
            "CREATE INDEX IF NOT EXISTS idx_workspaces_created_at ON workspaces (created_at)",
            
            # Tool metrics indexes
            "CREATE INDEX IF NOT EXISTS idx_tool_metrics_tool_name ON tool_metrics (tool_name)",
            "CREATE INDEX IF NOT EXISTS idx_tool_metrics_operation ON tool_metrics (operation)",
            "CREATE INDEX IF NOT EXISTS idx_tool_metrics_success ON tool_metrics (success)",
            "CREATE INDEX IF NOT EXISTS idx_tool_metrics_created_at ON tool_metrics (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_tool_metrics_tool_operation ON tool_metrics (tool_name, operation)",
            
            # Migrations indexes
            "CREATE INDEX IF NOT EXISTS idx_migrations_version ON migrations (version)",
            "CREATE INDEX IF NOT EXISTS idx_migrations_applied_at ON migrations (applied_at)",
        ]
        
        for index_sql in indexes:
            await conn.execute(index_sql)
    
    # Project operations
    async def create_project(self, name: str, path: str, description: str = None, 
                           metadata: Dict[str, Any] = None) -> str:
        """Create a new project."""
        project_id = str(uuid4())
        metadata = metadata or {}
        
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO projects (id, name, description, path, metadata)
                VALUES (?, ?, ?, ?, ?)
            """, (project_id, name, description, path, json.dumps(metadata)))
        
        logger.info(f"Created project: {name} ({project_id})")
        return project_id
    
    async def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get a project by ID."""
        async with self.get_connection() as conn:
            async with conn.execute("""
                SELECT id, name, description, path, status, metadata, created_at, updated_at
                FROM projects WHERE id = ?
            """, (project_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'name': row[1],
                        'description': row[2],
                        'path': row[3],
                        'status': row[4],
                        'metadata': json.loads(row[5]),
                        'created_at': row[6],
                        'updated_at': row[7]
                    }
                return None
    
    async def list_projects(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all projects, optionally filtered by status."""
        query = "SELECT id, name, description, path, status, metadata, created_at, updated_at FROM projects"
        params = []
        
        if status:
            query += " WHERE status = ?"
            params.append(status)
        
        query += " ORDER BY updated_at DESC"
        
        async with self.get_connection() as conn:
            async with conn.execute(query, params) as cursor:
                projects = []
                async for row in cursor:
                    projects.append({
                        'id': row[0],
                        'name': row[1],
                        'description': row[2],
                        'path': row[3],
                        'status': row[4],
                        'metadata': json.loads(row[5]),
                        'created_at': row[6],
                        'updated_at': row[7]
                    })
                return projects
    
    async def update_project(self, project_id: str, **kwargs) -> bool:
        """Update a project."""
        allowed_fields = {'name', 'description', 'status', 'metadata'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not updates:
            return False
        
        if 'metadata' in updates:
            updates['metadata'] = json.dumps(updates['metadata'])
        
        set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
        query = f"UPDATE projects SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        
        async with self.transaction() as conn:
            cursor = await conn.execute(query, list(updates.values()) + [project_id])
            return cursor.rowcount > 0
    
    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and all related data."""
        async with self.transaction() as conn:
            cursor = await conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cursor.rowcount > 0
    
    # Chat history operations
    async def add_chat_message(self, project_id: Optional[str], session_id: str, 
                              role: str, content: str, metadata: Dict[str, Any] = None,
                              token_count: int = 0) -> str:
        """Add a chat message to history."""
        message_id = str(uuid4())
        metadata = metadata or {}
        
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO chat_history (id, project_id, session_id, role, content, metadata, token_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (message_id, project_id, session_id, role, content, json.dumps(metadata), token_count))
        
        return message_id
    
    async def get_chat_history(self, session_id: str, project_id: Optional[str] = None,
                              limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get chat history for a session."""
        query = """
            SELECT id, project_id, session_id, role, content, metadata, token_count, created_at
            FROM chat_history
            WHERE session_id = ?
        """
        params = [session_id]
        
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        
        query += " ORDER BY created_at ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        async with self.get_connection() as conn:
            async with conn.execute(query, params) as cursor:
                messages = []
                async for row in cursor:
                    messages.append({
                        'id': row[0],
                        'project_id': row[1],
                        'session_id': row[2],
                        'role': row[3],
                        'content': row[4],
                        'metadata': json.loads(row[5]),
                        'token_count': row[6],
                        'created_at': row[7]
                    })
                return messages
    
    # Promise operations
    async def create_promise(self, project_id: Optional[str], title: str, 
                           description: str = None, priority: str = 'medium',
                           assigned_to: str = None, due_date: datetime = None,
                           completion_criteria: str = None,
                           metadata: Dict[str, Any] = None) -> str:
        """Create a new promise/commitment."""
        promise_id = str(uuid4())
        metadata = metadata or {}
        
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO promises (id, project_id, title, description, priority, 
                                    assigned_to, due_date, completion_criteria, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (promise_id, project_id, title, description, priority, assigned_to,
                  due_date, completion_criteria, json.dumps(metadata)))
        
        logger.info(f"Created promise: {title} ({promise_id})")
        return promise_id
    
    async def get_promise(self, promise_id: str) -> Optional[Dict[str, Any]]:
        """Get a promise by ID."""
        async with self.get_connection() as conn:
            async with conn.execute("""
                SELECT id, project_id, title, description, status, priority, assigned_to,
                       due_date, completion_criteria, metadata, created_at, updated_at, completed_at
                FROM promises WHERE id = ?
            """, (promise_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'project_id': row[1],
                        'title': row[2],
                        'description': row[3],
                        'status': row[4],
                        'priority': row[5],
                        'assigned_to': row[6],
                        'due_date': row[7],
                        'completion_criteria': row[8],
                        'metadata': json.loads(row[9]),
                        'created_at': row[10],
                        'updated_at': row[11],
                        'completed_at': row[12]
                    }
                return None
    
    async def list_promises(self, project_id: Optional[str] = None, 
                           status: Optional[str] = None,
                           assigned_to: Optional[str] = None) -> List[Dict[str, Any]]:
        """List promises with optional filters."""
        query = """
            SELECT id, project_id, title, description, status, priority, assigned_to,
                   due_date, completion_criteria, metadata, created_at, updated_at, completed_at
            FROM promises
        """
        params = []
        conditions = []
        
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        
        if status:
            conditions.append("status = ?")
            params.append(status)
        
        if assigned_to:
            conditions.append("assigned_to = ?")
            params.append(assigned_to)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY priority DESC, created_at DESC"
        
        async with self.get_connection() as conn:
            async with conn.execute(query, params) as cursor:
                promises = []
                async for row in cursor:
                    promises.append({
                        'id': row[0],
                        'project_id': row[1],
                        'title': row[2],
                        'description': row[3],
                        'status': row[4],
                        'priority': row[5],
                        'assigned_to': row[6],
                        'due_date': row[7],
                        'completion_criteria': row[8],
                        'metadata': json.loads(row[9]),
                        'created_at': row[10],
                        'updated_at': row[11],
                        'completed_at': row[12]
                    })
                return promises
    
    async def update_promise(self, promise_id: str, **kwargs) -> bool:
        """Update a promise."""
        allowed_fields = {'title', 'description', 'status', 'priority', 'assigned_to',
                         'due_date', 'completion_criteria', 'metadata'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not updates:
            return False
        
        if 'metadata' in updates:
            updates['metadata'] = json.dumps(updates['metadata'])
        
        # Set completion date if status is being changed to completed
        if updates.get('status') == 'completed':
            updates['completed_at'] = datetime.now(timezone.utc).isoformat()
        
        set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
        query = f"UPDATE promises SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        
        async with self.transaction() as conn:
            cursor = await conn.execute(query, list(updates.values()) + [promise_id])
            return cursor.rowcount > 0
    
    # Workspace operations
    async def create_workspace(self, name: str, description: str = None,
                             config: Dict[str, Any] = None) -> str:
        """Create a new workspace."""
        workspace_id = str(uuid4())
        config = config or {}
        
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO workspaces (id, name, description, config)
                VALUES (?, ?, ?, ?)
            """, (workspace_id, name, description, json.dumps(config)))
        
        logger.info(f"Created workspace: {name} ({workspace_id})")
        return workspace_id
    
    async def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Get a workspace by ID."""
        async with self.get_connection() as conn:
            async with conn.execute("""
                SELECT id, name, description, config, active, created_at, updated_at
                FROM workspaces WHERE id = ?
            """, (workspace_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'name': row[1],
                        'description': row[2],
                        'config': json.loads(row[3]),
                        'active': bool(row[4]),
                        'created_at': row[5],
                        'updated_at': row[6]
                    }
                return None
    
    async def list_workspaces(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all workspaces."""
        query = """
            SELECT id, name, description, config, active, created_at, updated_at
            FROM workspaces
        """
        params = []
        
        if active_only:
            query += " WHERE active = ?"
            params.append(True)
        
        query += " ORDER BY name ASC"
        
        async with self.get_connection() as conn:
            async with conn.execute(query, params) as cursor:
                workspaces = []
                async for row in cursor:
                    workspaces.append({
                        'id': row[0],
                        'name': row[1],
                        'description': row[2],
                        'config': json.loads(row[3]),
                        'active': bool(row[4]),
                        'created_at': row[5],
                        'updated_at': row[6]
                    })
                return workspaces
    
    async def update_workspace(self, workspace_id: str, **kwargs) -> bool:
        """Update a workspace."""
        allowed_fields = {'name', 'description', 'config', 'active'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not updates:
            return False
        
        if 'config' in updates:
            updates['config'] = json.dumps(updates['config'])
        
        set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
        query = f"UPDATE workspaces SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        
        async with self.transaction() as conn:
            cursor = await conn.execute(query, list(updates.values()) + [workspace_id])
            return cursor.rowcount > 0
    
    async def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace."""
        async with self.transaction() as conn:
            cursor = await conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            return cursor.rowcount > 0
    
    # Tool metrics operations
    async def record_tool_metric(self, tool_name: str, operation: str, 
                               execution_time_ms: int, success: bool,
                               error_message: str = None, 
                               metadata: Dict[str, Any] = None) -> str:
        """Record a tool performance metric."""
        metric_id = str(uuid4())
        metadata = metadata or {}
        
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO tool_metrics (id, tool_name, operation, execution_time_ms, 
                                        success, error_message, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (metric_id, tool_name, operation, execution_time_ms, success, 
                  error_message, json.dumps(metadata)))
        
        return metric_id
    
    async def get_tool_metrics(self, tool_name: Optional[str] = None,
                              operation: Optional[str] = None,
                              success: Optional[bool] = None,
                              limit: int = 1000) -> List[Dict[str, Any]]:
        """Get tool performance metrics."""
        query = """
            SELECT id, tool_name, operation, execution_time_ms, success, 
                   error_message, metadata, created_at
            FROM tool_metrics
        """
        params = []
        conditions = []
        
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        
        if operation:
            conditions.append("operation = ?")
            params.append(operation)
        
        if success is not None:
            conditions.append("success = ?")
            params.append(success)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        
        async with self.get_connection() as conn:
            async with conn.execute(query, params) as cursor:
                metrics = []
                async for row in cursor:
                    metrics.append({
                        'id': row[0],
                        'tool_name': row[1],
                        'operation': row[2],
                        'execution_time_ms': row[3],
                        'success': row[4],
                        'error_message': row[5],
                        'metadata': json.loads(row[6]),
                        'created_at': row[7]
                    })
                return metrics
    
    # Backup and restore operations
    async def create_backup(self, backup_path: Optional[Path] = None) -> Path:
        """Create a backup of the database."""
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.db_path.parent / f"backup_{timestamp}.db"
        
        try:
            # Close all connections temporarily
            if self._pool:
                await self._pool.close()
                self._pool = None
            
            # Copy the database file
            shutil.copy2(self.db_path, backup_path)
            
            # Verify the backup
            if not backup_path.exists() or backup_path.stat().st_size == 0:
                raise BackupError("Backup verification failed")
            
            logger.info(f"Database backup created: {backup_path}")
            return backup_path
            
        except Exception as e:
            raise BackupError(f"Backup failed: {e}")
    
    async def restore_backup(self, backup_path: Path) -> None:
        """Restore database from backup."""
        if not backup_path.exists():
            raise BackupError(f"Backup file not found: {backup_path}")
        
        try:
            # Close all connections
            if self._pool:
                await self._pool.close()
                self._pool = None
            
            # Create a backup of current database
            current_backup = await self.create_backup()
            
            try:
                # Restore from backup
                shutil.copy2(backup_path, self.db_path)
                
                # Reinitialize the database
                self._initialized = False
                await self.initialize()
                
                logger.info(f"Database restored from: {backup_path}")
                
            except Exception as e:
                # Restore the current database backup on failure
                shutil.copy2(current_backup, self.db_path)
                current_backup.unlink()  # Clean up
                raise
                
        except Exception as e:
            raise BackupError(f"Restore failed: {e}")
    
    # Utility operations
    async def vacuum(self) -> None:
        """Optimize the database by running VACUUM."""
        async with self.get_connection() as conn:
            await conn.execute("VACUUM")
        logger.info("Database vacuumed successfully")
    
    async def analyze(self) -> None:
        """Update database statistics."""
        async with self.get_connection() as conn:
            await conn.execute("ANALYZE")
        logger.info("Database analyzed successfully")
    
    async def get_database_info(self) -> Dict[str, Any]:
        """Get database information and statistics."""
        async with self.get_connection() as conn:
            # Get database size
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
            
            # Get table statistics
            tables = {}
            table_names = ['projects', 'chat_history', 'promises', 'workspaces', 'tool_metrics']
            
            for table in table_names:
                async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    row = await cursor.fetchone()
                    tables[table] = row[0] if row else 0
            
            # Get database version
            async with conn.execute("PRAGMA user_version") as cursor:
                version_row = await cursor.fetchone()
                version = version_row[0] if version_row else 0
            
            return {
                'database_path': str(self.db_path),
                'size_bytes': db_size,
                'size_mb': round(db_size / 1024 / 1024, 2),
                'version': version,
                'tables': tables,
                'total_records': sum(tables.values())
            }
    
    async def close(self) -> None:
        """Close the database manager and all connections."""
        if self._pool:
            await self._pool.close()
            self._pool = None
        
        logger.info("Database manager closed")
    
    def __del__(self):
        """Cleanup when object is destroyed."""
        if self._pool and not self._pool._closed:
            logger.warning("Database manager was not properly closed")


# Global database manager instance
db_manager = DatabaseManager()