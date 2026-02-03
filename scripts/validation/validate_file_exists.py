#!/usr/bin/env python3
"""
Validate that a file was created in the expected location.

Usage:
    python validate_file_exists.py <file_path> [--type plan|code|test|doc]

Exit codes:
    0 - File exists (and passes type-specific checks if specified)
    2 - Validation failed (blocks agent, returns feedback)

For use as a Claude Code Stop hook.
"""

import json
import sys
from pathlib import Path

TYPE_PATTERNS = {
    "plan": {
        "extensions": [".md"],
        "directories": ["docs/plans"],
        "required_content": ["##", "problem", "solution"],
    },
    "code": {
        "extensions": [".py", ".js", ".ts", ".jsx", ".tsx"],
        "directories": [],  # Any directory
        "required_content": [],
    },
    "test": {
        "extensions": [".py"],
        "directories": ["tests", "test"],
        "required_content": ["def test_", "pytest", "assert"],
    },
    "doc": {
        "extensions": [".md", ".rst", ".txt"],
        "directories": ["docs", ".claude"],
        "required_content": [],
    },
}


def validate_file(
    file_path: str, file_type: str | None = None
) -> tuple[bool, list[str]]:
    """
    Validate a file exists and optionally matches type expectations.

    Returns:
        (is_valid, issues) - True if valid, list of issues if not
    """
    issues = []
    path = Path(file_path)

    # Check file exists
    if not path.exists():
        return False, [f"File not found: {file_path}"]

    if not path.is_file():
        return False, [f"Path is not a file: {file_path}"]

    # Type-specific validation
    if file_type and file_type in TYPE_PATTERNS:
        pattern = TYPE_PATTERNS[file_type]

        # Check extension
        if pattern["extensions"]:
            if path.suffix not in pattern["extensions"]:
                issues.append(
                    f"Expected {file_type} file extension "
                    f"({', '.join(pattern['extensions'])}), got {path.suffix}"
                )

        # Check directory
        if pattern["directories"]:
            in_valid_dir = any(d in str(path) for d in pattern["directories"])
            if not in_valid_dir:
                issues.append(
                    f"Expected {file_type} in directories: "
                    f"{', '.join(pattern['directories'])}"
                )

        # Check required content
        if pattern["required_content"]:
            content = path.read_text().lower()
            missing = [
                rc for rc in pattern["required_content"] if rc.lower() not in content
            ]
            if missing:
                issues.append(f"Missing expected content: {', '.join(missing)}")

    is_valid = len(issues) == 0
    return is_valid, issues


def main():
    if len(sys.argv) < 2:
        usage = "Usage: validate_file_exists.py <file_path> [--type plan|code|test|doc]"
        print(usage, file=sys.stderr)
        sys.exit(1)

    file_path = sys.argv[1]
    file_type = None

    if "--type" in sys.argv:
        type_idx = sys.argv.index("--type")
        if type_idx + 1 < len(sys.argv):
            file_type = sys.argv[type_idx + 1]

    is_valid, issues = validate_file(file_path, file_type)

    if is_valid:
        print(
            json.dumps(
                {
                    "continue": True,
                    "validation": "passed",
                    "file_path": file_path,
                    "file_type": file_type,
                }
            )
        )
        sys.exit(0)
    else:
        issue_lines = "\n".join(f"  - {i}" for i in issues)
        feedback = f"File validation failed for {file_path}:\n{issue_lines}"
        print(feedback, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
