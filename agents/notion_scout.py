#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "python-dotenv",
#   "rich",
#   "requests",
#   "anthropic"
# ]
# ///

"""
Notion Scout Agent

A simple agent that can answer questions about what's in your Notion database.
Just ask it anything and it will search through your Notion content to find answers.

Usage:
    uv run notion_scout.py "What are my current project milestones?"
    uv run notion_scout.py "Show me all my research notes about AI"
    uv run notion_scout.py "What tasks are due this week?"

    # Query specific database by project name:
    uv run notion_scout.py --project PsyOPTIMAL "What tasks need attention?"
    uv run notion_scout.py --project FlexTrip "What's the development status?"
    uv run notion_scout.py --project psy "Show me current milestones"
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

# Initialize rich console
console = Console()


def load_project_mapping():
    """Load project name to database ID mapping from configuration file.
    
    Reads the database_mapping.json file from the integrations/notion directory
    to provide mappings between human-readable project names and Notion database IDs.
    
    Returns:
        tuple[dict, dict]: A tuple containing:
            - projects: Dict mapping project names to their configuration
            - aliases: Dict mapping short aliases to project names
            
    Examples:
        >>> projects, aliases = load_project_mapping()
        >>> 'PsyOPTIMAL' in projects
        True
        >>> aliases.get('psy') == 'PsyOPTIMAL'
        True
    """
    mapping_file = (
        Path(__file__).parent.parent / "integrations" / "notion" / "database_mapping.json"
    )

    if not mapping_file.exists():
        return {}, {}

    try:
        with open(mapping_file) as f:
            data = json.load(f)
            projects = data.get("projects", {})
            aliases = data.get("aliases", {})
            return projects, aliases
    except Exception as e:
        console.print(f"[yellow]Warning: Could not load project mapping: {e}[/yellow]")
        return {}, {}


def resolve_project_name(project_input: str) -> tuple[str | None, str | None]:
    """Resolve a project input (name or alias) to project name and database ID.
    
    Attempts to match the input against known project names and aliases,
    returning the canonical project name and corresponding Notion database ID.
    
    Args:
        project_input: Project name or alias to resolve (case-insensitive for aliases).
        
    Returns:
        tuple[str | None, str | None]: A tuple containing:
            - project_name: Canonical project name if found, None otherwise
            - database_id: Notion database ID if found, None otherwise
            
    Examples:
        >>> resolve_project_name('PsyOPTIMAL')
        ('PsyOPTIMAL', 'abc123...')
        >>> resolve_project_name('psy')
        ('PsyOPTIMAL', 'abc123...')
        >>> resolve_project_name('unknown')
        (None, None)
    """
    projects, aliases = load_project_mapping()

    # Check if it's a direct project name
    if project_input in projects:
        return project_input, projects[project_input]["database_id"]

    # Check if it's an alias
    if project_input.lower() in aliases:
        project_name = aliases[project_input.lower()]
        return project_name, projects[project_name]["database_id"]

    return None, None


def load_environment():
    """Load environment variables and validate required API keys.
    
    Loads environment variables from .env file and validates that required
    API keys for Notion and Anthropic are properly configured.
    
    Returns:
        tuple[str, str]: A tuple containing (notion_key, anthropic_key)
        
    Raises:
        SystemExit: If required API keys are missing or improperly configured.
        
    Examples:
        >>> notion_key, anthropic_key = load_environment()
        >>> notion_key.startswith('ntn_')
        True
        >>> anthropic_key.startswith('sk-ant-')
        True
    """
    load_dotenv()

    notion_key = os.getenv("NOTION_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    if not notion_key or notion_key.endswith("****"):
        console.print(
            "[red]❌ NOTION_API_KEY not found or not configured properly[/red]\n\n"
            "Please set your Notion API key in the .env file:\n"
            "NOTION_API_KEY=ntn_your_actual_key_here\n\n"
            "Get your key from: https://www.notion.so/my-integrations"
        )
        sys.exit(1)

    if not anthropic_key or anthropic_key.endswith("****"):
        console.print(
            "[red]❌ ANTHROPIC_API_KEY not found or not configured properly[/red]\n\n"
            "Please set your Anthropic API key in the .env file:\n"
            "ANTHROPIC_API_KEY=sk-ant-your_actual_key_here\n\n"
            "Get your key from: https://console.anthropic.com/"
        )
        sys.exit(1)

    return notion_key, anthropic_key


class NotionScout:
    """A Notion database query agent that provides AI-powered analysis of project data.
    
    NotionScout connects to Notion databases to retrieve project information and
    uses Claude (Anthropic API) to analyze the data and provide intelligent answers
    to questions about tasks, priorities, and project status.
    
    The agent can:
    - Query specific Notion databases by project name or ID
    - Extract and parse various Notion property types
    - Analyze database entries using AI to answer natural language questions
    - Provide specific recommendations about project priorities and tasks
    
    Attributes:
        notion_key: Notion API key for database access
        anthropic_client: Anthropic client for AI analysis
        db_filter: Optional database ID filter for targeted queries
        
    Examples:
        >>> scout = NotionScout(notion_key, anthropic_key)
        >>> scout.db_filter = 'abc123'  # Target specific database
        >>> answer = await scout.answer_question('What tasks need attention?')
    """

    def __init__(self, notion_key: str, anthropic_key: str):
        """Initialize NotionScout with API credentials.
        
        Args:
            notion_key: Notion API key for database access. Should start with 'ntn_'.
            anthropic_key: Anthropic API key for AI analysis. Should start with 'sk-ant-'.
            
        Examples:
            >>> scout = NotionScout('ntn_your_key', 'sk-ant-your_key')
            >>> scout.notion_key is not None
            True
        """
        self.notion_key = notion_key
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
        self.db_filter = None

    async def query_database_entries(self, database_id: str) -> dict:
        """Query entries from a specific Notion database via API.
        
        Retrieves all entries from the specified Notion database using the
        Notion API. This method handles API communication and error handling.
        
        Args:
            database_id: Notion database ID to query. Should be a valid UUID string.
            
        Returns:
            dict: Notion API response containing database entries, or error information.
                Success response includes 'results' key with list of entries.
                Error response includes 'error' key with error message.
                
        Examples:
            >>> result = await scout.query_database_entries('abc123-def456')
            >>> 'results' in result or 'error' in result
            True
        """
        import requests

        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        try:
            # Query the database entries
            query_url = f"https://api.notion.com/v1/databases/{database_id}/query"
            response = requests.post(query_url, headers=headers, json={})

            if response.status_code != 200:
                return {
                    "error": f"Error querying database: {response.status_code} - {response.text}"
                }

            return response.json()

        except Exception as e:
            return {"error": f"Error querying database entries: {str(e)}"}

    def extract_property_value(self, prop_value: dict) -> str:
        """Extract human-readable value from a Notion property object.
        
        Converts various Notion property types (title, rich_text, select, etc.)
        into readable string representations. Handles all common Notion property
        types including text, dates, numbers, and selections.
        
        Args:
            prop_value: Notion property object containing type and value data.
                Should include 'type' key indicating the property type.
                
        Returns:
            str: Human-readable string representation of the property value.
                Returns empty string if property is None or invalid.
                
        Examples:
            >>> prop = {'type': 'title', 'title': [{'plain_text': 'Task Name'}]}
            >>> scout.extract_property_value(prop)
            'Task Name'
            >>> prop = {'type': 'select', 'select': {'name': 'High Priority'}}
            >>> scout.extract_property_value(prop)
            'High Priority'
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

    async def query_notion_directly(self, question: str) -> str:
        """Query Notion API directly and analyze results with AI.
        
        This method orchestrates the complete workflow:
        1. Searches for accessible Notion databases
        2. Optionally filters by database ID if db_filter is set
        3. Queries entries from each relevant database
        4. Extracts property values from all entries
        5. Uses Claude AI to analyze the data and answer the question
        
        Args:
            question: Natural language question about the database content.
                Examples: 'What tasks need attention?', 'Show me high priority items'
                
        Returns:
            str: AI-generated answer based on the database content, or error message.
                Includes specific task details and recommendations when possible.
                
        Examples:
            >>> answer = await scout.query_notion_directly('What tasks are ready for dev?')
            >>> 'task' in answer.lower() or 'error' in answer.lower()
            True
        """
        import requests

        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        try:
            # First, get the databases
            search_url = "https://api.notion.com/v1/search"
            search_payload = {"filter": {"value": "database", "property": "object"}}

            response = requests.post(search_url, headers=headers, json=search_payload)

            if response.status_code != 200:
                return f"Error accessing Notion API: {response.status_code} - {response.text}"

            data = response.json()
            databases = data.get("results", [])

            # Filter databases if db_filter is specified
            if self.db_filter:
                databases = [db for db in databases if self.db_filter in db["id"]]
                if not databases:
                    return f"❌ No database found matching '{self.db_filter}'"

            if not databases:
                return "No databases found accessible to the integration."

            # For each database, get its entries
            all_entries = []
            for db in databases:
                db_id = db["id"]
                db_title = "".join([t.get("plain_text", "") for t in db.get("title", [])])

                # Get database entries
                entries_data = await self.query_database_entries(db_id)
                if "error" in entries_data:
                    continue

                entries = entries_data.get("results", [])

                for entry in entries:
                    entry_data = {
                        "database": db_title,
                        "id": entry["id"],
                        "url": entry.get("url", ""),
                        "properties": {},
                    }

                    # Extract all properties
                    for prop_name, prop_value in entry.get("properties", {}).items():
                        entry_data["properties"][prop_name] = self.extract_property_value(
                            prop_value
                        )

                    all_entries.append(entry_data)

            return self.analyze_entries_with_claude(all_entries, question)

        except Exception as e:
            return f"Error querying Notion: {str(e)}"

    def analyze_entries_with_claude(self, entries: list, question: str) -> str:
        """Analyze database entries using Claude AI to provide intelligent answers.
        
        Takes structured database entries and uses Claude (Anthropic's AI) to analyze
        the data and provide contextual answers to questions about tasks, priorities,
        status, and project management.
        
        Args:
            entries: List of database entry dictionaries. Each entry should contain
                'database', 'properties', and 'url' keys with relevant project data.
            question: Natural language question to answer based on the entries.
                
        Returns:
            str: AI-generated analysis and answer, formatted for conversation.
                Includes specific task recommendations and priority insights.
                Returns error message if analysis fails.
                
        Examples:
            >>> entries = [{'database': 'Tasks', 'properties': {'Title': 'Fix bug'}}]
            >>> answer = scout.analyze_entries_with_claude(entries, 'What should I work on?')
            >>> len(answer) > 0
            True
        """
        if not entries:
            return "No database entries found to analyze."

        # Prepare the data for Claude analysis
        entries_text = "NOTION DATABASE ENTRIES:\n\n"
        for i, entry in enumerate(entries[:50], 1):  # Limit to 50 entries
            entries_text += f"Entry {i}:\n  Database: {entry['database']}\n"
            for prop_name, prop_value in entry["properties"].items():
                if prop_value and prop_value.strip():
                    entries_text += f"  {prop_name}: {prop_value}\n"
            entries_text += f"  URL: {entry['url']}\n\n"

        system_prompt = """You are analyzing Notion database entries to answer specific questions about tasks, priorities, and project status.

When analyzing entries, look for:
- Priority indicators (High, Medium, Low, numbers, etc.)
- Status information (Ready for Dev, In Progress, Done, etc.)
- Task titles and descriptions
- Assignees or owners
- Due dates or deadlines

Provide specific, actionable answers. If asked for "highest priority" task, identify the specific entry and explain why it's the highest priority. Include the task title and relevant details.

Be concise but informative."""

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
            return f"Error analyzing entries: {str(e)}"

    async def answer_question(self, question: str) -> str:
        """Answer a question by querying and analyzing Notion database content.
        
        This is the main entry point for the NotionScout agent. It coordinates
        the complete workflow from database querying to AI analysis and response
        generation.
        
        Args:
            question: Natural language question about project data, tasks, or status.
                Examples: 'What are my current milestones?', 'Show me high priority tasks'
                
        Returns:
            str: Comprehensive answer based on current database content, including
                specific recommendations and actionable insights where applicable.
                
        Examples:
            >>> scout = NotionScout(notion_key, anthropic_key)
            >>> answer = await scout.answer_question('What tasks are due this week?')
            >>> isinstance(answer, str)
            True
        """

        # First try to get actual Notion data
        notion_data = await self.query_notion_directly(question)

        # If it's just a list of databases, ask a more specific question
        if "Found" in notion_data and "database(s) accessible" in notion_data:
            return (
                notion_data
                + "\n\nTo query specific content, I'll need to access the database pages. The integration appears to be working!"
            )

        return notion_data


