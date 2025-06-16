"""
Shared database utilities for the AI agent system.

Provides a centralized SQLite database for all system data including
token usage tracking, link analysis, and other persistent storage needs.
"""

import sqlite3
import os
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_database_path() -> Path:
    """Get the path to the shared system database."""
    return Path("system.db")


def get_database_connection(db_path: Optional[str] = None, timeout: int = 30) -> sqlite3.Connection:
    """Get a connection to the shared system database with optimized settings.
    
    Args:
        db_path: Optional custom database path. If None, uses default system.db
        timeout: Connection timeout in seconds (default: 30)
        
    Returns:
        sqlite3.Connection: Database connection with optimized PRAGMA settings
    """
    if db_path is None:
        db_path = get_database_path()
    
    # Create connection with timeout
    conn = sqlite3.connect(db_path, timeout=timeout)
    
    # Apply optimized PRAGMA settings for concurrency and performance
    conn.execute("PRAGMA journal_mode = WAL")          # Enable WAL mode for better concurrency
    conn.execute("PRAGMA busy_timeout = 30000")        # 30 second busy timeout
    conn.execute("PRAGMA synchronous = NORMAL")        # Balance safety and performance
    conn.execute("PRAGMA cache_size = -64000")         # 64MB cache size
    conn.execute("PRAGMA temp_store = MEMORY")         # Store temp tables in memory
    conn.execute("PRAGMA mmap_size = 268435456")       # 256MB memory-mapped I/O
    
    return conn


@contextmanager
def database_connection(db_path: Optional[str] = None, timeout: int = 30):
    """Context manager for safe database operations with automatic cleanup.
    
    Args:
        db_path: Optional custom database path
        timeout: Connection timeout in seconds
        
    Yields:
        sqlite3.Connection: Database connection
    """
    conn = None
    try:
        conn = get_database_connection(db_path, timeout)
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database operation failed: {e}")
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.error(f"Error closing database connection: {e}")


def checkpoint_wal_database(db_path: Optional[str] = None) -> bool:
    """Checkpoint WAL file to reduce size and improve performance.
    
    Should be called periodically to prevent WAL files from growing too large.
    Returns True if checkpoint was successful.
    """
    try:
        with get_database_connection(db_path, timeout=3) as conn:
            # TRUNCATE mode forces all WAL data to be written to main database
            result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result and result[0] == 0:  # 0 means success
                return True
            return False
    except Exception as e:
        logger.warning(f"WAL checkpoint failed: {e}")
        return False


def periodic_database_maintenance():
    """Perform periodic database maintenance to prevent locks and improve performance."""
    try:
        # Checkpoint WAL to prevent it from growing too large
        if checkpoint_wal_database():
            logger.debug("Database WAL checkpoint completed successfully")
        
        # Analyze database to update statistics
        with database_connection(timeout=10) as conn:
            conn.execute("ANALYZE")
            logger.debug("Database analysis completed")
            
    except Exception as e:
        logger.warning(f"Database maintenance failed: {e}")


