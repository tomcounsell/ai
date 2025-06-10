"""Notion integration utilities."""

import json
import re
from pathlib import Path


def extract_database_id_from_url(notion_db_url: str) -> str:
    """Extract database ID from Notion URL.
    
    Notion URLs contain database IDs with hyphens removed.
    This function extracts the ID and adds hyphens back to create proper UUID format.
    
    Example:
        URL: https://www.notion.so/yudame/1d22bc894d1080798dcbe7813b006c5c
        Returns: 1d22bc89-4d10-8079-8dcb-e7813b006c5c
    """
    if not notion_db_url or notion_db_url == "****":
        return ""
    
    # Extract the database ID part from URL (last 32 characters without hyphens)
    # Pattern: Extract 32-character hex string from end of URL path
    match = re.search(r'/([a-f0-9]{32})(?:\?|$|#)', notion_db_url)
    if match:
        db_id_no_hyphens = match.group(1)
        # Add hyphens to create proper UUID format: 8-4-4-4-12
        return f"{db_id_no_hyphens[:8]}-{db_id_no_hyphens[8:12]}-{db_id_no_hyphens[12:16]}-{db_id_no_hyphens[16:20]}-{db_id_no_hyphens[20:]}"
    
    return ""


def load_project_mapping():
    """Load project name to database ID mapping from consolidated config."""
    # Try new consolidated config first
    config_file = Path(__file__).parent.parent.parent / "config" / "workspace_config.json"
    if config_file.exists():
        try:
            with open(config_file) as f:
                data = json.load(f)
                workspaces = data.get("workspaces", {})
                
                # Convert to old format for backward compatibility
                projects = {}
                aliases = {}
                
                for workspace_name, workspace_data in workspaces.items():
                    notion_db_url = workspace_data.get("notion_db_url", "")
                    database_id = extract_database_id_from_url(notion_db_url)
                    projects[workspace_name] = {
                        "database_id": database_id,
                        "url": notion_db_url,
                        "description": workspace_data.get("description", "")
                    }
                    
                    # Add aliases
                    for alias in workspace_data.get("aliases", []):
                        aliases[alias.lower()] = workspace_name
                
                return projects, aliases
        except Exception:
            pass
    
    # Fallback to old config file
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
    # Try new consolidated config first
    config_file = Path(__file__).parent.parent.parent / "config" / "workspace_config.json"
    if config_file.exists():
        try:
            with open(config_file) as f:
                data = json.load(f)
                telegram_groups = data.get("telegram_groups", {})
                workspaces = data.get("workspaces", {})
                
                # Convert chat_id to string for lookup
                chat_id_str = str(chat_id)
                
                if chat_id_str in telegram_groups:
                    project_name = telegram_groups[chat_id_str]
                    if project_name in workspaces:
                        notion_db_url = workspaces[project_name].get("notion_db_url", "")
                        database_id = extract_database_id_from_url(notion_db_url)
                        return project_name, database_id
                
                return None, None
        except Exception:
            pass
    
    # Fallback to old config file
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


def is_dev_group(chat_id: int) -> bool:
    """Check if a Telegram chat ID is a dev group that should handle all messages."""
    config_file = Path(__file__).parent.parent.parent / "config" / "workspace_config.json"
    if not config_file.exists():
        return False
    
    try:
        with open(config_file) as f:
            data = json.load(f)
            telegram_groups = data.get("telegram_groups", {})
            workspaces = data.get("workspaces", {})
            
            # Convert chat_id to string for lookup
            chat_id_str = str(chat_id)
            
            if chat_id_str in telegram_groups:
                project_name = telegram_groups[chat_id_str]
                if project_name in workspaces:
                    workspace_data = workspaces[project_name]
                    return workspace_data.get("is_dev_group", False)
            
            return False
    except Exception:
        return False


def get_workspace_working_directory(chat_id: int) -> str | None:
    """Get the working directory for a specific chat ID's workspace."""
    config_file = Path(__file__).parent.parent.parent / "config" / "workspace_config.json"
    if not config_file.exists():
        return None
    
    try:
        with open(config_file) as f:
            data = json.load(f)
            telegram_groups = data.get("telegram_groups", {})
            workspaces = data.get("workspaces", {})
            
            # Convert chat_id to string for lookup
            chat_id_str = str(chat_id)
            
            if chat_id_str in telegram_groups:
                project_name = telegram_groups[chat_id_str]
                if project_name in workspaces:
                    workspace_data = workspaces[project_name]
                    return workspace_data.get("working_directory")
            
            return None
    except Exception:
        return None


def get_dm_working_directory(username: str) -> str:
    """Get the working directory for a DM user based on their whitelist configuration."""
    from utilities.workspace_validator import get_dm_user_working_directory
    return get_dm_user_working_directory(username)
