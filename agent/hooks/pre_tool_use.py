"""PreToolUse hook: blocks sensitive writes, enforces PM limits, registers child Dev sessions.

PM Bash enforcement
-------------------
For PM sessions (``SESSION_TYPE=pm``), the Bash branch of ``pre_tool_use_hook``
restricts tool access to a read-only allowlist defined by
``_is_pm_allowed_bash``. Any command not on the allowlist -- or any command that
contains shell metacharacters that could smuggle mutations -- is blocked with a
``{"decision": "block", "reason": ...}`` response.

The authoritative list of allowed/blocked commands lives in
``tests/unit/test_pm_session_permissions.py::TestPMBashRestriction``.
"""

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

# Maps Skill tool skill names to SDLC stage names.
# When a PM session calls Skill(skill="do-build"), the pre_tool_use hook uses
# this mapping to call start_stage("BUILD") on the parent PipelineStateMachine.
# Skills not in this dict are silently ignored (e.g., do-discover-paths).
_SKILL_TO_STAGE: dict[str, str] = {
    "do-plan": "PLAN",
    "do-plan-critique": "CRITIQUE",
    "do-build": "BUILD",
    "do-test": "TEST",
    "do-patch": "PATCH",
    "do-pr-review": "REVIEW",
    "do-docs": "DOCS",
    "do-merge": "MERGE",
}

# Pattern: "Stage: BUILD", "Stage to execute -- BUILD", "Stage to execute: BUILD"
_STAGE_PATTERN = re.compile(
    r"Stage(?:\s+to\s+execute)?[\s:\-]+(\b(?:" + "|".join(_SDLC_STAGE_NAMES) + r")\b)",
    re.IGNORECASE,
)

# Paths the PM session is allowed to write to.
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


# --- PM Bash allowlist ---------------------------------------------------------
#
# The PM session is allowed to run only these read-only command prefixes. The
# entire command string (after stripping whitespace and after ``git -C <token>``
# normalization) must start with one of these prefixes, optionally followed by
# a space. ``gh api`` is DELIBERATELY excluded because ``gh api ... --method
# POST`` is a silent mutation vector that would pass a naive prefix check.
PM_BASH_ALLOWED_PREFIXES: tuple[str, ...] = (
    # git (read-only verbs)
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "git rev-parse",
    "git ls-remote",
    "git stash list",
    "git config --get",
    "git remote -v",
    "git remote show",
    "git rev-list",
    "git describe",
    "git shortlog",
    # gh CLI (view/list verbs only -- gh api deliberately excluded)
    "gh issue view",
    "gh issue list",
    "gh pr view",
    "gh pr list",
    "gh pr diff",
    "gh pr checks",
    "gh pr status",
    "gh run view",
    "gh run list",
    "gh repo view",
    # log/file reading
    "tail logs/",
    "tail -n",
    "tail -f logs/",
    "cat docs/",
    "cat config/personas/",
    "cat CLAUDE.md",
    "head docs/",
    "head CLAUDE.md",
    "ls",
    "pwd",
    "wc",
    "file",
    # tools (read-only subcommands)
    "python -m tools.valor_session list",
    "python -m tools.valor_session status",
    "python -m tools.agent_session_scheduler status",
    "python -m tools.agent_session_scheduler list",
    "python -m tools.memory_search search",
    "python -m tools.memory_search inspect",
    "python -m tools.sdlc_stage_query",
    "python -m tools.code_impact_finder",
    # pytest collect-only (no execution)
    "pytest --collect-only",
    # curl to localhost dashboard
    "curl -s localhost:8500/dashboard.json",
    "curl localhost:8500/dashboard.json",
)

# Shell metacharacters that can smuggle mutations past a prefix check.
# Any of these in a PM Bash command forces a block, even if the command
# starts with an allowlisted prefix. ``&`` at any position is also rejected
# because PM sessions have no legitimate reason to background processes.
_PM_BASH_FORBIDDEN_METACHARS: tuple[str, ...] = (
    "|",
    ">",
    "<",
    "&&",
    "||",
    ";",
    "`",
    "$(",
    "$((",
    "&",
)

# Strip a leading ``git -C <token>`` so cross-repo forms like
# ``git -C "$REPO" status`` normalize to ``git status`` for allowlist
# purposes. ``<token>`` may be a double-quoted string, single-quoted string,
# or a single unquoted word. The metacharacter guard MUST run BEFORE this
# normalization so an injection like ``git -C "$(rm -rf /)" status`` is
# caught by the guard (via ``$(``) before the path is stripped.
_GIT_DASH_C_PATTERN = re.compile(r'^git -C (?:"[^"]*"|\'[^\']*\'|\S+)\s+')


