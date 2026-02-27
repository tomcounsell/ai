#!/usr/bin/env python3
"""Hook: PostToolUse - Log after tool execution and track SDLC session state."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Standalone script — sys.path mutation is safe (never imported as library)
# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from utils.constants import (
    append_to_log,
    ensure_session_log_dir,
    get_data_sessions_dir,
    get_project_dir,
    get_session_id,
    read_hook_input,
)

# File-specific reminders: when these files are modified, print a reminder
FILE_REMINDERS = {
    "SOUL.md": (
        "REMINDER: SOUL.md was modified. Review bridge/summarizer.py to ensure "
        "SUMMARIZER_SYSTEM_PROMPT still matches Valor's voice (senior dev → PM style)."
    ),
}

# Code file extensions that indicate a coding session requiring SDLC enforcement
CODE_EXTENSIONS = {".py", ".js", ".ts"}

# Quality commands to track in the SDLC state
QUALITY_COMMANDS = ("pytest", "ruff", "black")

# Map SDLC skill names to their pipeline stage
SKILL_TO_STAGE = {
    "sdlc": "ISSUE",
    "do-plan": "PLAN",
    "do-build": "BUILD",
    "do-test": "TEST",
    "do-pr-review": "REVIEW",
    "do-patch": None,  # Patch doesn't have its own stage
    "do-docs": "DOCS",
    "do-docs-audit": None,
}


def is_code_file(file_path: str) -> bool:
    """Return True if the file path has a code extension (.py, .js, .ts)."""
    if not file_path:
        return False
    return Path(file_path).suffix.lower() in CODE_EXTENSIONS


def get_sdlc_state_path(session_id: str) -> Path:
    """Return the path to the SDLC state file for a given session."""
    return get_data_sessions_dir() / session_id / "sdlc_state.json"


def _default_sdlc_state() -> dict:
    """Return a fresh default SDLC state dict."""
    return {
        "code_modified": False,
        "files": [],
        "quality_commands": {"pytest": False, "ruff": False, "black": False},
    }


def load_sdlc_state(session_id: str) -> dict:
    """Load the SDLC state for a session, returning defaults if not found."""
    state_path = get_sdlc_state_path(session_id)
    if not state_path.exists():
        return _default_sdlc_state()
    try:
        with open(state_path) as f:
            data = json.load(f)
        # Ensure all expected keys are present (forward-compat)
        state = _default_sdlc_state()
        state.update(data)
        return state
    except (json.JSONDecodeError, OSError):
        return _default_sdlc_state()


def save_sdlc_state(session_id: str, state: dict) -> None:
    """Persist the SDLC state for a session, creating parent dirs as needed.

    Uses atomic write (tmp + rename) to avoid partial writes on crash.
    """
    state_path = get_sdlc_state_path(session_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        tmp_path.rename(state_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _get_current_branch() -> str | None:
    """Return the current git branch name, or None if git fails."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = result.stdout.strip()
        return branch if branch else None
    except Exception:
        return None


def update_sdlc_state_for_file_write(hook_input: dict) -> None:
    """Update SDLC state when a Write or Edit tool use touches a code file.

    Fast path: if the tool is not Write/Edit, or the file is not a code file,
    return immediately without any I/O.

    On the first code modification, records the current git branch as
    ``modified_on_branch`` so the stop hook can distinguish code that arrived
    on main via a PR merge from code written directly on main.
    """
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        return

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not is_code_file(file_path):
        return

    session_id = hook_input.get("session_id", "unknown")
    state = load_sdlc_state(session_id)

    # Record the branch on the *first* code modification only.
    # Subsequent writes do not overwrite — the first branch wins.
    if not state.get("code_modified") and "modified_on_branch" not in state:
        branch = _get_current_branch()
        if branch:
            state["modified_on_branch"] = branch

    state["code_modified"] = True
    if file_path not in state["files"]:
        state["files"].append(file_path)
    save_sdlc_state(session_id, state)


