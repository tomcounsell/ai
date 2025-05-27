"""
Unified Notion tool for querying project databases.

This tool merges the NotionScout agent functionality into a proper PydanticAI tool
with workspace-based configuration. It provides intelligent database querying
capabilities with AI-powered analysis of project data.
"""

import os
from typing import Any

import anthropic
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Workspace settings - maps workspace names to database configurations
WORKSPACE_SETTINGS = {
    "PsyOPTIMAL": {
        "database_id": "1d22bc89-4d10-8079-8dcb-e7813b006c5c",
        "url": "https://www.notion.so/yudame/1d22bc894d1080798dcbe7813b006c5c",
        "description": "PsyOPTIMAL project tasks and management",
    },
    "FlexTrip": {
        "database_id": "1ed2bc89-4d10-80e5-89e9-feefe994dddd",
        "url": "https://www.notion.so/yudame/1ed2bc894d1080e589e9feefe994dddd",
        "description": "FlexTrip project tasks and management",
    },
}

# Workspace aliases for flexible input
WORKSPACE_ALIASES = {
    "psyoptimal": "PsyOPTIMAL",
    "psy": "PsyOPTIMAL",
    "optimal": "PsyOPTIMAL",
    "flextrip": "FlexTrip",
    "flex": "FlexTrip",
    "trip": "FlexTrip",
}


