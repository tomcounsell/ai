#!/usr/bin/env python3
"""SDLC reflection agent.

Fetches recently merged PRs, extracts per-stage learnings, and proposes
targeted edits to docs/sdlc/do-X.md files. Runs every 3 days via launchd.

Usage:
    python scripts/sdlc_reflection.py             # Run and open a PR with proposed changes
    python scripts/sdlc_reflection.py --dry-run   # Print proposed changes without writing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
LAST_RUN_FILE = DATA_DIR / "sdlc_reflection_last_run.json"
SDLC_DIR = PROJECT_DIR / "docs" / "sdlc"
LOG_FILE = PROJECT_DIR / "logs" / "reflections.log"

STAGE_KEYWORDS: dict[str, list[str]] = {
    "do-plan": ["plan", "planning", "do-plan", "shape up", "appetite", "slug"],
    "do-plan-critique": ["critique", "war room", "critic", "skeptic", "do-plan-critique"],
    "do-build": ["build", "implement", "worktree", "do-build", "builder"],
    "do-test": ["test", "pytest", "do-test", "unit test", "integration test"],
    "do-patch": ["patch", "fix", "do-patch", "failing test", "lint error"],
    "do-pr-review": ["review", "pr review", "do-pr-review", "pull request review"],
    "do-docs": ["docs", "documentation", "do-docs", "readme", "feature doc"],
    "do-merge": ["merge", "do-merge", "merge gate", "squash"],
}

MAX_LINES_PER_FILE = 300
DEFAULT_LOOKBACK_DAYS = 7


def log(msg: str) -> None:
    """Write to stdout and log file."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[sdlc-reflection] {timestamp} {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass  # Never crash on logging failure


def load_last_run() -> dict:
    """Load last-run metadata from data/sdlc_reflection_last_run.json."""
    if LAST_RUN_FILE.exists():
        try:
            return json.loads(LAST_RUN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_run_at": None, "last_pr_number": 0}


def save_last_run(state: dict) -> None:
    """Persist last-run metadata."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(json.dumps(state, indent=2) + "\n")


def fetch_merged_prs(since_days: int = DEFAULT_LOOKBACK_DAYS) -> list[dict]:
    """Fetch recently merged PRs using the gh CLI."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--limit",
                "30",
                "--json",
                "number,title,body,mergedAt,headRefName",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_DIR,
            timeout=30,
        )
        if result.returncode != 0:
            log(f"gh pr list failed: {result.stderr[:300]}")
            return []
        prs = json.loads(result.stdout)
        # Filter to PRs merged within the lookback window
        cutoff = datetime.now(UTC).timestamp() - (since_days * 86400)
        recent = []
        for pr in prs:
            merged_at = pr.get("mergedAt", "")
            if not merged_at:
                continue
            try:
                ts = datetime.fromisoformat(merged_at.replace("Z", "+00:00")).timestamp()
                if ts >= cutoff:
                    recent.append(pr)
            except (ValueError, AttributeError):
                continue
        return recent
    except subprocess.TimeoutExpired:
        log("gh pr list timed out")
        return []
    except Exception as e:
        log(f"fetch_merged_prs error: {e}")
        return []


def classify_pr_by_stage(pr: dict) -> list[str]:
    """Return list of SDLC stage names most relevant to this PR."""
    text = (
        (pr.get("title") or "") + " " + (pr.get("body") or "") + " " + (pr.get("headRefName") or "")
    ).lower()

    matches = []
    for stage, keywords in STAGE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            matches.append(stage)
    return matches


def extract_learnings_from_prs(prs: list[dict]) -> dict[str, list[str]]:
    """Extract per-stage learnings from PR titles and bodies.

    Returns dict mapping stage name -> list of learning strings.
    This is a lightweight heuristic extraction. The reflection agent
    intentionally stays simple to avoid noise.
    """
    learnings: dict[str, list[str]] = {stage: [] for stage in STAGE_KEYWORDS}

    for pr in prs:
        stages = classify_pr_by_stage(pr)
        if not stages:
            continue

        title = pr.get("title", "").strip()
        body = (pr.get("body") or "").strip()

        # Extract any lines starting with conventions we care about
        for line in body.splitlines():
            line = line.strip()
            # Look for explicitly flagged learnings in PR body
            if any(
                line.lower().startswith(prefix)
                for prefix in [
                    "- lesson:",
                    "- pattern:",
                    "- note:",
                    "- convention:",
                    "- learning:",
                    "- reminder:",
                    "- caveat:",
                ]
            ):
                for stage in stages:
                    learnings[stage].append(f"<!-- PR #{pr['number']}: {title} -->\n{line}")

    return learnings


def propose_edits(
    learnings: dict[str, list[str]],
    dry_run: bool = False,
) -> dict[str, bool]:
    """Write proposed learnings to docs/sdlc/ stub files.

    Returns dict mapping stage -> True if file was modified.
    """
    modified: dict[str, bool] = {}

    for stage, items in learnings.items():
        if not items:
            continue

        stub_path = SDLC_DIR / f"{stage}.md"
        if not stub_path.exists():
            log(f"Skipping {stage}: {stub_path} does not exist")
            continue

        current = stub_path.read_text()

        # Build proposed additions
        new_section = "\n## Reflection Notes (auto-generated)\n\n"
        for item in items:
            new_section += f"{item}\n\n"

        proposed = current.rstrip() + "\n" + new_section

        # Enforce 300-line limit — truncate oldest reflection notes if needed
        proposed_lines = proposed.splitlines()
        if len(proposed_lines) > MAX_LINES_PER_FILE:
            overage = len(proposed_lines) - MAX_LINES_PER_FILE
            log(
                f"WARNING: {stage}.md would exceed {MAX_LINES_PER_FILE} lines "
                f"(+{overage}); skipping to avoid limit violation"
            )
            continue

        if dry_run:
            log(f"[DRY RUN] Would add {len(items)} learning(s) to {stage}.md")
            for item in items:
                log(f"  - {item[:80]}")
            modified[stage] = True
        else:
            stub_path.write_text(proposed)
            log(f"Updated {stage}.md with {len(items)} learning(s)")
            modified[stage] = True

    return modified


