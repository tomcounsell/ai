"""
Claude Code Session Management

Manages persistent Claude Code sessions for continuity between chat messages.
Stores session IDs in SQLite database and enables session reuse for follow-up
questions and continued work in the same context.
"""

import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, List
from dataclasses import dataclass

from .database import get_database_connection


@dataclass
class ClaudeCodeSession:
    """Represents a Claude Code session with metadata"""
    session_id: str
    chat_id: Optional[str]
    username: Optional[str]
    tool_name: str
    working_directory: str
    initial_task: str
    task_count: int
    last_activity: datetime
    created_at: datetime
    is_active: bool
    session_metadata: Dict


class ClaudeCodeSessionManager:
    """Manages Claude Code sessions for persistent context"""
    
    # Session expiry settings
    SESSION_EXPIRY_HOURS = 24  # Sessions expire after 24 hours
    MAX_SESSIONS_PER_CHAT = 5  # Keep max 5 sessions per chat to avoid clutter
    
    @staticmethod
    def extract_session_id_from_output(claude_output: str) -> Optional[str]:
        """
        Extract Claude Code session ID from command output.
        
        Claude Code typically outputs session information in the format of UUIDs.
        This method looks for UUID patterns in the output.
        
        Args:
            claude_output: Raw output from Claude Code execution
            
        Returns:
            Session ID if found, None otherwise
        """
        # Look for UUID patterns (e.g., 550e8400-e29b-41d4-a716-446655440000)
        uuid_pattern = r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
        matches = re.findall(uuid_pattern, claude_output, re.IGNORECASE)
        
        # Return the first UUID found (assuming it's the session ID)
        return matches[0] if matches else None
    
    @staticmethod
    def store_session(
        session_id: str,
        chat_id: Optional[str],
        username: Optional[str],
        tool_name: str,
        working_directory: str,
        task_description: str,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Store a new Claude Code session in the database.
        
        Args:
            session_id: Claude Code session UUID
            chat_id: Telegram chat ID
            username: Username who initiated the session
            tool_name: 'delegate_coding_task' or 'technical_analysis'
            working_directory: Working directory for the session
            task_description: Description of the initial task
            metadata: Additional session metadata
            
        Returns:
            True if stored successfully, False otherwise
        """
        try:
            with get_database_connection() as conn:
                conn.execute("""
                    INSERT INTO claude_code_sessions 
                    (session_id, chat_id, username, tool_name, working_directory, 
                     initial_task, task_count, last_activity, created_at, is_active, session_metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    chat_id,
                    username,
                    tool_name,
                    working_directory,
                    task_description,
                    1,  # task_count
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    True,  # is_active
                    json.dumps(metadata or {})
                ))
                
                # Clean up old sessions to avoid database bloat
                ClaudeCodeSessionManager._cleanup_old_sessions(conn, chat_id)
                
                return True
                
        except sqlite3.Error as e:
            print(f"Error storing Claude Code session: {e}")
            return False
    
    @staticmethod
    def find_recent_session(
        chat_id: Optional[str],
        username: Optional[str],
        tool_name: Optional[str] = None,
        working_directory: Optional[str] = None,
        hours_back: int = 2
    ) -> Optional[ClaudeCodeSession]:
        """
        Find the most recent active session that matches the criteria.
        
        Args:
            chat_id: Telegram chat ID to search within
            username: Username to search for
            tool_name: Specific tool name to match (optional)
            working_directory: Specific working directory to match (optional)
            hours_back: How many hours back to search
            
        Returns:
            Most recent matching session or None
        """
        try:
            with get_database_connection() as conn:
                # Build query conditions
                conditions = ["is_active = 1"]
                params = []
                
                cutoff_time = datetime.now() - timedelta(hours=hours_back)
                conditions.append("last_activity > ?")
                params.append(cutoff_time.isoformat())
                
                if chat_id:
                    conditions.append("chat_id = ?")
                    params.append(chat_id)
                
                if username:
                    conditions.append("username = ?")
                    params.append(username)
                
                if tool_name:
                    conditions.append("tool_name = ?")
                    params.append(tool_name)
                
                if working_directory:
                    conditions.append("working_directory = ?")
                    params.append(working_directory)
                
                query = f"""
                    SELECT session_id, chat_id, username, tool_name, working_directory,
                           initial_task, task_count, last_activity, created_at, is_active, session_metadata
                    FROM claude_code_sessions
                    WHERE {' AND '.join(conditions)}
                    ORDER BY last_activity DESC
                    LIMIT 1
                """
                
                cursor = conn.execute(query, params)
                row = cursor.fetchone()
                
                if row:
                    return ClaudeCodeSession(
                        session_id=row[0],
                        chat_id=row[1],
                        username=row[2],
                        tool_name=row[3],
                        working_directory=row[4],
                        initial_task=row[5],
                        task_count=row[6],
                        last_activity=datetime.fromisoformat(row[7]),
                        created_at=datetime.fromisoformat(row[8]),
                        is_active=bool(row[9]),
                        session_metadata=json.loads(row[10] or '{}')
                    )
                
                return None
                
        except (sqlite3.Error, json.JSONDecodeError, ValueError) as e:
            print(f"Error finding recent session: {e}")
            return None
    
    @staticmethod
    def update_session_activity(
        session_id: str,
        new_task_description: Optional[str] = None
    ) -> bool:
        """
        Update session activity timestamp and optionally increment task count.
        
        Args:
            session_id: Claude Code session UUID
            new_task_description: If provided, increments task count
            
        Returns:
            True if updated successfully, False otherwise
        """
        try:
            with get_database_connection() as conn:
                if new_task_description:
                    # Increment task count for new task
                    cursor = conn.execute("""
                        UPDATE claude_code_sessions 
                        SET last_activity = ?, task_count = task_count + 1
                        WHERE session_id = ? AND is_active = 1
                    """, (datetime.now().isoformat(), session_id))
                else:
                    # Just update activity timestamp
                    cursor = conn.execute("""
                        UPDATE claude_code_sessions 
                        SET last_activity = ?
                        WHERE session_id = ? AND is_active = 1
                    """, (datetime.now().isoformat(), session_id))
                
                return cursor.rowcount > 0
                
        except sqlite3.Error as e:
            print(f"Error updating session activity: {e}")
            return False
    
    @staticmethod
    def deactivate_session(session_id: str) -> bool:
        """
        Mark a session as inactive (but keep for reference).
        
        Args:
            session_id: Claude Code session UUID
            
        Returns:
            True if deactivated successfully, False otherwise
        """
        try:
            with get_database_connection() as conn:
                cursor = conn.execute("""
                    UPDATE claude_code_sessions 
                    SET is_active = 0, last_activity = ?
                    WHERE session_id = ?
                """, (datetime.now().isoformat(), session_id))
                
                return cursor.rowcount > 0
                
        except sqlite3.Error as e:
            print(f"Error deactivating session: {e}")
            return False
    
    @staticmethod
    def get_chat_sessions(
        chat_id: str,
        limit: int = 10,
        active_only: bool = True
    ) -> List[ClaudeCodeSession]:
        """
        Get all sessions for a specific chat.
        
        Args:
            chat_id: Telegram chat ID
            limit: Maximum number of sessions to return
            active_only: Whether to only return active sessions
            
        Returns:
            List of sessions for the chat
        """
        try:
            with get_database_connection() as conn:
                conditions = ["chat_id = ?"]
                params = [chat_id]
                
                if active_only:
                    conditions.append("is_active = 1")
                
                query = f"""
                    SELECT session_id, chat_id, username, tool_name, working_directory,
                           initial_task, task_count, last_activity, created_at, is_active, session_metadata
                    FROM claude_code_sessions
                    WHERE {' AND '.join(conditions)}
                    ORDER BY last_activity DESC
                    LIMIT ?
                """
                params.append(limit)
                
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                
                sessions = []
                for row in rows:
                    sessions.append(ClaudeCodeSession(
                        session_id=row[0],
                        chat_id=row[1],
                        username=row[2],
                        tool_name=row[3],
                        working_directory=row[4],
                        initial_task=row[5],
                        task_count=row[6],
                        last_activity=datetime.fromisoformat(row[7]),
                        created_at=datetime.fromisoformat(row[8]),
                        is_active=bool(row[9]),
                        session_metadata=json.loads(row[10] or '{}')
                    ))
                
                return sessions
                
        except (sqlite3.Error, json.JSONDecodeError, ValueError) as e:
            print(f"Error getting chat sessions: {e}")
            return []
    
    @staticmethod
    def build_session_command(
        base_prompt: str,
        session_id: Optional[str] = None,
        should_continue: bool = False
    ) -> str:
        """
        Build Claude Code command with session management.
        
        Args:
            base_prompt: The prompt to send to Claude Code
            session_id: Specific session ID to resume
            should_continue: Whether to continue the most recent session
            
        Returns:
            Complete Claude Code command string
        """
        if session_id:
            return f'claude -r {session_id} "{base_prompt}"'
        elif should_continue:
            return f'claude -c "{base_prompt}"'
        else:
            return f'claude "{base_prompt}"'
    
    @staticmethod
    def _cleanup_old_sessions(conn: sqlite3.Connection, chat_id: Optional[str]) -> None:
        """
        Clean up old sessions to prevent database bloat.
        
        Args:
            conn: Database connection
            chat_id: Chat ID to clean up (optional)
        """
        try:
            # Deactivate sessions older than expiry time
            expiry_time = datetime.now() - timedelta(hours=ClaudeCodeSessionManager.SESSION_EXPIRY_HOURS)
            conn.execute("""
                UPDATE claude_code_sessions 
                SET is_active = 0 
                WHERE last_activity < ? AND is_active = 1
            """, (expiry_time.isoformat(),))
            
            # If chat_id provided, limit sessions per chat
            if chat_id:
                # Keep only the most recent MAX_SESSIONS_PER_CHAT active sessions per chat
                conn.execute("""
                    UPDATE claude_code_sessions 
                    SET is_active = 0 
                    WHERE chat_id = ? AND is_active = 1 
                    AND id NOT IN (
                        SELECT id FROM claude_code_sessions 
                        WHERE chat_id = ? AND is_active = 1 
                        ORDER BY last_activity DESC 
                        LIMIT ?
                    )
                """, (chat_id, chat_id, ClaudeCodeSessionManager.MAX_SESSIONS_PER_CHAT))
                
        except sqlite3.Error as e:
            print(f"Error cleaning up old sessions: {e}")
    
    @staticmethod
    def format_session_summary(session: ClaudeCodeSession) -> str:
        """
        Format a session for display to users.
        
        Args:
            session: Claude Code session to format
            
        Returns:
            Formatted session summary
        """
        age = datetime.now() - session.last_activity
        
        if age.total_seconds() < 3600:  # Less than 1 hour
            age_str = f"{int(age.total_seconds() / 60)}m ago"
        elif age.total_seconds() < 86400:  # Less than 1 day
            age_str = f"{int(age.total_seconds() / 3600)}h ago"
        else:
            age_str = f"{int(age.days)}d ago"
        
        return (
            f"📋 **{session.tool_name}** ({session.task_count} tasks)\n"
            f"   🔍 {session.initial_task[:60]}{'...' if len(session.initial_task) > 60 else ''}\n"
            f"   📁 {session.working_directory}\n"
            f"   🕐 {age_str} • Session: `{session.session_id[:8]}...`"
        )