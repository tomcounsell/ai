"""Regression test: ensure no references to the legacy Desktop/claude_code path remain."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Files that legitimately reference the old path (migration code, tests, plans)
ALLOWED_FILES = {
    "tests/unit/test_no_legacy_paths.py",
    "scripts/update/verify.py",  # Contains the migration function that searches for the old path
    "scripts/update/run.py",  # Log message referencing the migration
}


def test_no_legacy_claude_code_paths():
    """Verify zero unexpected references to Desktop/claude_code in git-tracked files.

    PR #438 migrated config from ~/Desktop/claude_code/ to ~/Desktop/Valor/.
    This test prevents regression by catching any re-introduction of the old path
    in files that should not reference it.
    """
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "grep", "-l", "Desktop/claude_code"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    # git grep exits 1 when no matches found (good), 0 when matches found
    if result.returncode == 0:
        files = set(result.stdout.strip().splitlines())
        # Filter out plan docs (they describe the migration)
        unexpected = {
            f for f in files if f not in ALLOWED_FILES and not f.startswith("docs/plans/")
        }
        if unexpected:
            raise AssertionError(
                "Legacy 'Desktop/claude_code' references found in:\n"
                + "\n".join(sorted(unexpected))
                + "\nThese should be updated to 'Desktop/Valor'."
            )
