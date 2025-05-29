#!/usr/bin/env python3
"""
Notion Tools MCP Server

Provides Notion workspace querying and analysis tools for Claude Code integration.
Converts existing NotionScout functionality to MCP server format.
"""

import asyncio
import concurrent.futures
import os
from typing import Any

import anthropic
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load environment variables
load_dotenv()

# Initialize MCP server
mcp = FastMCP("Notion Tools")

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
    """Core engine for Notion database queries and AI analysis."""

    def __init__(self, notion_key: str, anthropic_key: str):
        self.notion_key = notion_key
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_key)

    def resolve_workspace(self, workspace_input: str) -> tuple[str | None, dict | None]:
        """Resolve workspace input to canonical name and configuration."""
        if workspace_input in WORKSPACE_SETTINGS:
            return workspace_input, WORKSPACE_SETTINGS[workspace_input]

        canonical_name = WORKSPACE_ALIASES.get(workspace_input.lower())
        if canonical_name and canonical_name in WORKSPACE_SETTINGS:
            return canonical_name, WORKSPACE_SETTINGS[canonical_name]

        return None, None

    async def query_database_entries(self, database_id: str) -> dict[str, Any]:
        """Query entries from a specific Notion database."""
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
        """Extract human-readable value from a Notion property object."""
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
        """Analyze database entries using Claude AI for intelligent answers."""
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
        """Query a specific workspace and analyze results with AI."""
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


@mcp.tool()
def query_notion_projects(workspace_name: str, question: str) -> str:
    """Query a Notion workspace database and get AI-powered analysis.
    
    Use this tool to access project information, task status, and priorities from Notion databases.
    Supports multiple workspaces and provides intelligent analysis of project data.

    Args:
        workspace_name: Name of the workspace to query (e.g., "PsyOPTIMAL", "FlexTrip", "psy", "flex")
        question: Natural language question about the workspace data

    Returns:
        AI-generated answer with specific task details and recommendations
    """
    # Load API keys from environment
    notion_key = os.getenv("NOTION_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    # Validate API keys
    if not notion_key or notion_key.endswith("****"):
        return "❌ NOTION_API_KEY not configured. Please set your Notion API key in .env file."

    if not anthropic_key or anthropic_key.endswith("****"):
        return "❌ ANTHROPIC_API_KEY not configured. Please set your Anthropic API key in .env file."

    # Create query engine and execute query
    try:
        engine = NotionQueryEngine(notion_key, anthropic_key)

        # Run the async query in a new event loop if needed
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're already in an event loop, need to use run_in_executor or create new loop
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


if __name__ == "__main__":
    mcp.run()