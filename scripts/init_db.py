#!/usr/bin/env python3
"""
Database Initialization Script for AI Rebuild

This script initializes the database, applies all migrations, and optionally 
populates it with sample data for development.
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import click

# Add the parent directory to the path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings
from utilities.database import DatabaseManager, db_manager
from utilities.migrations import get_migration_manager


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def initialize_database(
    fresh_install: bool = False,
    with_sample_data: bool = False,
    backup_existing: bool = True
) -> None:
    """
    Initialize the database with all required tables and migrations.
    
    Args:
        fresh_install: If True, recreate database from scratch
        with_sample_data: If True, populate with sample data
        backup_existing: If True, backup existing database before changes
    """
    try:
        logger.info("Starting database initialization...")
        
        # Create directories if they don't exist
        settings.create_directories()
        
        # Handle fresh install
        if fresh_install and db_manager.db_path.exists():
            if backup_existing:
                backup_path = await db_manager.create_backup()
                logger.info(f"Existing database backed up to: {backup_path}")
            
            # Remove existing database
            db_manager.db_path.unlink()
            logger.info("Removed existing database for fresh install")
        
        # Initialize the database structure
        await db_manager.initialize()
        logger.info("Database structure initialized")
        
        # Apply all migrations
        migration_manager = get_migration_manager()
        await migration_manager.migrate_to_latest()
        
        # Get migration status
        migration_status = await migration_manager.get_migration_status()
        logger.info(f"Applied {migration_status['applied_migrations']} migrations")
        
        # Populate with sample data if requested
        if with_sample_data:
            await _populate_sample_data()
            logger.info("Sample data populated")
        
        # Get database info
        db_info = await db_manager.get_database_info()
        logger.info(f"Database initialized at: {db_info['database_path']}")
        logger.info(f"Database size: {db_info['size_mb']} MB")
        logger.info(f"Total records: {db_info['total_records']}")
        
        # Validate the database
        validation_result = await _validate_database()
        if validation_result['valid']:
            logger.info("Database validation passed")
        else:
            logger.warning(f"Database validation issues: {validation_result['issues']}")
        
        logger.info("Database initialization completed successfully!")
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


async def _populate_sample_data() -> None:
    """Populate the database with sample data for development."""
    logger.info("Populating sample data...")
    
    try:
        # Create sample projects
        project1_id = await db_manager.create_project(
            name="AI Assistant Development",
            path="/path/to/ai-assistant",
            description="Development of an intelligent AI assistant",
            metadata={
                "technology_stack": ["Python", "FastAPI", "SQLite"],
                "team_size": 3,
                "start_date": "2024-01-01"
            }
        )
        
        project2_id = await db_manager.create_project(
            name="Web Scraper Tool",
            path="/path/to/web-scraper",
            description="Tool for automated web content extraction",
            metadata={
                "technology_stack": ["Python", "BeautifulSoup", "Selenium"],
                "complexity": "medium"
            }
        )
        
        # Create sample chat history
        session_id = "sample-session-001"
        
        await db_manager.add_chat_message(
            project_id=project1_id,
            session_id=session_id,
            role="user",
            content="Help me implement a new feature for user authentication",
            metadata={"feature": "authentication"},
            token_count=12
        )
        
        await db_manager.add_chat_message(
            project_id=project1_id,
            session_id=session_id,
            role="assistant",
            content="I'll help you implement user authentication. Let's start with the database schema for users.",
            metadata={"feature": "authentication"},
            token_count=23
        )
        
        # Create sample promises
        promise1_id = await db_manager.create_promise(
            project_id=project1_id,
            title="Implement JWT token authentication",
            description="Add secure JWT-based authentication system with token refresh",
            priority="high",
            assigned_to="ai-assistant",
            completion_criteria="Users can login, logout, and access protected routes",
            metadata={
                "estimated_hours": 8,
                "dependencies": ["user_model", "password_hashing"]
            }
        )
        
        promise2_id = await db_manager.create_promise(
            project_id=project1_id,
            title="Add password reset functionality",
            description="Implement secure password reset via email",
            priority="medium",
            assigned_to="ai-assistant",
            completion_criteria="Users can reset passwords with email verification",
            metadata={
                "estimated_hours": 4,
                "dependencies": ["email_service", "user_authentication"]
            }
        )
        
        promise3_id = await db_manager.create_promise(
            project_id=project2_id,
            title="Add rate limiting to scraper",
            description="Implement rate limiting to avoid being blocked by websites",
            priority="medium",
            completion_criteria="Scraper respects robots.txt and implements delays",
            metadata={
                "estimated_hours": 3,
                "technical_debt": True
            }
        )
        
        # Create sample workspaces
        workspace1_id = await db_manager.create_workspace(
            name="Development Environment",
            description="Main development workspace",
            config={
                "editor": "vscode",
                "python_version": "3.11",
                "virtual_env": ".venv",
                "debug_mode": True
            }
        )
        
        workspace2_id = await db_manager.create_workspace(
            name="Production Environment",
            description="Production deployment workspace",
            config={
                "deployment": "docker",
                "scale": "auto",
                "monitoring": True,
                "backup_schedule": "daily"
            }
        )
        
        # Create sample tool metrics
        tools_data = [
            ("file_reader", "read", 150, True, None),
            ("web_scraper", "scrape", 2500, True, None),
            ("code_analyzer", "analyze", 800, True, None),
            ("database_query", "select", 25, True, None),
            ("api_client", "request", 1200, False, "Connection timeout"),
            ("text_processor", "summarize", 600, True, None),
        ]
        
        for tool_name, operation, exec_time, success, error_msg in tools_data:
            await db_manager.record_tool_metric(
                tool_name=tool_name,
                operation=operation,
                execution_time_ms=exec_time,
                success=success,
                error_message=error_msg,
                metadata={
                    "environment": "development",
                    "version": "1.0.0",
                    "sample_data": True
                }
            )
        
        logger.info(f"Created {len(tools_data)} tool metrics entries")
        logger.info(f"Created 2 projects, 3 promises, 2 workspaces")
        
    except Exception as e:
        logger.error(f"Failed to populate sample data: {e}")
        raise


async def _validate_database() -> dict:
    """Validate database structure and data integrity."""
    issues = []
    
    try:
        # Check table existence
        required_tables = [
            'projects', 'chat_history', 'promises', 
            'workspaces', 'tool_metrics', 'migrations'
        ]
        
        async with db_manager.get_connection() as conn:
            # Check tables exist
            async with conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """) as cursor:
                existing_tables = {row[0] async for row in cursor}
            
            missing_tables = set(required_tables) - existing_tables
            if missing_tables:
                issues.append(f"Missing tables: {missing_tables}")
            
            # Check foreign key constraints
            await conn.execute("PRAGMA foreign_keys=ON")
            
            # Check indexes exist
            required_indexes = [
                'idx_projects_status',
                'idx_chat_history_project_id',
                'idx_promises_status',
                'idx_tool_metrics_tool_name'
            ]
            
            async with conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
            """) as cursor:
                existing_indexes = {row[0] async for row in cursor}
            
            missing_indexes = set(required_indexes) - existing_indexes
            if missing_indexes:
                issues.append(f"Missing indexes: {missing_indexes}")
            
            # Check data integrity
            for table in existing_tables:
                try:
                    async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                        count_result = await cursor.fetchone()
                        if count_result is None:
                            issues.append(f"Cannot count records in table {table}")
                except Exception as e:
                    issues.append(f"Error accessing table {table}: {e}")
        
        return {
            'valid': len(issues) == 0,
            'issues': issues,
            'existing_tables': list(existing_tables),
            'table_count': len(existing_tables)
        }
        
    except Exception as e:
        return {
            'valid': False,
            'issues': [f"Validation failed: {e}"],
            'existing_tables': [],
            'table_count': 0
        }


async def get_database_status() -> dict:
    """Get comprehensive database status information."""
    try:
        # Basic database info
        db_info = await db_manager.get_database_info()
        
        # Migration status
        migration_manager = get_migration_manager()
        migration_status = await migration_manager.get_migration_status()
        
        # Validation results
        validation_result = await _validate_database()
        
        return {
            'database_info': db_info,
            'migration_status': migration_status,
            'validation': validation_result,
            'connection_pool': {
                'max_connections': db_manager.pool_size,
                'initialized': db_manager._initialized
            }
        }
        
    except Exception as e:
        return {
            'error': str(e),
            'database_accessible': False
        }


async def reset_database() -> None:
    """Reset the database by dropping all tables and reinitializing."""
    logger.warning("RESETTING DATABASE - ALL DATA WILL BE LOST!")
    
    try:
        # Create backup first
        if db_manager.db_path.exists():
            backup_path = await db_manager.create_backup()
            logger.info(f"Database backed up to: {backup_path}")
        
        # Close all connections
        await db_manager.close()
        
        # Remove database file
        if db_manager.db_path.exists():
            db_manager.db_path.unlink()
            logger.info("Database file removed")
        
        # Reinitialize
        db_manager._initialized = False
        await initialize_database(fresh_install=True, with_sample_data=False)
        
        logger.info("Database reset completed")
        
    except Exception as e:
        logger.error(f"Database reset failed: {e}")
        raise


# CLI Commands
@click.group()
def cli():
    """Database initialization and management commands."""
    pass


@cli.command()
@click.option('--fresh', is_flag=True, help='Fresh install (removes existing database)')
@click.option('--sample-data', is_flag=True, help='Populate with sample data')
@click.option('--no-backup', is_flag=True, help='Skip backing up existing database')
def init(fresh: bool, sample_data: bool, no_backup: bool):
    """Initialize the database."""
    asyncio.run(initialize_database(
        fresh_install=fresh,
        with_sample_data=sample_data,
        backup_existing=not no_backup
    ))


@cli.command()
def status():
    """Show database status."""
    async def show_status():
        status_info = await get_database_status()
        
        if 'error' in status_info:
            click.echo(f"Error: {status_info['error']}")
            return
        
        db_info = status_info['database_info']
        migration_status = status_info['migration_status']
        validation = status_info['validation']
        
        click.echo("\n=== Database Status ===")
        click.echo(f"Path: {db_info['database_path']}")
        click.echo(f"Size: {db_info['size_mb']} MB")
        click.echo(f"Version: {migration_status['current_version']}")
        click.echo(f"Total Records: {db_info['total_records']}")
        
        click.echo("\n=== Tables ===")
        for table, count in db_info['tables'].items():
            click.echo(f"  {table}: {count} records")
        
        click.echo("\n=== Migrations ===")
        click.echo(f"Applied: {migration_status['applied_migrations']}")
        click.echo(f"Pending: {migration_status['pending_migrations']}")
        
        click.echo("\n=== Validation ===")
        if validation['valid']:
            click.echo("✓ Database validation passed")
        else:
            click.echo("✗ Validation issues found:")
            for issue in validation['issues']:
                click.echo(f"  - {issue}")
    
    asyncio.run(show_status())


@cli.command()
@click.option('--confirm', is_flag=True, help='Confirm the reset operation')
def reset(confirm: bool):
    """Reset the database (DESTRUCTIVE)."""
    if not confirm:
        click.echo("This will delete all data in the database!")
        if not click.confirm("Are you sure you want to continue?"):
            return
    
    asyncio.run(reset_database())


@cli.command()
@click.option('--version', help='Target migration version')
def migrate(version: Optional[str]):
    """Apply database migrations."""
    async def run_migration():
        migration_manager = get_migration_manager()
        
        if version:
            await migration_manager.migrate_to_version(version)
            click.echo(f"Migrated to version {version}")
        else:
            await migration_manager.migrate_to_latest()
            current_version = await migration_manager.get_current_version()
            click.echo(f"Migrated to latest version {current_version}")
    
    asyncio.run(run_migration())


@cli.command()
def backup():
    """Create a database backup."""
    async def create_backup():
        backup_path = await db_manager.create_backup()
        click.echo(f"Backup created: {backup_path}")
    
    asyncio.run(create_backup())


@cli.command()
@click.argument('backup_path', type=click.Path(exists=True))
@click.option('--confirm', is_flag=True, help='Confirm the restore operation')
def restore(backup_path: str, confirm: bool):
    """Restore database from backup."""
    if not confirm:
        click.echo("This will replace the current database!")
        if not click.confirm("Are you sure you want to continue?"):
            return
    
    async def restore_backup():
        await db_manager.restore_backup(Path(backup_path))
        click.echo(f"Database restored from: {backup_path}")
    
    asyncio.run(restore_backup())


if __name__ == '__main__':
    cli()