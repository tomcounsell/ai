#!/usr/bin/env python3
"""
Notion Tools MCP Server

Provides Notion workspace querying and analysis tools for Claude Code integration.
Uses the shared NotionQueryEngine for all database operations.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from integrations.notion.query_engine import query_notion_workspace_sync, WORKSPACE_SETTINGS

# Load environment variables
load_dotenv()

# Initialize MCP server
mcp = FastMCP("Notion Tools")


@mcp.tool()
def query_notion_projects(workspace_name: str, question: str) -> str:
    """Query a Notion workspace database and get AI-powered analysis.
    
    This tool provides access to Notion project databases for querying tasks,
    priorities, and project status using natural language.
    
    Args:
        workspace_name: Name of the workspace to query (e.g., "PsyOPTIMAL", "FlexTrip")
        question: Natural language question about the workspace data
        
    Returns:
        str: AI-generated answer with specific task details and recommendations
    """
    return query_notion_workspace_sync(workspace_name, question)


@mcp.tool()
def list_notion_workspaces() -> str:
    """List all available Notion workspaces and their descriptions.
    
    Returns:
        str: Formatted list of available workspaces
    """
    workspaces = []
    for name, config in WORKSPACE_SETTINGS.items():
        workspaces.append(f"• **{name}**: {config['description']}")
    
    return "📁 **Available Notion Workspaces:**\n\n" + "\n".join(workspaces)


if __name__ == "__main__":
    mcp.run()