async def main():
    """Main entry point for the NotionScout CLI application.
    
    Handles command-line argument parsing, environment setup, agent initialization,
    and question processing. Supports both direct questions and project-specific
    queries using project names or aliases.
    
    Command-line usage:
        - uv run notion_scout.py "Question about database"
        - uv run notion_scout.py --project PROJECT_NAME "Specific question"
        
    The function will:
    1. Parse command line arguments
    2. Load and validate environment variables
    3. Resolve project names/aliases to database IDs
    4. Create NotionScout instance and process the question
    5. Display formatted results with progress indicators
    
    Examples:
        Command line usage:
        $ uv run notion_scout.py "What tasks need attention?"
        $ uv run notion_scout.py --project PsyOPTIMAL "Show me current status"
    """

    # Parse command line arguments
    db_filter = None
    project_name = None
    question = None

    if len(sys.argv) == 2:
        question = sys.argv[1]
    elif len(sys.argv) == 4 and sys.argv[1] in ["--db", "--project"]:
        project_input = sys.argv[2]
        question = sys.argv[3]

        if sys.argv[1] == "--project":
            # Resolve project name to database ID
            resolved_name, resolved_id = resolve_project_name(project_input)
            if resolved_id:
                project_name = resolved_name
                db_filter = resolved_id[:8]  # Use first 8 chars for filtering
            else:
                projects, aliases = load_project_mapping()
                list(projects.keys()) + list(aliases.keys())
                console.print(
                    f"❌ [red]Unknown project: '{project_input}'[/red]\n\n"
                    f"Available projects: {', '.join(projects.keys())}\n"
                    f"Available aliases: {', '.join(aliases.keys())}"
                )
                sys.exit(1)
        else:
            # Legacy --db support
            db_filter = project_input
    else:
        projects, _ = load_project_mapping()
        project_list = "\n".join([f"  - {name}" for name in projects.keys()])

        console.print(
            "🕵️ [bold blue]Notion Scout Agent[/bold blue]\n\n"
            "[yellow]Usage:[/yellow]\n"
            'uv run notion_scout.py "Your question about Notion database"\n'
            'uv run notion_scout.py --project PROJECT_NAME "Question about specific project"\n\n'
            "[yellow]Examples:[/yellow]\n"
            'uv run notion_scout.py "What are my current project milestones?"\n'
            'uv run notion_scout.py --project PsyOPTIMAL "What tasks need attention?"\n'
            'uv run notion_scout.py --project FlexTrip "What\'s the development status?"\n'
            'uv run notion_scout.py --project psy "Show me current milestones"\n\n'
            f"[yellow]Available projects:[/yellow]\n{project_list}"
        )
        sys.exit(1)

    # Load environment and check configuration
    notion_key, anthropic_key = load_environment()

    # Display startup information
    console.print("🕵️ [bold blue]Notion Scout Agent[/bold blue]")
    console.print(f"❓ Question: [bold]{question}[/bold]")
    if project_name:
        console.print(f"🎯 Project: [yellow]{project_name}[/yellow]")
    elif db_filter:
        console.print(f"🎯 Database filter: [yellow]{db_filter}[/yellow]")
    console.print()

    # Create the scout agent
    scout = NotionScout(notion_key, anthropic_key)
    scout.db_filter = db_filter  # Add the filter to the scout

    # Process the question with progress indicator
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("🔍 Searching Notion database...", total=None)

        try:
            answer = await scout.answer_question(question)

            progress.update(task, description="✅ Search completed!")

            # Display the answer
            console.print("\n🎯 [bold green]Scout's Answer[/bold green]")
            console.print(Markdown(answer))

        except Exception as e:
            console.print(f"❌ [red]Error: {str(e)}[/red]")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
