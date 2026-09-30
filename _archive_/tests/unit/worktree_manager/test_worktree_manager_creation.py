"""Unit tests for agent/worktree_manager.py — worktree creation and get-or-create."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.worktree_manager import (
    create_worktree,
    get_or_create_worktree,
)


class TestCreateWorktreeStaleRecovery:
    """Tests for create_worktree with stale worktree recovery."""

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_creates_normally_when_no_stale(self, mock_branch_exists, mock_find_wt, mock_run):
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


class TestGetOrCreateWorktree:
    """Tests for get_or_create_worktree — idempotent worktree access."""

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_creates_new_worktree_when_none_exists(
        self, mock_branch_exists, mock_find_wt, mock_run
    ):
        """Creates a fresh worktree when no existing one is found."""
        repo = Path("/fake/repo")
        slug = "new-feature"
        mock_find_wt.return_value = None
        mock_branch_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=0)

        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir"),
        ):
            result = get_or_create_worktree(repo, slug)

        assert result == repo / ".worktrees" / slug
        assert mock_run.called

    @patch("agent.worktree_manager.shutil.copy2")
    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_returns_existing_worktree_without_error(
        self, mock_branch_exists, mock_find_wt, mock_run, mock_copy
    ):
        """Returns existing worktree path when directory already exists (no-op)."""
        repo = Path("/fake/repo")
        slug = "existing-feature"

        with patch.object(Path, "exists", return_value=True):
            result = get_or_create_worktree(repo, slug)

        assert result == repo / ".worktrees" / slug
        # Should NOT have tried to create anything -- early return in create_worktree
        mock_find_wt.assert_not_called()
        mock_run.assert_not_called()

    def test_invalid_slug_raises(self):
        """Invalid slugs are rejected."""
        repo = Path("/fake/repo")
        with pytest.raises(ValueError, match="Invalid slug"):
            get_or_create_worktree(repo, "../bad")

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_passes_base_branch_to_create(self, mock_branch_exists, mock_find_wt, mock_run):
        """Custom base_branch is forwarded to create_worktree."""
        repo = Path("/fake/repo")
        slug = "custom-base"
        mock_find_wt.return_value = None
        mock_branch_exists.return_value = False
        mock_run.return_value = MagicMock(returncode=0)

        with (
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "mkdir"),
        ):
            result = get_or_create_worktree(repo, slug, base_branch="develop")

        assert result == repo / ".worktrees" / slug
        # Verify the git command used "develop" as base branch
        cmd = mock_run.call_args[0][0]
        assert "develop" in cmd