def _is_pm_allowed_bash(command: str | None) -> bool:
    """Return True iff *command* is on the PM session's read-only allowlist.

    Contract:
      - Empty / whitespace-only / ``None`` commands return ``False``.
      - A metacharacter guard rejects any command containing pipes, redirects,
        command substitution, ``&&``/``||``/``;``/``&``/backticks. This runs
        BEFORE the ``git -C`` normalization so shell-injection via the path
        argument (``git -C "$(rm -rf /)" status``) is blocked by the guard.
      - After the metacharacter guard, a leading ``git -C <token>`` is
        stripped once so cross-repo forms like ``git -C "$REPO" status``
        are treated as ``git status``.
      - The normalized command must start with (or exactly match) one of
        ``PM_BASH_ALLOWED_PREFIXES``. A prefix matches if the command equals
        it or continues with a space.

    Prefix-matching is deliberately simple; regex parsing is a rabbit hole
    (see the Rabbit Holes section of docs/plans/pm-bash-discipline.md).
    """
    if not command or not command.strip():
        return False

    stripped = command.strip()

    # 1. Metacharacter guard (runs BEFORE normalization to prevent injection
    #    via the git -C path argument, e.g. `git -C "$(rm -rf /)" status`).
    for metachar in _PM_BASH_FORBIDDEN_METACHARS:
        if metachar in stripped:
            return False

    # 2. Normalize `git -C <token>` to `git ` so cross-repo invocations
    #    match the bare `git status` / `git log` / ... prefixes.
    normalized = _GIT_DASH_C_PATTERN.sub("git ", stripped, count=1)

    # 3. Prefix match: the command must equal an allowlist entry, start with
    #    one followed by a space, or (for path-style prefixes ending in ``/``)
    #    start with the prefix directly so entries like ``tail logs/`` match
    #    ``tail logs/bridge.log`` without requiring a space between them.
    for prefix in PM_BASH_ALLOWED_PREFIXES:
        if normalized == prefix:
            return True
        if normalized.startswith(prefix + " "):
            return True
        if prefix.endswith("/") and normalized.startswith(prefix):
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
    """Start an SDLC stage on the parent PM session's PipelineStateMachine.

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


def _handle_skill_tool_start(tool_input: dict[str, Any], claude_uuid: str | None) -> None:
    """Handle Skill tool invocations by starting the corresponding pipeline stage.

    Called from pre_tool_use_hook when tool_name == "Skill". Looks up the skill
    name in _SKILL_TO_STAGE and calls _start_pipeline_stage if a mapping exists.
    Silently ignores unknown skills and missing session IDs.
    """
    skill_name = tool_input.get("skill", "")
    if not skill_name:
        logger.debug("[pre_tool_use] Skill tool called with empty skill name, skipping")
        return

    stage = _SKILL_TO_STAGE.get(skill_name)
    if not stage:
        logger.debug(
            f"[pre_tool_use] Skill '{skill_name}' not in _SKILL_TO_STAGE, skipping stage tracking"
        )
        return

    from agent.hooks.session_registry import resolve

    session_id = resolve(claude_uuid)
    if not session_id:
        logger.debug(
            f"[pre_tool_use] No session ID resolved for Skill '{skill_name}', skipping start_stage"
        )
        return

    _start_pipeline_stage(session_id, stage)


def _maybe_start_pipeline_stage(tool_input: dict[str, Any], claude_uuid: str | None = None) -> None:
    """Start an SDLC pipeline stage when the Agent tool spawns a dev-session.

    The child subprocess now self-registers its AgentSession via user_prompt_submit.py
    reading VALOR_PARENT_SESSION_ID (issue #808). This function no longer creates a
    dev-* AgentSession record — it only wires PipelineStateMachine.start_stage() so
    subagent_stop can later find the in_progress stage and mark it completed.

    Uses the session registry to resolve the bridge session ID from the
    Claude Code UUID (issue #597). Falls back gracefully if not found.
    """
    from agent.hooks.session_registry import resolve

    subagent_type = tool_input.get("type", "")
    if subagent_type != "dev-session":
        return

    parent_session_id = resolve(claude_uuid)
    if not parent_session_id:
        logger.debug("[pre_tool_use] No bridge session in registry, skipping pipeline stage start")
        return

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


# Backward-compatible alias so existing callers and tests can use either name.
_maybe_register_dev_session = _maybe_start_pipeline_stage


async def pre_tool_use_hook(
    input_data: PreToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Block writes to sensitive files and register child Dev sessions on Agent tool calls.

    Inspects Write and Edit tool calls for sensitive file paths
    and blocks them before execution. Also detects when the Agent tool
    spawns a dev-session and registers it in Redis with parent linkage.
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Detect Agent tool spawning a dev-session
    if tool_name == "Agent":
        claude_uuid = input_data.get("session_id")
        _maybe_start_pipeline_stage(tool_input, claude_uuid=claude_uuid)
        return {}

    # Detect Skill tool invocations and map to pipeline stage start
    if tool_name == "Skill":
        claude_uuid = input_data.get("session_id")
        try:
            _handle_skill_tool_start(tool_input, claude_uuid=claude_uuid)
        except Exception as e:
            logger.warning(f"[pre_tool_use] Skill stage start failed: {e}")
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

        # PM sessions: restrict Bash to the read-only allowlist. Runs AFTER
        # the sensitive-file check so sensitive-file violations surface with
        # their specific error message.
        if _is_pm_session() and not _is_pm_allowed_bash(command):
            truncated = (command or "")[:200]
            logger.warning(f"[pre_tool_use] PM blocked from running Bash command: {truncated!r}")
            return {
                "decision": "block",
                "reason": (
                    f"Blocked: PM session Bash restricted to a read-only allowlist. "
                    f"Command: {truncated!r}. "
                    "PM sessions may only run read-only git/gh/tail/cat/python -m tools "
                    "commands (see agent/hooks/pre_tool_use.py::PM_BASH_ALLOWED_PREFIXES). "
                    "Any mutation must be dispatched to a dev-session subagent."
                ),
            }

    return {}
