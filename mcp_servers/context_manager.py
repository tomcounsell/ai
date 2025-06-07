#!/usr/bin/env python3
"""
Context Manager for MCP Tools

Provides a mechanism to inject and manage context data for MCP tools,
particularly chat_id, username, and conversation history.

This addresses the bug where MCP tools expect context but receive none
when called through Claude Code.
"""

import os
import json
import threading
from typing import Dict, Any, Optional
from pathlib import Path


class MCPContextManager:
    """
    Thread-safe context manager for MCP tools.
    
    Provides a way to store and retrieve context data (chat_id, username, etc.)
    that MCP tools need but can't receive directly through function parameters
    when called from Claude Code.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._context_data: Dict[str, Any] = {}
        self._context_lock = threading.Lock()
        self._context_file = Path.home() / ".cache" / "ai_agent" / "mcp_context.json"
        self._ensure_cache_dir()
        self._load_context()
    
    def _ensure_cache_dir(self):
        """Ensure the cache directory exists."""
        self._context_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _load_context(self):
        """Load context from persistent file if it exists."""
        try:
            if self._context_file.exists():
                with open(self._context_file, 'r') as f:
                    self._context_data = json.load(f)
        except Exception:
            # If loading fails, start with empty context
            self._context_data = {}
    
    def _save_context(self):
        """Save context to persistent file."""
        try:
            with open(self._context_file, 'w') as f:
                json.dump(self._context_data, f, indent=2)
        except Exception:
            # If saving fails, context still exists in memory
            pass
    
    def set_context(self, chat_id: str = None, username: str = None, 
                   workspace: str = None, **kwargs):
        """
        Set context data for MCP tools.
        
        Args:
            chat_id: The current chat/conversation ID
            username: The current user's username
            workspace: The current workspace name
            **kwargs: Additional context data
        """
        with self._context_lock:
            if chat_id is not None:
                self._context_data['chat_id'] = str(chat_id)
            if username is not None:
                self._context_data['username'] = username
            if workspace is not None:
                self._context_data['workspace'] = workspace
            
            # Add any additional context
            self._context_data.update(kwargs)
            
            # Persist to file
            self._save_context()
    
    def get_context(self, key: str = None) -> Any:
        """
        Get context data.
        
        Args:
            key: Specific context key to retrieve, or None for all context
            
        Returns:
            Context value for key, or all context if key is None
        """
        with self._context_lock:
            if key is None:
                return self._context_data.copy()
            return self._context_data.get(key, "")
    
    def get_chat_id(self) -> str:
        """Get the current chat ID, with fallback logic."""
        chat_id = self.get_context('chat_id')
        if chat_id:
            return chat_id
        
        # Fallback: try to detect from environment or other sources
        env_chat_id = os.environ.get('CURRENT_CHAT_ID', '')
        if env_chat_id:
            self.set_context(chat_id=env_chat_id)
            return env_chat_id
        
        return ""
    
    def get_username(self) -> str:
        """Get the current username, with fallback logic."""
        username = self.get_context('username')
        if username:
            return username
        
        # Fallback: try to detect from environment
        env_username = os.environ.get('CURRENT_USERNAME', '')
        if env_username:
            self.set_context(username=env_username)
            return env_username
        
        return ""
    
    def clear_context(self):
        """Clear all context data."""
        with self._context_lock:
            self._context_data.clear()
            self._save_context()
    
    def inject_context_params(self, chat_id: str = "", username: str = "") -> tuple:
        """
        Inject context parameters for MCP tool functions.
        
        This method provides context parameters for tools that expect them,
        using provided values or falling back to stored context.
        
        Args:
            chat_id: Provided chat_id (may be empty)
            username: Provided username (may be empty)
            
        Returns:
            Tuple of (resolved_chat_id, resolved_username)
        """
        resolved_chat_id = chat_id or self.get_chat_id()
        resolved_username = username or self.get_username()
        
        return resolved_chat_id, resolved_username


# Global instance for easy access
context_manager = MCPContextManager()


def get_context_manager() -> MCPContextManager:
    """Get the global context manager instance."""
    return context_manager


def set_mcp_context(chat_id: str = None, username: str = None, **kwargs):
    """Convenience function to set MCP context."""
    context_manager.set_context(chat_id=chat_id, username=username, **kwargs)


def get_mcp_context(key: str = None):
    """Convenience function to get MCP context."""
    return context_manager.get_context(key)


def inject_context_for_tool(chat_id: str = "", username: str = ""):
    """
    Convenience function to inject context for MCP tools.
    
    This is the main function that MCP tools should use to get context
    when they don't receive it through parameters.
    """
    return context_manager.inject_context_params(chat_id, username)