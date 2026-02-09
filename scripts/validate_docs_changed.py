#!/usr/bin/env python3
"""Validate that documentation changes were made according to plan.

Extracts expected doc paths from plan's ## Documentation section and verifies
that those documents were actually created, modified, or removed in git.

Exit codes:
    0 - Validation passed (docs changed as planned)
    1 - Validation failed (docs not changed or plan unclear)
    2 - File or command error

Usage:
    python scripts/validate_docs_changed.py docs/plans/my-feature.md
    python scripts/validate_docs_changed.py docs/plans/my-feature.md --dry-run
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def extract_documentation_section(plan_text: str) -> str | None:
    """Extract the ## Documentation section from plan content.

    Returns the section content or None if not found.
    """
    match = re.search(
        r"^## Documentation\s*\n(.*?)(?=^## |\Z)",
        plan_text,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return None


def check_no_docs_needed(section_content: str) -> bool:
    """Check if section explicitly states no documentation is needed.

    Returns True if "no documentation needed/required/changes" pattern found.
    """
    no_doc_patterns = [
        r"no documentation.*needed",
        r"no documentation.*required",
        r"no documentation.*changes",
        r"documentation.*not.*needed",
        r"documentation.*not.*required",
    ]
    for pattern in no_doc_patterns:
        if re.search(pattern, section_content, re.IGNORECASE):
            return True
    return False


def extract_doc_paths_from_section(section_content: str) -> list[str]:
    """Extract expected documentation paths from section content.

    Looks for patterns like:
    - [ ] Create `docs/features/my-feature.md`
    - [ ] Update `README.md`
    - [ ] Add to `docs/features/README.md`

    Returns list of doc paths found (may be empty).
    """
    paths = []

    # Find all backtick-quoted paths in checklist items
    # Pattern: - [ ] (Create|Update|Add|...) `path/to/file.md`
    checklist_pattern = r"- \[ \].*?`([^`]+\.md)`"
    matches = re.findall(checklist_pattern, section_content, re.IGNORECASE)
    paths.extend(matches)

    # Also find bare paths in backticks (not necessarily in checklist)
    # Pattern: `docs/something.md` or `README.md`
    bare_path_pattern = r"`([a-zA-Z0-9_/.-]+\.md)`"
    bare_matches = re.findall(bare_path_pattern, section_content)
    for match in bare_matches:
        if match not in paths:
            paths.append(match)

    return paths


def get_changed_docs() -> list[str]:
    """Get list of documentation files changed in git (staged + unstaged).

    Returns list of changed .md file paths, or empty list on error.
    """
    try:
        # Get both staged and unstaged changes
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        changed_files = [
            f.strip() for f in result.stdout.strip().split("\n") if f.strip()
        ]

        # Also get untracked files
        result_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result_untracked.returncode == 0:
            untracked_files = [
                f.strip()
                for f in result_untracked.stdout.strip().split("\n")
                if f.strip()
            ]
            changed_files.extend(untracked_files)

        # Filter to .md files only
        doc_files = [f for f in changed_files if f.endswith(".md")]

        return list(set(doc_files))  # Remove duplicates

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def validate_docs_changed(plan_path: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Validate that docs were changed according to plan.

    Returns (success, message).
    """
    # Read plan
    try:
        plan_text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, f"Failed to read plan file: {e}"

    # Extract Documentation section
    doc_section = extract_documentation_section(plan_text)
    if not doc_section:
        return False, (
            f"Plan {plan_path} has no ## Documentation section. "
            "Cannot validate docs."
        )

    # Check for explicit "no docs needed"
    if check_no_docs_needed(doc_section):
        return True, (
            "Plan explicitly states no documentation changes needed. "
            "Validation passed."
        )

    # Extract expected doc paths
    expected_paths = extract_doc_paths_from_section(doc_section)
    if not expected_paths:
        return False, (
            f"Plan {plan_path} Documentation section contains no doc paths. "
            "Add checklist items with file paths in backticks, "
            'or state "No documentation changes needed".'
        )

    if dry_run:
        print(f"[DRY-RUN] Expected documentation paths: {expected_paths}")

    # Get actually changed docs
    changed_docs = get_changed_docs()
    if dry_run:
        print(
            f"[DRY-RUN] Changed documentation files: "
            f"{changed_docs if changed_docs else '(none)'}"
        )

    # Validate at least one expected path was changed
    matched_paths = []
    for expected in expected_paths:
        if expected in changed_docs:
            matched_paths.append(expected)

    if not matched_paths:
        return False, (
            f"Validation failed: No expected docs were changed.\n\n"
            f"Expected paths: {expected_paths}\n"
            f"Changed docs: {changed_docs if changed_docs else '(none)'}\n\n"
            f"Either:\n"
            f"1. Create/modify the expected documentation files, OR\n"
            f'2. Add "No documentation changes needed" to the plan '
            f"if this is intentional"
        )

    # Success
    return True, (
        f"Validation passed: {len(matched_paths)} doc(s) changed as expected. "
        f"Changed: {matched_paths}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate documentation changes match plan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1] if "Usage:" in __doc__ else "",
    )
    parser.add_argument(
        "plan_path",
        type=Path,
        help="Path to plan file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be validated without failing",
    )

    args = parser.parse_args()

    # Validate plan exists
    if not args.plan_path.exists():
        print(f"Error: Plan file not found: {args.plan_path}", file=sys.stderr)
        return 2

    # Validate docs changed
    success, message = validate_docs_changed(args.plan_path, args.dry_run)

    if success:
        print(message)
        return 0
    else:
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
