"""
Telegram user lookup utilities.

Provides functions to load whitelisted users and resolve usernames to user IDs.
"""

import json
from pathlib import Path


def get_whitelisted_users() -> dict[str, int]:
    """
    Load whitelisted Telegram users from config file.

    Returns:
        Dictionary mapping lowercase usernames to user IDs.
        Example: {"tom": 179144806, "kevin": 577036901}

    Raises:
        FileNotFoundError: If whitelist config file doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
    """
    whitelist_path = Path.home() / "Desktop/claude_code/dm_whitelist.json"

    with open(whitelist_path) as f:
        config = json.load(f)

    # Build username -> user_id mapping (case-insensitive)
    users = {}
    for user_id_str, user_data in config.get("users", {}).items():
        username = user_data.get("name", "").lower()
        if username:
            users[username] = int(user_id_str)

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