@contextmanager
def database_transaction(db_path: Optional[str] = None, timeout: int = 5):
    """Context manager for database transactions with proper cleanup.
    
    Args:
        db_path: Optional custom database path. If None, uses default system.db
        timeout: Connection timeout in seconds (default: 30)
        
    Yields:
        sqlite3.Connection: Database connection within transaction
        
    Example:
        with database_transaction() as conn:
            conn.execute("INSERT INTO table VALUES (?)", (value,))
            # Transaction automatically committed on success, rolled back on error
    """
    conn = get_database_connection(db_path, timeout)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> None:
    """Initialize the shared database with all required tables."""
    db_path = get_database_path()
    
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript("""
                -- Projects table for project metadata
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Hosts table for AI providers
                CREATE TABLE IF NOT EXISTS hosts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    base_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Models table for AI models
                CREATE TABLE IF NOT EXISTS models (
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
                CREATE TABLE IF NOT EXISTS token_usage (
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
                
                -- Links table for URL analysis and storage
                CREATE TABLE IF NOT EXISTS links (
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
                
                -- Claude Code sessions for persistent context
                CREATE TABLE IF NOT EXISTS claude_code_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE NOT NULL,  -- Claude Code session UUID
                    chat_id TEXT,  -- Telegram chat ID for session association
                    username TEXT,  -- Username who initiated the session
                    tool_name TEXT NOT NULL,  -- 'delegate_coding_task' or 'technical_analysis'
                    working_directory TEXT NOT NULL,
                    initial_task TEXT NOT NULL,  -- Original task description
                    task_count INTEGER DEFAULT 1,  -- Number of tasks completed in session
                    last_activity DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,  -- Whether session is still usable
                    session_metadata TEXT  -- JSON blob for additional context
                );
                
                -- Promises table for tracking long-running background tasks
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
                
                -- Message queue for missed/scheduled messages
                CREATE TABLE IF NOT EXISTS message_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER,
                    message_text TEXT NOT NULL,
                    message_type TEXT NOT NULL,  -- 'missed', 'scheduled', 'followup'
                    sender_username TEXT,
                    original_timestamp TIMESTAMP,
                    status TEXT DEFAULT 'pending',  -- 'pending', 'processing', 'completed', 'failed'
                    processed_at TIMESTAMP,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT  -- JSON blob with full message data
                );
                
                -- Server tasks table for main server to process background work
                CREATE TABLE IF NOT EXISTS server_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,  -- 'scan_missed_messages', 'send_message', 'cleanup', etc.
                    task_data TEXT,  -- JSON blob with task parameters
                    priority INTEGER DEFAULT 5,  -- 1=highest, 10=lowest
                    status TEXT DEFAULT 'pending',  -- 'pending', 'processing', 'completed', 'failed'
                    scheduled_for TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- When to run this task
                    attempts INTEGER DEFAULT 0,
                    max_attempts INTEGER DEFAULT 3,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                );
                
                -- Chat state tracking for persistent message resumption
                CREATE TABLE IF NOT EXISTS chat_state (
                    chat_id INTEGER PRIMARY KEY,
                    last_seen_message_id INTEGER,  -- Telegram message ID of last processed message
                    last_seen_timestamp TIMESTAMP,  -- Timestamp of last processed message
                    bot_last_online TIMESTAMP,     -- When bot was last confirmed online for this chat
                    bot_last_offline TIMESTAMP,    -- When bot went offline
                    scan_completed_at TIMESTAMP,   -- Last time we completed a full missed message scan
                    total_messages_scanned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Indexes for performance
                CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
                CREATE INDEX IF NOT EXISTS idx_token_usage_project ON token_usage(project_id);
                CREATE INDEX IF NOT EXISTS idx_token_usage_model ON token_usage(model_id);
                CREATE INDEX IF NOT EXISTS idx_token_usage_user ON token_usage(user_id);
                CREATE INDEX IF NOT EXISTS idx_token_usage_request ON token_usage(request_id);
                
                CREATE INDEX IF NOT EXISTS idx_links_url ON links(url);
                CREATE INDEX IF NOT EXISTS idx_links_domain ON links(domain);
                CREATE INDEX IF NOT EXISTS idx_links_timestamp ON links(timestamp);
                CREATE INDEX IF NOT EXISTS idx_links_status ON links(analysis_status);
                
                CREATE INDEX IF NOT EXISTS idx_claude_sessions_session_id ON claude_code_sessions(session_id);
                CREATE INDEX IF NOT EXISTS idx_claude_sessions_chat_id ON claude_code_sessions(chat_id);
                CREATE INDEX IF NOT EXISTS idx_claude_sessions_username ON claude_code_sessions(username);
                CREATE INDEX IF NOT EXISTS idx_claude_sessions_tool ON claude_code_sessions(tool_name);
                CREATE INDEX IF NOT EXISTS idx_claude_sessions_active ON claude_code_sessions(is_active);
                CREATE INDEX IF NOT EXISTS idx_claude_sessions_last_activity ON claude_code_sessions(last_activity);
                
                CREATE INDEX IF NOT EXISTS idx_promises_chat_id ON promises(chat_id);
                CREATE INDEX IF NOT EXISTS idx_promises_status ON promises(status);
                CREATE INDEX IF NOT EXISTS idx_promises_created_at ON promises(created_at);
                
                CREATE INDEX IF NOT EXISTS idx_message_queue_status ON message_queue(status);
                CREATE INDEX IF NOT EXISTS idx_message_queue_chat_id ON message_queue(chat_id);
                CREATE INDEX IF NOT EXISTS idx_message_queue_created_at ON message_queue(created_at);
                
                CREATE INDEX IF NOT EXISTS idx_server_tasks_status ON server_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_server_tasks_priority ON server_tasks(priority, scheduled_for);
                CREATE INDEX IF NOT EXISTS idx_server_tasks_type ON server_tasks(task_type);
                
                -- Chat messages table for conversation history
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    username TEXT,
                    text TEXT,
                    is_bot_message BOOLEAN DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, message_id)
                );
                
                -- Index for efficient history queries
                CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_timestamp
                ON chat_messages(chat_id, timestamp DESC);
                
                -- Message processing metrics
                CREATE TABLE IF NOT EXISTS message_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    hour INTEGER NOT NULL,
                    message_type TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    success_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    total_processing_time REAL DEFAULT 0,
                    avg_processing_time REAL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, hour, message_type, priority)
                );
                
                -- Error tracking table
                CREATE TABLE IF NOT EXISTS processing_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    chat_id INTEGER,
                    username TEXT,
                    error_category TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    error_message TEXT,
                    severity TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    resolved BOOLEAN DEFAULT 0,
                    resolution_time DATETIME,
                    metadata TEXT, -- JSON
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Index for error analysis
                CREATE INDEX IF NOT EXISTS idx_processing_errors_timestamp
                ON processing_errors(timestamp DESC);
                
                CREATE INDEX IF NOT EXISTS idx_processing_errors_category
                ON processing_errors(error_category, timestamp DESC);
                
                -- Rate limit tracking
                CREATE TABLE IF NOT EXISTS rate_limits (
                    chat_id INTEGER PRIMARY KEY,
                    message_count INTEGER DEFAULT 0,
                    window_start DATETIME NOT NULL,
                    last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
                    violations INTEGER DEFAULT 0
                );
                
                -- Feature flags for gradual migration
                CREATE TABLE IF NOT EXISTS feature_flags (
                    flag_name TEXT PRIMARY KEY,
                    enabled BOOLEAN DEFAULT 0,
                    rollout_percentage INTEGER DEFAULT 0,
                    description TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Insert default data
            _insert_default_data(conn)
            
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        raise


# Server Task Queue Functions

def queue_server_task(task_type: str, task_data: dict = None, priority: int = 5, scheduled_for: str = None) -> int:
    """Queue a task for the main server to process.
    
    Args:
        task_type: Type of task ('scan_missed_messages', 'send_message', etc.)
        task_data: Dictionary with task parameters (will be JSON encoded)
        priority: Priority 1-10 (1=highest, 10=lowest)
        scheduled_for: ISO timestamp when to run (None=now)
    
    Returns:
        int: Task ID
    """
    import json
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        
        task_data_json = json.dumps(task_data) if task_data else None
        
        cursor.execute("""
            INSERT INTO server_tasks (task_type, task_data, priority, scheduled_for)
            VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        """, (task_type, task_data_json, priority, scheduled_for))
        
        conn.commit()
        return cursor.lastrowid


def get_pending_server_tasks(limit: int = 10) -> list:
    """Get pending server tasks ready for processing.
    
    Returns tasks ordered by priority (highest first) then by scheduled_for.
    """
    with get_database_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM server_tasks 
            WHERE status = 'pending' 
            AND scheduled_for <= CURRENT_TIMESTAMP
            AND attempts < max_attempts
            ORDER BY priority ASC, scheduled_for ASC
            LIMIT ?
        """, (limit,))
        
        return [dict(row) for row in cursor.fetchall()]


