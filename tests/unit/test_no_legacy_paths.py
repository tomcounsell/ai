"""Regression test: ensure no references to the legacy Desktop/claude_code path remain."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_no_legacy_claude_code_paths():
    """Verify zero references to Desktop/claude_code in git-tracked files.

    PR #438 migrated config from ~/Desktop/claude_code/ to ~/Desktop/Valor/.
    This test prevents regression by catching any re-introduction of the old path.
    """
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "grep", "-l", "Desktop/claude_code"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    # git grep exits 1 when no matches found (good), 0 when matches found (bad)
    if result.returncode == 0:
        files = result.stdout.strip()
        raise AssertionError(
            f"Legacy 'Desktop/claude_code' references found in:\n{files}\n"
            "These should be updated to 'Desktop/Valor'."
        )
