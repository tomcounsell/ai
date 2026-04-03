"""PreToolUse hook: blocks sensitive writes, enforces PM restrictions, registers DevSessions."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from claude_agent_sdk import HookContext, PreToolUseHookInput

from config.enums import SessionType

logger = logging.getLogger(__name__)

# Known SDLC stages for extraction from dev-session prompts
_SDLC_STAGE_NAMES = frozenset(
    {"ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"}
)

# Pattern: "Stage: BUILD", "Stage to execute -- BUILD", "Stage to execute: BUILD"
_STAGE_PATTERN = re.compile(
    r"Stage(?:\s+to\s+execute)?[\s:\-]+(\b(?:" + "|".join(_SDLC_STAGE_NAMES) + r")\b)",
    re.IGNORECASE,
)

# Paths the PM (ChatSession) is allowed to write to.
# Everything else is blocked for PM sessions.
PM_ALLOWED_WRITE_PREFIXES = (
    "docs/",
    "/docs/",
)

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


def _is_pm_session() -> bool:
    """Check if the current session is a PM session."""
    return os.environ.get("SESSION_TYPE") == SessionType.PM


def _is_pm_allowed_write(file_path: str) -> bool:
    """Check if the PM is allowed to write to this path.

    PM sessions can only write to docs/ directories.
    """
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    # Check against allowed prefixes (relative and absolute)
    for prefix in PM_ALLOWED_WRITE_PREFIXES:
        if prefix in normalized:
            return True
    return False


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


def _extract_stage_from_prompt(prompt: str) -> str | None:
    """Extract an SDLC stage name from a dev-session prompt.

    The PM includes the stage assignment in the prompt when dispatching
    dev-sessions (e.g., "Stage: BUILD", "Stage to execute -- PLAN").
    Returns the uppercase stage name or None if no stage is found.
    """
    if not prompt:
        return None

    # Try structured pattern first (e.g., "Stage: BUILD")
    match = _STAGE_PATTERN.search(prompt)
    if match:
        return match.group(1).upper()

    # Fallback: scan for standalone stage names near "stage" keyword
    prompt_upper = prompt.upper()
    if "STAGE" in prompt_upper:
        for stage in _SDLC_STAGE_NAMES:
            if stage in prompt_upper:
                return stage

    return None


def _start_pipeline_stage(parent_session_id: str, stage: str) -> None:
    """Start an SDLC stage on the parent ChatSession's PipelineStateMachine.

    Loads the parent AgentSession from Redis, creates a PipelineStateMachine,
    and calls start_stage(). This marks the stage as in_progress so that
    subagent_stop can later find and complete it.

    Failures are logged but never raised -- this must not block the Agent tool.
    """
    try:
        from bridge.pipeline_state import PipelineStateMachine
        from models.agent_session import AgentSession

        parent_sessions = list(AgentSession.query.filter(session_id=parent_session_id))
        if not parent_sessions:
            logger.warning(
                f"[pre_tool_use] Parent session {parent_session_id} not found, "
                f"skipping start_stage({stage})"
            )
            return

        parent = parent_sessions[0]
        sm = PipelineStateMachine(parent)
        sm.start_stage(stage)
        logger.info(f"[pre_tool_use] Started pipeline stage {stage} on session {parent_session_id}")
    except Exception as e:
        logger.warning(
            f"[pre_tool_use] Failed to start pipeline stage {stage} "
            f"on session {parent_session_id}: {e}"
        )


def _maybe_register_dev_session(tool_input: dict[str, Any], claude_uuid: str | None = None) -> None:
    """Register a DevSession in Redis when the Agent tool spawns a dev-session.

    Uses the session registry to resolve the bridge session ID from the
    Claude Code UUID (issue #597). Falls back gracefully if not found.
    """
    from agent.hooks.session_registry import resolve
    from models.agent_session import AgentSession

    subagent_type = tool_input.get("type", "")
    if subagent_type != "dev-session":
        return

    parent_session_id = resolve(claude_uuid)
    if not parent_session_id:
        logger.debug(
            "[pre_tool_use] No bridge session in registry, skipping DevSession registration"
        )
        return

    try:
        prompt_text = tool_input.get("prompt", "")[:200] or "dev-session"
        dev_session = AgentSession.create_child(
            role="dev",
            session_id=f"dev-{parent_session_id}",
            project_key="default",
            working_dir=os.getcwd(),
            parent_session_id=parent_session_id,
            message_text=prompt_text,
        )
        logger.info(
            f"[pre_tool_use] Registered DevSession "
            f"{dev_session.agent_session_id} parent={parent_session_id}"
        )
    except Exception as e:
        logger.warning(f"[pre_tool_use] Failed to register DevSession: {e}")

    # Wire PipelineStateMachine.start_stage() so subagent_stop can later
    # find the in_progress stage and mark it completed.
    try:
        full_prompt = tool_input.get("prompt", "")
        stage = _extract_stage_from_prompt(full_prompt)
        if stage:
            _start_pipeline_stage(parent_session_id, stage)
        else:
            logger.debug(
                f"[pre_tool_use] No SDLC stage found in dev-session prompt, "
                f"skipping start_stage (prompt[:100]={full_prompt[:100]!r})"
            )
    except Exception as e:
        logger.warning(f"[pre_tool_use] Failed to start pipeline stage: {e}")


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
        claude_uuid = input_data.get("session_id")
        _maybe_register_dev_session(tool_input, claude_uuid=claude_uuid)
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
        # PM sessions can only write to docs/
        if _is_pm_session() and not _is_pm_allowed_write(file_path):
            logger.warning(f"[pre_tool_use] PM blocked from writing to: {file_path}")
            return {
                "decision": "block",
                "reason": (
                    f"Blocked: PM session cannot write to '{file_path}'. "
                    "PM can only write to docs/ directories. "
                    "Spawn a dev-session subagent for code changes."
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
