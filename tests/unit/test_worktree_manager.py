"""Unit tests for agent/worktree_manager.py — worktree lifecycle and cleanup."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.worktree_manager import (
    _cleanup_stale_worktree,
    _find_worktree_for_branch,
    _validate_slug,
    cleanup_after_merge,
    create_worktree,
)


class TestValidateSlug:
    """Tests for slug validation."""

    def test_valid_slugs(self):
        # Should not raise
        for slug in ["my-feature", "fix_bug", "v2.0", "Feature123"]:
            _validate_slug(slug)

    def test_empty_slug_raises(self):
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("")

    def test_path_traversal_raises(self):
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("../etc/passwd")

    def test_slash_in_slug_raises(self):
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("session/my-feature")

    def test_leading_dot_raises(self):
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug(".hidden")


class TestCleanupAfterMerge:
    """Tests for cleanup_after_merge function."""

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_worktree_and_branch_exist(
        self, mock_remove_wt, mock_branch_exists, mock_run
    ):
        """When both worktree and branch exist, both get cleaned up."""
        repo = Path("/fake/repo")
        slug = "my-feature"

        # Worktree directory exists
        with patch.object(Path, "exists", return_value=True):
            mock_remove_wt.return_value = True
            mock_branch_exists.return_value = True
            mock_run.return_value = MagicMock(returncode=0)

            result = cleanup_after_merge(repo, slug)

        assert result["slug"] == slug
        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is True
        assert result["already_clean"] is False

        # remove_worktree was called with delete_branch=False
        mock_remove_wt.assert_called_once_with(repo, slug, delete_branch=False)

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_worktree_gone_branch_exists(
        self, mock_remove_wt, mock_branch_exists, mock_run
    ):
        """When worktree is already removed but branch lingers."""
        repo = Path("/fake/repo")
        slug = "old-feature"

        mock_branch_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        result = cleanup_after_merge(repo, slug)

        assert result["worktree_removed"] is False
        assert result["branch_deleted"] is True
        assert result["already_clean"] is False

        # remove_worktree should NOT be called (worktree dir doesn't exist)
        mock_remove_wt.assert_not_called()

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_everything_already_clean(
        self, mock_remove_wt, mock_branch_exists, mock_run
    ):
        """When nothing needs cleanup (worktree gone, branch gone)."""
        repo = Path("/fake/repo")
        slug = "done-feature"

        mock_branch_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=0)

        result = cleanup_after_merge(repo, slug)

        assert result["worktree_removed"] is False
        assert result["branch_deleted"] is False
        assert result["already_clean"] is True
        mock_remove_wt.assert_not_called()

    def test_invalid_slug_raises(self):
        """Invalid slugs are rejected before any cleanup attempt."""
        repo = Path("/fake/repo")
        with pytest.raises(ValueError, match="Invalid slug"):
            cleanup_after_merge(repo, "../bad")

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_worktree_removal_fails_still_tries_branch(
        self, mock_remove_wt, mock_branch_exists, mock_run
    ):
        """If worktree removal fails, we still attempt branch deletion."""
        repo = Path("/fake/repo")
        slug = "stuck-feature"

        with patch.object(Path, "exists", return_value=True):
            mock_remove_wt.return_value = False  # removal failed
            mock_branch_exists.return_value = True
            mock_run.return_value = MagicMock(returncode=0)

            result = cleanup_after_merge(repo, slug)

        assert result["worktree_removed"] is False
        assert result["branch_deleted"] is True
        assert result["already_clean"] is False
        assert "Failed to remove worktree" in result["errors"][0]

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_branch_deletion_fails(self, mock_remove_wt, mock_branch_exists, mock_run):
        """If branch deletion fails, result reflects failure (not already_clean)."""
        repo = Path("/fake/repo")
        slug = "protected-feature"

        mock_branch_exists.return_value = True
        # First call is prune_worktrees, second is branch -D
        mock_run.side_effect = [
            MagicMock(returncode=0),  # prune
            MagicMock(returncode=1, stderr="error: branch not found"),  # branch -D
        ]

        result = cleanup_after_merge(repo, slug)

        assert result["worktree_removed"] is False
        assert result["branch_deleted"] is False
        # Not already_clean: the branch existed but deletion failed.
        # already_clean is only True when nothing needed cleanup at all.
        assert result["already_clean"] is False
        assert len(result["errors"]) == 1
        assert "Failed to delete branch" in result["errors"][0]

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_worktree_removal_failure_recorded_in_errors(
        self, mock_remove_wt, mock_branch_exists, mock_run
    ):
        """When worktree removal fails, the error is recorded."""
        repo = Path("/fake/repo")
        slug = "error-feature"

        with patch.object(Path, "exists", return_value=True):
            mock_remove_wt.return_value = False  # removal failed
            mock_branch_exists.return_value = False
            mock_run.return_value = MagicMock(returncode=0)

            result = cleanup_after_merge(repo, slug)

        assert result["worktree_removed"] is False
        assert result["already_clean"] is False
        assert len(result["errors"]) == 1
        assert "Failed to remove worktree" in result["errors"][0]

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_errors_empty_on_success(
        self, mock_remove_wt, mock_branch_exists, mock_run
    ):
        """Successful cleanup has an empty errors list."""
        repo = Path("/fake/repo")
        slug = "clean-feature"

        mock_branch_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=0)

        result = cleanup_after_merge(repo, slug)

        assert result["errors"] == []

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_prune_is_always_called(self, mock_remove_wt, mock_branch_exists, mock_run):
        """Prune is called regardless of worktree state."""
        repo = Path("/fake/repo")
        slug = "any-feature"

        mock_branch_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=0)

        cleanup_after_merge(repo, slug)

        # prune_worktrees runs subprocess with "git worktree prune"
        prune_calls = [c for c in mock_run.call_args_list if "prune" in str(c)]
        assert len(prune_calls) == 1


class TestFindWorktreeForBranch:
    """Tests for _find_worktree_for_branch."""

    @patch("agent.worktree_manager.subprocess.run")
    def test_finds_branch_in_worktree_list(self, mock_run):
        """Returns the worktree path when the branch is found."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "worktree /repo\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /repo/.worktrees/my-feat\n"
                "branch refs/heads/session/my-feat\n"
                "\n"
            ),
        )
        result = _find_worktree_for_branch(Path("/repo"), "session/my-feat")
        assert result == "/repo/.worktrees/my-feat"

    @patch("agent.worktree_manager.subprocess.run")
    def test_returns_none_when_branch_not_found(self, mock_run):
        """Returns None when the branch is not in any worktree."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=("worktree /repo\n" "branch refs/heads/main\n" "\n"),
        )
        result = _find_worktree_for_branch(Path("/repo"), "session/other")
        assert result is None

    @patch("agent.worktree_manager.subprocess.run")
    def test_returns_none_on_git_failure(self, mock_run):
        """Returns None when git command fails."""
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        result = _find_worktree_for_branch(Path("/repo"), "session/feat")
        assert result is None


class TestCleanupStaleWorktree:
    """Tests for _cleanup_stale_worktree."""

    @patch("agent.worktree_manager.prune_worktrees")
    def test_prunes_when_directory_missing(self, mock_prune):
        """When the worktree directory is gone, prune cleans the reference."""
        with patch.object(Path, "exists", return_value=False):
            _cleanup_stale_worktree(
                Path("/repo"), "session/feat", "/repo/.worktrees/feat"
            )
        mock_prune.assert_called_once_with(Path("/repo"))

    @patch("agent.worktree_manager.prune_worktrees")
    @patch("agent.worktree_manager.subprocess.run")
    def test_force_removes_existing_directory(self, mock_run, mock_prune):
        """When the worktree directory exists, force-remove it."""
        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(Path, "exists", return_value=True):
            _cleanup_stale_worktree(
                Path("/repo"), "session/feat", "/repo/.worktrees/old-feat"
            )
        # Should call git worktree remove --force
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "git",
            "worktree",
            "remove",
            "--force",
            "/repo/.worktrees/old-feat",
        ]

    @patch("agent.worktree_manager.shutil.rmtree")
    @patch("agent.worktree_manager.prune_worktrees")
    @patch("agent.worktree_manager.subprocess.run")
    def test_fallback_rmtree_on_force_remove_failure(
        self, mock_run, mock_prune, mock_rmtree
    ):
        """Falls back to rmtree + prune if git worktree remove fails."""
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "git", stderr="lock error")
        with patch.object(Path, "exists", return_value=True):
            _cleanup_stale_worktree(
                Path("/repo"), "session/feat", "/repo/.worktrees/stuck"
            )
        # prune called twice (fallback path)
        assert mock_prune.call_count == 2
        mock_rmtree.assert_called_once()


class TestCreateWorktreeStaleRecovery:
    """Tests for create_worktree with stale worktree recovery."""

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_creates_normally_when_no_stale(
        self, mock_branch_exists, mock_find_wt, mock_run
    ):
        """Normal creation when no stale worktree exists."""
        repo = Path("/fake/repo")
        mock_find_wt.return_value = None
        mock_branch_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=0)

        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir"),
        ):
            result = create_worktree(repo, "my-feature")

        assert result == repo / ".worktrees" / "my-feature"
        # git worktree add should have been called
        assert mock_run.called

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._cleanup_stale_worktree")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_cleans_stale_worktree_at_different_path(
        self, mock_branch_exists, mock_find_wt, mock_cleanup, mock_run
    ):
        """Cleans up stale worktree at a different path before creating."""
        repo = Path("/fake/repo")
        slug = "my-feature"
        stale_path = "/fake/repo/.worktrees/old-my-feature"

        mock_find_wt.return_value = stale_path
        mock_branch_exists.return_value = True  # branch exists after cleanup
        mock_run.return_value = MagicMock(returncode=0)

        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir"),
        ):
            result = create_worktree(repo, slug)

        # Should have called cleanup for the stale worktree
        mock_cleanup.assert_called_once_with(repo, f"session/{slug}", stale_path)
        assert result == repo / ".worktrees" / slug

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager.prune_worktrees")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_prunes_when_git_tracks_missing_dir_at_expected_path(
        self, mock_branch_exists, mock_find_wt, mock_prune, mock_run
    ):
        """Prunes when git tracks a worktree at expected path but dir is gone."""
        repo = Path("/fake/repo")
        slug = "my-feature"
        expected_path = str(repo / ".worktrees" / slug)

        mock_find_wt.return_value = expected_path
        mock_branch_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir"),
        ):
            result = create_worktree(repo, slug)

        # Should have pruned
        mock_prune.assert_called_once_with(repo)
        assert result == repo / ".worktrees" / slug

    @patch("agent.worktree_manager.shutil.copy2")
    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_returns_existing_valid_worktree(
        self, mock_branch_exists, mock_find_wt, mock_run, mock_copy
    ):
        """Returns existing worktree path when directory exists (no-op)."""
        repo = Path("/fake/repo")
        slug = "existing-feat"

        # First exists() check for worktree_dir returns True
        with patch.object(Path, "exists", return_value=True):
            result = create_worktree(repo, slug)

        assert result == repo / ".worktrees" / slug
        # Should NOT have called find or run -- early return
        mock_find_wt.assert_not_called()
        mock_run.assert_not_called()