class NotionQueryEngine:
    """Core engine for Notion database queries and AI analysis.

    This class encapsulates the complete Notion API integration functionality,
    handling database queries, property extraction, and AI-powered analysis
    of project data for intelligent task and priority recommendations.
    """

    def __init__(self, notion_key: str, anthropic_key: str):
        """Initialize the Notion query engine with API credentials.

        Args:
            notion_key: Notion API key for database access
            anthropic_key: Anthropic API key for AI analysis
        """
        self.notion_key = notion_key
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_key)

    def resolve_workspace(self, workspace_input: str) -> tuple[str | None, dict | None]:
        """Resolve workspace input to canonical name and configuration.

        Args:
            workspace_input: Workspace name or alias to resolve

        Returns:
            tuple[str | None, dict | None]: (workspace_name, workspace_config) or (None, None)
        """
        # Check direct workspace name
        if workspace_input in WORKSPACE_SETTINGS:
            return workspace_input, WORKSPACE_SETTINGS[workspace_input]

        # Check aliases
        canonical_name = WORKSPACE_ALIASES.get(workspace_input.lower())
        if canonical_name and canonical_name in WORKSPACE_SETTINGS:
            return canonical_name, WORKSPACE_SETTINGS[canonical_name]

        return None, None

    async def query_database_entries(self, database_id: str) -> dict[str, Any]:
        """Query entries from a specific Notion database.

        Args:
            database_id: Notion database ID to query

        Returns:
            dict: Notion API response with entries or error information
        """
        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        try:
            query_url = f"https://api.notion.com/v1/databases/{database_id}/query"
            response = requests.post(query_url, headers=headers, json={})

            if response.status_code != 200:
                return {
                    "error": f"Error querying database: {response.status_code} - {response.text}"
                }

            return response.json()

        except Exception as e:
            return {"error": f"Error querying database entries: {str(e)}"}

    def extract_property_value(self, prop_value: dict[str, Any]) -> str:
        """Extract human-readable value from a Notion property object.

        Args:
            prop_value: Notion property object containing type and value data

        Returns:
            str: Human-readable string representation of the property value
        """
        if not prop_value:
            return ""

        prop_type = prop_value.get("type", "")

        if prop_type == "title":
            return "".join([t.get("plain_text", "") for t in prop_value.get("title", [])])
        elif prop_type == "rich_text":
            return "".join([t.get("plain_text", "") for t in prop_value.get("rich_text", [])])
        elif prop_type == "select":
            select_obj = prop_value.get("select")
            return select_obj.get("name", "") if select_obj else ""
        elif prop_type == "multi_select":
            return ", ".join([s.get("name", "") for s in prop_value.get("multi_select", [])])
        elif prop_type == "status":
            status_obj = prop_value.get("status")
            return status_obj.get("name", "") if status_obj else ""
        elif prop_type == "checkbox":
            return "Yes" if prop_value.get("checkbox") else "No"
        elif prop_type == "number":
            return str(prop_value.get("number", ""))
        elif prop_type == "date":
            date_obj = prop_value.get("date")
            return date_obj.get("start", "") if date_obj else ""
        else:
            return str(prop_value.get(prop_type, ""))

    def analyze_entries_with_claude(
        self, entries: list[dict[str, Any]], question: str, workspace_name: str
    ) -> str:
        """Analyze database entries using Claude AI for intelligent answers.

        Args:
            entries: List of database entry dictionaries
            question: Natural language question to answer
            workspace_name: Name of the workspace being queried

        Returns:
            str: AI-generated analysis and answer
        """
        if not entries:
            return f"🔍 No database entries found in {workspace_name} workspace to analyze."

        # Prepare the data for Claude analysis
        entries_text = f"NOTION DATABASE ENTRIES ({workspace_name} workspace):\n\n"
        for i, entry in enumerate(entries[:50], 1):  # Limit to 50 entries
            entries_text += f"Entry {i}:\n  Database: {entry['database']}\n"
            for prop_name, prop_value in entry["properties"].items():
                if prop_value and prop_value.strip():
                    entries_text += f"  {prop_name}: {prop_value}\n"
            entries_text += f"  URL: {entry['url']}\n\n"

        system_prompt = f"""You are analyzing Notion database entries from the {workspace_name} workspace to answer specific questions about tasks, priorities, and project status.

When analyzing entries, look for:
- Priority indicators (High, Medium, Low, numbers, etc.)
- Status information (Ready for Dev, In Progress, Done, etc.)
- Task titles and descriptions
- Assignees or owners
- Due dates or deadlines

Provide specific, actionable answers. If asked for "highest priority" task, identify the specific entry and explain why it's the highest priority. Include the task title and relevant details.

Format your response with emojis and clear structure. Be concise but informative."""

        try:
            response = self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=800,
                temperature=0.3,
                system=system_prompt,
                messages=[{"role": "user", "content": f"Question: {question}\n\n{entries_text}"}],
            )

            return response.content[0].text

        except Exception as e:
            return f"❌ Error analyzing entries: {str(e)}"

    async def query_workspace(self, workspace_name: str, question: str) -> str:
        """Query a specific workspace and analyze results with AI.

        Args:
            workspace_name: Name of the workspace to query
            question: Natural language question about the workspace data

        Returns:
            str: AI-generated answer based on workspace content
        """
        # Resolve workspace to get configuration
        resolved_name, workspace_config = self.resolve_workspace(workspace_name)
        if not workspace_config:
            available_workspaces = list(WORKSPACE_SETTINGS.keys())
            return f"❌ Unknown workspace: '{workspace_name}'\n\n📁 Available workspaces: {', '.join(available_workspaces)}"

        database_id = workspace_config["database_id"]

        try:
            # Query the specific database
            entries_data = await self.query_database_entries(database_id)
            if "error" in entries_data:
                return f"❌ Error accessing {resolved_name} workspace: {entries_data['error']}"

            entries = entries_data.get("results", [])
            if not entries:
                return f"📭 No entries found in {resolved_name} workspace database."

            # Process entries into analyzable format
            processed_entries = []
            for entry in entries:
                entry_data = {
                    "database": resolved_name,
                    "id": entry["id"],
                    "url": entry.get("url", ""),
                    "properties": {},
                }

                # Extract all properties
                for prop_name, prop_value in entry.get("properties", {}).items():
                    entry_data["properties"][prop_name] = self.extract_property_value(prop_value)

                processed_entries.append(entry_data)

            # Analyze with Claude
            return self.analyze_entries_with_claude(processed_entries, question, resolved_name)

        except Exception as e:
            return f"❌ Error querying {resolved_name} workspace: {str(e)}"


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
    # Load API keys from environment
    notion_key = os.getenv("NOTION_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    # Validate API keys
    if not notion_key or notion_key.endswith("****"):
        return "❌ NOTION_API_KEY not configured. Please set your Notion API key in .env file."

    if not anthropic_key or anthropic_key.endswith("****"):
        return (
            "❌ ANTHROPIC_API_KEY not configured. Please set your Anthropic API key in .env file."
        )

    # Create query engine and execute query
    try:
        import asyncio

        engine = NotionQueryEngine(notion_key, anthropic_key)

        # Run the async query in a new event loop if needed
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're already in an event loop, need to use run_in_executor or create new loop
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run, engine.query_workspace(workspace_name, question)
                    )
                    return future.result()
            else:
                return asyncio.run(engine.query_workspace(workspace_name, question))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(engine.query_workspace(workspace_name, question))

    except Exception as e:
        return f"❌ Error setting up Notion query: {str(e)}"


def query_psyoptimal_workspace(question: str) -> str:
    """Query the PsyOPTIMAL workspace specifically.

    This is a convenience function that queries the PsyOPTIMAL workspace
    with a hardcoded workspace name, as specified for the tool registration.

    Args:
        question: Natural language question about PsyOPTIMAL project data

    Returns:
        str: AI-generated answer about PsyOPTIMAL tasks and priorities
    """
    return query_notion_workspace("PsyOPTIMAL", question)
