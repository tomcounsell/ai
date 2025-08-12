"""
Database Migration System for AI Rebuild

This module provides a comprehensive migration system for managing database schema 
changes, versioning, and rollback capabilities.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import aiosqlite

from .database import DatabaseManager, DatabaseError

logger = logging.getLogger(__name__)


class MigrationError(DatabaseError):
    """Exception for migration operations."""
    pass


class Migration(ABC):
    """Abstract base class for database migrations."""
    
    def __init__(self, version: str, description: str):
        self.version = version
        self.description = description
        self.applied_at: Optional[datetime] = None
    
    @abstractmethod
    async def up(self, conn: aiosqlite.Connection) -> None:
        """Apply the migration (forward)."""
        pass
    
    @abstractmethod
    async def down(self, conn: aiosqlite.Connection) -> None:
        """Rollback the migration (backward)."""
        pass
    
    def __str__(self) -> str:
        return f"Migration {self.version}: {self.description}"


class CreateInitialTables(Migration):
    """Initial migration to create all base tables."""
    
    def __init__(self):
        super().__init__("001", "Create initial database schema")
    
    async def up(self, conn: aiosqlite.Connection) -> None:
        """Create all initial tables."""
        # This migration is handled by DatabaseManager.initialize()
        # We include it here for completeness and future reference
        pass
    
    async def down(self, conn: aiosqlite.Connection) -> None:
        """Drop all tables (destructive)."""
        tables = [
            'tool_metrics',
            'promises',
            'chat_history',
            'workspaces',
            'projects'
        ]
        
        for table in tables:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        
        logger.warning("All tables dropped - this is destructive!")


class AddUserPreferences(Migration):
    """Add user preferences table."""
    
    def __init__(self):
        super().__init__("002", "Add user preferences table")
    
    async def up(self, conn: aiosqlite.Connection) -> None:
        """Create user preferences table."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                preference_key TEXT NOT NULL,
                preference_value TEXT,
                metadata JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, preference_key)
            )
        """)
        
        # Create indexes
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_preferences_user_id 
            ON user_preferences (user_id)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_preferences_key 
            ON user_preferences (preference_key)
        """)
    
    async def down(self, conn: aiosqlite.Connection) -> None:
        """Drop user preferences table."""
        await conn.execute("DROP TABLE IF EXISTS user_preferences")


class AddProjectTemplates(Migration):
    """Add project templates functionality."""
    
    def __init__(self):
        super().__init__("003", "Add project templates table")
    
    async def up(self, conn: aiosqlite.Connection) -> None:
        """Create project templates table."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS project_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                template_config JSON NOT NULL,
                category TEXT DEFAULT 'general',
                is_public BOOLEAN DEFAULT FALSE,
                created_by TEXT,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name)
            )
        """)
        
        # Create indexes
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_templates_category 
            ON project_templates (category)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_templates_public 
            ON project_templates (is_public)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_templates_created_by 
            ON project_templates (created_by)
        """)
    
    async def down(self, conn: aiosqlite.Connection) -> None:
        """Drop project templates table."""
        await conn.execute("DROP TABLE IF EXISTS project_templates")


class AddToolUsageTracking(Migration):
    """Enhanced tool usage tracking."""
    
    def __init__(self):
        super().__init__("004", "Add enhanced tool usage tracking")
    
    async def up(self, conn: aiosqlite.Connection) -> None:
        """Add columns to tool_metrics for enhanced tracking."""
        # Add new columns to existing tool_metrics table
        try:
            await conn.execute("""
                ALTER TABLE tool_metrics ADD COLUMN user_id TEXT
            """)
        except aiosqlite.OperationalError:
            # Column might already exist
            pass
        
        try:
            await conn.execute("""
                ALTER TABLE tool_metrics ADD COLUMN session_id TEXT
            """)
        except aiosqlite.OperationalError:
            # Column might already exist
            pass
        
        try:
            await conn.execute("""
                ALTER TABLE tool_metrics ADD COLUMN input_size INTEGER DEFAULT 0
            """)
        except aiosqlite.OperationalError:
            # Column might already exist
            pass
        
        try:
            await conn.execute("""
                ALTER TABLE tool_metrics ADD COLUMN output_size INTEGER DEFAULT 0
            """)
        except aiosqlite.OperationalError:
            # Column might already exist
            pass
        
        # Add new indexes for the enhanced tracking
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tool_metrics_user_id 
            ON tool_metrics (user_id)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tool_metrics_session_id 
            ON tool_metrics (session_id)
        """)
    
    async def down(self, conn: aiosqlite.Connection) -> None:
        """Remove enhanced tracking columns."""
        # SQLite doesn't support DROP COLUMN, so we'd need to recreate the table
        # For now, we'll leave the columns but drop the indexes
        await conn.execute("""
            DROP INDEX IF EXISTS idx_tool_metrics_user_id
        """)
        
        await conn.execute("""
            DROP INDEX IF EXISTS idx_tool_metrics_session_id
        """)


