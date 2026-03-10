#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that a plan file has a properly structured ## Verification section.

The Verification section must contain a markdown table with at least one
data row. Each row defines a named check with an executable command and
expected result.

Checks:
1. Section exists
2. Section has a table with header, separator, and at least one data row
3. Each data row has three columns: Check, Command, Expected

Exit codes:
- 0: Validation passed
- 2: Validation failed, blocks agent

Usage:
  uv run validate_verification_section.py docs/plans/feature-name.md
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MISSING_SECTION_ERROR = """
VALIDATION FAILED: Plan '{file}' is missing a ## Verification section.

ACTION REQUIRED: Add a ## Verification section with a machine-readable table:

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

Each row is a named check with an executable command and expected result.
Supported expectations: "exit code N", "output > N", "output contains X".
"""

INCOMPLETE_SECTION_ERROR = """
VALIDATION FAILED: Plan '{file}' has an incomplete ## Verification section.

The Verification section must contain a markdown table with at least one
data row (header + separator + 1 or more check rows).

CURRENT CONTENT:
{content}

ACTION REQUIRED: Add a table with at least one verification check:

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
"""


def find_newest_plan_file(directory: str = "docs/plans") -> str | None:
    """Find the most recently created plan file in git."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", f"{directory}/"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        new_files = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            status = line[:2]
            filepath = line[3:].strip()
            if status in ("??", "A ", " A", "AM") and filepath.endswith(".md"):
                new_files.append(filepath)

        if not new_files:
            return None

        newest = None
        newest_mtime = 0
        for filepath in new_files:
            path = Path(filepath)
            if path.exists():
                mtime = path.stat().st_mtime
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest = str(path)
        return newest
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None


def extract_verification_section(content: str) -> str | None:
    """Extract the ## Verification section from plan content."""
    match = re.search(
        r"^## Verification\s*$(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return None


def is_section_complete(section_content: str) -> tuple[bool, str]:
    """Check if the verification section has a valid table with data rows.

    Returns: (is_complete, reason)
    """
    if not section_content:
        return False, "Section is empty"

    # Check for placeholder text
    placeholder_patterns = [
        r"^\[.*\]$",
        r"^TBD\s*$",
        r"^TODO\s*$",
        r"^\.\.\.\s*$",
    ]
    for pattern in placeholder_patterns:
        if re.match(pattern, section_content.strip(), re.IGNORECASE):
            return False, "Section contains only placeholder text"

    # Find table rows
    rows = [line.strip() for line in section_content.splitlines() if line.strip().startswith("|")]

    if len(rows) < 3:
        # Need header + separator + at least one data row
        return False, "Table must have a header, separator, and at least one data row"

    # Verify the separator row (second row) contains dashes
    separator = rows[1]
    if not re.match(r"^\|[\s\-:|]+\|$", separator):
        return False, "Missing table separator row"

    # Count data rows (everything after header + separator)
    data_rows = rows[2:]
    valid_rows = 0
    for row in data_rows:
        cells = [c.strip() for c in row.split("|")]
        cells = [c for c in cells if c]
        if len(cells) >= 3:
            valid_rows += 1

    if valid_rows == 0:
        return False, "Table has no valid data rows (need Check, Command, Expected columns)"

    return True, f"Contains {valid_rows} verification check(s)"


def validate_verification_section(filepath: str) -> tuple[bool, str]:
    """Validate the plan file has a proper ## Verification section.

    Returns: (success, message)
    """
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, f"Failed to read file: {e}"

    section = extract_verification_section(content)
    if section is None:
        return False, MISSING_SECTION_ERROR.format(file=filepath)

    is_complete, reason = is_section_complete(section)
    if not is_complete:
        return False, INCOMPLETE_SECTION_ERROR.format(
            file=filepath,
            content=section[:500],
        )

    return True, f"Verification section is complete: {reason}"


def main():
    parser = argparse.ArgumentParser(description="Validate plan verification section")
    parser.add_argument(
        "plan_file",
        nargs="?",
        help="Path to plan file (auto-detects if not provided)",
    )
    args = parser.parse_args()

    # Consume stdin if provided (SDK passes context via stdin)
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    plan_file = args.plan_file
    if not plan_file:
        plan_file = find_newest_plan_file()
        if not plan_file:
            # No new plan file detected -- nothing to validate, pass through
            sys.exit(0)

    if not Path(plan_file).exists():
        print(f"ERROR: Plan file does not exist: {plan_file}", file=sys.stderr)
        sys.exit(2)

    success, message = validate_verification_section(plan_file)

    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
