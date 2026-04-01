#!/usr/bin/env python3
"""Hook: PostToolUse - Log after tool execution and track SDLC session state."""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

# Standalone script — sys.path mutation is safe (never imported as library)
# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from hook_utils.constants import (
    append_to_log,
    ensure_session_log_dir,
    get_data_sessions_dir,
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
QUALITY_COMMANDS = ("pytest", "ruff", "ruff-format")


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
        "quality_commands": {"pytest": False, "ruff": False, "ruff-format": False},
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
    try:
        save_sdlc_state(session_id, state)
    except Exception as e:
        print(
            f"HOOK WARNING: Failed to save SDLC state for {session_id}: {e}",
            file=sys.stderr,
        )


def update_sdlc_state_for_bash(hook_input: dict) -> None:
    """Update state when a Bash tool runs quality commands, merges a PR, or switches branches.

    Tracks three categories of bash commands:

    1. **Quality commands** (pytest, ruff, ruff-format): marks them as run in the
       session's quality_commands dict.
    2. **PR merge** (``gh pr merge``): resets ``code_modified`` to False and
       clears ``modified_on_branch``, since the code has been properly merged
       via a PR and is no longer "pending" on the session.
    3. **Branch switch** (``git checkout -b session/*`` or ``git switch -c session/*``):
       updates ``modified_on_branch`` to the new session branch, fixing the
       stale-state bug where code edited on main before branch creation would
       permanently record ``modified_on_branch: "main"`` (see issue #261).

    Key principle: if no sdlc_state.json exists (non-code session), do nothing.
    We only track these when the session is already classified as a code session
    (i.e., the state file was created by a prior code file write).
    """
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Bash":
        return

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # Detect branch switch to a session/* branch (fixes stale modified_on_branch)
    branch_match = re.search(r"\bgit\s+(?:checkout\s+-b|switch\s+-c)\s+(session/\S+)", command)
    if branch_match:
        session_id = hook_input.get("session_id", "unknown")
        state_path = get_sdlc_state_path(session_id)
        if state_path.exists():
            state = load_sdlc_state(session_id)
            if state.get("code_modified"):
                state["modified_on_branch"] = branch_match.group(1)
                try:
                    save_sdlc_state(session_id, state)
                except Exception as e:
                    print(
                        f"HOOK WARNING: Failed to save SDLC state for {session_id}: {e}",
                        file=sys.stderr,
                    )
        # Branch switch is handled; fall through to also check for quality/merge
        # in case the command is chained (e.g., "git checkout -b session/foo && pytest")

    # Detect gh pr merge commands (belt-and-suspenders cleanup after merge)
    is_merge = bool(re.search(r"\bgh\s+pr\s+merge\b", command))

    # Check if this command runs any quality tool.
    # Use regex word boundary to avoid false positives like `echo "pytest"` or
    # `grep pytest` matching as if pytest was actually run.
    # Map regex patterns to quality command keys (order matters: check
    # "ruff format" before bare "ruff" to avoid premature match).
    quality_patterns = [
        (r"(?:^|&&|\|\||;|\s)(?:python\s+-m\s+)?ruff\s+format\b", "ruff-format"),
        (r"(?:^|&&|\|\||;|\s)pytest\b", "pytest"),
        (r"(?:^|&&|\|\||;|\s)(?:python\s+-m\s+)?ruff\b", "ruff"),
    ]
    matched_command = None
    for pattern, key in quality_patterns:
        if re.search(pattern, command):
            matched_command = key
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
        state.pop("modified_on_branch", None)  # Clear stale branch tracking

        # Store merge_detected flag and PR number in agent session sidecar
        # for post-merge learning extraction in the Stop hook
        try:
            from hook_utils.memory_bridge import (
                load_agent_session_sidecar,
                save_agent_session_sidecar,
            )

            as_sidecar = load_agent_session_sidecar(session_id)
            as_sidecar["merge_detected"] = True
            # Extract PR number from command (e.g., "gh pr merge 123")
            pr_match = re.search(r"gh\s+pr\s+merge\s+(\d+)", command)
            if pr_match:
                as_sidecar["merged_pr_number"] = pr_match.group(1)
            save_agent_session_sidecar(session_id, as_sidecar)
        except Exception:
            pass  # Non-fatal

    try:
        save_sdlc_state(session_id, state)
    except Exception as e:
        print(
            f"HOOK WARNING: Failed to save SDLC state for {session_id}: {e}",
            file=sys.stderr,
        )


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


def _run_memory_recall(hook_input: dict) -> str | None:
    """Run memory recall and return additionalContext string or None.

    Queries subconscious memory based on accumulated tool calls.
    Fails silently -- memory errors never block tool execution.
    """
    try:
        from hook_utils.memory_bridge import recall

        session_id = hook_input.get("session_id", "unknown")
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})

        return recall(session_id, tool_name, tool_input)
    except Exception:
        return None


def _update_agent_session(hook_input: dict) -> None:
    """Update AgentSession last_activity and tool_call_count.

    Reads the agent_session_id from the sidecar file and updates
    the corresponding AgentSession record in Redis. Fails silently.
    """
    try:
        session_id = hook_input.get("session_id", "")
        if not session_id:
            return

        from hook_utils.memory_bridge import load_agent_session_sidecar

        sidecar = load_agent_session_sidecar(session_id)
        agent_session_id = sidecar.get("agent_session_id")
        if not agent_session_id:
            return

        from models.agent_session import AgentSession

        local_sid = f"local-{session_id}"
        matches = list(AgentSession.query.filter(session_id=local_sid))
        if not matches:
            return
        agent_session = matches[0]

        agent_session.last_activity = time.time()
        agent_session.tool_call_count = (agent_session.tool_call_count or 0) + 1
        agent_session.save()
    except Exception:
        pass  # Silent failure -- never block tool execution


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Check for file-specific reminders
    check_file_reminders(hook_input)

    # Update SDLC session state based on tool type
    update_sdlc_state_for_file_write(hook_input)
    update_sdlc_state_for_bash(hook_input)

    # Update AgentSession lifecycle tracking
    _update_agent_session(hook_input)

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

    # Memory recall -- query subconscious memory and inject thoughts
    additional_context = _run_memory_recall(hook_input)
    if additional_context:
        # Output hook response with additionalContext for thought injection
        response = json.dumps({"additionalContext": additional_context})
        print(response)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        from hook_utils.constants import log_hook_error

        log_hook_error("post_tool_use", str(e))
