"""
Comprehensive Notion Query Engine

This module provides the core Notion API integration functionality used by both
the CLI NotionScout agent and the PydanticAI tool integration. It handles
database queries, property extraction, and AI-powered analysis.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anthropic
import requests
from dotenv import load_dotenv

from .utils import extract_database_id_from_url

# Load environment variables
load_dotenv()


def load_workspace_settings() -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """Load workspace settings and aliases from consolidated configuration."""
    try:
        # Try consolidated config first
        config_file = Path(__file__).parent.parent.parent / "config" / "workspace_config.json"
        if config_file.exists():
            with open(config_file) as f:
                data = json.load(f)
                workspaces = data.get("workspaces", {})
                
                # Convert to workspace settings format
                workspace_settings = {}
                workspace_aliases = {}
                
                for workspace_name, workspace_data in workspaces.items():
                    notion_db_url = workspace_data.get("notion_db_url", "")
                    database_id = extract_database_id_from_url(notion_db_url)
                    workspace_settings[workspace_name] = {
                        "database_id": database_id,
                        "url": notion_db_url,
                        "description": workspace_data.get("description", "")
                    }
                    
                    # Add aliases
                    for alias in workspace_data.get("aliases", []):
                        workspace_aliases[alias.lower()] = workspace_name
                
                return workspace_settings, workspace_aliases
    except Exception as e:
        print(f"Warning: Could not load consolidated workspace config: {e}")
    
    # No fallback - force configuration to be used as source of truth
    print(f"ERROR: Could not load workspace configuration from {config_file}")
    print("System requires valid workspace configuration to function properly.")
    return {}, {}


# Load workspace settings from consolidated config
WORKSPACE_SETTINGS, WORKSPACE_ALIASES = load_workspace_settings()


def load_project_mapping() -> Dict[str, str]:
    """Load project name to database ID mapping from consolidated configuration."""
    return {name: config["database_id"] for name, config in WORKSPACE_SETTINGS.items()}


class NotionQueryEngine:
    """
    Core engine for Notion database queries and AI analysis.
    
    This class provides the complete Notion API integration functionality,
    handling database queries, property extraction, and AI-powered analysis
    of project data for intelligent task and priority recommendations.
    """
    
    def __init__(self, notion_key: str, anthropic_key: str):
        """Initialize the Notion query engine with API keys."""
        self.notion_key = notion_key
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
        self.project_mapping = load_project_mapping()
    
    def resolve_workspace(self, workspace_name: str) -> Tuple[str, Optional[Dict]]:
        """Resolve workspace name to configuration, handling aliases."""
        # Direct match
        if workspace_name in WORKSPACE_SETTINGS:
            return workspace_name, WORKSPACE_SETTINGS[workspace_name]
        
        # Alias match
        normalized = workspace_name.lower()
        if normalized in WORKSPACE_ALIASES:
            resolved = WORKSPACE_ALIASES[normalized]
            return resolved, WORKSPACE_SETTINGS[resolved]
        
        return workspace_name, None
    
    async def query_database_entries(self, database_id: str) -> Dict[str, Any]:
        """Query actual entries from a specific Notion database."""
        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        
        try:
            query_url = f"https://api.notion.com/v1/databases/{database_id}/query"
            response = requests.post(query_url, headers=headers, json={}, timeout=180)
            
            if response.status_code != 200:
                return {"error": f"Error querying database: {response.status_code} - {response.text}"}
            
            return response.json()
            
        except Exception as e:
            return {"error": f"Error querying database entries: {str(e)}"}
    
    def extract_property_value(self, prop_value: Dict[str, Any]) -> str:
        """Extract readable value from Notion property."""
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
        elif prop_type == "people":
            return ", ".join([person.get("name", "") for person in prop_value.get("people", [])])
        elif prop_type == "url":
            return prop_value.get("url", "")
        elif prop_type == "email":
            return prop_value.get("email", "")
        elif prop_type == "phone_number":
            return prop_value.get("phone_number", "")
        else:
            # Fallback for other property types
            return str(prop_value.get(prop_type, ""))
    
    def analyze_entries_with_claude(
        self, 
        entries: List[Dict[str, Any]], 
        question: str, 
        workspace_name: Optional[str] = None,
        max_tokens: int = 800,
        entry_limit: int = 50
    ) -> str:
        """Use Claude to analyze database entries and answer questions."""
        if not entries:
            return "No database entries found to analyze."
        
        # Prepare the data for Claude analysis
        entries_text = "NOTION DATABASE ENTRIES:\n\n"
        for i, entry in enumerate(entries[:entry_limit], 1):
            entries_text += f"Entry {i}:\n"
            if workspace_name:
                entries_text += f"  Workspace: {workspace_name}\n"
            entries_text += f"  Database: {entry['database']}\n"
            
            for prop_name, prop_value in entry["properties"].items():
                if prop_value and prop_value.strip():
                    entries_text += f"  {prop_name}: {prop_value}\n"
            
            if entry.get("url"):
                entries_text += f"  URL: {entry['url']}\n"
            entries_text += "\n"
        
        # Build system prompt
        system_prompt = """You are analyzing Notion database entries to answer specific questions about tasks, priorities, and project status.

