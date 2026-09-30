"""
Telegram user lookup utilities.

Provides functions to load whitelisted users and resolve usernames to user IDs.
Reads from the dms.whitelist array in projects.json.
"""

import json
from pathlib import Path

# Canonical config location (iCloud-synced)
PROJECTS_PATH = Path.home() / "Desktop" / "Valor" / "projects.json"


def get_whitelisted_users() -> dict[str, int]:
    """
    Load whitelisted Telegram users from projects.json dms.whitelist.

    Returns:
        Dictionary mapping lowercase names to user IDs.
        Example: {"tom": 179144806, "kevin": 577036901}

    Raises:
        FileNotFoundError: If projects.json doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
    """
    with open(PROJECTS_PATH) as f:
        config = json.load(f)

    # Build name -> user_id mapping (case-insensitive)
    users = {}
    for entry in config.get("dms", {}).get("whitelist", []):
        if isinstance(entry, dict) and "id" in entry:
            name = entry.get("name", "").lower()
            if name:
                users[name] = int(entry["id"])

    return users


def resolve_username(name: str) -> int | None:
    """
    Resolve a username to a Telegram user ID (case-insensitive).

    Args:
        name: Username to look up (e.g., "Tom", "tom", "TOM")

    Returns:
        User ID as integer if found, None otherwise.

    Example:
        >>> resolve_username("tom")
        179144806
        >>> resolve_username("TOM")
        179144806
        >>> resolve_username("unknown")
        None
    """
    users = get_whitelisted_users()
    return users.get(name.lower())