class AddPromiseReminders(Migration):
    """Add reminder functionality to promises."""
    
    def __init__(self):
        super().__init__("005", "Add promise reminders and notifications")
    
    async def up(self, conn: aiosqlite.Connection) -> None:
        """Add reminder-related columns to promises table."""
        try:
            await conn.execute("""
                ALTER TABLE promises ADD COLUMN reminder_date TIMESTAMP
            """)
        except aiosqlite.OperationalError:
            pass
        
        try:
            await conn.execute("""
                ALTER TABLE promises ADD COLUMN reminder_sent BOOLEAN DEFAULT FALSE
            """)
        except aiosqlite.OperationalError:
            pass
        
        try:
            await conn.execute("""
                ALTER TABLE promises ADD COLUMN reminder_count INTEGER DEFAULT 0
            """)
        except aiosqlite.OperationalError:
            pass
        
        # Create notifications table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL CHECK (type IN ('reminder', 'deadline', 'completion', 'error')),
                title TEXT NOT NULL,
                message TEXT,
                related_id TEXT,
                related_type TEXT,
                status TEXT DEFAULT 'unread' CHECK (status IN ('unread', 'read', 'dismissed')),
                priority TEXT DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
                metadata JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP,
                dismissed_at TIMESTAMP
            )
        """)
        
        # Create indexes for notifications
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications (type)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications (status)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_priority ON notifications (priority)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_related ON notifications (related_type, related_id)
        """)
        
        # Add index for reminder queries
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_promises_reminder_date ON promises (reminder_date)
        """)
    
    async def down(self, conn: aiosqlite.Connection) -> None:
        """Remove reminder functionality."""
        await conn.execute("DROP TABLE IF EXISTS notifications")
        await conn.execute("DROP INDEX IF EXISTS idx_promises_reminder_date")


class MigrationManager:
    """Manages database migrations and versioning."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.migrations: List[Migration] = []
        self._register_migrations()
    
    def _register_migrations(self) -> None:
        """Register all available migrations."""
        self.migrations = [
            CreateInitialTables(),
            AddUserPreferences(),
            AddProjectTemplates(),
            AddToolUsageTracking(),
            AddPromiseReminders(),
        ]
        
        # Sort by version to ensure correct order
        self.migrations.sort(key=lambda m: m.version)
    
    async def get_current_version(self) -> Optional[str]:
        """Get the current database version."""
        try:
            async with self.db_manager.get_connection() as conn:
                async with conn.execute("""
                    SELECT version FROM migrations ORDER BY applied_at DESC LIMIT 1
                """) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else None
        except Exception:
            # If migrations table doesn't exist, return None
            return None
    
    async def get_applied_migrations(self) -> List[Tuple[str, str, str]]:
        """Get list of applied migrations."""
        try:
            async with self.db_manager.get_connection() as conn:
                async with conn.execute("""
                    SELECT version, description, applied_at 
                    FROM migrations 
                    ORDER BY applied_at ASC
                """) as cursor:
                    return [(row[0], row[1], row[2]) async for row in cursor]
        except Exception:
            return []
    
    async def get_pending_migrations(self) -> List[Migration]:
        """Get list of pending migrations."""
        applied_versions = {version for version, _, _ in await self.get_applied_migrations()}
        return [m for m in self.migrations if m.version not in applied_versions]
    
    async def apply_migration(self, migration: Migration) -> None:
        """Apply a single migration."""
        logger.info(f"Applying migration {migration.version}: {migration.description}")
        
        try:
            async with self.db_manager.transaction() as conn:
                # Apply the migration
                await migration.up(conn)
                
                # Record the migration
                await conn.execute("""
                    INSERT INTO migrations (version, description, applied_at)
                    VALUES (?, ?, ?)
                """, (migration.version, migration.description, 
                      datetime.now(timezone.utc).isoformat()))
                
                migration.applied_at = datetime.now(timezone.utc)
                
                logger.info(f"Successfully applied migration {migration.version}")
                
        except Exception as e:
            logger.error(f"Failed to apply migration {migration.version}: {e}")
            raise MigrationError(f"Migration {migration.version} failed: {e}")
    
    async def rollback_migration(self, migration: Migration) -> None:
        """Rollback a single migration."""
        logger.info(f"Rolling back migration {migration.version}: {migration.description}")
        
        try:
            async with self.db_manager.transaction() as conn:
                # Rollback the migration
                await migration.down(conn)
                
                # Remove the migration record
                await conn.execute("""
                    DELETE FROM migrations WHERE version = ?
                """, (migration.version,))
                
                logger.info(f"Successfully rolled back migration {migration.version}")
                
        except Exception as e:
            logger.error(f"Failed to rollback migration {migration.version}: {e}")
            raise MigrationError(f"Rollback of migration {migration.version} failed: {e}")
    
    async def migrate_to_latest(self) -> None:
        """Apply all pending migrations."""
        pending = await self.get_pending_migrations()
        
        if not pending:
            logger.info("No pending migrations to apply")
            return
        
        logger.info(f"Applying {len(pending)} pending migrations")
        
        for migration in pending:
            await self.apply_migration(migration)
        
        current_version = await self.get_current_version()
        logger.info(f"Database migrated to version {current_version}")
    
    async def migrate_to_version(self, target_version: str) -> None:
        """Migrate to a specific version (forward or backward)."""
        current_version = await self.get_current_version()
        
        if current_version == target_version:
            logger.info(f"Already at version {target_version}")
            return
        
        applied_versions = {version for version, _, _ in await self.get_applied_migrations()}
        target_migration = next((m for m in self.migrations if m.version == target_version), None)
        
        if not target_migration:
            raise MigrationError(f"Migration version {target_version} not found")
        
        # Determine direction
        if target_version not in applied_versions:
            # Forward migration
            pending = [m for m in self.migrations 
                      if m.version not in applied_versions and m.version <= target_version]
            pending.sort(key=lambda m: m.version)
            
            for migration in pending:
                await self.apply_migration(migration)
                
        else:
            # Backward migration (rollback)
            to_rollback = [m for m in self.migrations 
                          if m.version in applied_versions and m.version > target_version]
            to_rollback.sort(key=lambda m: m.version, reverse=True)
            
            for migration in to_rollback:
                await self.rollback_migration(migration)
        
        logger.info(f"Database migrated to version {target_version}")
    
    async def get_migration_status(self) -> Dict[str, Any]:
        """Get current migration status."""
        current_version = await self.get_current_version()
        applied_migrations = await self.get_applied_migrations()
        pending_migrations = await self.get_pending_migrations()
        
        return {
            'current_version': current_version,
            'latest_available_version': self.migrations[-1].version if self.migrations else None,
            'applied_migrations': len(applied_migrations),
            'pending_migrations': len(pending_migrations),
            'total_migrations': len(self.migrations),
            'applied_list': [
                {
                    'version': version,
                    'description': description,
                    'applied_at': applied_at
                }
                for version, description, applied_at in applied_migrations
            ],
            'pending_list': [
                {
                    'version': migration.version,
                    'description': migration.description
                }
                for migration in pending_migrations
            ]
        }
    
    async def create_migration_file(self, version: str, description: str, 
                                  migration_dir: Path) -> Path:
        """Create a new migration file template."""
        migration_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{version}_{description.lower().replace(' ', '_')}.py"
        file_path = migration_dir / filename
        
        template = f'''"""
Migration {version}: {description}

Generated at {datetime.now().isoformat()}
"""

from utilities.migrations import Migration
import aiosqlite


class Migration{version}(Migration):
    """Migration for {description}."""
    
    def __init__(self):
        super().__init__("{version}", "{description}")
    
    async def up(self, conn: aiosqlite.Connection) -> None:
        """Apply the migration (forward)."""
        # TODO: Implement forward migration
        pass
    
    async def down(self, conn: aiosqlite.Connection) -> None:
        """Rollback the migration (backward)."""
        # TODO: Implement rollback migration
        pass
'''
        
        file_path.write_text(template)
        logger.info(f"Created migration file: {file_path}")
        
        return file_path
    
    async def validate_migrations(self) -> Dict[str, Any]:
        """Validate migration integrity and consistency."""
        issues = []
        
        # Check for duplicate versions
        versions = [m.version for m in self.migrations]
        duplicates = set([v for v in versions if versions.count(v) > 1])
        if duplicates:
            issues.append(f"Duplicate migration versions: {duplicates}")
        
        # Check version ordering
        sorted_versions = sorted(versions)
        if versions != sorted_versions:
            issues.append("Migration versions are not in order")
        
        # Check for gaps in version sequence (if using numeric versions)
        if all(v.isdigit() for v in versions):
            numeric_versions = [int(v) for v in versions]
            expected_range = list(range(1, max(numeric_versions) + 1))
            missing = [str(v) for v in expected_range if v not in numeric_versions]
            if missing:
                issues.append(f"Missing migration versions: {missing}")
        
        # Check applied migrations exist in code
        applied_versions = {version for version, _, _ in await self.get_applied_migrations()}
        code_versions = {m.version for m in self.migrations}
        orphaned = applied_versions - code_versions
        if orphaned:
            issues.append(f"Applied migrations not found in code: {orphaned}")
        
        return {
            'valid': len(issues) == 0,
            'issues': issues,
            'total_migrations': len(self.migrations),
            'applied_migrations': len(applied_versions),
            'pending_migrations': len(code_versions - applied_versions)
        }


# Global migration manager instance
migration_manager = None

def get_migration_manager(db_manager: Optional[DatabaseManager] = None) -> MigrationManager:
    """Get or create the global migration manager instance."""
    global migration_manager
    if migration_manager is None:
        from .database import db_manager as default_db_manager
        migration_manager = MigrationManager(db_manager or default_db_manager)
    return migration_manager