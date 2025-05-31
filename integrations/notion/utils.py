"""Notion integration utilities."""

import json
from pathlib import Path


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
                    projects[workspace_name] = {
                        "database_id": workspace_data["database_id"],
                        "url": workspace_data.get("url", ""),
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
                        return project_name, workspaces[project_name]["database_id"]
                
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
