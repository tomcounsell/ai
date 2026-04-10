#!/usr/bin/env python3
"""
Phase 1: Build the retroactive SDLC audit set.
Recovers deleted plan files from git history and cross-references with #823 issues.
"""

import json
import re
import subprocess
from pathlib import Path

WORKTREE = Path(__file__).parent.parent
DATA_DIR = WORKTREE / "data"
DIFFS_DIR = DATA_DIR / "retroactive-audit-diffs"
OUTPUT_FILE = DATA_DIR / "retroactive-audit-set.json"

# The 18 explicit #823 issues
ISSUE_823_LIST = [
    819,
    815,
    813,
    812,
    807,
    803,
    802,
    801,
    796,
    794,
    793,
    790,
    789,
    787,
    781,
    784,
    764,
    749,
]


def run(cmd, cwd=None, capture=True):
    result = subprocess.run(
        cmd, shell=True, capture_output=capture, text=True, cwd=cwd or str(WORKTREE)
    )
    return result.stdout.strip() if capture else result.returncode


def get_deleted_plans():
    """Get all deleted plan files from git history after SDLC enforcement date."""
    output = run(
        "git log --diff-filter=D --after='2026-03-24'"
        " --pretty='format:%H' --name-only -- 'docs/plans/*.md'"
    )
    lines = output.strip().split("\n")

    entries = {}
    current_commit = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^[0-9a-f]{40}$", line):
            current_commit = line
        elif line.startswith("docs/plans/") and current_commit:
            slug = line.replace("docs/plans/", "").replace(".md", "")
            # Keep the first (most recent) deletion
            if slug not in entries:
                entries[slug] = {"commit": current_commit, "file": line, "slug": slug}

    return entries


def recover_plan_content(commit, filepath):
    """Recover plan file content from before it was deleted."""
    result = subprocess.run(
        f"git show {commit}^:{filepath}",
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(WORKTREE),
    )
    if result.returncode == 0:
        return result.stdout[:10000]  # Truncate to 10K chars
    return None


def parse_frontmatter(content):
    """Extract tracking issue URL and other fields from plan frontmatter."""
    if not content or not content.startswith("---"):
        return {}

    end = content.find("---", 3)
    if end == -1:
        return {}

    fm_text = content[3:end]
    result = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()

    return result


def get_issue_pr(issue_number):
    """Find the merged PR for a given issue number."""
    result = subprocess.run(
        f"gh issue view {issue_number} --json number,title,closedByPullRequestsReferences",
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(WORKTREE),
    )
    if result.returncode != 0:
        return None, None

    try:
        data = json.loads(result.stdout)
        title = data.get("title", "")
        prs = data.get("closedByPullRequestsReferences", [])
        if prs:
            return prs[0].get("number"), title

        # Fallback: search for PR by issue reference
        search_result = subprocess.run(
            f"gh pr list --search 'Closes #{issue_number}' --state merged --json number,title",
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(WORKTREE),
        )
        if search_result.returncode == 0:
            prs2 = json.loads(search_result.stdout)
            if prs2:
                return prs2[0]["number"], title

        return None, title
    except Exception:
        return None, None


def fetch_pr_diff(pr_number, slug):
    """Fetch PR diff and save to file."""
    diff_path = DIFFS_DIR / f"{slug}.diff"
    result = subprocess.run(
        f"gh pr diff {pr_number}", shell=True, capture_output=True, text=True, cwd=str(WORKTREE)
    )
    if result.returncode == 0 and result.stdout:
        # Truncate if over 200KB
        content = result.stdout[:200000]
        diff_path.write_text(content)
        return str(diff_path.relative_to(WORKTREE))
    return None


def get_issue_body(issue_number):
    """Get issue body as fallback plan content."""
    result = subprocess.run(
        f"gh issue view {issue_number} --json body",
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(WORKTREE),
    )
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return data.get("body", "")[:5000]
        except Exception:
            pass
    return None


def find_plan_slug_in_git(issue_number):
    """Try to find a plan slug for an issue by searching git history."""
    # Simple approach: check if any plan file mentions the issue number
    all_deleted = run(
        "git log --diff-filter=D --pretty='format:%H' --name-only -- 'docs/plans/*.md'"
    )
    lines = all_deleted.split("\n")

    # For each deleted plan, check if content mentions the issue number
    current_commit = None
    for line in lines:
        line = line.strip()
        if re.match(r"^[0-9a-f]{40}$", line):
            current_commit = line
        elif line.startswith("docs/plans/") and current_commit:
            slug = line.replace("docs/plans/", "").replace(".md", "")
            content = recover_plan_content(current_commit, line)
            if content and f"/issues/{issue_number}" in content:
                return slug, current_commit, line, content

    return None, None, None, None


