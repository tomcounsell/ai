#!/usr/bin/env python3
"""Validate that plan type: field cannot change after status moves past Planning.

Once a plan's status has moved to Ready, In Progress, or Complete, the type:
field in frontmatter becomes immutable. This prevents accidental reclassification
of approved plans. Use /reclassify during Planning status instead.
"""

import re
import subprocess
import sys
from pathlib import Path

LOCKED_STATUSES = {"Ready", "In Progress", "Complete"}


def extract_frontmatter_field(content: str, field: str) -> str | None:
    """Extract a field value from YAML frontmatter.

    Args:
        content: Full file content with YAML frontmatter
        field: The field name to extract (e.g., 'type', 'status')

    Returns:
        The field value as a stripped string, or None if not found
    """
    match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None
    fm = match.group(1)
    field_match = re.search(rf"^{field}:\s*(.+)$", fm, re.MULTILINE)
    return field_match.group(1).strip() if field_match else None


def get_head_content(plan_path: str) -> str | None:
    """Get the file content from git HEAD.

    Args:
        plan_path: Relative path to the file from the git root

    Returns:
        File content from HEAD, or None if the file doesn't exist in HEAD
    """
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{plan_path}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout if result.returncode == 0 else None
    except Exception:
        return None


def validate_type_immutability(plan_path: str) -> tuple[bool, str]:
    """Check that the type: field hasn't changed if the plan is past Planning.

    Args:
        plan_path: Path to the plan markdown file

    Returns:
        Tuple of (is_valid, message) where is_valid is True if the check passes
    """
    current_content = Path(plan_path).read_text()
    current_type = extract_frontmatter_field(current_content, "type")

    # Get HEAD version
    head_content = get_head_content(plan_path)
    if not head_content:
        return True, "New file, no immutability check needed"

    head_status = extract_frontmatter_field(head_content, "status")
    head_type = extract_frontmatter_field(head_content, "type")

    if not head_status or not head_type:
        return True, "No previous status/type to check"

    if head_status in LOCKED_STATUSES and current_type != head_type:
        return False, (
            f"Cannot change type from '{head_type}' to '{current_type}' "
            f"- plan status is '{head_status}'. "
            f"Use /reclassify during Planning status instead."
        )

    return True, "Type immutability check passed"


if __name__ == "__main__":
    plans_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/plans")

    # If a single file path
    if plans_dir.is_file():
        valid, msg = validate_type_immutability(str(plans_dir))
        if not valid:
            print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    # If a directory, check all plans
    if not plans_dir.exists():
        sys.exit(0)

    plan_files = list(plans_dir.glob("*.md"))
    if not plan_files:
        sys.exit(0)

    failed = []
    for plan in plan_files:
        valid, msg = validate_type_immutability(str(plan))
        if not valid:
            failed.append((plan, msg))
            print(f"ERROR: {plan}: {msg}", file=sys.stderr)

    sys.exit(2 if failed else 0)
