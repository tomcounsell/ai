#!/usr/bin/env python3
"""User-level PreToolUse hook: Block git commits to main in SDLC context.

This is a STANDALONE script deployed to ~/.claude/hooks/sdlc/ by the update
system. It imports shared utilities from sdlc_context.py in the same directory.

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

import os
import subprocess
import sys

# Standalone script — sys.path mutation is safe (never imported as library)
# Import shared utilities from sibling module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdlc_context import allow, block, is_sdlc_context, read_stdin


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
