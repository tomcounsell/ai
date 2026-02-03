#!/usr/bin/env python3
"""
Validate that a plan document exists and contains required sections.

Usage:
    python validate_plan.py <plan_path>

Exit codes:
    0 - Plan is valid
    2 - Validation failed (blocks agent, returns feedback)

For use as a Claude Code Stop hook to ensure plans are complete.
"""

import json
import re
import sys
from pathlib import Path

REQUIRED_SECTIONS = [
    "problem statement",
    "appetite",
    "solution",
    "risks",
]

OPTIONAL_BUT_RECOMMENDED = [
    "agent assignments",
    "implementation steps",
    "boundaries",
]


def validate_plan(plan_path: str) -> tuple[bool, list[str]]:
    """
    Validate a plan document.

    Returns:
        (is_valid, issues) - True if valid, list of issues if not
    """
    issues = []
    path = Path(plan_path)

    # Check file exists
    if not path.exists():
        return False, [f"Plan file not found: {plan_path}"]

    # Read content
    content = path.read_text().lower()

    # Check for required sections
    missing_required = []
    for section in REQUIRED_SECTIONS:
        # Look for section as header (## Section) or bold (**Section**)
        patterns = [
            rf"##\s*{section}",
            rf"\*\*{section}\*\*",
            rf"^{section}:",
        ]
        found = any(
            re.search(p, content, re.MULTILINE | re.IGNORECASE) for p in patterns
        )
        if not found:
            missing_required.append(section)

    if missing_required:
        issues.append(f"Missing required sections: {', '.join(missing_required)}")

    # Check for frontmatter with tracking
    if "---" not in content[:100]:
        issues.append("Missing YAML frontmatter (should have --- delimiter)")

    if "tracking:" not in content[:500]:
        issues.append("Missing 'tracking:' field in frontmatter (link to GitHub issue)")

    # Check minimum content length (avoid empty plans)
    if len(content) < 200:
        issues.append("Plan appears too short - ensure all sections have content")

    # Warn about recommended sections
    missing_recommended = []
    for section in OPTIONAL_BUT_RECOMMENDED:
        patterns = [
            rf"##\s*{section}",
            rf"\*\*{section}\*\*",
        ]
        found = any(
            re.search(p, content, re.MULTILINE | re.IGNORECASE) for p in patterns
        )
        if not found:
            missing_recommended.append(section)

    if missing_recommended:
        issues.append(f"Consider adding: {', '.join(missing_recommended)}")

    is_valid = not any("Missing required" in i or "too short" in i for i in issues)
    return is_valid, issues


def main():
    if len(sys.argv) < 2:
        print("Usage: validate_plan.py <plan_path>", file=sys.stderr)
        sys.exit(1)

    plan_path = sys.argv[1]
    is_valid, issues = validate_plan(plan_path)

    if is_valid:
        print(
            json.dumps(
                {"continue": True, "validation": "passed", "plan_path": plan_path}
            )
        )
        sys.exit(0)
    else:
        # Exit code 2 blocks the agent and sends feedback
        feedback = "Plan validation failed:\n" + "\n".join(f"  - {i}" for i in issues)
        print(feedback, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
