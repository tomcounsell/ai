#!/usr/bin/env python3
"""Migrate completed plan to feature documentation.

Validates that feature documentation exists, is complete, and is indexed before
deleting the plan file and closing the tracking issue.

Usage:
    python scripts/migrate_completed_plan.py docs/plans/my-feature.md
    python scripts/migrate_completed_plan.py docs/plans/my-feature.md --dry-run

Also provides the path-independent migration primitive (issue #1900, Tier 0):
``migrate_plan_to_completed()`` performs a guarded ``git mv`` of a root plan into
``docs/plans/completed/`` -- the single authoritative mechanism two call sites
share: the deterministic ``/do-merge --issue`` invocation and the
``merged-branch-cleanup`` reflection backstop.

    python scripts/migrate_completed_plan.py --issue 1900 [--apply|--dry-run]
    python scripts/migrate_completed_plan.py --sweep [--apply] [--cap N]

Exit codes:
    0 - Plan successfully migrated (or would be in dry-run)
    1 - Validation failed, plan not migrated
    2 - File or command error
"""

import json
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


def extract_feature_name_from_index(feature_doc_filename: str) -> str | None:
    """Extract the display name for a feature doc from the README index table.

    Searches docs/features/README.md for a table row whose link target matches
    the given filename (e.g., 'pm-dev-session-architecture.md') and returns the
    bracketed display text (e.g., 'PM/Dev Session Architecture').

    Returns None if no matching row is found.
    """
    index_path = Path("docs/features/README.md")
    if not index_path.exists():
        return None

    content = index_path.read_text()

    # Match table rows: | [Display Name](filename.md) | ... |
    # The filename in the link target must match exactly
    pattern = rf"\|\s*\[([^\]]+)\]\({re.escape(feature_doc_filename)}\)"
    match = re.search(pattern, content)
    if match:
        return match.group(1)

    return None


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


# --- Path-independent migration primitive (issue #1900, Tier 0) ------------------
#
# ``migrate_plan_to_completed()`` is the ONE authoritative mechanism for moving a
# completed plan out of ``docs/plans/`` root into ``docs/plans/completed/``. Two
# call sites share it: the deterministic ``/do-merge --issue`` invocation (Site D)
# and the ``merged-branch-cleanup`` reflection backstop (Site C). Both call this
# same function -- neither re-implements the git mv / guard logic.


