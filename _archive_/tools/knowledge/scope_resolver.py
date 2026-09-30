"""Scope resolver for knowledge document indexing.

Maps file paths to (project_key, scope) tuples using projects.json as the
single source of truth. No CLAUDE.md parsing.

Rules:
- File under a project's knowledge_base directory -> (project_key, "client")
- File under ~/work-vault/ root but not under any project subfolder -> ("company", "company-wide")
- File outside known mappings -> None (skip)
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache for project knowledge_base mappings
_project_mappings: list[tuple[str, str]] | None = None


def _load_project_mappings() -> list[tuple[str, str]]:
    """Load project_key -> knowledge_base path mappings from projects.json.

    Returns list of (expanded_kb_path, project_key) sorted by path length
    descending (longest match first).
    """
    global _project_mappings
    if _project_mappings is not None:
        return _project_mappings

    projects_path = Path.home() / "Desktop" / "Valor" / "projects.json"
    if not projects_path.exists():
        logger.warning(f"projects.json not found at {projects_path}")
        _project_mappings = []
        return _project_mappings

    try:
        with open(projects_path) as f:
            config = json.load(f)

        mappings = []
        projects = config.get("projects", {})
        for project_key, project_config in projects.items():
            if not isinstance(project_config, dict):
                continue
            kb_path = project_config.get("knowledge_base")
            if kb_path:
                expanded = os.path.expanduser(kb_path)
                # Normalize and ensure trailing slash for prefix matching
                expanded = os.path.normpath(expanded)
                mappings.append((expanded, project_key))

        # Sort by path length descending so longest (most specific) match wins
        mappings.sort(key=lambda x: len(x[0]), reverse=True)
        _project_mappings = mappings
        logger.debug(f"Loaded {len(mappings)} knowledge_base mappings from projects.json")
        return _project_mappings

    except Exception as e:
        logger.warning(f"Failed to load projects.json: {e}")
        _project_mappings = []
        return _project_mappings


def resolve_scope(file_path: str) -> tuple[str, str] | None:
    """Resolve a file path to (project_key, scope).

    Args:
        file_path: Absolute or ~ path to a file.

    Returns:
        (project_key, scope) tuple, or None if path should be skipped.
        scope is "client" for project-specific docs, "company-wide" for shared docs.
    """
    expanded = os.path.normpath(os.path.expanduser(file_path))
    mappings = _load_project_mappings()

    # Check against project knowledge_base paths (longest match first)
    for kb_path, project_key in mappings:
        if expanded.startswith(kb_path + os.sep) or expanded == kb_path:
            return (project_key, "client")

    # Check if under ~/work-vault/ root (company-wide)
    work_vault = os.path.normpath(os.path.expanduser("~/work-vault"))
    if expanded.startswith(work_vault + os.sep) or expanded == work_vault:
        return ("company", "company-wide")

    # Outside known paths - skip
    return None


def reload_mappings() -> None:
    """Force reload of project mappings from projects.json.

    Call this if projects.json has been modified at runtime.
    """
    global _project_mappings
    _project_mappings = None
    _load_project_mappings()


def get_vault_path() -> str:
    """Return the expanded work-vault root path."""
    return os.path.normpath(os.path.expanduser("~/work-vault"))
