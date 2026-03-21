"""PreToolUse hook: blocks writes to sensitive files and registers DevSessions."""

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


def _maybe_register_dev_session(tool_input: dict[str, Any]) -> None:
    """Register a DevSession in Redis when the Agent tool spawns a dev-session.

    Reads VALOR_SESSION_ID from env to find the parent ChatSession.
    Creates an AgentSession record with session_type=dev and parent linkage.
    """
    import os

    from models.agent_session import AgentSession

    subagent_type = tool_input.get("type", "")
    if subagent_type != "dev-session":
        return

    parent_session_id = os.environ.get("VALOR_SESSION_ID")
    if not parent_session_id:
        logger.debug("[pre_tool_use] VALOR_SESSION_ID not set, skipping DevSession registration")
        return

    try:
        prompt_text = tool_input.get("prompt", "")[:200] or "dev-session"
        dev_session = AgentSession.create_dev(
            session_id=f"dev-{parent_session_id}",
            project_key="default",
            working_dir=os.getcwd(),
            parent_chat_session_id=parent_session_id,
            message_text=prompt_text,
            session_type_source="hook",
        )
        logger.info(
            f"[pre_tool_use] Registered DevSession {dev_session.job_id} parent={parent_session_id}"
        )
    except Exception as e:
        logger.warning(f"[pre_tool_use] Failed to register DevSession: {e}")


async def pre_tool_use_hook(
    input_data: PreToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Block writes to sensitive files and register DevSessions on Agent tool calls.

    Inspects Write and Edit tool calls for sensitive file paths
    and blocks them before execution. Also detects when the Agent tool
    spawns a dev-session and registers it in Redis with parent linkage.
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Detect Agent tool spawning a dev-session
    if tool_name == "Agent":
        _maybe_register_dev_session(tool_input)
        return {}

    # Only inspect write-capable tools
    if tool_name not in ("Write", "Edit", "Bash"):
        return {}

    # For Write/Edit, check the file_path parameter
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if _is_sensitive_path(file_path):
            logger.warning(f"[pre_tool_use] Blocked {tool_name} to sensitive path: {file_path}")
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
        for sensitive in SENSITIVE_PATHS:
            # Redirect operators: > file, >> file, >file, >>file
            if f"> {sensitive}" in command or f">{sensitive}" in command:
                logger.warning(f"[pre_tool_use] Blocked Bash write to sensitive file: {sensitive}")
                return {
                    "decision": "block",
                    "reason": (
                        f"Blocked: Bash command writes to sensitive file '{sensitive}'. "
                        "Sensitive files must be managed manually."
                    ),
                }
            # Commands that write/move/copy to sensitive files
            # e.g. cp x .env, mv x .env, tee .env, tee -a .env
            write_cmds = ("cp ", "mv ", "tee ", "tee -a ")
            for cmd in write_cmds:
                if cmd in command and sensitive in command:
                    logger.warning(
                        f"[pre_tool_use] Blocked Bash {cmd.strip()} to sensitive file: {sensitive}"
                    )
                    return {
                        "decision": "block",
                        "reason": (
                            f"Blocked: Bash command writes to sensitive file '{sensitive}'. "
                            "Sensitive files must be managed manually."
                        ),
                    }

    return {}
