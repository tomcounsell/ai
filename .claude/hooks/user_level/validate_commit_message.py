#!/usr/bin/env python3
"""User-level PreToolUse hook: Block git commits to main in SDLC context.

This is a STANDALONE script deployed to ~/.claude/hooks/sdlc/ by the update
system. It has NO imports from the AI project — all logic is self-contained.

Behavior:
- If a `git commit` command targets the `main` branch AND we are in an SDLC
  context (session/ branch detected or AgentSession has SDLC stages), the
  commit is BLOCKED with an explanatory error.
- If not in SDLC context (manual work, non-SDLC repo), silently allows.
- If not a git commit command, silently allows (fast path).

Exit codes:
  0 — always (Claude Code hook protocol: block via stdout JSON, not exit code)

Claude Code hook protocol:
  Stdin: JSON with tool_name, tool_input, session_id
  To BLOCK: print {"decision": "block", "reason": "..."} to stdout, exit 0
  To ALLOW: print nothing, exit 0
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def is_sdlc_context() -> bool:
    """Detect if we are in an SDLC-managed session.

    Two-tier check:
    1. Git branch starts with "session/" (inside do-build worktree)
    2. AgentSession model shows SDLC stages (requires Redis + AI repo)
    """
    # Check 1: On a session/ branch (inside do-build worktree)
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if branch.startswith("session/"):
            return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Check 2: Query AgentSession model for active SDLC session
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        try:
            sys.path.insert(0, str(Path.home() / "src" / "ai"))
            from models.agent_session import AgentSession

            sessions = AgentSession.query.filter(
                session_id=session_id, status="active"
            )
            for s in sessions:
                history = getattr(s, "history", None)
                if history and any("stage" in str(h) for h in history):
                    return True
        except Exception:
            pass  # Redis unavailable, model not importable, etc.

    return False


def read_stdin() -> dict:
    """Read and parse JSON from stdin. Returns empty dict on failure."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def block(reason: str) -> None:
    """Print a block decision and exit 0."""
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def allow() -> None:
    """Allow the command through and exit 0."""
    sys.exit(0)


def get_current_branch() -> str | None:
    """Return the current git branch name, or None if not in a git repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main():
    try:
        hook_input = read_stdin()

        # Fast path: ignore non-Bash tools
        if hook_input.get("tool_name") != "Bash":
            allow()

        tool_input = hook_input.get("tool_input", {})
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

        # Fast path: ignore non-commit commands
        if not command or "git commit" not in command:
            allow()

        # Check if committing to main
        branch = get_current_branch()
        if branch != "main":
            allow()

        # On main branch with a git commit — check SDLC context
        if is_sdlc_context():
            block(
                "SDLC enforcement: Cannot commit directly to main during an SDLC session. "
                "Create a feature branch (session/{slug}) or use a worktree. "
                "The SDLC pipeline requires: branch -> implement -> test -> review -> PR -> merge."
            )

        # Not in SDLC context — allow (manual work on main is fine)
        allow()

    except Exception:
        # Fail open: never block the user due to hook errors
        sys.exit(0)


if __name__ == "__main__":
    main()
