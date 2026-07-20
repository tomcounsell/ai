"""PreToolUse hook: blocks sensitive writes, enforces teammate limits, tracks SDLC stage starts.

Teammate write enforcement
--------------------------
For teammate sessions (``SESSION_TYPE=teammate``), the Write/Edit/MultiEdit
branch enforces ONE hard rule: writes to source-code paths are blocked.
``_teammate_is_allowed_write`` defines the universal allowlist (docs/,
.claude/, .github/, wiki/, skills/, top-level meta files, and the
~/work-vault/ knowledge base root). The check uses a two-pass algorithm:
``os.path.normpath`` defeats ``..`` path-traversal, then ``os.path.realpath``
on the parent directory defeats symlink-escape (e.g. ``ln -s ../agent
docs/escape && Write("docs/escape/sdk_client.py", ...)``).

Bash is NOT blocked for teammate sessions but every command is audit-logged
with the ``[teammate-audit]`` tag at INFO level. The audit call is wrapped
in try/except so an audit failure can never block the user's command.

The block message includes the exact ``valor-session create --role eng``
command the teammate should propose to the human, so the redirect is
self-contained and actionable.

Skill tool stage tracking
-------------------------
When an ENG session calls the Skill tool (e.g., ``Skill(skill="do-build")``),
``_handle_skill_tool_start()`` maps the skill name to an SDLC stage via
``_SKILL_TO_STAGE`` and calls ``PipelineStateMachine.start_stage()`` on the
parent session (resolved via the ``AGENT_SESSION_ID`` env var). This marks
the stage as ``in_progress`` so the worker post-completion handler can later
classify the outcome and call ``complete_stage()`` or ``fail_stage()``.

ENG session registration is no longer done in this hook. ENG sessions are
created as ``AgentSession`` records via ``valor_session create --role eng``
and self-register their parent linkage via the ``VALOR_PARENT_SESSION_ID``
env var (see Phase 4+5 of the harness abstraction in
``docs/features/harness-abstraction.md``).
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


# --- Teammate session write allowlist ------------------------------------------
#
# Teammate sessions (``SESSION_TYPE=teammate``) can do real operational work:
# update docs, edit .claude/ skills, run scripts, restart services, manage
# the knowledge base. The ONE hard rule enforced in code is that writes to
# source code paths require spawning a Dev session.
#
# This is a positive allowlist — anything NOT explicitly listed here is
# treated as a code path and blocked for Write/Edit/MultiEdit. Bash is
# audit-logged but not blocked (see Rabbit Holes in
# docs/plans/teammate-allowlist-enforce.md for the rationale).
#
# Assumes the teammate session's cwd is the project root (the worker
# establishes this contract when spawning the session, matching what PM
# enforcement already relies on). If the cwd contract breaks, PM enforcement
# would also break.

# Anchored directory names: parts[0] of the project-root-relative path must
# equal one of these to be allowed. NOT a substring match — a path like
# ``agent/docs_handler/foo.py`` does NOT match the docs/ rule.
TEAMMATE_ALLOWED_DIR_NAMES_AT_ROOT: frozenset[str] = frozenset(
    {
        "docs",
        ".claude",
        ".github",
        "wiki",
        "skills",
    }
)

# Exact top-level filenames at the project root (depth == 1).
TEAMMATE_ALLOWED_TOPLEVEL_NAMES: frozenset[str] = frozenset(
    {
        "README.md",
        "CHANGELOG.md",
        "CLAUDE.md",
        "AGENTS.md",
        "GEMINI.md",
        "OPENCLAW.md",
        "SWARM.md",
        "PLAN.md",
        "TODO.md",
        "ROADMAP.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "MAINTENANCE.md",
        "DEPLOYMENT.md",
        "INSTRUCTIONS.md",
        "LICENSE",
        "NOTICE",
        "CNAME",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
    }
)

# Top-level extensions allowed at depth == 1 only. Catches PHASE_*.md,
# MODERNIZATION_*.md, etc. without listing each by name. Nested *.md
# (e.g. apps/api/README.md) is NOT allowed by this rule — only the
# anchored top-level dir names (docs/, etc.) cover nested markdown.
TEAMMATE_ALLOWED_TOPLEVEL_EXTENSIONS: tuple[str, ...] = (".md",)

# Absolute path prefixes always allowed (knowledge base). These are matched
# BEFORE rebasing to the project root, so writes to the vault from any cwd
# work as expected.
TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES: tuple[str, ...] = (os.path.expanduser("~/work-vault/"),)


def _is_teammate_session() -> bool:
    """Check whether the current session is a teammate session."""
    return os.environ.get("SESSION_TYPE") == SessionType.TEAMMATE


def _path_on_teammate_allowlist(path: str, project_root: str) -> bool:
    """Return True iff *path* is on the teammate write allowlist.

    ``path`` may be relative or absolute. ``project_root`` is the project's
    real (symlink-resolved) working directory. Used by
    ``_teammate_is_allowed_write`` for both the normalization pass and the
    realpath pass.
    """
    # Absolute prefixes (vault) — match BEFORE rebasing to project root so
    # writes to ~/work-vault/ work from any cwd.
    abs_path = os.path.abspath(path)
    for prefix in TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES:
        if abs_path.startswith(prefix):
            return True

    # Rebase to project-root-relative for the directory/top-level checks.
    try:
        rel = os.path.relpath(abs_path, project_root)
    except ValueError:
        return False  # Different drives (Windows); shouldn't happen on macOS.
    if rel.startswith(".."):
        return False  # Outside project root.

    rel_posix = rel.replace("\\", "/")
    parts = rel_posix.split("/")
    first = parts[0] if parts else ""

    # Directory prefix check — ANCHORED to parts[0]. Require len(parts) > 1
    # so a bare file literally named ``docs`` (no extension) at project root
    # does NOT match the directory rule. Bare top-level files go through the
    # explicit filename / extension allowlist below.
    if len(parts) > 1 and first in TEAMMATE_ALLOWED_DIR_NAMES_AT_ROOT:
        return True

    # Top-level file check — exactly ONE part means top-level file.
    if len(parts) == 1:
        if first in TEAMMATE_ALLOWED_TOPLEVEL_NAMES:
            return True
        if any(first.endswith(ext) for ext in TEAMMATE_ALLOWED_TOPLEVEL_EXTENSIONS):
            return True

    return False


def _teammate_is_allowed_write(file_path: str) -> bool:
    """Check whether a teammate session is allowed to write to *file_path*.

    Two-pass algorithm:

    - Pass 1 (``os.path.normpath``) defeats syntactic path-traversal via
      ``..`` (e.g. ``docs/../agent/foo.py`` would otherwise sneak past a
      naive substring check because ``/docs/`` appears in the input).
    - Pass 2 (``os.path.realpath`` on the parent directory) defeats
      symlink-escape (e.g. ``ln -s ../agent docs/escape`` followed by
      Write to ``docs/escape/sdk_client.py``). We realpath the parent
      directory + raw basename so we don't follow a symlink that doesn't
      yet exist (Write creates files), but DO follow any symlink in the
      parent chain — the actual escape vector.

    Both passes must agree the path is on the allowlist. Default-deny on
    any error.
    """
    if not file_path:
        return False

    # Resolve project root from cwd (matches the cwd contract the worker
    # establishes when spawning the session — same contract PM relies on).
    project_root = os.path.realpath(os.getcwd())

    # PASS 1 — normalize input to defeat `..` traversal.
    normalized = os.path.normpath(file_path)
    if not _path_on_teammate_allowlist(normalized, project_root):
        return False

    # PASS 2 — realpath to defeat symlink escape.
    try:
        parent = os.path.realpath(os.path.dirname(os.path.abspath(normalized)))
        resolved = os.path.join(parent, os.path.basename(normalized))
    except OSError:
        return False  # Can't resolve → default-deny
    if not _path_on_teammate_allowlist(resolved, project_root):
        return False

    return True


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


def _start_pipeline_stage(pm_session_id: str, stage: str) -> None:
    """Start an SDLC stage, preferring the issue-keyed PipelineLedger.

    Loads the parent AgentSession from Redis, resolves the backing state
    machine via ``agent.pipeline_state.resolve_pipeline_state_machine`` --
    which prefers the durable, issue-keyed ``PipelineLedger`` when the
    session's per-issue run_id lease confirms ownership and a target_repo is
    pinned (issue #2012 follow-up), falling back to the original
    session-keyed ``PipelineStateMachine(session)`` path otherwise -- and
    calls ``start_stage()``. Marks the stage as in_progress in whichever
    backing store was resolved.

    Failures are logged but never raised -- this must not block the Agent tool.
    """
    try:
        from agent.pipeline_state import resolve_pipeline_state_machine
        from models.agent_session import AgentSession

        parent_sessions = list(AgentSession.query.filter(session_id=pm_session_id))
        if not parent_sessions:
            logger.warning(
                f"[pre_tool_use] Parent session {pm_session_id} not found, "
                f"skipping start_stage({stage})"
            )
            return

        parent = parent_sessions[0]
        sm, used_ledger, detail = resolve_pipeline_state_machine(parent)
        if not used_ledger:
            logger.debug(f"[pre_tool_use] {detail} for session {pm_session_id}")
        sm.start_stage(stage)
        logger.info(
            f"[pre_tool_use] Started pipeline stage {stage} on session {pm_session_id} ({detail})"
        )
    except Exception as e:
        logger.warning(
            f"[pre_tool_use] Failed to start pipeline stage {stage} on session {pm_session_id}: {e}"
        )


def _handle_skill_tool_start(tool_input: dict[str, Any], claude_uuid: str | None) -> None:
    """Handle Skill tool invocations by starting the corresponding pipeline stage.

    Called from pre_tool_use_hook when tool_name == "Skill". Looks up the skill
    name in _SKILL_TO_STAGE and calls _start_pipeline_stage if a mapping exists.
    Silently ignores unknown skills and missing session IDs.

    Session ID resolution uses AGENT_SESSION_ID env var (set by the worker when
    spawning the session). This replaces the previous session_registry.resolve()
    approach (Phase 5 cleanup).
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

    import os

    session_id = os.environ.get("AGENT_SESSION_ID")
    if not session_id:
        logger.debug(
            f"[pre_tool_use] AGENT_SESSION_ID not set for Skill '{skill_name}', "
            "skipping start_stage"
        )
        return

    _start_pipeline_stage(session_id, stage)


def _resolve_sdk_session():
    """Resolve the in-flight AgentSession for the SDK hook via ``AGENT_SESSION_ID``.

    Mirrors ``_handle_skill_tool_start``'s resolution: ``AGENT_SESSION_ID`` holds
    the session_id (set by the worker when spawning the session), looked up with
    ``AgentSession.query.filter(session_id=...)``.

    Returns the session, or ``None`` for a GENUINE no-session (env unset / no
    matching record). RAISES on an infra/resolution error (Redis raised) — the
    caller catches it separately for the loud "backstop BLIND" path.
    """
    session_id = os.environ.get("AGENT_SESSION_ID")
    if not session_id:
        return None
    from models.agent_session import AgentSession

    matches = list(AgentSession.query.filter(session_id=session_id))
    if not matches:
        return None
    return matches[0]


def _enforce_tool_budget_sdk() -> dict[str, Any] | None:
    """Synchronous per-tool budget backstop for the SDK/headless surface (#1821).

    Returns a ``{"decision":"block","reason":...}`` dict for an over-budget
    session (under ``TOOL_BUDGET_ENABLED``, default on), or ``None`` to allow.

    Fails OPEN on any resolution/infra error, splitting genuine no-session
    (silent allow) from an infra error (loud WARNING + ``resolution_errors``
    counter). A bug inside the evaluate/actuate step also fails OPEN (returns
    ``None``) — the budget is a backstop and must never brick a tool call.
    """
    # Resolution split: genuine no-session vs infra error.
    try:
        session = _resolve_sdk_session()
    except Exception as e:
        try:
            from agent.tool_budget import _project_key_env, record_resolution_error

            record_resolution_error(_project_key_env(), e, surface="sdk-hook")
        except Exception as inner:
            logger.warning("[pre_tool_use] tool-budget resolution error (BLIND): %s", inner)
        return None  # fail open
    if session is None:
        return None  # genuine no-session → silent allow

    # Successful resolution: evaluate + actuate. Fail OPEN on any internal error.
    try:
        from agent.tool_budget import evaluate_tool_budget, record_budget_trip

        verdict = evaluate_tool_budget(session)
        if verdict.allow:
            return None
        # DENY. Surface first (fail-quiet), then block — surfacing NEVER flips it.
        record_budget_trip(session, verdict)
        logger.warning(
            "[pre_tool_use] tool-budget DENY for session %s: %s",
            getattr(session, "session_id", "?"),
            verdict.reason,
        )
        return {"decision": "block", "reason": verdict.reason}
    except Exception as e:
        logger.warning("[pre_tool_use] tool-budget check failed (fail-open): %s", e)
        return None


def _extract_declared_timeout_s(tool_name: str, tool_input: Any) -> float | None:
    """Return the tool call's declared timeout in SECONDS, or None (issue #2145).

    Today only Bash carries a first-class timeout parameter: ``timeout`` in
    **milliseconds** (max 600000 per the tool schema; confirmed against the
    2026-07-17 incident's ``tool_use.jsonl`` showing ``timeout: 600000`` for a
    10-minute budget). Non-dict input, missing/non-numeric/bool/NaN or
    non-positive values → None, so the wedge detector falls back to the tier
    budget. Never raises.
    """
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("timeout")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if raw != raw or raw <= 0:  # NaN or non-positive
        return None
    return float(raw) / 1000.0


async def pre_tool_use_hook(
    input_data: PreToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Block writes to sensitive files and track pipeline stage starts on Skill tool calls.

    Inspects Write and Edit tool calls for sensitive file paths
    and blocks them before execution. Detects Skill tool invocations to
    wire PipelineStateMachine.start_stage() for PM session stage tracking.
    Also records the tool boundary on the in-flight AgentSession so the
    dashboard can show ``current_tool_name`` (issue #1172, Pillar A).
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Fix #6 (issue #1821): synchronous per-tool budget backstop. Runs at the
    # TOP so it covers ALL tools (before the write-capable filter) and fires
    # inline even when every background health loop is frozen.
    budget_block = _enforce_tool_budget_sdk()
    if budget_block is not None:
        return budget_block

    # Pillar A liveness write — fire-and-forget, never blocks the hook.
    try:
        from agent.hooks.liveness_writers import record_tool_boundary

        record_tool_boundary(
            tool_name=tool_name,
            clear=False,
            declared_timeout_s=_extract_declared_timeout_s(tool_name, tool_input),
        )
    except Exception as _liveness_err:
        logger.debug("[pre_tool_use] liveness write failed (non-fatal): %s", _liveness_err)

    # Detect Skill tool invocations and map to pipeline stage start
    if tool_name == "Skill":
        claude_uuid = input_data.get("session_id")
        try:
            _handle_skill_tool_start(tool_input, claude_uuid=claude_uuid)
        except Exception as e:
            logger.warning(f"[pre_tool_use] Skill stage start failed: {e}")
        return {}

    # Only inspect write-capable tools
    if tool_name not in ("Write", "Edit", "MultiEdit", "Bash"):
        return {}

    # For Write/Edit/MultiEdit, check the file_path parameter
    if tool_name in ("Write", "Edit", "MultiEdit"):
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
        # Teammate sessions: writes restricted to docs/, .claude/, .github/,
        # wiki/, skills/, top-level meta files, and ~/work-vault/. Source
        # code paths require spawning an ENG session.
        if _is_teammate_session() and not _teammate_is_allowed_write(file_path):
            logger.warning(f"[pre_tool_use] Teammate blocked from writing to: {file_path}")
            return {
                "decision": "block",
                "reason": (
                    f"Blocked: teammate sessions cannot write to '{file_path}'. "
                    "This path looks like source code, which requires an ENG session. "
                    "To proceed:\n\n"
                    "  valor-session create --role eng --slug <slug> "
                    '--message "<task description>"\n\n'
                    "Suggest this to the human first and wait for explicit "
                    "confirmation before spawning the ENG session. Teammates "
                    "may write to: docs/, .claude/, .github/, wiki/, skills/, "
                    "top-level *.md and meta files, and ~/work-vault/."
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

        # Teammate sessions: Bash is NOT blocked, but every command is
        # audit-logged so misuse is visible after the fact. Fire-and-forget
        # — an audit failure must never block the user's command.
        if _is_teammate_session():
            try:
                truncated = (command or "")[:500]
                logger.info(f"[teammate-audit] bash command={truncated!r}")
            except Exception as _audit_err:
                logger.debug(
                    "[pre_tool_use] teammate audit log failed (non-fatal): %s",
                    _audit_err,
                )

    return {}
