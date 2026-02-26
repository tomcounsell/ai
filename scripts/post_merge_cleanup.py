#!/usr/bin/env python3
"""Post-merge cleanup: remove worktree and local branch for a merged PR.

Usage:
    python scripts/post_merge_cleanup.py <slug>
    python scripts/post_merge_cleanup.py --help

This script is called after `gh pr merge --squash --delete-branch` to clean
up the local worktree and session branch that would otherwise block deletion.

It is safe to run multiple times -- if the worktree or branch is already gone,
the script treats it as a no-op.

Examples:
    # Clean up after merging the auto-continue-audit PR
    python scripts/post_merge_cleanup.py auto-continue-audit

    # Clean up after merging worktree-merge-cleanup PR
    python scripts/post_merge_cleanup.py worktree-merge-cleanup
"""

import argparse
import logging
import sys
from pathlib import Path

# Add repo root to path so we can import agent modules
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.worktree_manager import cleanup_after_merge  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean up worktree and local branch after PR merge.",
        epilog="Run after `gh pr merge --squash --delete-branch` to remove "
        "the local worktree and session branch.",
    )
    parser.add_argument(
        "slug",
        help="Work item slug (e.g., 'my-feature'). "
        "Matches .worktrees/<slug> and branch session/<slug>.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    try:
        result = cleanup_after_merge(REPO_ROOT, args.slug)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if result["already_clean"]:
        print(f"Nothing to clean up for '{args.slug}' -- already clean.")
        return 0

    actions = []
    if result["worktree_removed"]:
        actions.append(f"Removed worktree .worktrees/{args.slug}")
    if result["branch_deleted"]:
        actions.append(f"Deleted branch session/{args.slug}")

    if actions:
        print(f"Cleaned up '{args.slug}': {'; '.join(actions)}")

    if result["errors"]:
        for err in result["errors"]:
            print(f"Error: {err}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
