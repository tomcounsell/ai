#!/usr/bin/env python3
"""
Validate that the file a Write just targeted contains required content strings.

The target is the path the hook payload names, and nothing else — taken as
given, never rewritten against a cwd. ``--directory`` and ``--extension`` are
the scope filter they have always effectively been: a write outside them passes
through untouched.

Checks:
1. Resolve the written file from the hook payload (or an explicit CLI path)
2. Verify that file contains all required strings

Exit codes:
- 0: Validation passed (file contains all required content, or out of scope)
- 2: Validation failed, blocks agent (missing content)

Usage:
  uv run validate_file_contains.py -d docs/plans -e .md --contains "## Problem"
  uv run validate_file_contains.py docs/plans/my-plan.md --contains "## Problem"

Frontmatter example:
  hooks:
    PostToolUse:
      - hooks:
          - type: command
            command: >-
              uv run $CLAUDE_PROJECT_DIR/.claude/hooks/validators/validate_file_contains.py
              -d docs/plans -e .md
              --contains '## Problem'
              --contains '## Appetite'
"""

import argparse
import json
import sys
from pathlib import Path

# Standalone script — sys.path mutation is safe (never imported as library).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hook_utils.hook_target import in_scope, read_hook_input, target_from_hook_input  # noqa: E402

DEFAULT_DIRECTORY = "docs/plans"
DEFAULT_EXTENSION = ".md"

MISSING_CONTENT_ERROR = (
    "VALIDATION FAILED: File '{file}' is missing {count} required section(s).\n\n"
    "MISSING SECTIONS:\n{missing_list}\n\n"
    "ACTION REQUIRED: Add the missing sections to '{file}'. "
    "Do not stop until all required sections are present."
)


def check_contains(filepath: str, required: list[str]) -> tuple[bool, list[str], list[str]]:
    """Check if file contains all required strings (case-insensitive)."""
    try:
        content = Path(filepath).read_text(encoding="utf-8").lower()
    except (OSError, UnicodeDecodeError):
        return False, [], required

    found = []
    missing = []
    for req in required:
        if req.lower() in content:
            found.append(req)
        else:
            missing.append(req)
    return len(missing) == 0, found, missing


def validate(filepath: str, required_strings: list[str]) -> tuple[bool, str]:
    """Validate that the named file contains every required string."""
    if not required_strings:
        return True, f"File found: {filepath} (no content checks specified)"

    all_found, _found, missing = check_contains(filepath, required_strings)
    if all_found:
        return (
            True,
            f"File '{filepath}' contains all {len(required_strings)} required sections",
        )

    missing_list = "\n".join(f"  - {m}" for m in missing)
    return False, MISSING_CONTENT_ERROR.format(
        file=filepath, count=len(missing), missing_list=missing_list
    )


def main():
    parser = argparse.ArgumentParser(description="Validate file contains required content")
    parser.add_argument("target_file", nargs="?", help="Path to check (defaults to hook payload)")
    parser.add_argument("-d", "--directory", default=DEFAULT_DIRECTORY)
    parser.add_argument("-e", "--extension", default=DEFAULT_EXTENSION)
    parser.add_argument(
        "--contains",
        action="append",
        dest="required_strings",
        default=[],
        metavar="STRING",
        help="Required string (can be used multiple times)",
    )
    args = parser.parse_args()

    # An explicit path (CLI / tests) wins and bypasses the scope filter: it was
    # named on purpose. Otherwise take the path the hook's own tool input names.
    # Never guess: selecting by mtime or git status meant one lane's write got
    # judged against another lane's file (#2682, #2689).
    if args.target_file:
        target = args.target_file
        if not Path(target).exists():
            # An explicit CLI argument that names nothing is a user error worth
            # reporting. A hook-derived path we cannot resolve is not: there is
            # no content to judge, so there is no finding to make.
            print(f"ERROR: File does not exist: {target}", file=sys.stderr)
            sys.exit(2)
    else:
        target = target_from_hook_input(read_hook_input())
        if not target:
            sys.exit(0)

        # The payload's path is judged as given. Rewriting it relative to a cwd
        # would put process state back into target selection: with no `cwd` key
        # the absolute path would fall out of scope and silently pass, and once
        # reduced to a relative path the read would resolve against whichever
        # checkout the hook process started in.
        #
        # Scope filter runs before any filesystem access, so a write this hook
        # has no business judging is never even statted.
        if not in_scope(target, args.directory, args.extension):
            sys.exit(0)
        if not Path(target).exists():
            sys.exit(0)

    success, message = validate(target, args.required_strings)

    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
