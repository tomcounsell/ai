"""Unit tests for ensure_clean_git_state() in agent/worktree_manager.py."""

from subprocess import CalledProcessError
from unittest.mock import MagicMock, patch

import pytest

from agent.worktree_manager import (
    _is_worktree,
    _resolve_git_dir,
    ensure_clean_git_state,
)


class TestResolveGitDir:
    """Tests for _resolve_git_dir helper."""

    def test_regular_repo_returns_git_dir(self, tmp_path):
        """Regular repo: .git is a directory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        assert _resolve_git_dir(tmp_path) == git_dir

    def test_worktree_follows_gitdir_pointer(self, tmp_path):
        """Worktree: .git is a file with gitdir pointer."""
        actual_git_dir = tmp_path / "actual_git"
        actual_git_dir.mkdir()
        git_file = tmp_path / ".git"
        git_file.write_text(f"gitdir: {actual_git_dir}\n")
        assert _resolve_git_dir(tmp_path) == actual_git_dir

    def test_worktree_relative_gitdir(self, tmp_path):
        """Worktree: .git file with relative gitdir path."""
        actual_git_dir = tmp_path / "repo" / ".git" / "worktrees" / "feat"
        actual_git_dir.mkdir(parents=True)
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        git_file = wt_dir / ".git"
        git_file.write_text("gitdir: ../repo/.git/worktrees/feat\n")
        result = _resolve_git_dir(wt_dir)
        assert result == actual_git_dir.resolve()

    def test_no_git_raises_valueerror(self, tmp_path):
        """No .git at all raises ValueError."""
        with pytest.raises(ValueError, match="Not a git repository"):
            _resolve_git_dir(tmp_path)

    def test_unexpected_git_file_content_raises(self, tmp_path):
        """Unexpected .git file content raises ValueError."""
        git_file = tmp_path / ".git"
        git_file.write_text("unexpected content\n")
        with pytest.raises(ValueError, match="Unexpected .git file content"):
            _resolve_git_dir(tmp_path)


class TestIsWorktree:
    """Tests for _is_worktree helper."""

    def test_main_repo_is_not_worktree(self, tmp_path):
        """Main repo (.git is a directory) is not a worktree."""
        (tmp_path / ".git").mkdir()
        assert _is_worktree(tmp_path) is False

    def test_worktree_is_detected(self, tmp_path):
        """Worktree (.git is a file) is detected."""
        (tmp_path / ".git").write_text("gitdir: /some/path\n")
        assert _is_worktree(tmp_path) is True

    def test_no_git_is_not_worktree(self, tmp_path):
        """Directory without .git is not a worktree."""
        assert _is_worktree(tmp_path) is False


class TestEnsureCleanGitState:
    """Tests for ensure_clean_git_state."""

    def _make_repo(self, tmp_path):
        """Create a fake repo root with a .git directory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        return tmp_path, git_dir

    def test_clean_state_is_noop(self, tmp_path):
        """Clean repo returns was_clean=True with no actions."""
        repo, git_dir = self._make_repo(tmp_path)

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            # git status --porcelain returns empty
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = ensure_clean_git_state(repo)

        assert result["was_clean"] is True
        assert result["merge_aborted"] is False
        assert result["rebase_aborted"] is False
        assert result["cherry_pick_aborted"] is False
        assert result["changes_stashed"] is False
        assert result["errors"] == []

    def test_skips_worktree(self, tmp_path):
        """Worktree directories are skipped entirely."""
        (tmp_path / ".git").write_text("gitdir: /some/path\n")

        result = ensure_clean_git_state(tmp_path)

        assert result["skipped"] is True
        assert result["was_clean"] is False

    def test_aborts_in_progress_merge(self, tmp_path):
        """Detects and aborts in-progress merge."""
        repo, git_dir = self._make_repo(tmp_path)
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            # First call: git merge --abort (success)
            # Second call: git status --porcelain (clean after abort)
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),  # merge --abort
                MagicMock(returncode=0, stdout="", stderr=""),  # status
            ]
            result = ensure_clean_git_state(repo)

        assert result["merge_aborted"] is True
        assert result["was_clean"] is False
        assert result["errors"] == []
        # Verify git merge --abort was called
        merge_call = mock_run.call_args_list[0]
        assert merge_call[0][0] == ["git", "merge", "--abort"]

    def test_aborts_in_progress_rebase_merge(self, tmp_path):
        """Detects and aborts in-progress rebase (rebase-merge dir)."""
        repo, git_dir = self._make_repo(tmp_path)
        (git_dir / "rebase-merge").mkdir()

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),  # rebase --abort
                MagicMock(returncode=0, stdout="", stderr=""),  # status
            ]
            result = ensure_clean_git_state(repo)

        assert result["rebase_aborted"] is True
        assert result["was_clean"] is False
        rebase_call = mock_run.call_args_list[0]
        assert rebase_call[0][0] == ["git", "rebase", "--abort"]

    def test_aborts_in_progress_rebase_apply(self, tmp_path):
        """Detects and aborts in-progress rebase (rebase-apply dir)."""
        repo, git_dir = self._make_repo(tmp_path)
        (git_dir / "rebase-apply").mkdir()

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),  # rebase --abort
                MagicMock(returncode=0, stdout="", stderr=""),  # status
            ]
            result = ensure_clean_git_state(repo)

        assert result["rebase_aborted"] is True

    def test_aborts_in_progress_cherry_pick(self, tmp_path):
        """Detects and aborts in-progress cherry-pick."""
        repo, git_dir = self._make_repo(tmp_path)
        (git_dir / "CHERRY_PICK_HEAD").write_text("abc123\n")

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),  # cherry-pick --abort
                MagicMock(returncode=0, stdout="", stderr=""),  # status
            ]
            result = ensure_clean_git_state(repo)

        assert result["cherry_pick_aborted"] is True
        cherry_call = mock_run.call_args_list[0]
        assert cherry_call[0][0] == ["git", "cherry-pick", "--abort"]

    def test_stashes_uncommitted_changes(self, tmp_path):
        """Stashes uncommitted changes when no merge/rebase/cherry-pick in progress."""
        repo, git_dir = self._make_repo(tmp_path)

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.side_effect = [
                # git status --porcelain returns changes
                MagicMock(returncode=0, stdout=" M file.py\n", stderr=""),
                # git stash push
                MagicMock(returncode=0, stdout="", stderr=""),
            ]
            result = ensure_clean_git_state(repo)

        assert result["changes_stashed"] is True
        assert result["stash_name"] == "sdlc-auto-stash"
        assert result["was_clean"] is False
        stash_call = mock_run.call_args_list[1]
        assert stash_call[0][0] == ["git", "stash", "push", "-m", "sdlc-auto-stash"]

    def test_combined_merge_and_uncommitted(self, tmp_path):
        """Handles merge in progress + uncommitted changes together."""
        repo, git_dir = self._make_repo(tmp_path)
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),  # merge --abort
                MagicMock(returncode=0, stdout=" M file.py\n", stderr=""),  # status
                MagicMock(returncode=0, stdout="", stderr=""),  # stash
            ]
            result = ensure_clean_git_state(repo)

        assert result["merge_aborted"] is True
        assert result["changes_stashed"] is True
        assert result["errors"] == []

    def test_merge_abort_failure_raises(self, tmp_path):
        """When merge --abort fails, raises ValueError."""
        repo, git_dir = self._make_repo(tmp_path)
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.side_effect = [
                CalledProcessError(1, "git", stderr="abort failed"),  # merge --abort
                MagicMock(returncode=0, stdout="", stderr=""),  # status (clean)
            ]
            with pytest.raises(ValueError, match="could not fully clean"):
                ensure_clean_git_state(repo)

    def test_rebase_abort_failure_raises(self, tmp_path):
        """When rebase --abort fails, raises ValueError."""
        repo, git_dir = self._make_repo(tmp_path)
        (git_dir / "rebase-merge").mkdir()

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.side_effect = [
                CalledProcessError(1, "git", stderr="abort failed"),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]
            with pytest.raises(ValueError, match="could not fully clean"):
                ensure_clean_git_state(repo)

    def test_stash_failure_raises(self, tmp_path):
        """When stash fails, raises ValueError."""
        repo, git_dir = self._make_repo(tmp_path)

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=" M file.py\n", stderr=""),  # status
                CalledProcessError(1, "git", stderr="stash failed"),  # stash
            ]
            with pytest.raises(ValueError, match="could not fully clean"):
                ensure_clean_git_state(repo)

    def test_no_git_dir_raises(self, tmp_path):
        """Raises ValueError when no .git found."""
        with pytest.raises(ValueError, match="Cannot guard git state"):
            ensure_clean_git_state(tmp_path)

    def test_returns_structured_dict(self, tmp_path):
        """Result dict has all expected keys."""
        repo, git_dir = self._make_repo(tmp_path)

        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = ensure_clean_git_state(repo)

        expected_keys = {
            "skipped",
            "merge_aborted",
            "rebase_aborted",
            "cherry_pick_aborted",
            "changes_stashed",
            "stash_name",
            "errors",
            "was_clean",
        }
        assert set(result.keys()) == expected_keys
