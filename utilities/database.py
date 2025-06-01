"""
Shared database utilities for the AI agent system.

Provides a centralized SQLite database for all system data including
token usage tracking, link analysis, and other persistent storage needs.
"""

import sqlite3
import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_database_path() -> Path:
    """Get the path to the shared system database."""
    return Path("system.db")


def get_database_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get a connection to the shared system database.
    
    Args:
        db_path: Optional custom database path. If None, uses default system.db
        
    Returns:
        sqlite3.Connection: Database connection
    """
    if db_path is None:
        db_path = get_database_path()
    
    return sqlite3.connect(db_path)


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
            """)
            
            # Insert default data
            _insert_default_data(conn)
            
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        raise


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