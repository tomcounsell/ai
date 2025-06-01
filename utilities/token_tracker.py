"""Token usage tracking system for AI model interactions.

This module provides comprehensive token usage tracking and reporting
functionality with shared SQLite backend storage.
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Union
from dataclasses import dataclass
from pathlib import Path

from .database import get_database_connection, init_database

logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    """Token usage data for a single request."""
    timestamp: datetime
    project: str
    host: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: Optional[float] = None
    request_id: Optional[str] = None
    user_id: Optional[str] = None


class TokenTracker:
    """SQLite-based token usage tracking system using shared database."""
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize token tracker with database."""
        self.db_path = db_path
        if db_path is None:
            # Use shared database
            init_database()
        else:
            # Use custom database for testing
            self._init_custom_database()
    
    def _init_custom_database(self) -> None:
        """Initialize custom database with required tables (for testing)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
                    
                    -- Indexes for performance
                    CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_token_usage_project ON token_usage(project_id);
                    CREATE INDEX IF NOT EXISTS idx_token_usage_model ON token_usage(model_id);
                    CREATE INDEX IF NOT EXISTS idx_token_usage_user ON token_usage(user_id);
                    CREATE INDEX IF NOT EXISTS idx_token_usage_request ON token_usage(request_id);
                """)
                
                # Insert default hosts for testing
                self._insert_default_data_custom(conn)
                
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")
            raise
    
    def _insert_default_data_custom(self, conn: sqlite3.Connection) -> None:
        """Insert default hosts and models for testing."""
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
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection (shared or custom)."""
        if self.db_path is None:
            return get_database_connection()
        else:
            return sqlite3.connect(self.db_path)
    
    def _ensure_project_exists(self, project_name: str) -> int:
        """Ensure project exists and return its ID."""
        with self._get_connection() as conn:
            # Try to get existing project
            result = conn.execute(
                "SELECT id FROM projects WHERE name = ?", (project_name,)
            ).fetchone()
            
            if result:
                return result[0]
            
            # Create new project
            cursor = conn.execute(
                "INSERT INTO projects (name) VALUES (?)", (project_name,)
            )
            return cursor.lastrowid
    
    def _ensure_model_exists(self, model_name: str, host_name: str) -> int:
        """Ensure model exists and return its ID."""
        with self._get_connection() as conn:
            # Try to get existing model
            result = conn.execute("""
                SELECT m.id FROM models m
                JOIN hosts h ON m.host_id = h.id
                WHERE m.name = ? AND h.name = ?
            """, (model_name, host_name)).fetchone()
            
            if result:
                return result[0]
            
            # Get or create host
            host_result = conn.execute(
                "SELECT id FROM hosts WHERE name = ?", (host_name,)
            ).fetchone()
            
            if not host_result:
                cursor = conn.execute(
                    "INSERT INTO hosts (name) VALUES (?)", (host_name,)
                )
                host_id = cursor.lastrowid
            else:
                host_id = host_result[0]
            
            # Create new model
            cursor = conn.execute(
                "INSERT INTO models (name, host_id) VALUES (?, ?)",
                (model_name, host_id)
            )
            return cursor.lastrowid
    
    def log_usage(
        self,
        project: str,
        host: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        timestamp: Optional[datetime] = None,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> int:
        """Log token usage and return record ID."""
        if timestamp is None:
            timestamp = datetime.utcnow()
        
        total_tokens = input_tokens + output_tokens
        
        try:
            project_id = self._ensure_project_exists(project)
            model_id = self._ensure_model_exists(model, host)
            
            # Calculate cost if model pricing is available
            cost_usd = self._calculate_cost(model_id, input_tokens, output_tokens)
            
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    INSERT INTO token_usage 
                    (timestamp, project_id, model_id, input_tokens, output_tokens, 
                     total_tokens, cost_usd, request_id, user_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp, project_id, model_id, input_tokens, output_tokens,
                    total_tokens, cost_usd, request_id, user_id
                ))
                
                record_id = cursor.lastrowid
                logger.info(f"Logged token usage: {total_tokens} tokens for {project}/{host}/{model}")
                return record_id
                
        except sqlite3.Error as e:
            logger.error(f"Error logging token usage: {e}")
            raise
    
    def _calculate_cost(self, model_id: int, input_tokens: int, output_tokens: int) -> Optional[float]:
        """Calculate cost based on model pricing."""
        try:
            with self._get_connection() as conn:
                result = conn.execute("""
                    SELECT input_cost_per_1k, output_cost_per_1k 
                    FROM models WHERE id = ?
                """, (model_id,)).fetchone()
                
                if result and result[0] is not None and result[1] is not None:
                    input_cost, output_cost = result
                    total_cost = (input_tokens * input_cost / 1000) + (output_tokens * output_cost / 1000)
                    return round(total_cost, 6)
                    
        except sqlite3.Error as e:
            logger.error(f"Error calculating cost: {e}")
        
        return None
    
    def get_usage_summary(
        self,
        project: Optional[str] = None,
        host: Optional[str] = None,
        model: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        user_id: Optional[str] = None
    ) -> Dict[str, Union[int, float]]:
        """Get usage summary with optional filters."""
        where_conditions = []
        params = []
        
        if project:
            where_conditions.append("p.name = ?")
            params.append(project)
        
        if host:
            where_conditions.append("h.name = ?")
            params.append(host)
        
        if model:
            where_conditions.append("m.name = ?")
            params.append(model)
        
        if start_date:
            where_conditions.append("tu.timestamp >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("tu.timestamp <= ?")
            params.append(end_date)
        
        if user_id:
            where_conditions.append("tu.user_id = ?")
            params.append(user_id)
        
        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
        
        query = f"""
            SELECT 
                COUNT(*) as request_count,
                SUM(tu.input_tokens) as total_input_tokens,
                SUM(tu.output_tokens) as total_output_tokens,
                SUM(tu.total_tokens) as total_tokens,
                SUM(tu.cost_usd) as total_cost_usd,
                AVG(tu.total_tokens) as avg_tokens_per_request
            FROM token_usage tu
            JOIN projects p ON tu.project_id = p.id
            JOIN models m ON tu.model_id = m.id
            JOIN hosts h ON m.host_id = h.id
            {where_clause}
        """
        
        try:
            with self._get_connection() as conn:
                result = conn.execute(query, params).fetchone()
                
                return {
                    "request_count": result[0] or 0,
                    "total_input_tokens": result[1] or 0,
                    "total_output_tokens": result[2] or 0,
                    "total_tokens": result[3] or 0,
                    "total_cost_usd": round(result[4] or 0, 4),
                    "avg_tokens_per_request": round(result[5] or 0, 2)
                }
                
        except sqlite3.Error as e:
            logger.error(f"Error getting usage summary: {e}")
            return {}
    
    def get_usage_by_project(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """Get usage breakdown by project."""
        where_conditions = []
        params = []
        
        if start_date:
            where_conditions.append("tu.timestamp >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("tu.timestamp <= ?")
            params.append(end_date)
        
        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
        
        query = f"""
            SELECT 
                p.name as project,
                COUNT(*) as request_count,
                SUM(tu.total_tokens) as total_tokens,
                SUM(tu.cost_usd) as total_cost_usd
            FROM token_usage tu
            JOIN projects p ON tu.project_id = p.id
            {where_clause}
            GROUP BY p.name
            ORDER BY total_tokens DESC
        """
        
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                results = conn.execute(query, params).fetchall()
                
                return [
                    {
                        "project": row["project"],
                        "request_count": row["request_count"],
                        "total_tokens": row["total_tokens"],
                        "total_cost_usd": round(row["total_cost_usd"] or 0, 4)
                    }
                    for row in results
                ]
                
        except sqlite3.Error as e:
            logger.error(f"Error getting usage by project: {e}")
            return []
    
    def get_usage_by_model(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """Get usage breakdown by model."""
        where_conditions = []
        params = []
        
        if start_date:
            where_conditions.append("tu.timestamp >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("tu.timestamp <= ?")
            params.append(end_date)
        
        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
        
        query = f"""
            SELECT 
                h.name as host,
                m.name as model,
                COUNT(*) as request_count,
                SUM(tu.total_tokens) as total_tokens,
                SUM(tu.cost_usd) as total_cost_usd
            FROM token_usage tu
            JOIN models m ON tu.model_id = m.id
            JOIN hosts h ON m.host_id = h.id
            {where_clause}
            GROUP BY h.name, m.name
            ORDER BY total_tokens DESC
        """
        
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                results = conn.execute(query, params).fetchall()
                
                return [
                    {
                        "host": row["host"],
                        "model": row["model"],
                        "request_count": row["request_count"],
                        "total_tokens": row["total_tokens"],
                        "total_cost_usd": round(row["total_cost_usd"] or 0, 4)
                    }
                    for row in results
                ]
                
        except sqlite3.Error as e:
            logger.error(f"Error getting usage by model: {e}")
            return []
    
    def get_daily_usage(
        self,
        days: int = 30,
        project: Optional[str] = None
    ) -> List[Dict]:
        """Get daily usage for the last N days."""
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)
        
        where_conditions = ["tu.timestamp >= ?", "tu.timestamp <= ?"]
        params = [start_date, end_date]
        
        if project:
            where_conditions.append("p.name = ?")
            params.append(project)
        
        where_clause = "WHERE " + " AND ".join(where_conditions)
        
        query = f"""
            SELECT 
                DATE(tu.timestamp) as date,
                COUNT(*) as request_count,
                SUM(tu.total_tokens) as total_tokens,
                SUM(tu.cost_usd) as total_cost_usd
            FROM token_usage tu
            JOIN projects p ON tu.project_id = p.id
            {where_clause}
            GROUP BY DATE(tu.timestamp)
            ORDER BY date DESC
        """
        
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                results = conn.execute(query, params).fetchall()
                
                return [
                    {
                        "date": row["date"],
                        "request_count": row["request_count"],
                        "total_tokens": row["total_tokens"],
                        "total_cost_usd": round(row["total_cost_usd"] or 0, 4)
                    }
                    for row in results
                ]
                
        except sqlite3.Error as e:
            logger.error(f"Error getting daily usage: {e}")
            return []
    
    def export_usage_data(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        format: str = "csv"
    ) -> str:
        """Export usage data in specified format."""
        where_conditions = []
        params = []
        
        if start_date:
            where_conditions.append("tu.timestamp >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("tu.timestamp <= ?")
            params.append(end_date)
        
        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
        
        query = f"""
            SELECT 
                tu.timestamp,
                p.name as project,
                h.name as host,
                m.name as model,
                tu.input_tokens,
                tu.output_tokens,
                tu.total_tokens,
                tu.cost_usd,
                tu.request_id,
                tu.user_id
            FROM token_usage tu
            JOIN projects p ON tu.project_id = p.id
            JOIN models m ON tu.model_id = m.id
            JOIN hosts h ON m.host_id = h.id
            {where_clause}
            ORDER BY tu.timestamp DESC
        """
        
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                results = conn.execute(query, params).fetchall()
                
                if format.lower() == "csv":
                    import csv
                    import io
                    
                    output = io.StringIO()
                    writer = csv.writer(output)
                    
                    # Write header
                    writer.writerow([
                        "timestamp", "project", "host", "model",
                        "input_tokens", "output_tokens", "total_tokens",
                        "cost_usd", "request_id", "user_id"
                    ])
                    
                    # Write data
                    for row in results:
                        writer.writerow([
                            row["timestamp"], row["project"], row["host"], row["model"],
                            row["input_tokens"], row["output_tokens"], row["total_tokens"],
                            row["cost_usd"], row["request_id"], row["user_id"]
                        ])
                    
                    return output.getvalue()
                
                else:
                    raise ValueError(f"Unsupported export format: {format}")
                    
        except sqlite3.Error as e:
            logger.error(f"Error exporting usage data: {e}")
            return ""


# Global tracker instance
_tracker = None


def get_tracker(db_path: Optional[str] = None) -> TokenTracker:
    """Get global token tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = TokenTracker(db_path)
    return _tracker


def log_token_usage(
    project: str,
    host: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    timestamp: Optional[datetime] = None,
    request_id: Optional[str] = None,
    user_id: Optional[str] = None
) -> int:
    """Convenience function to log token usage."""
    tracker = get_tracker()
    return tracker.log_usage(
        project=project,
        host=host,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        timestamp=timestamp,
        request_id=request_id,
        user_id=user_id
    )