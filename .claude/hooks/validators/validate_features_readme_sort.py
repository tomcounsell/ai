#!/usr/bin/env python3
"""
Validate that docs/features/README.md has alphabetically sorted feature entries.

Two modes:
  --check (default): exit 0 if sorted, exit 2 if not (with helpful error)
  --fix: re-sort the table in place and exit 0

Usage:
  python validate_features_readme_sort.py --check docs/features/README.md
  python validate_features_readme_sort.py --fix docs/features/README.md

Hook integration:
  Registered as a PostToolUse hook for Write and Edit matchers in .claude/settings.json.
  Only fires when the file path contains docs/features/README.md.
"""

import argparse
import json
import re
import sys
from pathlib import Path


def parse_table_rows(content: str) -> tuple[list[str], int, int]:
    """
    Extract table rows between ## Features and ## Adding New Entries headers.

    Returns:
        (rows, start_line_index, end_line_index)
        rows: list of raw markdown table row strings (excluding header and separator)
        start_line_index: index of first data row in the lines list
        end_line_index: index after last data row in the lines list
    """
    lines = content.split("\n")

    # Find ## Features header
    features_start = None
    for i, line in enumerate(lines):
        if re.match(r"^## Features\s*$", line):
            features_start = i
            break

    if features_start is None:
        return [], -1, -1

    # Find ## Adding New Entries header (or end of file)
    features_end = len(lines)
    for i in range(features_start + 1, len(lines)):
        if re.match(r"^## ", lines[i]):
            features_end = i
            break

    # Find table rows (skip header row and separator row)
    table_rows = []
    data_start = -1
    data_end = -1
    header_seen = False
    separator_seen = False

    for i in range(features_start + 1, features_end):
        line = lines[i].strip()
        if not line:
            if header_seen and separator_seen and table_rows:
                # Empty line after table data means end of table
                break
            continue

        if line.startswith("|"):
            if not header_seen:
                header_seen = True
                continue
            if not separator_seen:
                # Separator row contains dashes
                if re.match(r"^\|[-|\s]+\|$", line):
                    separator_seen = True
                    continue

            # This is a data row
            if data_start == -1:
                data_start = i
            data_end = i + 1
            table_rows.append(lines[i])

    return table_rows, data_start, data_end


def extract_feature_name(row: str) -> str | None:
    """
    Extract feature name from a table row with [Name](file.md) link syntax.

    Returns the link text (feature name) or None if no link found.
    """
    match = re.search(r"\[([^\]]+)\]\([^)]+\)", row)
    if match:
        return match.group(1)
    return None


def check_sort_order(rows: list[str]) -> tuple[bool, list[tuple[int, str, str]]]:
    """
    Check if table rows are sorted alphabetically by feature name (case-insensitive).

    Returns:
        (is_sorted, violations)
        violations: list of (row_index, current_name, should_be_after_name)
    """
    if len(rows) <= 1:
        return True, []

    names = []
    for i, row in enumerate(rows):
        name = extract_feature_name(row)
        if name is not None:
            names.append((i, name))

    violations = []
    for j in range(1, len(names)):
        idx, current = names[j]
        prev_idx, prev = names[j - 1]
        if current.lower() < prev.lower():
            violations.append((idx, current, prev))

    return len(violations) == 0, violations


def sort_rows(rows: list[str]) -> list[str]:
    """Sort table rows alphabetically by feature name (case-insensitive)."""
    def sort_key(row: str) -> str:
        name = extract_feature_name(row)
        if name is None:
            return ""
        return name.lower()

    return sorted(rows, key=sort_key)


def check_mode(filepath: str) -> int:
    """
    Validate sort order of the features README table.

    Returns exit code: 0 if sorted, 2 if not.
    """
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"ERROR: Failed to read file: {e}", file=sys.stderr)
        return 2

    rows, _, _ = parse_table_rows(content)

    if not rows:
        # No table found or empty table -- warn if ## Features exists
        if "## Features" in content:
            print(
                "WARNING: ## Features header found but no table rows detected.",
                file=sys.stderr,
            )
        # Nothing to validate
        return 0

    is_sorted, violations = check_sort_order(rows)

    if is_sorted:
        return 0

    # Build error message
    error_lines = [
        "VALIDATION FAILED: docs/features/README.md entries are not alphabetically sorted.",
        "",
        "Out-of-order entries:",
    ]
    for _, current, prev in violations:
        error_lines.append(f'  - "{current}" should come before "{prev}"')

    error_lines.extend([
        "",
        "Fix by running:",
        f"  python .claude/hooks/validators/validate_features_readme_sort.py --fix {filepath}",
    ])

    print("\n".join(error_lines), file=sys.stderr)
    return 2


def fix_mode(filepath: str) -> int:
    """Re-sort the features table in place."""
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"ERROR: Failed to read file: {e}", file=sys.stderr)
        return 1

    rows, data_start, data_end = parse_table_rows(content)

    if not rows:
        print("No table rows found to sort.")
        return 0

    is_sorted, _ = check_sort_order(rows)
    if is_sorted:
        print("Table is already sorted.")
        return 0

    sorted_rows = sort_rows(rows)

    lines = content.split("\n")
    new_lines = lines[:data_start] + sorted_rows + lines[data_end:]
    new_content = "\n".join(new_lines)

    try:
        Path(filepath).write_text(new_content, encoding="utf-8")
    except OSError as e:
        print(f"ERROR: Failed to write file: {e}", file=sys.stderr)
        return 1

    print(f"Sorted {len(rows)} entries in {filepath}.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Validate or fix alphabetical sort order of docs/features/README.md"
    )
    parser.add_argument(
        "filepath",
        nargs="?",
        default="docs/features/README.md",
        help="Path to the features README file (default: docs/features/README.md)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Check sort order (default mode)",
    )
    group.add_argument(
        "--fix",
        action="store_true",
        help="Re-sort the table in place",
    )
    args = parser.parse_args()

    # Consume stdin if provided (Claude Code hooks pass context via stdin)
    try:
        stdin_data = json.load(sys.stdin)
        # If invoked as a hook, check if the tool output path matches
        tool_input = stdin_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")
        if file_path and "docs/features/README.md" not in file_path:
            # Not our file, pass through
            sys.exit(0)
        # Use the file_path from hook context if available
        if file_path and "docs/features/README.md" in file_path:
            args.filepath = file_path
    except (json.JSONDecodeError, EOFError, ValueError):
        pass

    if args.fix:
        sys.exit(fix_mode(args.filepath))
    else:
        exit_code = check_mode(args.filepath)
        if exit_code == 0:
            # Output JSON for hook compatibility
            print(json.dumps({"result": "continue"}))
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
