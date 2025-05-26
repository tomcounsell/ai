"""Notion integration utilities."""

import json
from pathlib import Path
from typing import Tuple, Optional


def load_project_mapping():
    """Load project name to database ID mapping."""
    mapping_file = Path(__file__).parent / "database_mapping.json"
    
    if not mapping_file.exists():
        return {}, {}
    
    try:
        with open(mapping_file, 'r') as f:
            data = json.load(f)
            projects = data.get("projects", {})
            aliases = data.get("aliases", {})
            return projects, aliases
    except Exception:
        return {}, {}


def resolve_project_name(project_input: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a project input to project name and database ID."""
    projects, aliases = load_project_mapping()
    
    if project_input in projects:
        return project_input, projects[project_input]["database_id"]
    
    if project_input.lower() in aliases:
        project_name = aliases[project_input.lower()]
        return project_name, projects[project_name]["database_id"]
    
    return None, None