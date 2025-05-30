#!/usr/bin/env python3
"""
Notion Tools MCP Server

Provides Notion workspace querying and analysis tools for Claude Code integration.
Uses the shared NotionQueryEngine for all database operations with strict workspace validation.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from integrations.notion.query_engine import query_notion_workspace_sync, WORKSPACE_SETTINGS
from utilities.workspace_validator import get_workspace_validator, WorkspaceAccessError

# Load environment variables
load_dotenv()

# Initialize MCP server
mcp = FastMCP("Notion Tools")


@mcp.tool()
def query_notion_projects(workspace_name: str, question: str, chat_id: str = "") -> str:
    """Query a Notion workspace database and get AI-powered analysis with strict access controls.
    
    This tool provides access to Notion project databases for querying tasks,
    priorities, and project status using natural language. Access is restricted
    based on chat-to-workspace mappings to ensure data isolation.
    
    SECURITY: Each chat can only access its mapped workspace:
    - DeckFusion chats → DeckFusion Dev workspace only
    - PsyOPTIMAL chats → PsyOPTIMAL workspace only
    - FlexTrip chats → FlexTrip workspace only
    
    Args:
        workspace_name: Name of the workspace to query (e.g., "PsyOPTIMAL", "FlexTrip")
        question: Natural language question about the workspace data
        chat_id: Telegram chat ID for access validation (optional for direct use)
        
    Returns:
        str: AI-generated answer with specific task details and recommendations
    """
    # Validate workspace access if chat_id is provided
    if chat_id:
        try:
            validator = get_workspace_validator()
            validator.validate_notion_access(chat_id, workspace_name)
            
            # Log successful access for audit trail
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Notion access granted: Chat {chat_id} querying workspace {workspace_name}")
            
        except WorkspaceAccessError as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Notion access denied: {str(e)}")
            return f"❌ Access Denied: {str(e)}"
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Notion validation error: {str(e)}")
            return f"❌ Validation Error: {str(e)}"
    
    return query_notion_workspace_sync(workspace_name, question)


@mcp.tool()
def list_notion_workspaces(chat_id: str = "") -> str:
    """List available Notion workspaces with access control information.
    
    Args:
        chat_id: Telegram chat ID to show accessible workspaces (optional)
    
    Returns:
        str: Formatted list of workspaces with access information
    """
    validator = get_workspace_validator()
    
    if chat_id:
        # Show only allowed workspace for this chat
        try:
            allowed_workspace = validator.get_workspace_for_chat(chat_id)
            if allowed_workspace:
                workspace_config = validator.workspaces[allowed_workspace]
                return f"📁 **Accessible Workspace for Chat {chat_id}:**\n\n• **{allowed_workspace}**: {workspace_config.allowed_directories[0]} workspace"
            else:
                return f"❌ Chat {chat_id} is not mapped to any workspace. Contact administrator for access."
        except Exception as e:
            return f"❌ Error checking workspace access: {str(e)}"
    else:
        # Show all configured workspaces (for admin use)
        workspaces = []
        for name, config in WORKSPACE_SETTINGS.items():
            workspaces.append(f"• **{name}**: {config['description']}")
        
        return "📁 **Available Notion Workspaces:**\n\n" + "\n".join(workspaces)


@mcp.tool() 
def validate_workspace_access(chat_id: str, workspace_name: str) -> str:
    """Validate if a chat has access to a specific workspace.
    
    Args:
        chat_id: Telegram chat ID to validate
        workspace_name: Workspace name to check access for
        
    Returns:
        str: Validation result with access details
    """
    try:
        validator = get_workspace_validator()
        validator.validate_notion_access(chat_id, workspace_name)
        
        # Get workspace details
        allowed_workspace = validator.get_workspace_for_chat(chat_id)
        allowed_dirs = validator.get_allowed_directories(chat_id)
        
        return f"✅ **Access Granted**\n\n" \
               f"• Chat: {chat_id}\n" \
               f"• Workspace: {allowed_workspace}\n" \
               f"• Allowed Directories: {', '.join(allowed_dirs)}"
        
    except WorkspaceAccessError as e:
        return f"❌ **Access Denied**: {str(e)}"
    except Exception as e:
        return f"❌ **Validation Error**: {str(e)}"


if __name__ == "__main__":
    mcp.run()