When analyzing entries, look for:
- Priority indicators (High, Medium, Low, numbers, urgency markers)
- Status information (Ready for Dev, In Progress, Done, Blocked, etc.)
- Task titles, descriptions, and details
- Assignees, owners, or responsible parties
- Due dates, deadlines, or timeline information
- Project milestones and deliverables

Provide specific, actionable answers. If asked for "highest priority" tasks, identify the specific entries and explain why they're prioritized. Include task titles and relevant details.

Be concise but informative. Focus on the most relevant and actionable information."""
        
        try:
            response = self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=max_tokens,
                temperature=0.3,
                system=system_prompt,
                messages=[{"role": "user", "content": f"Question: {question}\n\n{entries_text}"}],
            )
            
            return response.content[0].text
            
        except Exception as e:
            return f"Error analyzing entries: {str(e)}"
    
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
    
    async def query_all_accessible_databases(self, question: str, db_filter: Optional[str] = None) -> str:
        """Query all accessible databases and analyze results with AI."""
        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        
        try:
            # Search for all databases
            search_url = "https://api.notion.com/v1/search"
            search_payload = {"filter": {"value": "database", "property": "object"}}
            
            response = requests.post(search_url, headers=headers, json=search_payload, timeout=180)
            
            if response.status_code != 200:
                return f"Error accessing Notion API: {response.status_code} - {response.text}"
            
            data = response.json()
            databases = data.get("results", [])
            
            # Apply database filter if provided
            if db_filter:
                print(f"DEBUG: Applying database filter '{db_filter}'")
                print(f"DEBUG: Found {len(databases)} total databases before filtering")
                for i, db in enumerate(databases):
                    db_title = "".join([t.get("plain_text", "") for t in db.get("title", [])])
                    print(f"DEBUG: Database {i+1}: {db['id']} (title: {db_title})")
                
                filtered_databases = [db for db in databases if db_filter in db["id"]]
                print(f"DEBUG: Found {len(filtered_databases)} databases after filtering")
                databases = filtered_databases
                
                if not databases:
                    return f"No database found matching '{db_filter}'"
            
            if not databases:
                return "No databases found accessible to the integration."
            
            # Collect entries from all databases
            all_entries = []
            for db in databases:
                db_id = db["id"]
                db_title = "".join([t.get("plain_text", "") for t in db.get("title", [])])
                
                entries_data = await self.query_database_entries(db_id)
                if "error" in entries_data:
                    continue
                
                entries = entries_data.get("results", [])
                
                for entry in entries:
                    entry_data = {
                        "database": db_title or "Untitled Database",
                        "id": entry["id"],
                        "url": entry.get("url", ""),
                        "properties": {},
                    }
                    
                    # Extract all properties
                    for prop_name, prop_value in entry.get("properties", {}).items():
                        entry_data["properties"][prop_name] = self.extract_property_value(prop_value)
                    
                    all_entries.append(entry_data)
            
            if not all_entries:
                return f"Found {len(databases)} database(s) but no entries to analyze."
            
            # Analyze with Claude
            return self.analyze_entries_with_claude(all_entries, question)
            
        except Exception as e:
            return f"Error querying Notion: {str(e)}"


def get_notion_engine() -> Optional[NotionQueryEngine]:
    """Get a configured Notion query engine instance."""
    notion_key = os.getenv("NOTION_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    
    # Validate API keys
    if not notion_key or notion_key.endswith("****"):
        print("❌ NOTION_API_KEY not configured. Please set your Notion API key in .env file.")
        return None
    
    if not anthropic_key or anthropic_key.endswith("****"):
        print("❌ ANTHROPIC_API_KEY not configured. Please set your Anthropic API key in .env file.")
        return None
    
    return NotionQueryEngine(notion_key, anthropic_key)


# Convenience functions for async/sync usage
def query_notion_workspace_sync(workspace_name: str, question: str) -> str:
    """Synchronous wrapper for workspace querying."""
    engine = get_notion_engine()
    if not engine:
        return "❌ Notion engine not available - check API key configuration."
    
    try:
        # Handle existing event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, engine.query_workspace(workspace_name, question))
                    return future.result()
            else:
                return asyncio.run(engine.query_workspace(workspace_name, question))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(engine.query_workspace(workspace_name, question))
    
    except Exception as e:
        return f"❌ Error querying workspace: {str(e)}"


async def query_notion_workspace_async(workspace_name: str, question: str) -> str:
    """Async function for workspace querying."""
    engine = get_notion_engine()
    if not engine:
        return "❌ Notion engine not available - check API key configuration."
    
    return await engine.query_workspace(workspace_name, question)