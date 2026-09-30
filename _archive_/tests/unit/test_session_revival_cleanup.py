"""Tests for the unmerged-branch guard in session_revival.cleanup_stale_branches (Site D).

Verifies the scheduler-driven stale-branch cleanup vector is closed:
- Stale but unmerged session/* branches are preserved
- Stale and squash-merged branches are still cleaned
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.worktree_manager import merged_via_tree, safe_delete_branch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


def _branch_exists(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSiteDStaleBranchCleanup:
    """Site D: session_revival.cleanup_stale_branches guard via merged_via_tree."""

    def test_stale_unmerged_branch_preserved(self, tmp_path, caplog):
        """A stale-but-unmerged session/* branch is preserved, not force-deleted.

        This closes the scheduler-driven vector (cleanup_stale_branches fires autonomously
        on a timer). The age threshold only selects candidates; the merged check decides.
        """
        repo = _make_repo(tmp_path)
        branch = "session/dev-stale-unmerged"
        _git(repo, "checkout", "-b", branch)
        (repo / "unmerged_work.py").write_text("# unmerged\n")
        _git(repo, "add", "unmerged_work.py")
        _git(repo, "commit", "-m", "feat: unmerged work")
        _git(repo, "checkout", "main")

        # Site D uses merged_via_tree with force=True
        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            result = safe_delete_branch(
                str(repo),
                branch,
                predicate=merged_via_tree,
                force=True,
            )

        assert result["deleted"] is False, "Unmerged branch should not be deleted"
        assert result["skipped_unmerged"] is True
        assert _branch_exists(repo, branch), (
            f"Branch {branch} should be preserved — stale but unmerged"
        )
        assert any("[unmerged-branch-guard]" in r.message for r in caplog.records)

    def test_stale_squash_merged_branch_cleaned(self, tmp_path):
        """A stale-but-squash-merged session/* branch is correctly deleted.

        The most common stale-ref class: branch squash-merged via PR, local ref never
        deleted. merged_via_tree correctly identifies these as landed (is-ancestor would
        read them as unmerged and preserve them forever).
        """
        repo = _make_repo(tmp_path)
        branch = "session/dev-stale-squashed"
        _git(repo, "checkout", "-b", branch)
        (repo / "f1.py").write_text("# commit 1\n")
        _git(repo, "add", "f1.py")
        _git(repo, "commit", "-m", "feat: commit 1")
        (repo / "f2.py").write_text("# commit 2\n")
        _git(repo, "add", "f2.py")
        _git(repo, "commit", "-m", "feat: commit 2")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", branch)
        _git(repo, "commit", "-m", "squash: dev-stale-squashed")
        # Advance main (simulating time passing after the squash)
        (repo / "extra.txt").write_text("extra\n")
        _git(repo, "add", "extra.txt")
        _git(repo, "commit", "-m", "chore: advance main")

        result = safe_delete_branch(
            str(repo),
            branch,
            predicate=merged_via_tree,
            force=True,
        )

        assert result["deleted"] is True, "Squash-merged branch should be cleaned up"
        assert result["skipped_unmerged"] is False
        assert not _branch_exists(repo, branch)

    def test_is_ancestor_would_preserve_squash_merged(self, tmp_path):
        """Confirms that is-ancestor alone would have preserved squash-merged stale refs forever.

        This justifies why Site D must use merged_via_tree, not merged_via_ancestor.
        """
        from agent.worktree_manager import merged_via_ancestor

        repo = _make_repo(tmp_path)
        branch = "session/dev-stale-ancestor-fail"
        _git(repo, "checkout", "-b", branch)
        (repo / "c1.py").write_text("c1\n")
        _git(repo, "add", "c1.py")
        _git(repo, "commit", "-m", "c1")
        (repo / "c2.py").write_text("c2\n")
        _git(repo, "add", "c2.py")
        _git(repo, "commit", "-m", "c2")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", branch)
        _git(repo, "commit", "-m", "squash: ancestor-fail")

        # is-ancestor returns False (would preserve this stale ref forever)
        assert merged_via_ancestor(str(repo), branch, "main") is False, (
            "is-ancestor should return False for squash-merged branch"
        )
        # merged_via_tree correctly identifies it as landed
        assert merged_via_tree(str(repo), branch, "main") is True, (
            "merged_via_tree should return True for squash-merged branch"
        )


# ---------------------------------------------------------------------------
# Nightly-triage lane reaping (issue #3162)
# ---------------------------------------------------------------------------


def _commit_dated(repo: Path, message: str, when: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def _registered_worktrees(repo: Path) -> set[Path]:
    out = _git(repo, "worktree", "list", "--porcelain").stdout
    return {
        Path(line.split(" ", 1)[1]).resolve()
        for line in out.splitlines()
        if line.startswith("worktree ")
    }


class TestNightlyTriageLaneSweep:
    """cleanup_stale_branches reaps a stale nightly-triage worktree, and only that namespace.

    Every lane here is checked out in its worktree, so ``git branch --list``
    reports it with the ``+ `` marker. These tests therefore also pin the
    branch-list parsing: a sweep that drops the marker's branches never sees a
    lane at all.
    """

    @pytest.fixture
    def idle_lanes(self, monkeypatch):
        import agent.worktree_manager as wm

        monkeypatch.setattr(wm, "_worktree_has_live_process", lambda _p: None)
        monkeypatch.setattr(wm, "worktree_busy_probe", lambda _r, _s: ("clear", ""))

    @staticmethod
    def _add_lane(repo: Path, slug: str, when: str) -> Path:
        lane = repo / ".worktrees" / slug
        _git(repo, "worktree", "add", "-b", f"session/{slug}", str(lane), "main")
        _commit_dated(lane, f"lane {slug}", when)
        return lane

    async def test_stale_triage_lane_is_reaped_and_branch_deleted(self, tmp_path, idle_lanes):
        from agent.session_revival import cleanup_stale_branches

        repo = _make_repo(tmp_path)
        old = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        lane = self._add_lane(repo, "nightly-triage-1a2b3c4d", old)

        cleaned = await cleanup_stale_branches(str(repo), max_age_hours=72)

        assert "session/nightly-triage-1a2b3c4d" in cleaned
        assert not lane.exists()
        assert lane.resolve() not in _registered_worktrees(repo)
        assert not _branch_exists(repo, "session/nightly-triage-1a2b3c4d")

    async def test_young_triage_lane_is_untouched(self, tmp_path, idle_lanes):
        from agent.session_revival import cleanup_stale_branches

        repo = _make_repo(tmp_path)
        recent = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
        lane = self._add_lane(repo, "nightly-triage-1a2b3c4d", recent)

        cleaned = await cleanup_stale_branches(str(repo), max_age_hours=72)

        assert cleaned == []
        assert lane.is_dir()
        assert _branch_exists(repo, "session/nightly-triage-1a2b3c4d")

    async def test_other_namespaces_keep_their_worktree(self, tmp_path, idle_lanes):
        """A stale dev lane's branch stays checked out; the sweep only reaps triage lanes."""
        from agent.session_revival import cleanup_stale_branches

        repo = _make_repo(tmp_path)
        old = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        lane = self._add_lane(repo, "dev-a4e15370", old)

        cleaned = await cleanup_stale_branches(str(repo), max_age_hours=72)

        assert cleaned == []
        assert lane.is_dir()
        assert _branch_exists(repo, "session/dev-a4e15370")
