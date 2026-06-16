#!/usr/bin/env python3
"""Pre-push hook: reject Plan-only commits pushed to refs/heads/main (issue #1394).

Plans committed directly to main create rebase pressure on open feature branches.
This hook blocks pushes to main where:
  1. The commit message matches the Plan commit pattern (Plan: / plan(#N):), AND
  2. ALL changed files are under docs/plans/*.md (pure plan-only push).

Mixed commits (plan doc + code) are allowed — only pure plan-only pushes are blocked.

Exit codes:
  0: Always (Claude Code hook protocol — block/allow signalled via JSON stdout)

JSON output:
  {"decision": "block", "reason": "..."}  — push rejected
  (no output)                              — push allowed
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
    """Allow the push and exit 0 (no output)."""
    sys.exit(0)


# Matches: Plan: ..., plan: ..., Plan(#123): ..., plan(#123): ...
PLAN_MESSAGE_RE = re.compile(r"^[Pp]lan(\(#\d+\))?:", re.IGNORECASE)

# Only docs/plans/ markdown files count as "plan-only"
PLAN_FILE_RE = re.compile(r"^docs/plans/[^/]+\.md$")


def is_plan_message(message: str) -> bool:
    """Return True if the commit message matches the Plan commit pattern."""
    return bool(PLAN_MESSAGE_RE.match(message.strip()))


def is_plan_only_files(files: list[str]) -> bool:
    """Return True if ALL changed files are docs/plans/*.md."""
    if not files:
        return False
    return all(PLAN_FILE_RE.match(f) for f in files)


def is_main_branch(ref: str) -> bool:
    """Return True if the push target is refs/heads/main."""
    return ref == "refs/heads/main"


def main() -> None:
    hook_input = read_stdin()

    # Fast path: only inspect Bash tool calls (git push commands)
    if hook_input.get("tool_name") != "Bash":
        allow()

    # Extract push context from the hook input
    context = hook_input.get("context", {})
    ref = context.get("ref", "")
    commit_message = context.get("commit_message", "")
    changed_files = context.get("changed_files", [])

    # Fast path: not a push to main
    if not ref or not is_main_branch(ref):
        allow()

    # Fast path: commit message does not match Plan pattern
    if not commit_message or not is_plan_message(commit_message):
        allow()

    # Fast path: not a pure plan-only file set
    if not is_plan_only_files(changed_files):
        allow()

    # All conditions met: block the push
    block(
        "Plan-only commits must not go directly to main (issue #1394). "
        "Commit the plan on the session/{slug} branch instead. "
        f"Commit message '{commit_message}' touches only docs/plans/ files. "
        "See docs/sdlc/do-merge.md for the correct plan lifecycle."
    )


if __name__ == "__main__":
    main()
