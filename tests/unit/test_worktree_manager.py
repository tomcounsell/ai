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
    get_or_create_worktree,
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
    def test_worktree_and_branch_exist(self, mock_remove_wt, mock_branch_exists, mock_run):
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
    def test_worktree_gone_branch_exists(self, mock_remove_wt, mock_branch_exists, mock_run):
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
    def test_everything_already_clean(self, mock_remove_wt, mock_branch_exists, mock_run):
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
    def test_errors_empty_on_success(self, mock_remove_wt, mock_branch_exists, mock_run):
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
            stdout=("worktree /repo\nbranch refs/heads/main\n\n"),
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
            _cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo/.worktrees/feat")
        mock_prune.assert_called_once_with(Path("/repo"))

    @patch("agent.worktree_manager.prune_worktrees")
    @patch("agent.worktree_manager.subprocess.run")
    def test_force_removes_existing_directory(self, mock_run, mock_prune):
        """When the worktree directory exists, force-remove it."""
        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(Path, "exists", return_value=True):
            _cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo/.worktrees/old-feat")
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
    def test_fallback_rmtree_on_force_remove_failure(self, mock_run, mock_prune, mock_rmtree):
        """Falls back to rmtree + prune if git worktree remove fails."""
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "git", stderr="lock error")
        with patch.object(Path, "exists", return_value=True):
            _cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo/.worktrees/stuck")
        # prune called twice (fallback path)
        assert mock_prune.call_count == 2
        mock_rmtree.assert_called_once()
        # Tightened assertion for #880: ignore_errors must NOT be True.
        # Silent partial destruction was the primary bug class; fail loud.
        assert mock_rmtree.call_args.kwargs.get("ignore_errors") is not True

    def test_guard_rejects_repo_root_path(self):
        """Guard raises RuntimeError when worktree_path resolves to repo_root itself.

        This is the exact path from the 2026-04-10 incident (issue #880):
        a session branch got checked out in the main working tree, and the
        helper was called with ``worktree_path == repo_root``. The guard
        must refuse and raise loudly.

        Uses ``match=r"not under"`` instead of a literal path substring
        because ``.resolve()`` may follow platform symlinks (C3).
        """
        with pytest.raises(RuntimeError, match=r"not under"):
            _cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo")

    def test_guard_rejects_path_outside_worktrees(self):
        """Guard raises RuntimeError when worktree_path is outside the repo.

        C3: MUST use ``match=r"not under"`` -- on macOS ``/tmp`` is a
        symlink to ``/private/tmp``, so ``Path("/tmp/foo").resolve()``
        returns ``/private/tmp/foo``. Any test asserting a literal
        ``"/tmp/foo"`` substring fails on macOS but passes on Linux.
        The ``"not under"`` phrase is platform-stable.
        """
        with pytest.raises(RuntimeError, match=r"not under"):
            _cleanup_stale_worktree(Path("/repo"), "session/feat", "/tmp/foo")

    def test_guard_rejects_sibling_dir_under_repo(self):
        """Guard rejects paths inside repo_root but outside ``.worktrees/``.

        A path that is under the repo but not under ``.worktrees/`` is
        just as dangerous as a path outside the repo entirely -- the
        helper should never recurse into arbitrary repo subdirs.
        """
        with pytest.raises(RuntimeError, match=r"not under"):
            _cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo/some-other-dir")

    @patch("agent.worktree_manager.shutil.rmtree")
    @patch("agent.worktree_manager.prune_worktrees")
    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager.logger")
    def test_fallback_does_not_pass_ignore_errors(
        self, mock_logger, mock_run, mock_prune, mock_rmtree
    ):
        """Fallback branch fires logger.critical before rmtree and does not
        swallow errors via ``ignore_errors=True``.

        Asserts C4 ordering: ``logger.error`` -> ``logger.critical`` ->
        ``prune_worktrees`` -> ``rmtree``. The critical log MUST precede
        ``prune_worktrees`` so a prune exception cannot swallow the
        crash-tracker signal.
        """
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "git", stderr="lock error")
        with patch.object(Path, "exists", return_value=True):
            _cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo/.worktrees/stuck")

        # ignore_errors must NOT be passed as True (C1 / #880).
        assert mock_rmtree.call_args.kwargs.get("ignore_errors") is not True

        # logger.critical must have been called in the fallback branch.
        assert mock_logger.critical.called, (
            "logger.critical must fire in fallback branch for crash-tracker "
            "correlation (see issue #880)"
        )

        # C4: call order must be logger.error -> logger.critical ->
        # prune_worktrees -> rmtree. We verify this by inspecting
        # mock_logger.mock_calls and the relative call ordering of the
        # separately-patched mocks.
        method_names = [
            call[0] for call in mock_logger.mock_calls if call[0] in ("error", "critical")
        ]
        assert method_names[:2] == ["error", "critical"], (
            f"Expected logger.error then logger.critical, got {method_names}"
        )

        # logger.critical must fire BEFORE prune_worktrees (C4). Compare
        # mock_calls list positions using an ordering-sensitive Mock parent.
        parent = MagicMock()
        parent.attach_mock(mock_logger.critical, "critical")
        parent.attach_mock(mock_prune, "prune")
        parent.attach_mock(mock_rmtree, "rmtree")
        # Re-run under the ordering mock to validate sequencing.
        mock_run.side_effect = CalledProcessError(1, "git", stderr="lock error")
        parent.reset_mock()
        mock_logger.reset_mock()
        mock_prune.reset_mock()
        mock_rmtree.reset_mock()
        with patch.object(Path, "exists", return_value=True):
            _cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo/.worktrees/stuck")
        ordered = [c[0] for c in parent.mock_calls]
        # critical must appear before prune; prune must appear before rmtree.
        assert "critical" in ordered and "prune" in ordered and "rmtree" in ordered
        assert ordered.index("critical") < ordered.index("prune"), (
            f"logger.critical must precede prune_worktrees (C4). Order: {ordered}"
        )
        assert ordered.index("prune") < ordered.index("rmtree"), (
            f"prune_worktrees must precede rmtree fallback. Order: {ordered}"
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
