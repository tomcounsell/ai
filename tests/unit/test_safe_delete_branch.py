"""Tests for safe_delete_branch, merged_via_ancestor, and merged_via_tree.

These tests use real temporary git repos to verify the oracle functions and
the safe_delete_branch helper. Covering both oracles plus the incident
regression (ec1e7c6e) and the squash-merge edge cases.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from agent.worktree_manager import (
    merged_via_ancestor,
    merged_via_tree,
    safe_delete_branch,
)


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
    """Create a minimal git repo with an initial commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")
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
# merged_via_ancestor tests
# ---------------------------------------------------------------------------


class TestMergedViaAncestor:
    def test_true_merged_branch(self, tmp_path):
        """A branch merged to main returns True."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "feature")
        (repo / "f.txt").write_text("feature\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-m", "feature commit")
        # Merge into main (non-squash)
        _git(repo, "checkout", "main")
        _git(repo, "merge", "feature", "--no-ff", "-m", "merge feature")
        assert merged_via_ancestor(str(repo), "feature", "main") is True

    def test_unmerged_branch(self, tmp_path):
        """An unmerged branch returns False."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/dev-ec1e7c6e")
        (repo / "work.txt").write_text("unmerged work\n")
        _git(repo, "add", "work.txt")
        _git(repo, "commit", "-m", "feat: unmerged work")
        _git(repo, "checkout", "main")
        assert merged_via_ancestor(str(repo), "session/dev-ec1e7c6e", "main") is False

    def test_squash_merged_branch_returns_false(self, tmp_path):
        """A squash-merged branch returns False — proving why squash sites cannot use this oracle."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/sdlc-1234")
        # Two commits (>=2 is mandatory per plan — single commit may coincidentally pass cherry)
        (repo / "a.txt").write_text("commit 1\n")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-m", "commit 1")
        (repo / "b.txt").write_text("commit 2\n")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-m", "commit 2")
        # Squash merge
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", "session/sdlc-1234")
        _git(repo, "commit", "-m", "squash merge sdlc-1234")
        # is-ancestor returns False for a squash-merged branch (the branch tip is NOT in main's ancestry)
        assert merged_via_ancestor(str(repo), "session/sdlc-1234", "main") is False


# ---------------------------------------------------------------------------
# merged_via_tree tests
# ---------------------------------------------------------------------------


class TestMergedViaTree:
    def test_squash_merged_two_commits(self, tmp_path):
        """A >=2-commit squash-merged branch is correctly identified as landed."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/dev-squash")
        (repo / "x.txt").write_text("commit 1\n")
        _git(repo, "add", "x.txt")
        _git(repo, "commit", "-m", "feat: commit 1")
        (repo / "y.txt").write_text("commit 2\n")
        _git(repo, "add", "y.txt")
        _git(repo, "commit", "-m", "feat: commit 2")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", "session/dev-squash")
        _git(repo, "commit", "-m", "squash: session/dev-squash")

        assert merged_via_tree(str(repo), "session/dev-squash", "main") is True

    def test_cherry_oracle_broken_on_squash(self, tmp_path):
        """On a >=2-commit squash fixture, git cherry main branch returns + lines.

        This locks in that the cherry oracle is insufficient and can never be
        silently re-introduced (plan requirement: explicit git cherry assertion).
        """
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/multi-squash")
        (repo / "p.txt").write_text("commit 1\n")
        _git(repo, "add", "p.txt")
        _git(repo, "commit", "-m", "commit 1")
        (repo / "q.txt").write_text("commit 2\n")
        _git(repo, "add", "q.txt")
        _git(repo, "commit", "-m", "commit 2")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", "session/multi-squash")
        _git(repo, "commit", "-m", "squash: multi-squash")

        # Prove the cherry oracle is broken: it should return + lines (reads "unmerged")
        cherry_result = subprocess.run(
            ["git", "cherry", "main", "session/multi-squash"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        cherry_lines = cherry_result.stdout.strip().splitlines()
        assert any(line.startswith("+") for line in cherry_lines), (
            "git cherry should show + (unmerged) for squash-merged branch, "
            f"but got: {cherry_result.stdout!r}"
        )
        # But merged_via_tree correctly identifies it as landed
        assert merged_via_tree(str(repo), "session/multi-squash", "main") is True

    def test_squash_merged_then_main_advances(self, tmp_path):
        """merged_via_tree still returns True after main advances past the squash commit.

        This proves robustness to the moving-main edge case that breaks naive tree-equality.
        """
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/advance-test")
        (repo / "f1.txt").write_text("file 1\n")
        _git(repo, "add", "f1.txt")
        _git(repo, "commit", "-m", "feat: file 1")
        (repo / "f2.txt").write_text("file 2\n")
        _git(repo, "add", "f2.txt")
        _git(repo, "commit", "-m", "feat: file 2")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", "session/advance-test")
        _git(repo, "commit", "-m", "squash: advance-test")

        # Advance main with a new commit unrelated to the branch
        (repo / "extra.txt").write_text("extra commit\n")
        _git(repo, "add", "extra.txt")
        _git(repo, "commit", "-m", "chore: extra commit after squash")

        # merged_via_tree must still return True (branch is fully landed)
        assert merged_via_tree(str(repo), "session/advance-test", "main") is True

    def test_truly_unmerged_branch(self, tmp_path):
        """An unmerged branch returns False."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/unmerged")
        (repo / "unmerged.txt").write_text("not landed\n")
        _git(repo, "add", "unmerged.txt")
        _git(repo, "commit", "-m", "feat: unmerged work")
        _git(repo, "checkout", "main")

        assert merged_via_tree(str(repo), "session/unmerged", "main") is False

    def test_conflicting_branch_returns_false(self, tmp_path):
        """A branch that conflicts with main returns False (fail-safe)."""
        repo = _make_repo(tmp_path)
        # Create conflict: both branches modify the same file differently
        _git(repo, "checkout", "-b", "session/conflict")
        (repo / "README.md").write_text("branch version\n")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-m", "branch version")
        _git(repo, "checkout", "main")
        (repo / "README.md").write_text("main version\n")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-m", "main version")

        assert merged_via_tree(str(repo), "session/conflict", "main") is False

    def test_is_ancestor_refuses_squash_merged(self, tmp_path):
        """merged_via_ancestor returns False for squash-merged branch, while merged_via_tree returns True.

        This is the key differentiation test proving the two oracles serve different contexts.
        """
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/squash-diff")
        (repo / "a.txt").write_text("a\n")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-m", "commit a")
        (repo / "b.txt").write_text("b\n")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-m", "commit b")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", "session/squash-diff")
        _git(repo, "commit", "-m", "squash: squash-diff")

        # is-ancestor REFUSES the squash-merged branch
        assert merged_via_ancestor(str(repo), "session/squash-diff", "main") is False
        # merged_via_tree ACCEPTS it (correct oracle for squash sites)
        assert merged_via_tree(str(repo), "session/squash-diff", "main") is True


# ---------------------------------------------------------------------------
# safe_delete_branch tests
# ---------------------------------------------------------------------------


class TestSafeDeleteBranch:
    def test_deletes_ancestor_merged_branch(self, tmp_path):
        """merged_via_ancestor: a non-squash merged branch gets deleted."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/to-delete")
        (repo / "d.txt").write_text("delete me\n")
        _git(repo, "add", "d.txt")
        _git(repo, "commit", "-m", "commit")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "session/to-delete", "--no-ff", "-m", "merge")

        result = safe_delete_branch(
            str(repo), "session/to-delete", predicate=merged_via_ancestor, force=False
        )
        assert result["deleted"] is True
        assert result["skipped_unmerged"] is False
        assert not _branch_exists(repo, "session/to-delete")

    def test_preserves_unmerged_branch_via_ancestor(self, tmp_path, caplog):
        """merged_via_ancestor: an unmerged branch is preserved with [unmerged-branch-guard] log."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/dev-ec1e7c6e")
        (repo / "work.txt").write_text("unmerged work\n")
        _git(repo, "add", "work.txt")
        _git(repo, "commit", "-m", "feat(image_gen): make gpt-image-1 the default provider")
        _git(repo, "checkout", "main")

        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            result = safe_delete_branch(
                str(repo), "session/dev-ec1e7c6e", predicate=merged_via_ancestor, force=False
            )

        assert result["deleted"] is False
        assert result["skipped_unmerged"] is True
        # Branch must still exist (the incident is prevented)
        assert _branch_exists(repo, "session/dev-ec1e7c6e"), (
            "Branch session/dev-ec1e7c6e should be preserved after unmerged guard"
        )
        # Log line must be greppable
        assert any("[unmerged-branch-guard]" in record.message for record in caplog.records), (
            f"Expected [unmerged-branch-guard] log line, got: {[r.message for r in caplog.records]}"
        )

    def test_deletes_squash_merged_branch_via_tree(self, tmp_path):
        """merged_via_tree: a squash-merged >=2-commit branch gets deleted."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/squash-del")
        (repo / "s1.txt").write_text("squash 1\n")
        _git(repo, "add", "s1.txt")
        _git(repo, "commit", "-m", "squash commit 1")
        (repo / "s2.txt").write_text("squash 2\n")
        _git(repo, "add", "s2.txt")
        _git(repo, "commit", "-m", "squash commit 2")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", "session/squash-del")
        _git(repo, "commit", "-m", "squash: squash-del")

        result = safe_delete_branch(
            str(repo), "session/squash-del", predicate=merged_via_tree, force=True
        )
        assert result["deleted"] is True
        assert result["skipped_unmerged"] is False
        assert not _branch_exists(repo, "session/squash-del")

    def test_preserves_truly_unmerged_via_tree(self, tmp_path, caplog):
        """merged_via_tree: an unmerged branch is preserved."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/stale-unmerged")
        (repo / "u.txt").write_text("unmerged\n")
        _git(repo, "add", "u.txt")
        _git(repo, "commit", "-m", "unmerged work")
        _git(repo, "checkout", "main")

        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            result = safe_delete_branch(
                str(repo), "session/stale-unmerged", predicate=merged_via_tree, force=True
            )

        assert result["deleted"] is False
        assert result["skipped_unmerged"] is True
        assert _branch_exists(repo, "session/stale-unmerged")
        assert any("[unmerged-branch-guard]" in r.message for r in caplog.records)

    def test_missing_branch_returns_error_no_exception(self, tmp_path):
        """Non-existent branch returns error result without raising."""
        repo = _make_repo(tmp_path)
        result = safe_delete_branch(
            str(repo), "session/does-not-exist", predicate=merged_via_ancestor, force=False
        )
        # Should not raise; returns a structured result
        assert isinstance(result, dict)
        assert result["deleted"] is False
        # Either an error or skipped_unmerged (predicate may fail or git branch -d fails)
        assert result.get("error") is not None or result["skipped_unmerged"] is True

    def test_unresolvable_base_fails_safe(self, tmp_path, caplog):
        """When base branch cannot be resolved, deletion is refused (fail-safe)."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/test-failsafe")
        (repo / "t.txt").write_text("test\n")
        _git(repo, "add", "t.txt")
        _git(repo, "commit", "-m", "test commit")
        _git(repo, "checkout", "main")

        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            result = safe_delete_branch(
                str(repo),
                "session/test-failsafe",
                base="nonexistent-base-branch-xyz",
                predicate=merged_via_ancestor,
                force=False,
            )

        assert result["deleted"] is False
        assert result["skipped_unmerged"] is True
        assert result["error"] is not None
        # Branch must still exist
        assert _branch_exists(repo, "session/test-failsafe")

    def test_squash_then_main_advances_still_deletes(self, tmp_path):
        """merged_via_tree deletes correctly even after main advances past squash commit."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/advance-del")
        (repo / "m1.txt").write_text("m1\n")
        _git(repo, "add", "m1.txt")
        _git(repo, "commit", "-m", "m1")
        (repo / "m2.txt").write_text("m2\n")
        _git(repo, "add", "m2.txt")
        _git(repo, "commit", "-m", "m2")
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--squash", "session/advance-del")
        _git(repo, "commit", "-m", "squash: advance-del")
        # Advance main
        (repo / "extra.txt").write_text("extra\n")
        _git(repo, "add", "extra.txt")
        _git(repo, "commit", "-m", "chore: advance main")

        result = safe_delete_branch(
            str(repo), "session/advance-del", predicate=merged_via_tree, force=True
        )
        assert result["deleted"] is True
        assert not _branch_exists(repo, "session/advance-del")


# ---------------------------------------------------------------------------
# Regression test reproducing incident ec1e7c6e
# ---------------------------------------------------------------------------


class TestIncidentRegression:
    """Regression: dev session branch with unmerged commit survives cleanup (incident ec1e7c6e)."""

    def test_unmerged_branch_survives_auto_mark_cleanup(self, tmp_path, caplog):
        """Reproduces the incident: safe_delete_branch with merged_via_ancestor preserves unmerged work.

        The incident: session ec1e7c6e committed work to session/dev-ec1e7c6e,
        then git branch -D was called unconditionally, destroying the commit.
        This test verifies the guard prevents that.
        """
        repo = _make_repo(tmp_path)
        # Simulate a dev session branch with >=2 commits (matching real-world pattern)
        branch = "session/dev-ec1e7c6e"
        _git(repo, "checkout", "-b", branch)
        (repo / "image_gen.py").write_text("# gpt-image-1 as default\n")
        _git(repo, "add", "image_gen.py")
        _git(repo, "commit", "-m", "feat(image_gen): make gpt-image-1 the default provider")
        (repo / "image_gen_config.py").write_text("DEFAULT_PROVIDER = 'gpt-image-1'\n")
        _git(repo, "add", "image_gen_config.py")
        _git(repo, "commit", "-m", "feat(image_gen): add config module")
        _git(repo, "checkout", "main")

        # The auto-mark path uses merged_via_ancestor (no prior merge exists)
        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            result = safe_delete_branch(
                str(repo), branch, predicate=merged_via_ancestor, force=False
            )

        # The branch must be preserved
        assert result["deleted"] is False, (
            "Guard failed: branch was deleted despite unmerged commits"
        )
        assert result["skipped_unmerged"] is True
        assert _branch_exists(repo, branch), (
            f"Branch {branch} was deleted — incident would have recurred"
        )
        # Commits must still be reachable
        log_result = subprocess.run(
            ["git", "log", "--oneline", branch],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert "gpt-image-1" in log_result.stdout, (
            f"Commits not reachable on {branch}: {log_result.stdout!r}"
        )
        # Greppable log line
        assert any("[unmerged-branch-guard]" in r.message for r in caplog.records), (
            "Expected [unmerged-branch-guard] log line"
        )

    def test_merged_branch_still_deleted(self, tmp_path):
        """Positive case: a merged branch still gets cleaned up (no regression in SDLC happy path)."""
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-b", "session/sdlc-merged")
        (repo / "feature.py").write_text("# feature\n")
        _git(repo, "add", "feature.py")
        _git(repo, "commit", "-m", "feat: feature commit 1")
        (repo / "feature2.py").write_text("# feature 2\n")
        _git(repo, "add", "feature2.py")
        _git(repo, "commit", "-m", "feat: feature commit 2")
        _git(repo, "checkout", "main")
        # Squash merge (production pattern)
        _git(repo, "merge", "--squash", "session/sdlc-merged")
        _git(repo, "commit", "-m", "squash: sdlc-merged")

        # The squash sites use merged_via_tree
        result = safe_delete_branch(
            str(repo), "session/sdlc-merged", predicate=merged_via_tree, force=True
        )
        assert result["deleted"] is True, "Merged branch should be deleted"
        assert not _branch_exists(repo, "session/sdlc-merged")
