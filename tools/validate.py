#!/usr/bin/env python3
"""
Tool Validation Script

Validates that tools follow the standard defined in STANDARD.md.

Usage:
    python tools/validate.py                    # Validate all tools
    python tools/validate.py tools/browser/    # Validate specific tool
"""

import json
import sys
from pathlib import Path

# Required fields in manifest.json
REQUIRED_FIELDS = ["name", "version", "description", "type", "status", "capabilities"]

# Valid values for enum fields
VALID_TYPES = ["cli", "api", "library"]
VALID_STATUSES = ["stable", "beta", "experimental"]

# Required sections in README.md (as lowercase for matching)
REQUIRED_README_SECTIONS = ["overview", "installation", "quick start"]


class ValidationError:
    def __init__(self, path: str, message: str):
        self.path = path
        self.message = message

    def __str__(self):
        return f"[{self.path}] {self.message}"


def validate_manifest(tool_path: Path) -> list[ValidationError]:
    """Validate manifest.json for a tool."""
    errors = []
    manifest_path = tool_path / "manifest.json"

    if not manifest_path.exists():
        errors.append(ValidationError(str(tool_path), "Missing manifest.json"))
        return errors

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(ValidationError(str(manifest_path), f"Invalid JSON: {e}"))
        return errors

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in manifest:
            errors.append(
                ValidationError(str(manifest_path), f"Missing required field: {field}")
            )

    # Validate type
    if "type" in manifest and manifest["type"] not in VALID_TYPES:
        errors.append(
            ValidationError(
                str(manifest_path),
                f"Invalid type '{manifest['type']}'. Must be one of: {VALID_TYPES}",
            )
        )

    # Validate status
    if "status" in manifest and manifest["status"] not in VALID_STATUSES:
        errors.append(
            ValidationError(
                str(manifest_path),
                f"Invalid status '{manifest['status']}'. Must be one of: {VALID_STATUSES}",
            )
        )

    # Validate capabilities is a list
    if "capabilities" in manifest and not isinstance(manifest["capabilities"], list):
        errors.append(
            ValidationError(str(manifest_path), "capabilities must be a list")
        )

    # Validate name matches directory
    if "name" in manifest:
        expected_name = tool_path.name
        if manifest["name"] != expected_name:
            errors.append(
                ValidationError(
                    str(manifest_path),
                    f"name '{manifest['name']}' doesn't match directory '{expected_name}'",
                )
            )

    return errors


def validate_readme(tool_path: Path) -> list[ValidationError]:
    """Validate README.md for a tool."""
    errors = []
    readme_path = tool_path / "README.md"

    if not readme_path.exists():
        errors.append(ValidationError(str(tool_path), "Missing README.md"))
        return errors

    with open(readme_path) as f:
        content = f.read().lower()

    # Check for required sections (flexible matching)
    for section in REQUIRED_README_SECTIONS:
        # Look for section as header (## Section or # Section)
        if f"# {section}" not in content and section not in content:
            errors.append(
                ValidationError(
                    str(readme_path), f"Missing recommended section: {section}"
                )
            )

    return errors


def validate_tests(tool_path: Path) -> list[ValidationError]:
    """Validate tests exist for a tool."""
    errors = []
    tests_path = tool_path / "tests"

    if not tests_path.exists():
        errors.append(ValidationError(str(tool_path), "Missing tests/ directory"))
        return errors

    # Check for test files
    test_files = list(tests_path.glob("test_*.py"))
    if not test_files:
        errors.append(
            ValidationError(str(tests_path), "No test files found (test_*.py)")
        )

    return errors


def validate_tool(tool_path: Path) -> list[ValidationError]:
    """Validate a single tool against the standard."""
    errors = []

    # Skip non-directories and special files
    if not tool_path.is_dir():
        return errors

    # Skip __pycache__ and other special directories
    if tool_path.name.startswith("_") or tool_path.name.startswith("."):
        return errors

    # Skip if it's just a file (like STANDARD.md, validate.py)
    if (
        not (tool_path / "manifest.json").exists()
        and not (tool_path / "README.md").exists()
    ):
        # Check if this looks like a tool directory at all
        if not any(tool_path.iterdir()):
            return errors

    errors.extend(validate_manifest(tool_path))
    errors.extend(validate_readme(tool_path))
    errors.extend(validate_tests(tool_path))

    return errors


def validate_all_tools(tools_dir: Path) -> list[ValidationError]:
    """Validate all tools in the tools directory."""
    errors = []

    for item in tools_dir.iterdir():
        if item.is_dir() and not item.name.startswith("_"):
            errors.extend(validate_tool(item))

    return errors


def main():
    tools_dir = Path(__file__).parent

    if len(sys.argv) > 1:
        # Validate specific tool
        tool_path = Path(sys.argv[1])
        if not tool_path.exists():
            print(f"Error: Path does not exist: {tool_path}")
            sys.exit(1)
        errors = validate_tool(tool_path)
    else:
        # Validate all tools
        errors = validate_all_tools(tools_dir)

    if errors:
        print(f"Found {len(errors)} validation error(s):\n")
        for error in errors:
            print(f"  ✗ {error}")
        sys.exit(1)
    else:
        print("✓ All tools pass validation")
        sys.exit(0)


if __name__ == "__main__":
    main()
