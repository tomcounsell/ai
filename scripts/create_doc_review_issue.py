#!/usr/bin/env python3
"""Create GitHub issues for documentation that needs review.

Accepts scan output from scan_related_docs.py and creates GitHub issues
for HIGH and MED-HIGH confidence matches.

Usage:
    # From stdin
    python scripts/scan_related_docs.py --json file.py | python scripts/create_doc_review_issue.py

    # From file
    python scripts/scan_related_docs.py --json file.py > scan.json
    python scripts/create_doc_review_issue.py --scan-output scan.json

    # With custom title
    python scripts/create_doc_review_issue.py --scan-output scan.json --title "Review docs after feature X"

Exit codes:
    0 - Success (issue created or no actionable items)
    1 - Error (invalid input, gh CLI missing, etc.)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def load_scan_results(scan_file: str | None) -> dict:
    """Load scan results from file or stdin.

    Args:
        scan_file: Path to scan output JSON file, or None to read from stdin

    Returns:
        Parsed JSON scan results

    Raises:
        ValueError: If JSON is invalid
        FileNotFoundError: If scan_file doesn't exist
    """
    if scan_file:
        path = Path(scan_file)
        if not path.exists():
            raise FileNotFoundError(f"Scan output file not found: {scan_file}")
        with open(path) as f:
            content = f.read()
    else:
        content = sys.stdin.read()

    if not content.strip():
        raise ValueError("Empty input - no scan results to process")

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in scan results: {e}")


def filter_actionable_results(results: list[dict]) -> list[dict]:
    """Filter results to only HIGH and MED-HIGH confidence.

    Args:
        results: List of scan results with confidence levels

    Returns:
        Filtered list containing only HIGH and MED-HIGH items
    """
    return [r for r in results if r.get("confidence") in ["HIGH", "MED-HIGH"]]


def group_by_document(results: list[dict]) -> dict[str, list[dict]]:
    """Group results by document path.

    Args:
        results: List of scan results

    Returns:
        Dict mapping doc_path to list of result entries for that doc
    """
    grouped = {}
    for result in results:
        doc_path = result.get("doc_path", "unknown")
        if doc_path not in grouped:
            grouped[doc_path] = []
        grouped[doc_path].append(result)
    return grouped


def format_issue_body(scan_data: dict, actionable: list[dict]) -> str:
    """Format GitHub issue body with context and findings.

    Args:
        scan_data: Full scan output data
        actionable: Filtered HIGH/MED-HIGH results

    Returns:
        Markdown-formatted issue body
    """
    changed_files = scan_data.get("changed_files", [])
    grouped = group_by_document(actionable)

    lines = []
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"Code changes detected in {len(changed_files)} file(s) that may affect documentation. "
        f"Found {len(actionable)} high-confidence reference(s) across {len(grouped)} document(s)."
    )
    lines.append("")

    lines.append("## Changed Files")
    lines.append("")
    for file_path in changed_files:
        lines.append(f"- `{file_path}`")
    lines.append("")

    lines.append("## Documents Requiring Review")
    lines.append("")

    # Group by confidence level for clarity
    high_confidence = [r for r in actionable if r["confidence"] == "HIGH"]
    med_high_confidence = [r for r in actionable if r["confidence"] == "MED-HIGH"]

    if high_confidence:
        lines.append("### HIGH Confidence")
        lines.append("")
        lines.append("These documents contain direct references to the changed files:")
        lines.append("")
        high_grouped = group_by_document(high_confidence)
        for doc_path, results in sorted(high_grouped.items()):
            lines.append(f"#### `{doc_path}`")
            lines.append("")
            all_matches = []
            for result in results:
                all_matches.extend(result.get("matches", []))
            for match in sorted(set(all_matches)):
                lines.append(f"- {match}")
            lines.append("")

    if med_high_confidence:
        lines.append("### MED-HIGH Confidence")
        lines.append("")
        lines.append(
            "These documents reference functions/classes from the changed files:"
        )
        lines.append("")
        med_high_grouped = group_by_document(med_high_confidence)
        for doc_path, results in sorted(med_high_grouped.items()):
            lines.append(f"#### `{doc_path}`")
            lines.append("")
            all_matches = []
            for result in results:
                all_matches.extend(result.get("matches", []))
            for match in sorted(set(all_matches)):
                lines.append(f"- {match}")
            lines.append("")

    lines.append("## Suggested Actions")
    lines.append("")
    lines.append("For each document listed above:")
    lines.append("")
    lines.append("1. Review the referenced code changes")
    lines.append("2. Verify documentation accuracy")
    lines.append("3. Update examples, usage instructions, or diagrams as needed")
    lines.append("4. Check for broken links or outdated function signatures")
    lines.append("")
    lines.append("---")
    lines.append("*Generated by `scripts/create_doc_review_issue.py`*")

    return "\n".join(lines)


def create_github_issue(title: str, body: str, label: str = "docs-review") -> str:
    """Create a GitHub issue using gh CLI.

    Args:
        title: Issue title
        body: Issue body (markdown)
        label: Label to apply to the issue

    Returns:
        Issue URL

    Raises:
        RuntimeError: If gh CLI fails
    """
    # Check if gh CLI is available
    try:
        subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError(
            "GitHub CLI (gh) not found. Install it: https://cli.github.com/"
        )

    # Create issue
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--label",
                label,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        issue_url = result.stdout.strip()
        return issue_url
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create GitHub issue: {e.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create GitHub issues for documentation review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1] if "Usage:" in __doc__ else "",
    )
    parser.add_argument(
        "--scan-output",
        help="Path to scan output JSON file (default: read from stdin)",
    )
    parser.add_argument(
        "--title",
        help="Custom issue title (default: generated from scan data)",
    )
    parser.add_argument(
        "--label",
        default="docs-review",
        help="GitHub label to apply (default: docs-review)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print issue content without creating it",
    )

    args = parser.parse_args()

    try:
        # Load scan results
        scan_data = load_scan_results(args.scan_output)

        # Filter to actionable items (HIGH and MED-HIGH only)
        all_results = scan_data.get("results", [])
        actionable = filter_actionable_results(all_results)

        if not actionable:
            print("No HIGH or MED-HIGH confidence matches found - no issue needed.")
            return 0

        # Generate issue title
        if args.title:
            title = args.title
        else:
            changed_count = len(scan_data.get("changed_files", []))
            doc_count = len(set(r["doc_path"] for r in actionable))
            title = (
                f"Review {doc_count} doc(s) after changes to {changed_count} file(s)"
            )

        # Generate issue body
        body = format_issue_body(scan_data, actionable)

        # Dry run: just print
        if args.dry_run:
            print("=" * 70)
            print(f"TITLE: {title}")
            print(f"LABEL: {args.label}")
            print("=" * 70)
            print(body)
            print("=" * 70)
            return 0

        # Create issue
        issue_url = create_github_issue(title, body, args.label)
        print(f"Created issue: {issue_url}")
        return 0

    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
