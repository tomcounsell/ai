#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that a plan file has a properly structured ## Test Impact section.

Checks:
1. Section exists
2. Section has substantive content (not just a placeholder)
3. Section includes checklist items with dispositions (UPDATE/DELETE/REPLACE)
   or explicit "No existing tests affected" with justification

Exit codes:
- 0: Validation passed (proper test impact section present)
- 2: Validation failed, blocks agent (missing or incomplete test impact section)

Usage:
  uv run validate_test_impact_section.py docs/plans/feature-name.md
  uv run validate_test_impact_section.py path/to/plan.md
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MISSING_SECTION_ERROR = """
VALIDATION FAILED: Plan '{file}' is missing a ## Test Impact section.

ACTION REQUIRED: Add a ## Test Impact section to the plan following this
template:

## Test Impact

[Audit existing tests that will break or need changes due to this work.
For each affected test, specify a disposition: UPDATE, DELETE, or REPLACE.]

- [ ] `tests/unit/test_example.py::test_old_behavior` — UPDATE: assert new return value
- [ ] `tests/integration/test_flow.py::test_end_to_end` — REPLACE: rewrite for new API

[If no existing tests are affected, state that explicitly:]

No existing tests affected — [justification explaining why, e.g., "this is a
greenfield feature with no prior test coverage" or "changes are additive and
don't modify existing behavior"].
"""

INCOMPLETE_SECTION_ERROR = """
VALIDATION FAILED: Plan '{file}' has an incomplete ## Test Impact section.

The Test Impact section appears to be a placeholder or lacks substantive content.

CURRENT CONTENT:
{content}

ACTION REQUIRED: Either:
1. Add specific test impact items with dispositions (UPDATE/DELETE/REPLACE), OR
2. Explicitly state "No existing tests affected" with justification (50+ chars)

Do not leave the section empty or with only generic boilerplate.
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

        # Return the newest by mtime
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


def extract_test_impact_section(content: str) -> str | None:
    """Extract the ## Test Impact section from plan content."""
    match = re.search(
        r"^## Test Impact\s*$(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return None


def is_section_complete(section_content: str) -> tuple[bool, str]:
    """
    Check if test impact section has substantive content.

    Returns: (is_complete, reason)
    """
    if not section_content:
        return False, "Section is empty"

    # Check for explicit "no existing tests affected" statement
    no_impact_patterns = [
        r"no existing tests affected",
        r"no existing tests? (?:are |will be )?(?:affected|impacted|broken)",
        r"no tests? (?:are |will be )?(?:affected|impacted|broken)",
    ]
    for pattern in no_impact_patterns:
        if re.search(pattern, section_content, re.IGNORECASE):
            # Must include justification (at least 50 chars total)
            if len(section_content) >= 50:
                return (
                    True,
                    "Explicitly states no existing tests affected with justification",
                )
            return (
                False,
                "States no tests affected but justification is too brief (need 50+ chars)",
            )

    # Check for checklist items with dispositions
    disposition_pattern = r"- \[[ x]\].*(?:UPDATE|DELETE|REPLACE)"
    dispositions = re.findall(disposition_pattern, section_content, re.IGNORECASE)
    if dispositions:
        return True, f"Contains {len(dispositions)} test impact items with dispositions"

    # Check for checklist items without dispositions (partial compliance)
    checklist_pattern = r"- \[[ x]\]"
    checklists = re.findall(checklist_pattern, section_content)
    if checklists:
        # Has checklists but no dispositions — warn but accept if substantive
        if len(section_content) >= 50:
            return True, f"Contains {len(checklists)} checklist items (consider adding UPDATE/DELETE/REPLACE dispositions)"

    # Check for common placeholder text
    placeholder_patterns = [
        r"^\[.*\]$",
        r"^TBD\s*$",
        r"^TODO\s*$",
        r"^\.\.\.\s*$",
    ]
    for pattern in placeholder_patterns:
        if re.match(pattern, section_content.strip(), re.IGNORECASE):
            return False, "Section contains only placeholder text"

    # Too brief
    if len(section_content) < 50:
        return False, "Section content is too brief and lacks specific test impact items"

    # Has some content but unclear
    return False, "Section lacks checklist items with dispositions or explicit exemption statement"


def validate_test_impact_section(filepath: str) -> tuple[bool, str]:
    """
    Validate the plan file has a proper ## Test Impact section.

    Returns: (success, message)
    """
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, f"Failed to read file: {e}"

    doc_section = extract_test_impact_section(content)
    if doc_section is None:
        return False, MISSING_SECTION_ERROR.format(file=filepath)

    is_complete, reason = is_section_complete(doc_section)
    if not is_complete:
        return False, INCOMPLETE_SECTION_ERROR.format(
            file=filepath,
            content=doc_section[:500],
        )

    return True, f"Test Impact section is complete: {reason}"


def main():
    parser = argparse.ArgumentParser(description="Validate plan test impact section")
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

    # Determine which file to validate
    plan_file = args.plan_file
    if not plan_file:
        plan_file = find_newest_plan_file()
        if not plan_file:
            # No new plan file detected — nothing to validate, pass through
            sys.exit(0)

    if not Path(plan_file).exists():
        print(f"ERROR: Plan file does not exist: {plan_file}", file=sys.stderr)
        sys.exit(2)

    success, message = validate_test_impact_section(plan_file)

    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