def update_server_task_status(task_id: int, status: str, error_message: str = None) -> None:
    """Update server task status."""
    with get_database_connection() as conn:
        cursor = conn.cursor()
        
        if status in ('completed', 'failed'):
            cursor.execute("""
                UPDATE server_tasks 
                SET status = ?, error_message = ?, processed_at = CURRENT_TIMESTAMP,
                    attempts = attempts + 1
                WHERE id = ?
            """, (status, error_message, task_id))
        else:
            cursor.execute("""
                UPDATE server_tasks 
                SET status = ?, attempts = attempts + 1
                WHERE id = ?
            """, (status, task_id))
        
        conn.commit()


def _insert_default_data(conn: sqlite3.Connection) -> None:
    """Insert default hosts and models."""
    default_hosts = [
        ("Anthropic", "https://api.anthropic.com"),
        ("OpenAI", "https://api.openai.com"),
        ("Ollama", "http://localhost:11434"),
    ]
    
    for name, url in default_hosts:
        conn.execute(
            "INSERT OR IGNORE INTO hosts (name, base_url) VALUES (?, ?)",
            (name, url)
        )
    
    # Default models with pricing (as of 2024)
    default_models = [
        # Anthropic models
        ("claude-3-5-sonnet-20241022", "Anthropic", 0.003, 0.015),
        ("claude-3-5-haiku-20241022", "Anthropic", 0.0008, 0.004),
        ("claude-3-opus-20240229", "Anthropic", 0.015, 0.075),
        
        # OpenAI models
        ("gpt-4o", "OpenAI", 0.005, 0.015),
        ("gpt-4o-mini", "OpenAI", 0.00015, 0.0006),
        ("gpt-4-turbo", "OpenAI", 0.01, 0.03),
        ("gpt-3.5-turbo", "OpenAI", 0.0005, 0.0015),
        
        # Ollama models (free)
        ("llama3.2", "Ollama", 0.0, 0.0),
        ("mistral", "Ollama", 0.0, 0.0),
        ("codellama", "Ollama", 0.0, 0.0),
    ]
    
    for model_name, host_name, input_cost, output_cost in default_models:
        # Get host ID
        host_id = conn.execute(
            "SELECT id FROM hosts WHERE name = ?", (host_name,)
        ).fetchone()
        
        if host_id:
            conn.execute("""
                INSERT OR IGNORE INTO models 
                (name, host_id, input_cost_per_1k, output_cost_per_1k) 
                VALUES (?, ?, ?, ?)
            """, (model_name, host_id[0], input_cost, output_cost))
    
    # Insert default feature flags
    default_feature_flags = [
        ('unified_message_processor', 1, 100, 'Use new unified message processing pipeline'),
        ('legacy_fallback', 0, 0, 'Enable fallback to legacy handler on errors'),
        ('intent_classification', 1, 100, 'Enable intent classification for messages'),
        ('advanced_error_handling', 1, 100, 'Use advanced error categorization and recovery')
    ]
    
    for flag_name, enabled, rollout_percentage, description in default_feature_flags:
        conn.execute("""
            INSERT OR IGNORE INTO feature_flags 
            (flag_name, enabled, rollout_percentage, description) 
            VALUES (?, ?, ?, ?)
        """, (flag_name, enabled, rollout_percentage, description))


