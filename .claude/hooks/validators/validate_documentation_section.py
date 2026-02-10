#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that a plan file has a properly structured ## Documentation section.

Checks:
1. Section exists
2. Section has substantive content (not just a placeholder)
3. Section includes checklist items or explicit "no documentation needed" statement

Exit codes:
- 0: Validation passed (proper documentation section present)
- 2: Validation failed, blocks agent (missing or incomplete documentation section)

Usage:
  uv run validate_documentation_section.py docs/plans/feature-name.md
  uv run validate_documentation_section.py path/to/plan.md

Frontmatter example:
  hooks:
    Stop:
      - hooks:
          - type: command
            command: >-
              uv run $CLAUDE_PROJECT_DIR/.claude/hooks/validators/validate_documentation_section.py
              docs/plans/{plan-file}.md
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MISSING_SECTION_ERROR = """
VALIDATION FAILED: Plan '{file}' is missing a ## Documentation section.

ACTION REQUIRED: Add a ## Documentation section to the plan following this
template:

## Documentation

[What documentation needs to be created or updated when this work ships.
Use the `documentarian` agent type for these tasks.]

### Feature Documentation
- [ ] Create/update `docs/features/[feature-name].md` describing the feature
- [ ] Add entry to `docs/features/README.md` index table

### External Documentation Site
[If the repo uses Sphinx, Read the Docs, MkDocs, or similar:]
- [ ] Update relevant pages in the documentation site
- [ ] Verify docs build passes

### Inline Documentation
- [ ] Code comments on non-obvious logic
- [ ] Updated docstrings for public APIs

[If no documentation changes are needed, state that explicitly and explain why.]
"""

INCOMPLETE_SECTION_ERROR = """
VALIDATION FAILED: Plan '{file}' has an incomplete ## Documentation section.

The Documentation section appears to be a placeholder or lacks substantive content.

CURRENT CONTENT:
{content}

ACTION REQUIRED: Either:
1. Add specific documentation tasks with checklist items (- [ ]), OR
2. Explicitly state "No documentation changes needed" and explain why

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


def extract_documentation_section(content: str) -> str | None:
    """Extract the ## Documentation section from plan content."""
    # Match ## Documentation followed by content until the next ## section
    match = re.search(
        r"^## Documentation\s*$(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return None


def is_section_complete(section_content: str) -> tuple[bool, str]:
    """
    Check if documentation section has substantive content.

    Returns: (is_complete, reason)
    """
    if not section_content:
        return False, "Section is empty"

    # Check for explicit "no documentation needed" statement
    no_doc_patterns = [
        r"no documentation.*needed",
        r"no documentation.*required",
        r"no documentation.*changes",
        r"documentation.*not.*needed",
        r"documentation.*not.*required",
    ]
    for pattern in no_doc_patterns:
        if re.search(pattern, section_content, re.IGNORECASE):
            # Must also include an explanation (at least 10 chars after the statement)
            if len(section_content) > 50:
                return (
                    True,
                    "Explicitly states no documentation needed with explanation",
                )

    # Check for checklist items (- [ ])
    checklist_pattern = r"- \[ \]"
    checklists = re.findall(checklist_pattern, section_content)
    if len(checklists) >= 2:  # At least 2 checklist items
        return True, f"Contains {len(checklists)} documentation tasks"

    # Check for common placeholder text
    placeholder_patterns = [
        r"^\[.*\]$",  # Just a single [placeholder] line
        r"^TBD\s*$",
        r"^TODO\s*$",
        r"^\.\.\.\s*$",
    ]
    for pattern in placeholder_patterns:
        if re.match(pattern, section_content.strip(), re.IGNORECASE):
            return False, "Section contains only placeholder text"

    # If we have some content but no checklists and no explicit "no doc needed", flag it
    if len(section_content) < 50:
        return False, "Section content is too brief and lacks specific tasks"

    # Has some content but unclear
    return False, "Section lacks clear checklist items or explicit exemption statement"


def validate_documentation_section(filepath: str) -> tuple[bool, str]:
    """
    Validate the plan file has a proper ## Documentation section.

    Returns: (success, message)
    """
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, f"Failed to read file: {e}"

    # Check if section exists
    doc_section = extract_documentation_section(content)
    if doc_section is None:
        return False, MISSING_SECTION_ERROR.format(file=filepath)

    # Check if section is complete
    is_complete, reason = is_section_complete(doc_section)
    if not is_complete:
        return False, INCOMPLETE_SECTION_ERROR.format(
            file=filepath, content=doc_section[:500]  # Truncate for display
        )

    return True, f"Documentation section is complete: {reason}"


def main():
    parser = argparse.ArgumentParser(description="Validate plan documentation section")
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

    success, message = validate_documentation_section(plan_file)

    if success:
        print(json.dumps({"result": "continue", "message": message}))
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
