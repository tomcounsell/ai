# Database Architecture

## Overview

The AI agent system uses a **single shared SQLite database** (`system.db`) for all persistent data storage. This unified approach eliminates the overhead of managing multiple separate databases while providing better data consistency and performance.

## Shared Database Structure

### Database File
- **Location**: `system.db` (project root)
- **Type**: SQLite 3
- **Purpose**: Unified storage for all system data

### Tables

#### Promise Queue System
```sql
-- Promises table for asynchronous task tracking
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    task_description TEXT NOT NULL,
    task_type TEXT DEFAULT 'code',  -- 'code', 'search', 'analysis'
    status TEXT DEFAULT 'pending',  -- 'pending', 'in_progress', 'completed', 'failed'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    result_summary TEXT,
    error_message TEXT,
    metadata TEXT  -- JSON blob for task-specific data
);

-- Indexes for performance
CREATE INDEX idx_promises_status ON promises(status);
CREATE INDEX idx_promises_chat_id ON promises(chat_id);
```

For detailed promise queue operations, see [Promise Queue Documentation](promise-queue.md).

#### Token Usage Tracking
```sql
-- Projects table for project metadata
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Hosts table for AI providers
CREATE TABLE hosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    base_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Models table for AI models
CREATE TABLE models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host_id INTEGER NOT NULL,
    input_cost_per_1k REAL,
    output_cost_per_1k REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (host_id) REFERENCES hosts(id),
    UNIQUE(name, host_id)
);

-- Token usage records
CREATE TABLE token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    project_id INTEGER NOT NULL,
    model_id INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cost_usd REAL,
    request_id TEXT,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (model_id) REFERENCES models(id)
);
```

#### Link Analysis Storage
```sql
-- Links table for URL analysis and storage
CREATE TABLE links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    analysis_result TEXT,  -- JSON blob
    analysis_status TEXT DEFAULT 'pending',  -- 'success', 'error', 'pending'
    title TEXT,
    main_topic TEXT,
    reasons_to_care TEXT,
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Performance Indexes
```sql
-- Token usage indexes
CREATE INDEX idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX idx_token_usage_project ON token_usage(project_id);
CREATE INDEX idx_token_usage_model ON token_usage(model_id);
CREATE INDEX idx_token_usage_user ON token_usage(user_id);
CREATE INDEX idx_token_usage_request ON token_usage(request_id);

-- Link analysis indexes
CREATE INDEX idx_links_url ON links(url);
CREATE INDEX idx_links_domain ON links(domain);
CREATE INDEX idx_links_timestamp ON links(timestamp);
CREATE INDEX idx_links_status ON links(analysis_status);
```

## Database Utilities

### Core Module: `utilities/database.py`

The shared database utilities provide centralized access and management:

```python
from utilities.database import get_database_connection, init_database

# Initialize database with all tables
init_database()

# Get database connection
with get_database_connection() as conn:
    # Perform database operations
    results = conn.execute("SELECT * FROM links").fetchall()
```

### Key Functions

#### Database Connection
```python
def get_database_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get a connection to the shared system database."""
```

#### Database Initialization
```python
def init_database() -> None:
    """Initialize the shared database with all required tables."""
```

#### Migration Support
```python
def migrate_existing_databases() -> None:
    """Migrate data from separate token_usage.db and links.db if they exist."""

def cleanup_old_databases() -> None:
    """Remove old separate database files after successful migration."""
```

## Integration Points

### Token Tracking
- **Module**: `utilities/token_tracker.py`
- **Usage**: Automatic token usage logging for all AI model interactions
- **Tables**: `projects`, `hosts`, `models`, `token_usage`

### Link Analysis
- **Module**: `tools/link_analysis_tool.py`
- **Usage**: URL analysis and storage for shared links
- **Tables**: `links`

### MCP Server Integration
- **Module**: `mcp_servers/social_tools.py`
- **Usage**: Claude Code tool access to link analysis
- **Tables**: `links`

## Benefits of Shared Database

### Performance
- **Single connection pool**: Reduced overhead compared to multiple databases
- **Unified indexing**: Better query performance across related data
- **Atomic transactions**: ACID compliance across all data operations

### Data Consistency
- **Referential integrity**: Foreign key constraints across related tables
- **Unified schema**: Consistent data types and naming conventions
- **Single source of truth**: No data synchronization issues

### Development Experience
- **Simplified configuration**: One database connection to manage
- **Easier backups**: Single file to backup/restore
- **Better debugging**: All data in one place for analysis

### Operational Benefits
- **Reduced complexity**: No database proliferation over time
- **Centralized monitoring**: Single database to monitor for performance
- **Simplified deployment**: One database file to manage in production

## Migration from Legacy Databases

The system automatically migrates data from legacy separate databases:

### Legacy Files (Automatically Backed Up)
- `token_usage.db` → `token_usage.db.backup`
- `links.db` → `links.db.backup`

### Migration Process
1. **Automatic Detection**: System checks for existing legacy databases
2. **Data Migration**: All data migrated to shared `system.db`
3. **Backup Creation**: Legacy files renamed with `.backup` extension
4. **Seamless Operation**: No downtime or configuration changes required

## Version Control

The shared database is excluded from version control:

```gitignore
# SQLite databases - data files not for version control
*.db
system.db
```

This prevents commit history pollution while allowing proper local development and production deployment.

## Best Practices

### Development
- Always use `init_database()` before first database access
- Use context managers (`with get_database_connection()`) for proper connection handling
- Never commit database files to version control

### Production
- Regular backups of `system.db` file
- Monitor database size and performance
- Use database migrations for schema changes

### Testing
- Use separate test databases for isolation
- Clean up test data after test runs
- Mock database operations for unit tests when appropriate

This unified database architecture provides a solid foundation for the AI agent system while maintaining simplicity and performance.