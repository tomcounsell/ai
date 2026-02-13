"""PreToolUse hook: blocks writes to sensitive files."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import HookContext, PreToolUseHookInput

logger = logging.getLogger(__name__)

# Files that should never be written to by the agent
SENSITIVE_PATHS = frozenset(
    {
        ".env",
        "credentials.json",
        "secrets.json",
        ".env.local",
        ".env.production",
        "service-account.json",
    }
)

# Path fragments that indicate sensitive files
SENSITIVE_FRAGMENTS = (
    "/credentials",
    "/secrets/",
    "/.ssh/",
    "/private_key",
)


def _is_sensitive_path(file_path: str) -> bool:
    """Check whether a file path points to a sensitive file."""
    if not file_path:
        return False

    # Check exact filename matches (basename)
    from pathlib import PurePosixPath

    basename = PurePosixPath(file_path).name
    if basename in SENSITIVE_PATHS:
        return True

    # Check path fragments
    normalized = file_path.replace("\\", "/")
    for fragment in SENSITIVE_FRAGMENTS:
        if fragment in normalized:
            return True

    return False


async def pre_tool_use_hook(
    input_data: PreToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Block writes to sensitive files like .env and credentials.json.

    Inspects Write and Edit tool calls for sensitive file paths
    and blocks them before execution.
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Only inspect write-capable tools
    if tool_name not in ("Write", "Edit", "Bash"):
        return {}

    # For Write/Edit, check the file_path parameter
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if _is_sensitive_path(file_path):
            logger.warning(
                f"[pre_tool_use] Blocked {tool_name} to sensitive path: {file_path}"
            )
            return {
                "decision": "block",
                "reason": (
                    f"Blocked: writing to sensitive file '{file_path}' is not allowed. "
                    "Sensitive files (.env, credentials, secrets) must be managed manually."
                ),
            }

    # For Bash, check if command writes to sensitive files
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        # Simple heuristic: check for redirect operators targeting sensitive files
        for sensitive in SENSITIVE_PATHS:
            if f"> {sensitive}" in command or f">{sensitive}" in command:
                logger.warning(
                    f"[pre_tool_use] Blocked Bash write to sensitive file: {sensitive}"
                )
                return {
                    "decision": "block",
                    "reason": (
                        f"Blocked: Bash command writes to sensitive file '{sensitive}'. "
                        "Sensitive files must be managed manually."
                    ),
                }

    return {}
