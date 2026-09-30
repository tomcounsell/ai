#!/usr/bin/env python3
"""Validate that documentation changes were made according to plan.

Extracts expected doc paths from plan's ## Documentation section and verifies
that those documents were actually created, modified, or removed in git.
Also checks for deprecated/legacy markers in documentation files.

The stale-marker scan is diff-scoped: it examines ONLY the lines this branch
ADDED (`git diff {base}...HEAD -U0` `+` lines), never pre-existing file content,
so a plan that targets a large pre-existing doc never trips on unchanged text.

Exit codes:
    0 - Validation passed (expected docs changed, no stale markers in added lines)
    1 - Missing docs (expected docs not changed) — hard fail, BLOCKS the PR
    2 - Stale markers found in added lines — non-blocking WARNING (the only
        non-blocking code); the PR proceeds
    3 - Internal/usage error (plan file not found or unreadable) — BLOCKS the PR

Usage:
    python scripts/validate_docs_changed.py docs/plans/my-feature.md
    python scripts/validate_docs_changed.py docs/plans/my-feature.md --dry-run
    python scripts/validate_docs_changed.py docs/plans/my-feature.md --base-branch develop
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Patterns that indicate stale/deprecated documentation content
DEPRECATED_PATTERNS = [
    "deprecated",
    "legacy",
    "obsolete",
    "no longer used",
    "no longer supported",
    "removed in",
    "will be removed",
    "do not use",
    "superseded by",
    "replaced by",
    "out of date",
    "outdated",
]

# Compiled regex for deprecated markers (case-insensitive, word boundaries)
DEPRECATED_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in DEPRECATED_PATTERNS) + r")\b",
    re.IGNORECASE,
)


def extract_doc_paths(plan_text: str) -> list[str]:
    """Extract expected documentation paths from plan's ## Documentation section.

    Looks for:
    - Any .md path in backticks (e.g., `docs/features/foo.md`, `README.md`)
    - Bare paths containing "/" or well-known filenames (README.md, CLAUDE.md)

    Returns list of doc paths, or empty list if "no documentation changes needed"
    is stated. Exits with error if ## Documentation section is missing entirely.
    """
    # Extract the ## Documentation section
    match = re.search(
        r"^## Documentation\s*\n(.*?)(?=^## |\Z)",
        plan_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        print(
            "Error: Plan file has no ## Documentation section. "
            "Cannot validate documentation changes.",
            file=sys.stderr,
        )
        sys.exit(1)

    section = match.group(1).strip()

    # Check for explicit "no documentation changes needed"
    no_doc_patterns = [
        r"no documentation.*needed",
        r"no documentation.*required",
        r"no documentation.*changes",
        r"documentation.*not.*needed",
        r"documentation.*not.*required",
    ]
    for pattern in no_doc_patterns:
        if re.search(pattern, section, re.IGNORECASE):
            return []

    paths = []

    # Phase 1: Any .md path in backticks (broad capture)
    backtick_pattern = r"`([^\s`]*\.md)`"
    backtick_matches = re.findall(backtick_pattern, section)
    for m in backtick_matches:
        if m not in paths:
            paths.append(m)

    # Phase 2: Bare paths containing "/" or well-known names
    # Match paths like docs/features/foo.md or README.md on their own
    bare_pattern = r"(?<!\`)(\b[a-zA-Z0-9_./-]*(?:/[a-zA-Z0-9_./-]*)*\.md)\b(?!\`)"
    bare_matches = re.findall(bare_pattern, section)
    well_known = {"README.md", "CLAUDE.md", "CHANGELOG.md", "CONTRIBUTING.md"}
    for m in bare_matches:
        if (m not in paths) and ("/" in m or m in well_known):
            paths.append(m)

    return paths


def get_changed_files(base_branch: str = "main") -> list[str]:
    """Get list of files changed between base_branch and HEAD.

    Uses `git diff --name-only {base_branch} HEAD` with fallback to
    staged + unstaged changes if the base branch comparison fails.

    Returns list of changed file paths (all files, not just .md).
    """
    changed = set()

    try:
        # Primary: compare against base branch
        result = subprocess.run(
            ["git", "diff", "--name-only", base_branch, "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            for f in result.stdout.strip().split("\n"):
                if f.strip():
                    changed.add(f.strip())
        else:
            # Fallback: staged changes
            result_staged = subprocess.run(
                ["git", "diff", "--name-only", "--cached"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result_staged.returncode == 0:
                for f in result_staged.stdout.strip().split("\n"):
                    if f.strip():
                        changed.add(f.strip())

            # Fallback: unstaged changes
            result_unstaged = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result_unstaged.returncode == 0:
                for f in result_unstaged.stdout.strip().split("\n"):
                    if f.strip():
                        changed.add(f.strip())

        # Also check untracked files (new files not yet committed)
        result_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result_untracked.returncode == 0:
            for f in result_untracked.stdout.strip().split("\n"):
                if f.strip():
                    changed.add(f.strip())

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []

    return sorted(changed)


# Hunk header: `@@ -a,b +c,d @@` — git OMITS the `,d` count when it equals 1,
# so single-line additions emit `@@ -a +c @@` and a brand-new single-line file
# emits `@@ -0,0 +1 @@`. Capture the new-file start line (c); count group is optional.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _parse_diff_added(diff_text: str) -> list[tuple[int, str]]:
    """Parse `git diff -U0` output → list of (new_file_line_number, added_text).

    With --unified=0 there are no context lines, so a hunk is a run of `-` then
    `+` bodies. Deletions do not advance the new-file counter; additions start at
    the hunk's `+c` line number.
    """
    added: list[tuple[int, str]] = []
    new_lineno = 0
    for line in diff_text.split("\n"):
        m = _HUNK_RE.match(line)
        if m:
            new_lineno = int(m.group(1))
            continue
        # Skip the file-header lines (`+++ b/path`, `--- a/path`); they always
        # have a trailing space, whereas an added body line never does after
        # its single leading `+`.
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            added.append((new_lineno, line[1:]))
            new_lineno += 1
        elif line.startswith("-"):
            continue
        elif line:
            # Defensive: any context line advances the new-file counter.
            new_lineno += 1
    return added


def _is_new_branch_file(doc_path: str, base_branch: str) -> bool:
    """True if doc_path is a brand-new file this branch introduces (absent in base).

    Used only when the three-dot diff is empty — covers untracked new docs and
    staged-but-uncommitted new docs that `{base}...HEAD` misses. Fail-safe: any
    git error (including "not a repo") returns False.
    """
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", doc_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False
    # 128 == fatal (not a git repository); do not guess.
    if tracked.returncode == 128:
        return False

    try:
        in_base = subprocess.run(
            ["git", "cat-file", "-e", f"{base_branch}:{doc_path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False
    # Present in base → not brand-new (a modification, already covered by the
    # three-dot diff); absent → brand-new on this branch.
    return in_base.returncode != 0


def get_added_lines(doc_path: str, base_branch: str = "main") -> list[tuple[int, str]]:
    """Return (line_number, text) for lines this branch ADDED to doc_path.

    Uses a three-dot diff (`git diff -U0 {base}...HEAD -- {doc}`) so only changes
    made on HEAD since the branch point count — matching the "added by this PR"
    intent. For brand-new docs the three-dot diff misses (untracked, or
    staged-but-uncommitted additions), every current line is treated as added.

    Fail-safe: on any git failure returns [] (nothing to scan) rather than raising.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--unified=0", f"{base_branch}...HEAD", "--", doc_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []

    if result.returncode == 0 and result.stdout.strip():
        added = _parse_diff_added(result.stdout)
        if added:
            return added

    # Empty diff: a brand-new file the branch adds won't appear in {base}...HEAD.
    if _is_new_branch_file(doc_path, base_branch):
        try:
            content = Path(doc_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        return list(enumerate(content.split("\n"), start=1))

    return []


def check_deprecated_markers(
    doc_paths: list[str], base_branch: str = "main"
) -> list[tuple[str, int, str]]:
    """Scan the ADDED lines of documentation files for deprecated/legacy language.

    Only lines this branch added (per `get_added_lines`) are examined, so
    pre-existing file content is never flagged. Skips:
    - Markdown headings (lines starting with #)
    - Code blocks (lines between ``` fences)
    - Inline code (backtick-wrapped text)

    Returns list of (filepath, line_number, line_content) for each violation.
    """
    violations = []

    for doc_path in doc_paths:
        added_lines = get_added_lines(doc_path, base_branch)
        if not added_lines:
            continue

        in_code_block = False
        for line_num, line in added_lines:
            stripped = line.strip()

            # Toggle code block state
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue

            # Skip lines inside code blocks
            if in_code_block:
                continue

            # Skip markdown headings
            if stripped.startswith("#"):
                continue

            # Strip inline code (backtick-wrapped text) before checking
            cleaned = re.sub(r"`[^`]+`", "", stripped)

            # Check for deprecated markers
            if DEPRECATED_RE.search(cleaned):
                violations.append((doc_path, line_num, stripped))

    return violations


def validate_docs_changed(
    plan_path: Path,
    dry_run: bool = False,
    base_branch: str = "main",
) -> tuple[str, str]:
    """Validate that docs were changed according to plan.

    Two-phase validation:
    1. Check that expected doc files appear in the git diff
    2. Check that no deprecated/legacy markers exist in the ADDED lines of those docs

    In dry-run mode, prints expected paths and returns "pass" immediately
    without running actual git validation.

    Returns (outcome, message), where outcome is one of:
        "pass"          — validation succeeded
        "missing_docs"  — expected docs were not changed (hard fail)
        "stale_markers" — stale markers found in added lines (non-blocking warning)
        "error"         — internal/usage error (plan unreadable)
    """
    # Read plan
    try:
        plan_text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return "error", f"Failed to read plan file: {e}"

    # Extract expected doc paths (this function handles missing section via sys.exit)
    expected_paths = extract_doc_paths(plan_text)

    # If explicitly "no docs needed", pass validation
    if not expected_paths:
        return "pass", (
            "Plan explicitly states no documentation changes needed. Validation passed."
        )

    # Dry-run: print expected paths and return early
    if dry_run:
        print("[DRY-RUN] Expected documentation paths:")
        for p in expected_paths:
            print(f"  - {p}")
        return "pass", "[DRY-RUN] Would validate the above paths against git diff."

    # Phase 1: Check expected docs appear in git diff
    changed_files = get_changed_files(base_branch)
    changed_docs = [f for f in changed_files if f.endswith(".md")]

    matched = []
    missing = []
    for expected in expected_paths:
        if expected in changed_docs:
            matched.append(expected)
        else:
            missing.append(expected)

    if not matched:
        return "missing_docs", (
            f"Validation failed: No expected docs were changed.\n\n"
            f"Expected paths: {expected_paths}\n"
            f"Changed docs: {changed_docs if changed_docs else '(none)'}\n\n"
            f"Either:\n"
            f"1. Create/modify the expected documentation files, OR\n"
            f'2. Add "No documentation changes needed" to the plan '
            f"if this is intentional"
        )

    # Phase 2: Check for deprecated markers in the ADDED lines of changed docs
    deprecated_violations = check_deprecated_markers(matched, base_branch)
    if deprecated_violations:
        violation_lines = []
        for filepath, line_num, line_content in deprecated_violations:
            violation_lines.append(f"  {filepath}:{line_num}: {line_content}")
        violation_report = "\n".join(violation_lines)
        return "stale_markers", (
            f"Warning (non-blocking): stale/deprecated markers found in added doc lines.\n\n"
            f"Violations:\n{violation_report}\n\n"
            f"Consider removing or rewriting the deprecated language, but this does "
            f"NOT block the PR."
        )

    # Success
    msg_parts = [
        f"Validation passed: {len(matched)} doc(s) changed as expected.",
        f"Changed: {matched}",
    ]
    if missing:
        msg_parts.append(f"Note: {len(missing)} expected doc(s) not yet changed: {missing}")
    return "pass", " ".join(msg_parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate documentation changes match plan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "plan_path",
        type=Path,
        help="Path to plan file (e.g., docs/plans/my-feature.md)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show expected doc paths without running git validation",
    )
    parser.add_argument(
        "--base-branch",
        default="main",
        help="Base branch to compare against (default: main)",
    )

    args = parser.parse_args()

    # Validate plan exists — internal/usage error (exit 3, BLOCKS the PR).
    if not args.plan_path.exists():
        print(f"Error: Plan file not found: {args.plan_path}", file=sys.stderr)
        return 3

    # Run validation
    outcome, message = validate_docs_changed(
        args.plan_path,
        dry_run=args.dry_run,
        base_branch=args.base_branch,
    )

    if outcome == "pass":
        print(message)
        return 0
    if outcome == "missing_docs":
        print(message, file=sys.stderr)
        return 1
    if outcome == "stale_markers":
        # Non-blocking warning — the only non-blocking exit code.
        print(message, file=sys.stderr)
        return 2
    # outcome == "error": internal/usage error, BLOCKS the PR.
    print(message, file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