def _run_git(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand rooted at ``cwd``. Never raises on non-zero exit."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _rebase_in_progress(repo_root: Path) -> bool:
    """Detect a half-finished rebase (rebase-merge/rebase-apply state dir present)."""
    git_dir_result = _run_git(["rev-parse", "--git-dir"], cwd=repo_root)
    if git_dir_result.returncode != 0:
        return False
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def migrate_plan_to_completed(plan_path: Path, *, apply: bool) -> str:
    """Guarded git-mv of a root plan into docs/plans/completed/.

    Returns one of: "migrated", "already-migrated", "dirty-tree-skip",
    "rebase-conflict-skip". Never raises -- all failure modes return a verdict
    string and log the reason.

    Known behavior: the final ``git push origin main`` publishes the whole
    local main, so unpushed commits already sitting on a clean local main
    ride along with the migration commit. Accepted for this repo's
    always-push workflow (PR #1903 review nit).
    """
    plan_path = Path(plan_path)

    # Resolve repo layout from the plan's own path: docs/plans/{name}.md implies
    # repo_root == plan_path.parent.parent.parent. This keeps the function usable
    # from callers with different process cwds (CLI vs. reflection worker) without
    # needing a cwd parameter in the public signature.
    anchor = plan_path if plan_path.is_absolute() else plan_path.resolve()
    plans_dir = anchor.parent
    repo_root = plans_dir.parent.parent
    completed_path = plans_dir / "completed" / plan_path.name

    # Existence-guard (idempotency): git mv is NOT idempotent -- a second attempt
    # on an already-moved plan must not look like a failure.
    if not plan_path.exists():
        if completed_path.exists():
            print(f"[SKIP] Already migrated: {plan_path.name}")
            return "already-migrated"
        print(f"[SKIP] Plan not found in root or completed/: {plan_path}")
        return "already-migrated"

    # Clean-tree/HEAD==main precondition. If either fails, this is the
    # report-only fallback: log what would be migrated, mutate nothing.
    try:
        branch_result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        status_result = _run_git(["status", "--porcelain"], cwd=repo_root)
        tree_dirty = bool(status_result.stdout.strip()) if status_result.returncode == 0 else True
    except Exception as e:
        print(f"[SKIP] Could not inspect git state for {plan_path.name}: {e}")
        return "dirty-tree-skip"

    if current_branch != "main" or tree_dirty:
        reasons = []
        if current_branch != "main":
            reasons.append(f"HEAD is '{current_branch}', not 'main'")
        if tree_dirty:
            reasons.append("working tree is dirty")
        print(
            f"[REPORT-ONLY] Would migrate {plan_path.name} -> docs/plans/completed/ "
            f"(blocked: {'; '.join(reasons)})"
        )
        return "dirty-tree-skip"

    if not apply:
        print(f"[DRY-RUN] Would migrate {plan_path.name} -> docs/plans/completed/")
        return "migrated"

    completed_path.parent.mkdir(parents=True, exist_ok=True)
    mv_result = _run_git(["mv", str(plan_path), str(completed_path)], cwd=repo_root)
    if mv_result.returncode != 0:
        print(f"[ERROR] git mv failed for {plan_path.name}: {mv_result.stderr.strip()}")
        return "dirty-tree-skip"

    commit_result = _run_git(
        ["commit", "-m", f"Migrate completed plan: {plan_path.stem}"], cwd=repo_root
    )
    if commit_result.returncode != 0:
        print(f"[ERROR] git commit failed for {plan_path.name}: {commit_result.stderr.strip()}")
        _run_git(["reset", "--hard", "HEAD"], cwd=repo_root)
        return "dirty-tree-skip"

    # If there's no 'origin' remote (e.g. a local-only test repo), the migration
    # is already durable as a local commit -- nothing more to do.
    remote_check = _run_git(["remote", "get-url", "origin"], cwd=repo_root)
    if remote_check.returncode != 0:
        print(f"[MIGRATED] {plan_path.name} -> docs/plans/completed/ (no 'origin' remote)")
        return "migrated"

    # Rebase-retry loop: a losing push replays atop the winner. Distinguish a
    # genuine textual conflict (abort + leave tree clean, never resolve
    # unattended) from a plain non-fast-forward rejection (retry).
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        push_result = _run_git(["push", "origin", "main"], cwd=repo_root, timeout=60)
        if push_result.returncode == 0:
            print(f"[MIGRATED] {plan_path.name} -> docs/plans/completed/")
            return "migrated"

        print(
            f"[WARN] git push rejected for {plan_path.name} "
            f"(attempt {attempt}/{max_attempts}): {push_result.stderr.strip()}"
        )
        pull_result = _run_git(["pull", "--rebase", "origin", "main"], cwd=repo_root, timeout=60)
        conflict_text = (pull_result.stdout + pull_result.stderr).lower()
        if pull_result.returncode != 0 and (
            _rebase_in_progress(repo_root) or "conflict" in conflict_text
        ):
            _run_git(["rebase", "--abort"], cwd=repo_root)
            print(
                f"[ERROR] Rebase conflict migrating {plan_path.name}; "
                "aborted rebase, tree left clean"
            )
            return "rebase-conflict-skip"

    print(f"[ERROR] Failed to push migration for {plan_path.name} after {max_attempts} attempts")
    return "rebase-conflict-skip"


def find_plan_by_issue(issue_number: str, plans_dir: Path = Path("docs/plans")) -> Path | None:
    """Scan root plans for the one whose tracking: frontmatter matches issue_number."""
    for plan_file in sorted(plans_dir.glob("*.md")):
        text = plan_file.read_text(errors="replace")
        tracking_url = extract_tracking_issue(text)
        if not tracking_url:
            continue
        match = re.search(r"/issues/(\d+)", tracking_url)
        if match and match.group(1) == str(issue_number):
            return plan_file
    return None


def _gh_issue_state(issue_number: str) -> str:
    """Look up a GitHub issue's state via gh. Returns 'unknown' on any failure."""
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "state"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return str(data.get("state", "unknown")).lower()
    except Exception as e:
        print(f"[WARN] Could not check issue #{issue_number}: {e}")
    return "unknown"


def run_issue(issue_number: str, *, apply: bool) -> int:
    """CLI handler for --issue <N>: resolve the plan by tracking issue, migrate it.

    Evidence-gated like run_sweep and the reflection: the migration only fires
    when the tracking issue is literally "closed". A multi-PR issue (PR 1
    merged, issue still open for PR 2) must keep its plan live in the root;
    a gh outage ("unknown") must defer, never migrate.
    """
    plan_file = find_plan_by_issue(issue_number)
    if not plan_file:
        print(f"Error: no plan found with tracking issue #{issue_number}")
        return 2

    state = _gh_issue_state(issue_number)
    if state != "closed":
        print(f"Verdict: skipped-open (issue #{issue_number} state={state})")
        return 1

    verdict = migrate_plan_to_completed(plan_file, apply=apply)
    print(f"Verdict: {verdict}")
    return 0 if verdict in ("migrated", "already-migrated") else 1


def run_sweep(*, apply: bool, cap: int | None) -> int:
    """CLI handler for --sweep: migrate every root plan with a closed tracking issue."""
    plans_dir = Path("docs/plans")
    migrated_count = 0
    rows: list[tuple[str, str, str]] = []

    for plan_file in sorted(plans_dir.glob("*.md")):
        text = plan_file.read_text(errors="replace")
        tracking_url = extract_tracking_issue(text)
        if not tracking_url:
            rows.append((plan_file.name, "no tracking issue in frontmatter", "skip"))
            continue

        match = re.search(r"/issues/(\d+)", tracking_url)
        if not match:
            rows.append((plan_file.name, f"unparseable tracking url: {tracking_url}", "skip"))
            continue

        issue_number = match.group(1)
        state = _gh_issue_state(issue_number)
        if state != "closed":
            rows.append((plan_file.name, f"issue #{issue_number} state={state}", "skip"))
            continue

        if cap is not None and migrated_count >= cap:
            rows.append((plan_file.name, f"issue #{issue_number} closed", "deferred (cap reached)"))
            continue

        verdict = migrate_plan_to_completed(plan_file, apply=apply)
        rows.append((plan_file.name, f"issue #{issue_number} closed", verdict))
        if verdict == "migrated":
            migrated_count += 1

    print(f"{'plan':<50} {'evidence':<40} action")
    for name, evidence, action in rows:
        print(f"{name:<50} {evidence:<40} {action}")
    return 0


def main() -> int:
    args = sys.argv[1:]

    # --issue <N>: path-independent migration keyed on the plan's own tracking
    # frontmatter (issue #1900, Tier 0). This is what /do-merge invokes after a
    # real merge, so it defaults to apply=True unless --dry-run is passed.
    if "--issue" in args:
        idx = args.index("--issue")
        if idx + 1 >= len(args):
            print("Error: --issue requires an issue number")
            return 2
        issue_number = args[idx + 1]
        apply = "--dry-run" not in args
        return run_issue(issue_number, apply=apply)

    # --sweep [--apply] [--cap N]: iterate every root plan, migrate the ones
    # whose tracking issue is closed. Report-only (apply=False) by default.
    if "--sweep" in args:
        apply = "--apply" in args
        cap: int | None = None
        if "--cap" in args:
            cap_idx = args.index("--cap")
            if cap_idx + 1 >= len(args):
                print("Error: --cap requires an integer value")
                return 2
            try:
                cap = int(args[cap_idx + 1])
            except ValueError:
                print("Error: --cap requires an integer value")
                return 2
        return run_sweep(apply=apply, cap=cap)

    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_completed_plan.py <plan-path> [--dry-run]")
        print("       python scripts/migrate_completed_plan.py --issue <N> [--dry-run]")
        print("       python scripts/migrate_completed_plan.py --sweep [--apply] [--cap N]")
        print()
        print("Validates feature documentation and migrates completed plan.")
        print()
        print("Checks:")
        print("  - Feature doc exists at path specified in plan")
        print("  - Feature doc contains minimum sections (title + content)")
        print("  - Feature is indexed in docs/features/README.md")
        print("  - Tracking issue exists (closed on PR merge, not here)")
        print()
        print("On success:")
        print("  - Deletes the plan file")
        print()
        print("Options:")
        print("  --dry-run  Validate only, do not delete plan or close issue")
        print()
        print("  --issue <N>          Migrate the plan tracking issue N (git mv, not delete)")
        print("  --sweep [--apply]    Migrate every root plan with a closed tracking issue")
        print("  --cap N              With --sweep, migrate at most N plans this run")
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

    # Extract feature name from README index (avoids .title() mangling acronyms)
    feature_doc_filename = feature_doc_path.name
    feature_name = extract_feature_name_from_index(feature_doc_filename)
    if feature_name:
        print(f"Feature name (from index): {feature_name}")
        # Validate using the extracted name
        valid, error = validate_feature_index(feature_name)
        if not valid:
            print(f"Error: {error}")
            return 1
    else:
        # Fallback: check if the filename appears as a link target in the index
        print(
            f"Warning: No README entry found linking to {feature_doc_filename}. "
            f"Add entry to docs/features/README.md"
        )
        return 1
    print("  PASS: Feature indexed in docs/features/README.md")

    # Note: Issues are closed automatically when the PR merges (via `Closes #N` in PR body)
    tracking_issue = extract_tracking_issue(plan_text)
    if tracking_issue:
        print(f"Tracking issue: {tracking_issue} (will close on PR merge)")
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
