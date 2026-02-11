#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that a new file was created in a specified directory.

Checks:
1. Git status for untracked/new files matching the pattern
2. File modification time within the specified age

Exit codes:
- 0: Validation passed (new file found)
- 2: Validation failed, blocks agent (no new file found)

Usage:
  uv run validate_new_file.py --directory docs/plans --extension .md
  uv run validate_new_file.py -d specs -e .json --max-age 10
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DIRECTORY = "docs/plans"
DEFAULT_EXTENSION = ".md"
DEFAULT_MAX_AGE_MINUTES = 480

NO_FILE_ERROR = (
    "VALIDATION FAILED: No new file found matching {pattern}.\n\n"
    "ACTION REQUIRED: Create a file in the {directory}/ directory "
    "with extension {extension}. Do not stop until the file exists."
)


def get_git_new_files(directory: str, extension: str) -> list[str]:
    """Get list of new/untracked files in directory from git."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", f"{directory}/"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        new_files = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            status = line[:2]
            filepath = line[3:].strip()
            # ?? = untracked, A = added, M = modified
            if status in ("??", "A ", " A", "AM") and filepath.endswith(extension):
                new_files.append(filepath)
        return new_files
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def get_recent_files(directory: str, extension: str, max_age_minutes: int) -> list[str]:
    """Get files in directory modified within the last N minutes."""
    target_dir = Path(directory)
    if not target_dir.exists():
        return []

    recent = []
    now = time.time()
    max_age_seconds = max_age_minutes * 60
    ext = extension if extension.startswith(".") else f".{extension}"

    for filepath in target_dir.glob(f"*{ext}"):
        try:
            mtime = filepath.stat().st_mtime
            if now - mtime <= max_age_seconds:
                recent.append(str(filepath))
        except OSError:
            continue
    return recent


def get_git_committed_files(directory: str, extension: str, max_age_minutes: int) -> list[str]:
    """Check git log for recently committed files in directory (even if later deleted)."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={max_age_minutes} minutes ago", "--diff-filter=A",
             "--name-only", "--pretty=format:", "--", f"{directory}/*{extension}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [f for f in result.stdout.strip().split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def validate(directory: str, extension: str, max_age_minutes: int) -> tuple[bool, str]:
    """Validate that a new file was created."""
    pattern = f"{directory}/*{extension}"

    git_new = get_git_new_files(directory, extension)
    if git_new:
        return True, f"New file(s) found: {', '.join(git_new)}"

    recent = get_recent_files(directory, extension, max_age_minutes)
    if recent:
        return True, f"Recently created file(s): {', '.join(recent)}"

    # Check if a file was committed recently (covers create-then-migrate workflow)
    committed = get_git_committed_files(directory, extension, max_age_minutes)
    if committed:
        return True, f"File(s) committed in recent history: {', '.join(committed)}"

    return False, NO_FILE_ERROR.format(
        pattern=pattern, directory=directory, extension=extension
    )


def main():
    parser = argparse.ArgumentParser(description="Validate new file creation")
    parser.add_argument(
        "-d", "--directory", default=DEFAULT_DIRECTORY, help="Directory to check"
    )
    parser.add_argument(
        "-e", "--extension", default=DEFAULT_EXTENSION, help="File extension"
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE_MINUTES,
        help="Max age in minutes",
    )
    args = parser.parse_args()

    # Consume stdin if provided (hook input)
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    success, message = validate(args.directory, args.extension, args.max_age)

    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
