"""Notion integration utilities."""

import json
from pathlib import Path


def load_project_mapping():
    """Load project name to database ID mapping."""
    mapping_file = Path(__file__).parent / "database_mapping.json"

    if not mapping_file.exists():
        return {}, {}

    try:
        with open(mapping_file) as f:
            data = json.load(f)
            projects = data.get("projects", {})
            aliases = data.get("aliases", {})
            return projects, aliases
    except Exception:
        return {}, {}


def resolve_project_name(project_input: str) -> tuple[str | None, str | None]:
    """Resolve a project input to project name and database ID."""
    projects, aliases = load_project_mapping()

    if project_input in projects:
        return project_input, projects[project_input]["database_id"]

    if project_input.lower() in aliases:
        project_name = aliases[project_input.lower()]
        return project_name, projects[project_name]["database_id"]

    return None, None


def get_telegram_group_project(chat_id: int) -> tuple[str | None, str | None]:
    """Get the Notion project associated with a Telegram group chat ID."""
    mapping_file = Path(__file__).parent / "database_mapping.json"
    
    if not mapping_file.exists():
        return None, None
    
    try:
        with open(mapping_file) as f:
            data = json.load(f)
            telegram_groups = data.get("telegram_groups", {})
            projects = data.get("projects", {})
            
            # Convert chat_id to string for lookup
            chat_id_str = str(chat_id)
            
            if chat_id_str in telegram_groups:
                project_name = telegram_groups[chat_id_str]
                if project_name in projects:
                    return project_name, projects[project_name]["database_id"]
            
            return None, None
    except Exception:
        return None, None
