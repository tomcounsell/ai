"""
Notion integration package for database querying and project management.

This package provides unified Notion API integration used by both the CLI
NotionScout agent and the PydanticAI tool integration.
"""

from .query_engine import (
    NotionQueryEngine,
    get_notion_engine,
    query_notion_workspace_sync,
    query_notion_workspace_async,
    WORKSPACE_SETTINGS,
    WORKSPACE_ALIASES
)

__all__ = [
    "NotionQueryEngine",
    "get_notion_engine", 
    "query_notion_workspace_sync",
    "query_notion_workspace_async",
    "WORKSPACE_SETTINGS",
    "WORKSPACE_ALIASES"
]
