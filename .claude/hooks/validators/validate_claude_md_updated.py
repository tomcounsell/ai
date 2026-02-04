#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that CLAUDE.md was updated to document a new tool.

Checks:
1. CLAUDE.md contains a reference to the tool name
2. Tool documentation includes CLI command pattern (valor-*)

Exit codes:
- 0: Validation passed
- 2: Validation failed, blocks agent

Usage:
  uv run validate_claude_md_updated.py --tool-name my_tool
  uv run validate_claude_md_updated.py -n image_gen --cli-name valor-image-gen
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_MD_PATH = "CLAUDE.md"
TOOLS_DIR = "tools"
DEFAULT_MAX_AGE_MINUTES = 15

NOT_UPDATED_ERROR = (
    "VALIDATION FAILED: CLAUDE.md does not document the new tool.\n\n"
    "Tool: {tool_name}\n"
    "Expected CLI: {cli_name}\n\n"
    "ACTION REQUIRED: Add documentation to CLAUDE.md in the appropriate section:\n\n"
    "**Local Python Tools** or **Image Tools** section:\n"
    "- **Tool Name** (`{cli_name}`): Brief description\n"
    "  ```bash\n"
    "  {cli_name} arg1 arg2    # Example usage\n"
    "  ```"
)


def get_recent_tool_dirs(tools_dir: str, max_age_minutes: int) -> list[str]:
    """Get tool directories created/modified recently."""
    target = Path(tools_dir)
    if not target.exists():
        return []

    recent = []
    now = time.time()
    max_age_seconds = max_age_minutes * 60

    for item in target.iterdir():
        if item.is_dir() and not item.name.startswith(("_", ".")):
            try:
                for f in item.rglob("*"):
                    if f.is_file() and now - f.stat().st_mtime <= max_age_seconds:
                        recent.append(item.name)
                        break
            except OSError:
                continue
    return recent


def get_git_new_tool_dirs(tools_dir: str) -> list[str]:
    """Get new/modified tool directories from git status."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", f"{tools_dir}/"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        dirs = set()
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            filepath = line[3:].strip()
            parts = filepath.split("/")
            if len(parts) >= 2 and parts[0] == tools_dir:
                dirs.add(parts[1])
        return list(dirs)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def find_tool_name(tools_dir: str, name: str | None, max_age: int) -> str | None:
    """Find the tool name to validate."""
    if name:
        return name

    git_new = get_git_new_tool_dirs(tools_dir)
    if git_new:
        return git_new[0]

    recent = get_recent_tool_dirs(tools_dir, max_age)
    if recent:
        return recent[0]

    return None


def tool_to_cli_name(tool_name: str) -> str:
    """Convert tool_name to valor-tool-name CLI format."""
    # Replace underscores with hyphens and add valor- prefix
    return f"valor-{tool_name.replace('_', '-')}"


def validate_claude_md(tool_name: str, cli_name: str | None = None) -> tuple[bool, str]:
    """Validate CLAUDE.md documents the tool."""
    claude_md = Path(CLAUDE_MD_PATH)
    if not claude_md.exists():
        return False, f"CLAUDE.md not found at {CLAUDE_MD_PATH}"

    content = claude_md.read_text(encoding="utf-8").lower()
    expected_cli = cli_name or tool_to_cli_name(tool_name)
    expected_cli_lower = expected_cli.lower()

    # Check for tool reference (either module name or CLI name)
    tool_lower = tool_name.lower()
    module_ref = f"tools.{tool_lower}"

    found_cli = expected_cli_lower in content
    found_module = module_ref in content
    found_tool_name = tool_lower in content

    if found_cli or found_module:
        return True, f"CLAUDE.md documents tool '{tool_name}' (CLI: {expected_cli})"

    if found_tool_name:
        # Tool name mentioned but not properly documented
        return (
            True,
            f"CLAUDE.md mentions '{tool_name}' but consider adding "
            f"CLI documentation for {expected_cli}",
        )

    return False, NOT_UPDATED_ERROR.format(
        tool_name=tool_name,
        cli_name=expected_cli,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Validate CLAUDE.md documents the tool"
    )
    parser.add_argument(
        "-n", "--tool-name", help="Tool name (auto-detects if not provided)"
    )
    parser.add_argument(
        "--cli-name", help="Expected CLI command name (defaults to valor-{tool})"
    )
    parser.add_argument("-d", "--tools-dir", default=TOOLS_DIR, help="Tools directory")
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE_MINUTES,
        help="Max age for auto-detection (minutes)",
    )
    args = parser.parse_args()

    # Consume stdin if provided
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    tool_name = find_tool_name(args.tools_dir, args.tool_name, args.max_age)
    if not tool_name:
        # No tool to validate - this is not a failure, just nothing to check
        print(json.dumps({"result": "continue", "message": "No tool changes detected, skipping validation"}))
        sys.exit(0)

    success, message = validate_claude_md(tool_name, args.cli_name)

    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
