#!/usr/bin/env python3
"""User-level PreToolUse hook: Block code file commits to main.

This is a STANDALONE script deployed to ~/.claude/hooks/sdlc/ by the update
system. It imports shared utilities from sdlc_context.py in the same directory.

Behavior:
- If a `git commit` command targets the `main` branch AND staged files include
  code extensions (.py, .js, .ts), the commit is BLOCKED unconditionally.
- Non-code files (docs, plans, configs) are allowed on main.
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
from sdlc_context import allow, block, read_stdin


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

        # Only block code commits to main for repos with branch protection (popoto)
        try:
            repo_root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            repo_name = os.path.basename(repo_root)
            if repo_name != "popoto":
                allow()
        except Exception:
            pass

        # Popoto: block if staged files include code extensions
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            code_extensions = {".py", ".js", ".ts"}
            staged_files = [f for f in result.stdout.strip().split("\n") if f]
            staged_code = [
                f for f in staged_files if any(f.endswith(ext) for ext in code_extensions)
            ]
            if staged_code:
                block(
                    f"Cannot commit code files to main: {', '.join(staged_code[:3])}. "
                    "Use /sdlc to create a branch and PR. "
                    "Docs, plans, and configs can be committed to main."
                )
        except Exception:
            pass  # Fail open if git diff fails

        # Non-code files only — allow
        allow()

    except Exception:
        # Fail open: never block the user due to hook errors
        sys.exit(0)


if __name__ == "__main__":
    main()
