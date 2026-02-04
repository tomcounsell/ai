#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that a Valor tool has the required structure.

Checks:
1. Tool directory exists in tools/
2. Required files present: __init__.py, README.md
3. Optional but recommended: manifest.json, tests/

Exit codes:
- 0: Validation passed
- 2: Validation failed, blocks agent

Usage:
  uv run validate_tool_structure.py --name my_tool
  uv run validate_tool_structure.py -n image_gen --require-manifest
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

TOOLS_DIR = "tools"
DEFAULT_MAX_AGE_MINUTES = 15

REQUIRED_FILES = ["__init__.py", "README.md"]
RECOMMENDED_FILES = ["manifest.json"]

NO_TOOL_ERROR = (
    "VALIDATION FAILED: Tool directory not found.\n\n"
    "Expected: {tools_dir}/{name}/\n\n"
    "ACTION REQUIRED: Create the tool directory with required files:\n"
    "  mkdir -p {tools_dir}/{name}/tests\n"
    "  touch {tools_dir}/{name}/__init__.py\n"
    "  touch {tools_dir}/{name}/README.md"
)

MISSING_FILES_ERROR = (
    "VALIDATION FAILED: Tool '{name}' is missing required files.\n\n"
    "MISSING:\n{missing_list}\n\n"
    "ACTION REQUIRED: Create the missing files in {tools_dir}/{name}/"
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
                # Check if any file in dir was modified recently
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
            # Extract first-level dir under tools/
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

    # Auto-detect from git status or recent modifications
    git_new = get_git_new_tool_dirs(tools_dir)
    if git_new:
        return git_new[0]

    recent = get_recent_tool_dirs(tools_dir, max_age)
    if recent:
        return recent[0]

    return None


def validate_tool(
    tools_dir: str, name: str, require_manifest: bool = False
) -> tuple[bool, str]:
    """Validate tool has required structure."""
    tool_path = Path(tools_dir) / name

    if not tool_path.exists() or not tool_path.is_dir():
        return False, NO_TOOL_ERROR.format(tools_dir=tools_dir, name=name)

    required = REQUIRED_FILES.copy()
    if require_manifest:
        required.append("manifest.json")

    missing = []
    for filename in required:
        if not (tool_path / filename).exists():
            missing.append(f"  - {filename}")

    if missing:
        return False, MISSING_FILES_ERROR.format(
            name=name, tools_dir=tools_dir, missing_list="\n".join(missing)
        )

    # Check for recommended files
    warnings = []
    for filename in RECOMMENDED_FILES:
        if filename not in required and not (tool_path / filename).exists():
            warnings.append(f"  - {filename} (recommended)")

    has_tests = (tool_path / "tests").exists()
    if not has_tests:
        warnings.append("  - tests/ directory (recommended)")

    msg = f"Tool '{name}' has valid structure"
    if warnings:
        msg += "\n\nConsider adding:\n" + "\n".join(warnings)

    return True, msg


def main():
    parser = argparse.ArgumentParser(description="Validate Valor tool structure")
    parser.add_argument("-n", "--name", help="Tool name (auto-detects if not provided)")
    parser.add_argument("-d", "--tools-dir", default=TOOLS_DIR, help="Tools directory")
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE_MINUTES,
        help="Max age for auto-detection (minutes)",
    )
    parser.add_argument(
        "--require-manifest",
        action="store_true",
        help="Require manifest.json",
    )
    args = parser.parse_args()

    # Consume stdin if provided
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    tool_name = find_tool_name(args.tools_dir, args.name, args.max_age)
    if not tool_name:
        print(
            "VALIDATION FAILED: No tool found to validate.\n\n"
            f"No new or recently modified tool directories in {args.tools_dir}/.\n"
            "Use --name to specify a tool explicitly.",
            file=sys.stderr,
        )
        sys.exit(2)

    success, message = validate_tool(args.tools_dir, tool_name, args.require_manifest)

    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
