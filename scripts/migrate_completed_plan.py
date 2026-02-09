#!/usr/bin/env python3
"""Migrate completed plan to feature documentation.

Validates that feature documentation exists, is complete, and is indexed before
deleting the plan file and closing the tracking issue.

Usage:
    python scripts/migrate_completed_plan.py docs/plans/my-feature.md
    python scripts/migrate_completed_plan.py docs/plans/my-feature.md --dry-run

Exit codes:
    0 - Plan successfully migrated (or would be in dry-run)
    1 - Validation failed, plan not migrated
    2 - File or command error
"""

import re
import subprocess
import sys
from pathlib import Path


def extract_tracking_issue(plan_text: str) -> str | None:
    """Extract tracking issue URL from plan frontmatter.

    Returns the issue URL or None if not found.
    """
    match = re.search(r"^tracking:\s*(.+)$", plan_text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def extract_feature_doc_path(plan_text: str) -> str | None:
    """Extract feature doc path from Documentation section.

    Looks for patterns like:
    - [ ] Create `docs/features/my-feature.md`
    - [ ] Update `docs/features/existing.md`

    Returns the first feature doc path found, or None.
    """
    # Find Documentation section
    section_match = re.search(
        r"^## Documentation\s*\n(.*?)(?=^## |\Z)",
        plan_text,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return None

    section = section_match.group(1)

    # Extract first docs/features/*.md path from backticks
    path_match = re.search(r"`(docs/features/[^`]+\.md)`", section)
    if path_match:
        return path_match.group(1)

    return None


def validate_feature_doc(doc_path: Path) -> tuple[bool, str]:
    """Validate feature doc exists and has minimum required sections.

    Returns (is_valid, error_message).
    """
    if not doc_path.exists():
        return False, f"Feature doc not found: {doc_path}"

    content = doc_path.read_text()

    # Check for title (# Heading)
    if not re.search(r"^# .+", content, re.MULTILINE):
        return False, f"Feature doc missing title: {doc_path}"

    # Check for substantial content (more than just title)
    # Must have at least 10 non-whitespace characters beyond the title
    content_without_title = re.sub(r"^#[^\n]*\n", "", content, count=1)
    stripped_content = content_without_title.strip()
    if len(stripped_content) < 10:
        return False, f"Feature doc too short (needs content beyond title): {doc_path}"

    return True, ""


def validate_feature_index(feature_name: str) -> tuple[bool, str]:
    """Validate feature is indexed in docs/features/README.md.

    Returns (is_indexed, error_message).
    """
    index_path = Path("docs/features/README.md")
    if not index_path.exists():
        return False, "Feature index not found: docs/features/README.md"

    content = index_path.read_text()

    # Look for feature name in markdown table row
    # Pattern: | [Feature Name](filename.md) | Description | Status |
    pattern = rf"\|\s*\[.*{re.escape(feature_name)}.*\]"
    if not re.search(pattern, content, re.IGNORECASE):
        return (
            False,
            f"Feature not found in index: {feature_name}. Add entry to docs/features/README.md",
        )

    return True, ""


def close_tracking_issue(issue_url: str, dry_run: bool) -> tuple[bool, str]:
    """Close the tracking issue using gh CLI.

    Returns (success, error_message).
    """
    # Extract issue number from URL
    # Pattern: https://github.com/owner/repo/issues/123
    match = re.search(r"/issues/(\d+)", issue_url)
    if not match:
        return False, f"Could not extract issue number from URL: {issue_url}"

    issue_number = match.group(1)

    if dry_run:
        print(f"[DRY-RUN] Would close issue #{issue_number}")
        return True, ""

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "close",
                issue_number,
                "--comment",
                "Plan completed and migrated to feature documentation.",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False, f"Failed to close issue: {result.stderr}"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "gh command timed out"
    except FileNotFoundError:
        return False, "gh CLI not found. Install from https://cli.github.com/"
    except Exception as e:
        return False, f"Error closing issue: {e}"


def delete_plan(plan_path: Path, dry_run: bool) -> tuple[bool, str]:
    """Delete the plan file.

    Returns (success, error_message).
    """
    if dry_run:
        print(f"[DRY-RUN] Would delete plan: {plan_path}")
        return True, ""

    try:
        plan_path.unlink()
        return True, ""
    except Exception as e:
        return False, f"Error deleting plan: {e}"


def main() -> int:
    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_completed_plan.py <plan-path> [--dry-run]")
        print()
        print("Validates feature documentation and migrates completed plan.")
        print()
        print("Checks:")
        print("  - Feature doc exists at path specified in plan")
        print("  - Feature doc contains minimum sections (title + content)")
        print("  - Feature is indexed in docs/features/README.md")
        print("  - Tracking issue can be closed")
        print()
        print("On success:")
        print("  - Deletes the plan file")
        print("  - Closes tracking issue with comment")
        print()
        print("Options:")
        print("  --dry-run  Validate only, do not delete plan or close issue")
        return 2

    plan_path = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    # Validate plan exists
    if not plan_path.exists():
        print(f"Error: Plan file not found: {plan_path}")
        return 2

    # Read plan
    try:
        plan_text = plan_path.read_text()
    except Exception as e:
        print(f"Error reading plan: {e}")
        return 2

    print(f"Validating migration for: {plan_path}")
    if dry_run:
        print("[DRY-RUN MODE]")
    print()

    # Extract feature doc path
    feature_doc_path_str = extract_feature_doc_path(plan_text)
    if not feature_doc_path_str:
        print("Error: Could not find feature doc path in ## Documentation section")
        print("Expected pattern: - [ ] Create `docs/features/my-feature.md`")
        return 1

    feature_doc_path = Path(feature_doc_path_str)
    print(f"Feature doc path: {feature_doc_path}")

    # Validate feature doc
    valid, error = validate_feature_doc(feature_doc_path)
    if not valid:
        print(f"Error: {error}")
        return 1
    print("  PASS: Feature doc exists and has content")

    # Extract feature name from doc path
    feature_name = feature_doc_path.stem.replace("-", " ").title()
    print(f"Feature name: {feature_name}")

    # Validate feature index
    valid, error = validate_feature_index(feature_name)
    if not valid:
        print(f"Error: {error}")
        return 1
    print("  PASS: Feature indexed in docs/features/README.md")

    # Extract tracking issue
    tracking_issue = extract_tracking_issue(plan_text)
    if tracking_issue:
        print(f"Tracking issue: {tracking_issue}")

        # Close tracking issue
        success, error = close_tracking_issue(tracking_issue, dry_run)
        if not success:
            print(f"Error: {error}")
            return 1
        if not dry_run:
            print("  PASS: Tracking issue closed")
    else:
        print("Warning: No tracking issue found in plan frontmatter")

    # Delete plan
    success, error = delete_plan(plan_path, dry_run)
    if not success:
        print(f"Error: {error}")
        return 1
    if not dry_run:
        print("  PASS: Plan file deleted")

    print()
    if dry_run:
        print("Dry-run validation complete. Plan would be migrated successfully.")
    else:
        print("Plan migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
