#!/usr/bin/env python3
"""Validate that plan has a classification label in frontmatter."""
import re
import sys
from pathlib import Path


def validate_plan_label(plan_path: str) -> bool:
    """Validate that a plan has a classification label in frontmatter.

    Args:
        plan_path: Path to the plan markdown file

    Returns:
        True if valid (has frontmatter with type: bug|feature|chore), False otherwise
    """
    content = Path(plan_path).read_text()

    # Check frontmatter for type field
    frontmatter_match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        return False

    frontmatter = frontmatter_match.group(1)

    # Must have type: bug|feature|chore
    return bool(
        re.search(r"^type:\s*(bug|feature|chore)\s*$", frontmatter, re.MULTILINE)
    )


if __name__ == "__main__":
    # Validate all .md files in docs/plans/
    plans_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/plans")

    # Check if directory exists
    if not plans_dir.exists():
        print(f"ERROR: Directory {plans_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    # Find all plan files
    plan_files = list(plans_dir.glob("*.md"))

    if not plan_files:
        # No plans to validate is success
        sys.exit(0)

    # Validate each plan
    failed_plans = []
    for plan in plan_files:
        if not validate_plan_label(str(plan)):
            failed_plans.append(plan)
            print(
                f"ERROR: {plan} missing required 'type: bug|feature|chore' in frontmatter",
                file=sys.stderr,
            )

    if failed_plans:
        sys.exit(1)

    sys.exit(0)