def migrate_existing_databases() -> None:
    """Migrate data from separate token_usage.db and links.db if they exist."""
    # Initialize the new shared database
    init_database()
    
    # Migrate token usage data
    token_db_path = Path("token_usage.db")
    if token_db_path.exists():
        _migrate_token_data(token_db_path)
    
    # Migrate links data  
    links_db_path = Path("links.db")
    if links_db_path.exists():
        _migrate_links_data(links_db_path)


def _migrate_token_data(old_db_path: Path) -> None:
    """Migrate token usage data from old database."""
    try:
        shared_conn = get_database_connection()
        old_conn = sqlite3.connect(old_db_path)
        old_conn.row_factory = sqlite3.Row
        
        # Migrate projects
        projects = old_conn.execute("SELECT * FROM projects").fetchall()
        for project in projects:
            shared_conn.execute("""
                INSERT OR IGNORE INTO projects (name, description, created_at)
                VALUES (?, ?, ?)
            """, (project["name"], project["description"], project["created_at"]))
        
        # Migrate hosts
        hosts = old_conn.execute("SELECT * FROM hosts").fetchall()
        for host in hosts:
            shared_conn.execute("""
                INSERT OR IGNORE INTO hosts (name, base_url, created_at)
                VALUES (?, ?, ?)
            """, (host["name"], host["base_url"], host["created_at"]))
        
        # Migrate models
        models = old_conn.execute("SELECT * FROM models").fetchall()
        for model in models:
            shared_conn.execute("""
                INSERT OR IGNORE INTO models 
                (name, host_id, input_cost_per_1k, output_cost_per_1k, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (model["name"], model["host_id"], model["input_cost_per_1k"], 
                  model["output_cost_per_1k"], model["created_at"]))
        
        # Migrate token usage
        usage_records = old_conn.execute("SELECT * FROM token_usage").fetchall()
        for record in usage_records:
            shared_conn.execute("""
                INSERT OR IGNORE INTO token_usage 
                (timestamp, project_id, model_id, input_tokens, output_tokens, 
                 total_tokens, cost_usd, request_id, user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (record["timestamp"], record["project_id"], record["model_id"],
                  record["input_tokens"], record["output_tokens"], record["total_tokens"],
                  record["cost_usd"], record["request_id"], record["user_id"], 
                  record["created_at"]))
        
        shared_conn.commit()
        shared_conn.close()
        old_conn.close()
        
        logger.info(f"Migrated token usage data from {old_db_path}")
        
    except Exception as e:
        logger.error(f"Error migrating token data: {e}")


def _migrate_links_data(old_db_path: Path) -> None:
    """Migrate links data from old database."""
    try:
        shared_conn = get_database_connection()
        old_conn = sqlite3.connect(old_db_path)
        old_conn.row_factory = sqlite3.Row
        
        # Migrate links
        links = old_conn.execute("SELECT * FROM links").fetchall()
        for link in links:
            shared_conn.execute("""
                INSERT OR IGNORE INTO links 
                (url, domain, timestamp, analysis_result, analysis_status,
                 title, main_topic, reasons_to_care, error_message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (link["url"], link["domain"], link["timestamp"], link["analysis_result"],
                  link["analysis_status"], link["title"], link["main_topic"], 
                  link["reasons_to_care"], link["error_message"], 
                  link["created_at"], link["updated_at"]))
        
        shared_conn.commit()
        shared_conn.close()
        old_conn.close()
        
        logger.info(f"Migrated links data from {old_db_path}")
        
    except Exception as e:
        logger.error(f"Error migrating links data: {e}")


def cleanup_old_databases() -> None:
    """Remove old separate database files after successful migration."""
    old_files = ["token_usage.db", "links.db"]
    for file_path in old_files:
        path = Path(file_path)
        if path.exists():
            try:
                path.rename(f"{file_path}.backup")
                logger.info(f"Backed up {file_path} to {file_path}.backup")
            except Exception as e:
                logger.error(f"Error backing up {file_path}: {e}")


# Promise Management Functions
def create_promise(chat_id: int, message_id: int, task_description: str, metadata: Optional[dict] = None) -> int:
    """Create a new promise entry in the database.
    
    Args:
        chat_id: Telegram chat ID
        message_id: Telegram message ID that triggered the promise
        task_description: Description of the promised task
        metadata: Optional metadata dict containing message context, user info, etc.
        
    Returns:
        int: Promise ID
    """
    import json
    
    conn = get_database_connection()
    cursor = conn.cursor()
    
    # Serialize metadata to JSON
    metadata_json = json.dumps(metadata) if metadata else None
    
    cursor.execute("""
        INSERT INTO promises (chat_id, message_id, task_description, metadata)
        VALUES (?, ?, ?, ?)
    """, (chat_id, message_id, task_description, metadata_json))
    
    promise_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    logger.info(f"Created promise {promise_id} for chat {chat_id}")
    return promise_id


def update_promise_status(promise_id: int, status: str, result_summary: Optional[str] = None, 
                         error_message: Optional[str] = None) -> None:
    """Update the status of a promise.
    
    Args:
        promise_id: Promise ID to update
        status: New status ('pending', 'in_progress', 'completed', 'failed')
        result_summary: Summary of results if completed
        error_message: Error message if failed
    """
    conn = get_database_connection()
    
    if status == 'completed' or status == 'failed':
        conn.execute("""
            UPDATE promises 
            SET status = ?, completed_at = CURRENT_TIMESTAMP, 
                result_summary = ?, error_message = ?
            WHERE id = ?
        """, (status, result_summary, error_message, promise_id))
    else:
        conn.execute("""
            UPDATE promises 
            SET status = ?
            WHERE id = ?
        """, (status, promise_id))
    
    conn.commit()
    conn.close()
    
    logger.info(f"Updated promise {promise_id} status to {status}")


def get_promise(promise_id: int) -> Optional[dict]:
    """Get a promise by ID.
    
    Args:
        promise_id: Promise ID to retrieve
        
    Returns:
        dict: Promise data or None if not found
    """
    conn = get_database_connection()
    conn.row_factory = sqlite3.Row
    
    result = conn.execute("""
        SELECT * FROM promises WHERE id = ?
    """, (promise_id,)).fetchone()
    
    conn.close()
    
    return dict(result) if result else None


def get_pending_promises(chat_id: Optional[int] = None) -> list:
    """Get all pending promises, optionally filtered by chat.
    
    Args:
        chat_id: Optional chat ID to filter by
        
    Returns:
        list: List of promise dictionaries
    """
    conn = get_database_connection()
    conn.row_factory = sqlite3.Row
    
    if chat_id:
        results = conn.execute("""
            SELECT * FROM promises 
            WHERE status = 'pending' AND chat_id = ?
            ORDER BY created_at ASC
        """, (chat_id,)).fetchall()
    else:
        results = conn.execute("""
            SELECT * FROM promises 
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """).fetchall()
    
    conn.close()
    
    return [dict(row) for row in results]


# Message Queue Functions
def queue_missed_message(
    chat_id: int,
    message_text: str,
    sender_username: Optional[str] = None,
    message_id: Optional[int] = None,
    original_timestamp: Optional[str] = None,
    metadata: Optional[dict] = None
) -> int:
    """Queue a missed message for later processing.
    
    Args:
        chat_id: Telegram chat ID
        message_text: The message content
        sender_username: Username of sender
        message_id: Original message ID
        original_timestamp: When message was originally sent
        metadata: Additional message data as dict
        
    Returns:
        int: Message queue ID
    """
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO message_queue (
                chat_id, message_id, message_text, message_type,
                sender_username, original_timestamp, metadata
            ) VALUES (?, ?, ?, 'missed', ?, ?, ?)
        """, (
            chat_id, message_id, message_text,
            sender_username, original_timestamp,
            json.dumps(metadata) if metadata else None
        ))
        
        message_queue_id = cursor.lastrowid
        conn.commit()
        
    logger.info(f"Queued missed message {message_queue_id} from {sender_username} in chat {chat_id}")
    return message_queue_id


def get_pending_messages(limit: int = 10) -> list:
    """Get pending messages from the queue.
    
    Args:
        limit: Maximum number of messages to return
        
    Returns:
        list: List of message dictionaries
    """
    with get_database_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        results = cursor.execute("""
            SELECT * FROM message_queue 
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
        
    return [dict(row) for row in results]


def update_message_queue_status(
    message_id: int,
    status: str,
    error_message: Optional[str] = None
) -> None:
    """Update the status of a queued message.
    
    Args:
        message_id: Message queue ID
        status: New status ('processing', 'completed', 'failed')
        error_message: Error message if failed
    """
    with get_database_connection() as conn:
        cursor = conn.cursor()
        
        if status == 'completed':
            cursor.execute("""
                UPDATE message_queue 
                SET status = ?, processed_at = ?
                WHERE id = ?
            """, (status, datetime.utcnow().isoformat(), message_id))
        elif status == 'failed' and error_message:
            cursor.execute("""
                UPDATE message_queue 
                SET status = ?, error_message = ?, processed_at = ?
                WHERE id = ?
            """, (status, error_message, datetime.utcnow().isoformat(), message_id))
        else:
            cursor.execute("""
                UPDATE message_queue 
                SET status = ?
                WHERE id = ?
            """, (status, message_id))
        
        conn.commit()
        
    logger.debug(f"Updated message queue {message_id} status to {status}")


def update_chat_state(chat_id: int, last_seen_message_id: int = None, 
                     last_seen_timestamp: str = None, bot_online: bool = None) -> None:
    """Update chat state tracking for missed message detection."""
    from datetime import datetime
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        
        # Get current state
        cursor.execute("SELECT * FROM chat_state WHERE chat_id = ?", (chat_id,))
        existing = cursor.fetchone()
        
        now = datetime.utcnow().isoformat()
        
        if existing:
            # Update existing record
            updates = ["updated_at = ?"]
            params = [now]
            
            if last_seen_message_id is not None:
                updates.extend(["last_seen_message_id = ?", "last_seen_timestamp = ?"])
                params.extend([last_seen_message_id, last_seen_timestamp or now])
            
            if bot_online is True:
                updates.append("bot_last_online = ?")
                params.append(now)
            elif bot_online is False:
                updates.append("bot_last_offline = ?")
                params.append(now)
            
            params.append(chat_id)
            
            cursor.execute(f"""
                UPDATE chat_state 
                SET {', '.join(updates)}
                WHERE chat_id = ?
            """, params)
        else:
            # Create new record
            cursor.execute("""
                INSERT INTO chat_state 
                (chat_id, last_seen_message_id, last_seen_timestamp, bot_last_online, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (chat_id, last_seen_message_id, last_seen_timestamp or now, now, now, now))
        
        conn.commit()


def get_chat_state(chat_id: int) -> Optional[dict]:
    """Get chat state for missed message tracking."""
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM chat_state WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        
        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None


def queue_missed_message(chat_id: int, message_id: int, message_text: str, 
                        sender_username: str, original_timestamp: str, metadata: dict = None) -> int:
    """Queue a missed message for background processing."""
    import json
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO message_queue 
            (chat_id, message_id, message_text, message_type, sender_username, 
             original_timestamp, metadata)
            VALUES (?, ?, ?, 'missed', ?, ?, ?)
        """, (chat_id, message_id, message_text, sender_username, 
              original_timestamp, json.dumps(metadata or {})))
        
        conn.commit()
        return cursor.lastrowid


def get_pending_missed_messages(chat_id: int = None) -> list:
    """Get pending missed messages for processing."""
    with get_database_connection() as conn:
        cursor = conn.cursor()
        
        if chat_id:
            cursor.execute("""
                SELECT * FROM message_queue 
                WHERE chat_id = ? AND message_type = 'missed' AND status = 'pending'
                ORDER BY original_timestamp ASC
            """, (chat_id,))
        else:
            cursor.execute("""
                SELECT * FROM message_queue 
                WHERE message_type = 'missed' AND status = 'pending'
                ORDER BY original_timestamp ASC
            """)
        
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]


def mark_scan_completed(chat_id: int, messages_scanned: int) -> None:
    """Mark that we completed a full message scan for a chat."""
    from datetime import datetime
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        
        cursor.execute("""
            UPDATE chat_state 
            SET scan_completed_at = ?, total_messages_scanned = ?, updated_at = ?
            WHERE chat_id = ?
        """, (now, messages_scanned, now, chat_id))
        
        if cursor.rowcount == 0:
            # Create record if it doesn't exist
            cursor.execute("""
                INSERT INTO chat_state 
                (chat_id, scan_completed_at, total_messages_scanned, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (chat_id, now, messages_scanned, now, now))
        
        conn.commit()
    
    logger.debug(f"Marked scan completed for chat {chat_id}, scanned {messages_scanned} messages")