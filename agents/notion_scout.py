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

A standalone CLI agent that can answer questions about what's in your Notion database.
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

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from integrations.notion.query_engine import (
    NotionQueryEngine,
    get_notion_engine,
    WORKSPACE_SETTINGS,
    WORKSPACE_ALIASES
)

# Initialize rich console
console = Console()


def load_environment() -> tuple[str, str]:
    """Load and validate environment variables for API access.
    
    Returns:
        tuple: (notion_api_key, anthropic_api_key)
        
    Raises:
        SystemExit: If required environment variables are missing
    """
    load_dotenv()
    
    notion_key = os.getenv("NOTION_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    
    missing_keys = []
    if not notion_key or notion_key.endswith("****"):
        missing_keys.append("NOTION_API_KEY")
    if not anthropic_key or anthropic_key.endswith("****"):
        missing_keys.append("ANTHROPIC_API_KEY")
    
    if missing_keys:
        console.print(f"❌ [red]Missing required environment variables: {', '.join(missing_keys)}[/red]")
        console.print("Please set these in your .env file.")
        sys.exit(1)
    
    return notion_key, anthropic_key


def resolve_project_name(project_name: str) -> tuple[str, str]:
    """Resolve project name or alias to workspace name and database ID.
    
    Args:
        project_name: Project name or alias (e.g., "psy", "PsyOPTIMAL", "flex")
        
    Returns:
        tuple: (resolved_workspace_name, database_id) or (None, None) if not found
    """
    # Direct match
    if project_name in WORKSPACE_SETTINGS:
        return project_name, WORKSPACE_SETTINGS[project_name]["database_id"]
    
    # Alias match
    normalized = project_name.lower()
    if normalized in WORKSPACE_ALIASES:
        resolved = WORKSPACE_ALIASES[normalized]
        return resolved, WORKSPACE_SETTINGS[resolved]["database_id"]
    
    return None, None


def display_available_projects():
    """Display available projects and their aliases."""
    console.print("\n📁 [bold blue]Available Projects:[/bold blue]")
    
    for workspace_name, config in WORKSPACE_SETTINGS.items():
        console.print(f"  • [bold]{workspace_name}[/bold]: {config['description']}")
        
        # Show aliases for this workspace
        aliases = [alias for alias, target in WORKSPACE_ALIASES.items() if target == workspace_name]
        if aliases:
            console.print(f"    Aliases: {', '.join(aliases)}")
    
    console.print()


async def main():
    """Main entry point for the NotionScout CLI application.
    
    Handles command-line argument parsing, environment setup, agent initialization,
    and question processing. Supports both direct questions and project-specific
    queries using project names or aliases.
    """
    parser = argparse.ArgumentParser(description="Query your Notion databases with AI assistance")
    parser.add_argument("question", help="Question to ask about your Notion content")
    parser.add_argument(
        "--project",
        "-p",
        help="Specific project/workspace to query (e.g., PsyOPTIMAL, psy, FlexTrip, flex)"
    )
    parser.add_argument(
        "--list-projects",
        "-l",
        action="store_true",
        help="List available projects and exit"
    )
    
    args = parser.parse_args()
    
    # Handle list projects request
    if args.list_projects:
        display_available_projects()
        return
    
    # Load and validate environment
    notion_key, anthropic_key = load_environment()
    
    # Display startup information
    console.print("🕵️ [bold blue]Notion Scout Agent[/bold blue]")
    console.print(f"❓ Question: [bold]{args.question}[/bold]")
    
    # Handle project-specific query
    if args.project:
        workspace_name, database_id = resolve_project_name(args.project)
        if not workspace_name:
            console.print(f"❌ [red]Unknown project: '{args.project}'[/red]")
            display_available_projects()
            sys.exit(1)
        
        console.print(f"🎯 Project: [yellow]{workspace_name}[/yellow]")
        console.print()
        
        # Create engine and query specific workspace
        engine = NotionQueryEngine(notion_key, anthropic_key)
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"🔍 Searching {workspace_name} workspace...", total=None)
            
            try:
                answer = await engine.query_workspace(workspace_name, args.question)
                progress.update(task, description="✅ Search completed!")
                
                # Display the answer
                console.print("\n🎯 [bold green]Scout's Answer[/bold green]")
                console.print(Markdown(answer))
                
            except Exception as e:
                console.print(f"❌ [red]Error: {str(e)}[/red]")
                sys.exit(1)
    
    else:
        # Query all accessible databases
        console.print("🎯 Scope: [yellow]All accessible databases[/yellow]")
        console.print()
        
        # Create engine and query all databases
        engine = NotionQueryEngine(notion_key, anthropic_key)
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("🔍 Searching all Notion databases...", total=None)
            
            try:
                answer = await engine.query_all_accessible_databases(args.question)
                progress.update(task, description="✅ Search completed!")
                
                # Display the answer
                console.print("\n🎯 [bold green]Scout's Answer[/bold green]")
                console.print(Markdown(answer))
                
            except Exception as e:
                console.print(f"❌ [red]Error: {str(e)}[/red]")
                sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())