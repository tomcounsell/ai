"""
Notion tool for PydanticAI agent integration.

This tool provides a clean interface for querying Notion workspaces from within
AI agents. It uses the shared NotionQueryEngine for all database operations.
"""

from integrations.notion.query_engine import query_notion_workspace_sync, WORKSPACE_SETTINGS


def query_notion_workspace(workspace_name: str, question: str) -> str:
    """Query a Notion workspace database and get AI-powered analysis.

    This is the main function used by PydanticAI tools to access Notion data.
    It handles authentication, workspace resolution, database querying, and
    AI analysis to provide intelligent answers about project status and tasks.

    Args:
        workspace_name: Name of the workspace to query (e.g., "PsyOPTIMAL", "FlexTrip")
        question: Natural language question about the workspace data

    Returns:
        str: AI-generated answer with specific task details and recommendations

    Example:
        >>> result = query_notion_workspace("PsyOPTIMAL", "What tasks are ready for dev?")
        >>> "task" in result.lower()
        True
    """
    return query_notion_workspace_sync(workspace_name, question)


# REMOVED: query_psyoptimal_workspace() function was causing workspace isolation violations
# All workspace queries should now use the workspace-aware MCP tools with proper access validation


def list_available_workspaces() -> str:
    """List all available Notion workspaces.
    
    Returns:
        str: Formatted list of available workspaces and their descriptions
    """
    workspaces = []
    for name, config in WORKSPACE_SETTINGS.items():
        workspaces.append(f"• **{name}**: {config['description']}")
    
    return "📁 **Available Notion Workspaces:**\n\n" + "\n".join(workspaces)