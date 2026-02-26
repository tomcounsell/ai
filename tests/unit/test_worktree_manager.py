"""Unit tests for agent/worktree_manager.py — cleanup_after_merge."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.worktree_manager import (
    _validate_slug,
    cleanup_after_merge,
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

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_branch_deletion_fails(self, mock_remove_wt, mock_branch_exists, mock_run):
        """If branch deletion fails, result reflects that."""
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
        # Not already_clean because we attempted work (branch existed)
        # but the deletion failed, so neither flag is True
        assert result["already_clean"] is True

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
