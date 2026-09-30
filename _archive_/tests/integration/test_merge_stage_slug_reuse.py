"""Integration test for MERGE-stage slug-reuse failure mode (issue #1377).

A BUILD dev session leaves ``.worktrees/{slug}/`` checked out to
``session/{slug}``. When the PM later dispatches a MERGE dev session reusing
the same slug, ``resolve_branch_for_stage`` may return ``("main", False)``
(if ``current_stage`` is None or maps to main), and the executor used to
launch the Claude Code subprocess inside the worktree while it was still on
the wrong branch — producing zero output until the startup watchdog killed
the session 6+ minutes later.

This test exercises ``verify_worktree_branch`` against a worktree that
mirrors the failure shape: a real on-disk worktree checked out to
``session/{slug}`` where the executor's expected branch is ``main``.

  * Clean worktree → auto-checkout, ends up on the expected branch.
  * Dirty worktree → fails loudly with ``WorktreeBranchMismatchError``,
    never silently hangs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.worktree_manager import (
    WorktreeBranchMismatchError,
    verify_worktree_branch,
)


def _make_worktree_like_session(tmp_path: Path, slug: str) -> Path:
    """Create a real git worktree checked out to session/{slug}.

    This mirrors the on-disk shape ``valor-session create --role dev`` leaves
    behind after a BUILD stage: a directory at ``.worktrees/{slug}/`` with HEAD
    pointing at ``session/{slug}``.
    """
    repo = tmp_path / "wt"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", f"session/{slug}"], check=True)
    return repo


@pytest.mark.timeout(30)
def test_merge_slug_reuse_clean_worktree_auto_recovers(tmp_path):
    """The MERGE bug shape with a clean worktree: guard auto-recovers."""
    slug = "sdlc-1377-merge"
    worktree = _make_worktree_like_session(tmp_path, slug)

    # Executor would resolve branch=main for a MERGE-stage dev session whose
    # current_stage was not populated (the #1377 failure mode).
    verify_worktree_branch(worktree, "main")

    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "main", "guard should have checked out main on a clean worktree"


@pytest.mark.timeout(30)
def test_merge_slug_reuse_dirty_worktree_fails_loudly(tmp_path):
    """Dirty worktree on the wrong branch: fail fast instead of silent hang."""
    slug = "sdlc-1377-merge"
    worktree = _make_worktree_like_session(tmp_path, slug)
    (worktree / "wip.txt").write_text("uncommitted work\n")

    with pytest.raises(WorktreeBranchMismatchError) as ei:
        verify_worktree_branch(worktree, "main")

    # The error must carry actionable detail so the caller (executor) can
    # surface it in AgentSession.last_error and the dashboard can render it.
    assert ei.value.expected_branch == "main"
    assert ei.value.actual_branch == f"session/{slug}"
    assert ei.value.dirty_files, "dirty_files must be populated"
    msg = str(ei.value)
    assert "main" in msg
    assert f"session/{slug}" in msg