def update_sdlc_state_for_bash(hook_input: dict) -> None:
    """Update state when a Bash tool runs quality commands or merges a PR.

    Tracks two categories of bash commands:

    1. **Quality commands** (pytest, ruff, black): marks them as run in the
       session's quality_commands dict.
    2. **PR merge** (``gh pr merge``): resets ``code_modified`` to False,
       since the code has been properly merged via a PR and is no longer
       "pending" on the session.

    Key principle: if no sdlc_state.json exists (non-code session), do nothing.
    We only track these when the session is already classified as a code session
    (i.e., the state file was created by a prior code file write).
    """
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Bash":
        return

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # Detect gh pr merge commands (belt-and-suspenders cleanup after merge)
    is_merge = bool(re.search(r"\bgh\s+pr\s+merge\b", command))

    # Check if this command runs any quality tool.
    # Use regex word boundary to avoid false positives like `echo "pytest"` or
    # `grep pytest` matching as if pytest was actually run.
    matched_command = None
    for cmd in QUALITY_COMMANDS:
        if re.search(r"(?:^|&&|\|\||;|\s)" + re.escape(cmd) + r"\b", command):
            matched_command = cmd
            break

    if matched_command is None and not is_merge:
        return

    session_id = hook_input.get("session_id", "unknown")
    state_path = get_sdlc_state_path(session_id)

    # Fast path: if no state file exists, this is not a code session — skip
    if not state_path.exists():
        return

    state = load_sdlc_state(session_id)

    if matched_command is not None:
        state["quality_commands"][matched_command] = True

    if is_merge:
        state["code_modified"] = False

    save_sdlc_state(session_id, state)


def update_stage_progress_for_skill(hook_input: dict) -> None:
    """Update SDLC stage progress when a Skill tool completes an SDLC skill.

    Detects Skill tool calls for SDLC skills (sdlc, do-plan, do-build, etc.)
    and calls session_progress.py to mark the stage as completed. This runs
    deterministically in the hook — no agent cooperation needed.
    """
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Skill":
        return

    tool_input = hook_input.get("tool_input", {})
    skill_name = tool_input.get("skill", "")

    # Only track SDLC skills
    if skill_name not in SKILL_TO_STAGE:
        return

    stage = SKILL_TO_STAGE[skill_name]
    if stage is None:
        return  # do-patch etc. don't have their own stage

    session_id = hook_input.get("session_id", "")
    if not session_id:
        return

    # Extract links from tool output if present
    tool_output = str(hook_input.get("tool_output", ""))
    link_args = []

    # Try to find PR URL in output
    pr_match = re.search(r"https://github\.com/[^/]+/[^/]+/pull/\d+", tool_output)
    if pr_match and stage == "BUILD":
        link_args.extend(["--pr-url", pr_match.group(0)])

    issue_match = re.search(r"https://github\.com/[^/]+/[^/]+/issues/\d+", tool_output)
    if issue_match and stage == "ISSUE":
        link_args.extend(["--issue-url", issue_match.group(0)])

    # Determine status - check if output suggests failure
    output_lower = tool_output.lower()
    if any(
        kw in output_lower
        for kw in ["failed", "stuck", "error", "blocker", "changes requested"]
    ):
        status = "failed" if stage in ("TEST",) else "in_progress"
    else:
        status = "completed"

    # Call session_progress.py - fire and forget
    project_dir = get_project_dir()
    cmd = [
        sys.executable,
        "-m",
        "tools.session_progress",
        "--session-id",
        session_id,
        "--stage",
        stage,
        "--status",
        status,
    ] + link_args

    try:
        subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            timeout=5,
            env={**os.environ, "PYTHONPATH": str(project_dir)},
        )
    except Exception:
        pass  # Fire and forget - never block the agent


def check_file_reminders(hook_input: dict) -> None:
    """Print reminders when specific files are modified."""
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        return

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    for filename, reminder in FILE_REMINDERS.items():
        if filename in file_path:
            print(reminder)


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Check for file-specific reminders
    check_file_reminders(hook_input)

    # Update SDLC session state based on tool type
    update_sdlc_state_for_file_write(hook_input)
    update_sdlc_state_for_bash(hook_input)
    update_stage_progress_for_skill(hook_input)

    session_id = get_session_id(hook_input)
    session_dir = ensure_session_log_dir(session_id)

    tool_name = hook_input.get("tool_name", "unknown")
    tool_output = hook_input.get("tool_output", "")

    # Truncate large outputs to avoid bloating logs
    if isinstance(tool_output, str) and len(tool_output) > 2000:
        tool_output = tool_output[:2000] + "... [truncated]"

    entry = {
        "event": "post_tool_use",
        "tool_name": tool_name,
        "tool_output_preview": tool_output,
        "end_time": time.time(),
    }

    append_to_log(session_dir, "tool_use.jsonl", entry)


if __name__ == "__main__":
    main()