def _check_existing_reflection_pr() -> bool:
    """Check if an open reflection PR already exists (title-prefix search).

    Returns True if a matching open PR is found, False otherwise.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--search",
                "docs(sdlc): reflection update",
                "--json",
                "number,title",
                "--limit",
                "5",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_DIR,
            timeout=30,
        )
        if result.returncode != 0:
            log(f"gh pr list (duplicate check) failed: {result.stderr[:300]}")
            return False
        prs = json.loads(result.stdout)
        return len(prs) > 0
    except Exception as e:
        log(f"_check_existing_reflection_pr error: {e}")
        return False


def open_pr_with_changes(modified_stages: list[str]) -> bool:
    """Commit changes to a branch and open a PR for human review."""
    if not modified_stages:
        return False

    if _check_existing_reflection_pr():
        log("Reflection PR already exists; skipping PR creation")
        return False

    branch = f"session/sdlc-reflection-{datetime.now(UTC).strftime('%Y%m%d')}"

    try:
        # Check if we're on main
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=PROJECT_DIR,
        )
        current_branch = result.stdout.strip()

        if current_branch != "main":
            log(f"Not on main (on {current_branch}); skipping PR creation")
            return False

        # Create branch
        subprocess.run(
            ["git", "checkout", "-b", branch], check=True, capture_output=True, cwd=PROJECT_DIR
        )

        # Stage modified files
        for stage in modified_stages:
            stub_path = SDLC_DIR / f"{stage}.md"
            subprocess.run(
                ["git", "add", str(stub_path)], check=True, capture_output=True, cwd=PROJECT_DIR
            )

        # Commit
        commit_msg = (
            f"docs(sdlc): reflection update for {', '.join(modified_stages)}\n\n"
            f"Auto-generated by scripts/sdlc_reflection.py.\n"
            f"Review before merging — do not merge without human approval.\n"
            f"Stages updated: {', '.join(modified_stages)}"
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg], check=True, capture_output=True, cwd=PROJECT_DIR
        )

        # Push
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            check=True,
            capture_output=True,
            cwd=PROJECT_DIR,
        )

        # Open PR
        pr_body = (
            "## Summary\n\n"
            "Auto-generated SDLC addendum updates from reflection agent.\n\n"
            "**Stages updated:**\n"
            + "\n".join(f"- `docs/sdlc/{s}.md`" for s in modified_stages)
            + "\n\n**Review carefully** — only merge if changes are accurate and non-duplicative "
            "of the global skill content.\n\n"
            "Generated by `scripts/sdlc_reflection.py` (3-day cadence)."
        )
        result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                f"docs(sdlc): reflection update ({datetime.now(UTC).strftime('%Y-%m-%d')})",
                "--body",
                pr_body,
                "--base",
                "main",
                "--head",
                branch,
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_DIR,
            timeout=30,
        )
        if result.returncode == 0:
            pr_url = result.stdout.strip()
            log(f"PR opened: {pr_url}")
            return True
        else:
            log(f"gh pr create failed: {result.stderr[:300]}")
            return False

    except subprocess.CalledProcessError as e:
        log(f"git/gh error: {e}")
        return False
    finally:
        # Return to main
        subprocess.run(["git", "checkout", "main"], capture_output=True, cwd=PROJECT_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description="SDLC reflection agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed changes without writing files or opening a PR",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Lookback window in days (default: {DEFAULT_LOOKBACK_DAYS})",
    )
    args = parser.parse_args()

    log(f"Starting SDLC reflection (dry_run={args.dry_run}, days={args.days})")

    # Ensure docs/sdlc/ exists
    if not SDLC_DIR.exists():
        log("docs/sdlc/ does not exist; nothing to update")
        return 0

    # Fetch merged PRs
    prs = fetch_merged_prs(since_days=args.days)
    log(f"Found {len(prs)} merged PRs in last {args.days} days")

    if not prs:
        log("No merged PRs — nothing to learn from. Exiting.")
        save_last_run(
            {
                "last_run_at": datetime.now(UTC).isoformat(),
                "last_pr_number": 0,
                "prs_found": 0,
            }
        )
        return 0

    # Extract learnings
    learnings = extract_learnings_from_prs(prs)
    total_learnings = sum(len(v) for v in learnings.values())
    log(f"Extracted {total_learnings} learning(s) across stages")

    if total_learnings == 0:
        log("No actionable learnings found. Exiting.")
        save_last_run(
            {
                "last_run_at": datetime.now(UTC).isoformat(),
                "prs_found": len(prs),
                "learnings_extracted": 0,
            }
        )
        return 0

    # Propose edits
    modified = propose_edits(learnings, dry_run=args.dry_run)
    modified_stages = [s for s, changed in modified.items() if changed]

    if modified_stages and not args.dry_run:
        success = open_pr_with_changes(modified_stages)
        log(f"PR creation: {'success' if success else 'failed'}")

    # Save state
    save_last_run(
        {
            "last_run_at": datetime.now(UTC).isoformat(),
            "prs_found": len(prs),
            "learnings_extracted": total_learnings,
            "stages_modified": modified_stages,
        }
    )

    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
