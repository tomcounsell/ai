#!/usr/bin/env python3
"""
Validate that a GitHub issue has reconnaissance evidence before /do-plan proceeds.

The recon routine (explore → concerns → fan-out → synthesize) surfaces unknowns,
conflicts, and stale assumptions. This validator ensures it was performed.

Checks the issue body for:
1. A "## Recon Summary" section with the four required buckets
2. At least one item in any bucket (not an empty template)
3. OR an explicit skip justification for trivial issues

Exit codes:
- 0: Validation passed
- 2: Validation failed, blocks agent

Usage:
  python validate_issue_recon.py <issue_number>
  python validate_issue_recon.py  # auto-detect from current branch or args

The validator is called by /do-plan (or /sdlc before dispatching /do-plan)
to gate the ISSUE → PLAN transition.
"""

import json
import re
import subprocess
import sys

MISSING_RECON_ERROR = """
VALIDATION FAILED: Issue #{number} is missing reconnaissance evidence.

Before planning, run the recon routine (Step 3 in /do-issue) to surface
unknowns and conflicts. The issue body must contain either:

1. A "## Recon Summary" section with findings, OR
2. A "## Recon: Skipped" section with justification (for trivial issues)

Add one of these to the issue body, then retry /do-plan.

See .claude/skills/do-issue/RECON.md for the full pattern.
"""

INCOMPLETE_RECON_ERROR = """
VALIDATION FAILED: Issue #{number} has an incomplete Recon Summary.

The "## Recon Summary" section exists but appears to be a template without
actual findings. It must contain at least one concrete item in any bucket
(Confirmed, Revised, Pre-requisites, or Dropped).

CURRENT CONTENT:
{content}
"""

# Patterns that indicate recon was performed
RECON_SECTION_PATTERNS = [
    r"^## Recon Summary",
    r"^## Recon",
]

# Patterns that indicate an explicit skip
SKIP_PATTERNS = [
    r"^## Recon:\s*Skip",
    r"recon.*skip.*trivial",
    r"recon.*not.*needed.*because",
    r"recon.*unnecessary.*because",
]

# Bucket headers expected in a complete recon summary
BUCKET_PATTERNS = [
    r"\*\*Confirmed\*\*:?",
    r"\*\*Revised\*\*:?",
    r"\*\*Pre-requisites?\*\*:?",
    r"\*\*Dropped\*\*:?",
]


def get_issue_body(issue_number: str) -> str | None:
    """Fetch issue body from GitHub using gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_number, "--json", "body", "--jq", ".body"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None


def extract_recon_section(body: str) -> str | None:
    """Extract the ## Recon Summary section from the issue body."""
    for pattern in RECON_SECTION_PATTERNS:
        match = re.search(
            pattern + r"\s*$(.*?)(?=^## |\Z)",
            body,
            re.MULTILINE | re.DOTALL,
        )
        if match:
            return match.group(1).strip()
    return None


def has_skip_justification(body: str) -> bool:
    """Check if the issue explicitly skips recon with justification."""
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, body, re.IGNORECASE | re.MULTILINE):
            # Must have at least 30 chars of justification
            match = re.search(pattern + r"(.{30,})", body, re.IGNORECASE | re.DOTALL)
            if match:
                return True
    return False


def is_recon_complete(section: str) -> tuple[bool, str]:
    """Check if the recon summary has substantive content."""
    if not section:
        return False, "Section is empty"

    # Check for at least one bucket with content
    buckets_found = 0
    for pattern in BUCKET_PATTERNS:
        if re.search(pattern, section, re.IGNORECASE):
            buckets_found += 1

    if buckets_found == 0:
        return False, "No recon buckets found (Confirmed/Revised/Pre-requisites/Dropped)"

    # Check for actual items (lines starting with - after a bucket header)
    items = re.findall(r"^- .+", section, re.MULTILINE)
    if len(items) == 0:
        return False, "Recon buckets exist but contain no items"

    return True, f"Found {buckets_found} buckets with {len(items)} items"


def detect_issue_number() -> str | None:
    """Try to detect issue number from stdin context or branch name."""
    # Try stdin (hook context)
    try:
        ctx = json.load(sys.stdin)
        # Look for issue number in tool input
        tool_input = ctx.get("tool_input", {})
        command = tool_input.get("command", "")
        # Match: gh issue view 123, /do-plan #123, etc.
        match = re.search(r"#?(\d+)", command)
        if match:
            return match.group(1)
    except (json.JSONDecodeError, EOFError):
        pass

    return None


def main():
    # Get issue number from args or auto-detect
    issue_number = None
    if len(sys.argv) > 1:
        # Extract number from arg (could be "#123" or "123")
        match = re.search(r"(\d+)", sys.argv[1])
        if match:
            issue_number = match.group(1)

    if not issue_number:
        issue_number = detect_issue_number()

    if not issue_number:
        # Can't determine issue — pass through (don't block on ambiguity)
        sys.exit(0)

    # Fetch issue body
    body = get_issue_body(issue_number)
    if body is None:
        # Can't reach GitHub — pass through
        print(
            f"WARNING: Could not fetch issue #{issue_number} from GitHub",
            file=sys.stderr,
        )
        sys.exit(0)

    # Check for explicit skip
    if has_skip_justification(body):
        print(
            json.dumps(
                {
                    "result": "continue",
                    "message": f"Issue #{issue_number}: Recon skipped with justification",
                }
            )
        )
        sys.exit(0)

    # Check for recon section
    recon_section = extract_recon_section(body)
    if recon_section is None:
        print(MISSING_RECON_ERROR.format(number=issue_number), file=sys.stderr)
        sys.exit(2)

    # Check completeness
    is_complete, reason = is_recon_complete(recon_section)
    if not is_complete:
        print(
            INCOMPLETE_RECON_ERROR.format(
                number=issue_number,
                content=recon_section[:500],
            ),
            file=sys.stderr,
        )
        sys.exit(2)

    print(
        json.dumps(
            {
                "result": "continue",
                "message": f"Issue #{issue_number} recon validated: {reason}",
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
