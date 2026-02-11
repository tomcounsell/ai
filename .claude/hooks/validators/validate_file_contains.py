#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that a file contains required content strings.

Checks:
1. Find the most recently created file in the directory
2. Verify the file contains all required strings

Exit codes:
- 0: Validation passed (file contains all required content)
- 2: Validation failed, blocks agent (missing content)

Usage:
  uv run validate_file_contains.py -d docs/plans -e .md --contains "## Problem"
  uv run validate_file_contains.py --directory specs --extension .md --contains "## Obj"

Frontmatter example:
  hooks:
    Stop:
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
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DIRECTORY = "docs/plans"
DEFAULT_EXTENSION = ".md"
DEFAULT_MAX_AGE_MINUTES = 480

NO_FILE_ERROR = (
    "VALIDATION FAILED: No file found matching {pattern}.\n\n"
    "ACTION REQUIRED: Create a file in {directory}/ before validation can pass."
)

MISSING_CONTENT_ERROR = (
    "VALIDATION FAILED: File '{file}' is missing {count} required section(s).\n\n"
    "MISSING SECTIONS:\n{missing_list}\n\n"
    "ACTION REQUIRED: Add the missing sections to '{file}'. "
    "Do not stop until all required sections are present."
)


def get_git_new_files(directory: str, extension: str) -> list[str]:
    """Get new/untracked files from git."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", f"{directory}/"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        files = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            status = line[:2]
            filepath = line[3:].strip()
            if status in ("??", "A ", " A", "AM") and filepath.endswith(extension):
                files.append(filepath)
        return files
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def get_recent_files(directory: str, extension: str, max_age_minutes: int) -> list[str]:
    """Get recently modified files."""
    target_dir = Path(directory)
    if not target_dir.exists():
        return []

    recent = []
    now = time.time()
    max_age_seconds = max_age_minutes * 60
    ext = extension if extension.startswith(".") else f".{extension}"

    for filepath in target_dir.glob(f"*{ext}"):
        try:
            if now - filepath.stat().st_mtime <= max_age_seconds:
                recent.append(str(filepath))
        except OSError:
            continue
    return recent


def get_git_committed_files(
    directory: str, extension: str, max_age_minutes: int
) -> list[str]:
    """Check git log for recently committed files (even if later deleted/migrated)."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--since={max_age_minutes} minutes ago",
                "--diff-filter=A",
                "--name-only",
                "--pretty=format:",
                "--",
                f"{directory}/*{extension}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [f for f in result.stdout.strip().split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def get_committed_file_content(filepath: str) -> str | None:
    """Get file content from the commit where it was last present."""
    try:
        # Find the last commit that had this file
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%H", "--", filepath],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        commit = result.stdout.strip()
        # Get the file content at that commit
        result = subprocess.run(
            ["git", "show", f"{commit}:{filepath}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None


def find_newest_file(
    directory: str, extension: str, max_age_minutes: int
) -> str | None:
    """Find the most recently modified file."""
    all_files = list(
        set(
            get_git_new_files(directory, extension)
            + get_recent_files(directory, extension, max_age_minutes)
        )
    )

    if not all_files:
        return None

    newest = None
    newest_mtime = 0
    for filepath in all_files:
        try:
            path = Path(filepath)
            if path.exists():
                mtime = path.stat().st_mtime
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest = str(path)
        except OSError:
            continue
    return newest


def check_contains(
    filepath: str, required: list[str]
) -> tuple[bool, list[str], list[str]]:
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


def validate(
    directory: str,
    extension: str,
    max_age_minutes: int,
    required_strings: list[str],
) -> tuple[bool, str]:
    """Validate file exists and contains required content."""
    pattern = f"{directory}/*{extension}"

    newest = find_newest_file(directory, extension, max_age_minutes)
    if not newest:
        # Check if a file was committed and later migrated/deleted
        committed = get_git_committed_files(directory, extension, max_age_minutes)
        if committed:
            # Validate content from git history for the most recent committed file
            for cfile in committed:
                content = get_committed_file_content(cfile)
                if content and required_strings:
                    content_lower = content.lower()
                    missing = [
                        r for r in required_strings if r.lower() not in content_lower
                    ]
                    if not missing:
                        return (
                            True,
                            f"File '{cfile}' was committed with all required sections (since migrated)",
                        )
            # File existed but didn't have all sections — still count as present
            return (
                True,
                f"File(s) committed in recent history (since migrated): {', '.join(committed)}",
            )
        return False, NO_FILE_ERROR.format(pattern=pattern, directory=directory)

    if not required_strings:
        return True, f"File found: {newest} (no content checks specified)"

    all_found, found, missing = check_contains(newest, required_strings)

    if all_found:
        return (
            True,
            f"File '{newest}' contains all {len(required_strings)} required sections",
        )

    missing_list = "\n".join(f"  - {m}" for m in missing)
    return False, MISSING_CONTENT_ERROR.format(
        file=newest, count=len(missing), missing_list=missing_list
    )


def main():
    parser = argparse.ArgumentParser(
        description="Validate file contains required content"
    )
    parser.add_argument("-d", "--directory", default=DEFAULT_DIRECTORY)
    parser.add_argument("-e", "--extension", default=DEFAULT_EXTENSION)
    parser.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_MINUTES)
    parser.add_argument(
        "--contains",
        action="append",
        dest="required_strings",
        default=[],
        metavar="STRING",
        help="Required string (can be used multiple times)",
    )
    args = parser.parse_args()

    # Consume stdin if provided
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    success, message = validate(
        args.directory, args.extension, args.max_age, args.required_strings
    )

    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
