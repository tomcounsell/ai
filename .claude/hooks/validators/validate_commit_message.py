#!/usr/bin/env python3
"""
Validate git commit commands to block prohibited patterns.

Checks:
1. Co-author trailers (Co-Authored-By:) — prohibited in this repo
2. Empty commit messages — would create useless history

Fast path: non-commit bash commands exit immediately with no overhead.

Exit codes:
- 0: Validation passed (or not a commit command)
- 1: Validation failed (blocked)

Usage as PreToolUse hook:
  echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \\"fix: thing\\""}}' \\
    | python validate_commit_message.py

Claude Code hook protocol:
- Stdin: JSON with tool_name, tool_input, session_id
- To BLOCK: print {"decision": "block", "reason": "..."} to stdout, exit 0
- To ALLOW: print nothing or {"decision": "allow"}, exit 0
"""

import json
import re
import sys


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


def extract_commit_message(command: str) -> str | None:
    """Extract the commit message string from a git commit command.

    Handles common patterns:
    - git commit -m "message"
    - git commit -m 'message'
    - git commit --message="message"
    - git commit -m "$(cat <<'EOF'\n...\nEOF\n)"   (heredoc style)

    Returns the raw command text after -m if extraction fails, so we can
    still scan the full command string for prohibited patterns.
    """
    # Match -m or --message followed by quoted string
    patterns = [
        r'(?:-m|--message)\s+"((?:[^"\\]|\\.|\n)*)"',
        r"(?:-m|--message)\s+'((?:[^'\\]|\\.|\n)*)'",
        r'(?:-m|--message)=?"((?:[^"\\]|\\.|\n)*)"',
        r"(?:-m|--message)=?'((?:[^'\\]|\\.|\n)*)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, command, re.DOTALL)
        if match:
            return match.group(1)
    return None


def has_co_author_trailer(text: str) -> bool:
    """Return True if text contains a Co-Authored-By trailer (case-insensitive)."""
    return bool(re.search(r"co-authored-by\s*:", text, re.IGNORECASE))


def is_empty_message(message: str) -> bool:
    """Return True if the commit message is empty or whitespace only."""
    return not message.strip()


def main():
    hook_input = read_stdin()

    # Fast path: ignore non-Bash tools
    if hook_input.get("tool_name") != "Bash":
        allow()

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    # Fast path: ignore non-commit commands
    if not command or "git commit" not in command:
        allow()

    # Scan the full command string for co-author trailer (covers heredocs and
    # variable-interpolated strings where message extraction may not catch it)
    if has_co_author_trailer(command):
        block(
            "Co-author trailers (Co-Authored-By:) are not allowed in this repository. "
            "Remove the co-author trailer from the commit message."
        )

    # Try to extract the explicit commit message for empty-check
    message = extract_commit_message(command)
    if message is not None and is_empty_message(message):
        block(
            "Empty commit messages are not allowed. Provide a descriptive commit message with -m."
        )

    allow()


if __name__ == "__main__":
    main()