def main():
    DIFFS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    audit_items = {}  # keyed by issue_number

    print("=== Phase 1: Building Audit Set ===\n")

    # Step 1: Get all deleted plans from git history
    print("Step 1: Recovering deleted plans from git history...")
    deleted_plans = get_deleted_plans()
    print(f"  Found {len(deleted_plans)} unique deleted plan files\n")

    for slug, entry in deleted_plans.items():
        content = recover_plan_content(entry["commit"], entry["file"])
        if not content:
            print(f"  WARNING: Could not recover {slug}")
            continue

        fm = parse_frontmatter(content)
        tracking = fm.get("tracking", "")

        # Check for frontmatter (structured plan)
        has_frontmatter = content.startswith("---") and "---" in content[3:]
        if not has_frontmatter:
            print(f"  SKIP {slug}: no frontmatter (pre-SDLC format)")
            continue

        # Extract issue number from tracking URL
        issue_match = re.search(r"/issues/(\d+)", tracking)
        if not issue_match:
            print(f"  SKIP {slug}: no tracking issue URL in frontmatter")
            continue

        issue_number = int(issue_match.group(1))

        item = {
            "issue_number": issue_number,
            "plan_slug": slug,
            "plan_content": content,
            "merged_pr_number": None,
            "pr_diff_path": None,
            "source": "git-history",
            "issue_title": "",
            "recoverable": True,
        }

        # Cross-reference with #823 list
        if issue_number in ISSUE_823_LIST:
            item["source"] = "both"

        if issue_number not in audit_items:
            audit_items[issue_number] = item
        else:
            # Keep the one with more data
            existing = audit_items[issue_number]
            if not existing.get("plan_content") and item.get("plan_content"):
                audit_items[issue_number] = item

    print("\nStep 2: Cross-referencing with GitHub for PRs...")
    for issue_number, item in list(audit_items.items()):
        pr_number, title = get_issue_pr(issue_number)
        if title:
            item["issue_title"] = title
        if pr_number:
            item["merged_pr_number"] = pr_number
            print(f"  #{issue_number} -> PR #{pr_number}: {title[:60] if title else ''}")
        else:
            print(f"  #{issue_number}: no PR found - {title[:60] if title else ''}")

    print("\nStep 3: Fetching PR diffs...")
    for issue_number, item in list(audit_items.items()):
        pr_number = item.get("merged_pr_number")
        slug = item.get("plan_slug", f"issue-{issue_number}")
        if pr_number:
            diff_path = fetch_pr_diff(pr_number, slug)
            if diff_path:
                item["pr_diff_path"] = diff_path
                print(f"  PR #{pr_number} ({slug}): diff saved")
            else:
                print(f"  PR #{pr_number} ({slug}): no diff available")

    print("\nStep 4: Processing explicit #823 issues...")
    for issue_number in ISSUE_823_LIST:
        if issue_number in audit_items:
            if audit_items[issue_number]["source"] == "git-history":
                audit_items[issue_number]["source"] = "both"
            src = audit_items[issue_number]["source"]
            print(f"  #{issue_number}: already in set (source: {src})")
            continue

        # Not found in git history - look harder
        slug, commit, filepath, content = find_plan_slug_in_git(issue_number)

        if slug and content:
            print(f"  #{issue_number}: found via content search -> {slug}")
            pr_number, title = get_issue_pr(issue_number)
            item = {
                "issue_number": issue_number,
                "plan_slug": slug,
                "plan_content": content[:10000],
                "merged_pr_number": pr_number,
                "pr_diff_path": None,
                "source": "#823-list",
                "issue_title": title or "",
                "recoverable": True,
            }
            if pr_number:
                diff_path = fetch_pr_diff(pr_number, slug)
                item["pr_diff_path"] = diff_path
        else:
            # Fallback: use issue body as context
            print(f"  #{issue_number}: plan not recoverable, using issue body")
            pr_number, title = get_issue_pr(issue_number)
            issue_body = get_issue_body(issue_number)
            item = {
                "issue_number": issue_number,
                "plan_slug": f"issue-{issue_number}",
                "plan_content": issue_body,
                "merged_pr_number": pr_number,
                "pr_diff_path": None,
                "source": "#823-list",
                "issue_title": title or "",
                "recoverable": False,
            }
            if pr_number:
                diff_path = fetch_pr_diff(pr_number, f"issue-{issue_number}")
                item["pr_diff_path"] = diff_path

        audit_items[issue_number] = item

    # Convert to list, sorted by issue number
    result = sorted(audit_items.values(), key=lambda x: x["issue_number"])

    # Save output
    OUTPUT_FILE.write_text(json.dumps(result, indent=2))

    print("\n=== COMPLETE ===")
    print(f"Audit set: {len(result)} items")
    print(f"Saved to: {OUTPUT_FILE}")

    # Summary stats
    recoverable = sum(1 for x in result if x["recoverable"])
    has_pr = sum(1 for x in result if x["merged_pr_number"])
    has_diff = sum(1 for x in result if x["pr_diff_path"])
    from_823 = sum(1 for x in result if "#823" in x["source"] or x["source"] == "both")
    from_git = sum(1 for x in result if "git" in x["source"] or x["source"] == "both")

    print("\nStats:")
    print(f"  Total items: {len(result)}")
    print(f"  Recoverable plans: {recoverable}")
    print(f"  Has PR number: {has_pr}")
    print(f"  Has PR diff: {has_diff}")
    print(f"  From #823 list: {from_823}")
    print(f"  From git history: {from_git}")


if __name__ == "__main__":
    main()
