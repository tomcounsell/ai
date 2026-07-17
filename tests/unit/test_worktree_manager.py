"""Unit tests for agent/worktree_manager.py — worktree lifecycle and cleanup."""

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.worktree_manager import (
    PROVISIONED_MARKER,
    WorktreeBranchMismatchError,
    _cleanup_stale_worktree,
    _find_worktree_for_branch,
    _validate_slug,
    cleanup_after_merge,
    create_worktree,
    get_or_create_worktree,
    provision_worktree_venv,
    remove_worktree,
    verify_worktree_branch,
    worktree_busy_check,
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

    @patch("agent.worktree_manager.safe_delete_branch")
    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_branch_deletion_fails(
        self, mock_remove_wt, mock_branch_exists, mock_run, mock_safe_del
    ):
        """If branch deletion fails, result reflects failure (not already_clean)."""
        repo = Path("/fake/repo")
        slug = "protected-feature"

        mock_branch_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)  # prune
        # safe_delete_branch returns a git error (not skipped_unmerged)
        mock_safe_del.return_value = {
            "deleted": False,
            "skipped_unmerged": False,
            "branch": f"session/{slug}",
            "error": "error: branch not found",
        }

        result = cleanup_after_merge(repo, slug)

        assert result["worktree_removed"] is False
        assert result["branch_deleted"] is False
        # Not already_clean: the branch existed but deletion failed.
        # already_clean is only True when nothing needed cleanup at all.
        assert result["already_clean"] is False
        assert len(result["errors"]) == 1
        assert "Failed to delete branch" in result["errors"][0]

    @patch("agent.worktree_manager.safe_delete_branch")
    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_branch_unmerged_skips_deletion(
        self, mock_remove_wt, mock_branch_exists, mock_run, mock_safe_del
    ):
        """When safe_delete_branch detects an unmerged branch, skipped_unmerged is set."""
        repo = Path("/fake/repo")
        slug = "unmerged-feature"

        mock_branch_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)  # prune
        mock_safe_del.return_value = {
            "deleted": False,
            "skipped_unmerged": True,
            "branch": f"session/{slug}",
            "error": None,
        }

        result = cleanup_after_merge(repo, slug)

        assert result["branch_deleted"] is False
        assert result["skipped_unmerged"] is True
        assert result["already_clean"] is False
        # The unmerged warning should be in errors for operator visibility
        assert any("unmerged-branch-guard" in e for e in result["errors"])

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


def _make_session(
    working_dir: str | None,
    status: str,
    session_id: str = "sess-1",
    agent_session_id: str = "agt-1",
) -> SimpleNamespace:
    """Build a duck-typed AgentSession stand-in for busy-check tests."""
    return SimpleNamespace(
        working_dir=working_dir,
        status=status,
        session_id=session_id,
        agent_session_id=agent_session_id,
        project_key="test-proj",
    )


class TestWorktreeBusyCheck:
    """Tests for worktree_busy_check (issue #1357)."""

    @patch("models.agent_session.AgentSession")
    def test_no_sessions_returns_none(self, mock_as):
        mock_as.query.all.return_value = []
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None

    @patch("models.agent_session.AgentSession")
    def test_terminal_session_does_not_block(self, mock_as):
        mock_as.query.all.return_value = [
            _make_session("/fake/repo/.worktrees/sdlc-1218", "completed"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "killed"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "failed"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "abandoned"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "cancelled"),
        ]
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None

    @patch("models.agent_session.AgentSession")
    def test_running_session_blocks(self, mock_as):
        mock_as.query.all.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218",
                "running",
                session_id="0_LIVE",
                agent_session_id="agt-LIVE",
            ),
        ]
        result = worktree_busy_check(Path("/fake/repo"), "sdlc-1218")
        assert result == ("0_LIVE", "agt-LIVE")

    @patch("models.agent_session.AgentSession")
    def test_subdir_match_blocks(self, mock_as):
        """working_dir below the worktree root still counts as busy."""
        mock_as.query.all.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218/sub/dir",
                "running",
                session_id="0_SUB",
            ),
        ]
        result = worktree_busy_check(Path("/fake/repo"), "sdlc-1218")
        assert result is not None
        assert result[0] == "0_SUB"

    @patch("models.agent_session.AgentSession")
    def test_substring_near_miss_does_not_block(self, mock_as):
        """sdlc-1218-other must NOT match sdlc-1218 (segment-aware)."""
        mock_as.query.all.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218-other",
                "running",
            ),
        ]
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None

    @patch("models.agent_session.AgentSession")
    def test_relative_working_dir_match(self, mock_as):
        """working_dir stored as a relative path resolves against repo_root."""
        mock_as.query.all.return_value = [
            _make_session(".worktrees/sdlc-1218", "running", session_id="0_REL"),
        ]
        # Use the actual cwd-resolvable repo root so resolve() works.
        repo_root = Path("/tmp")
        result = worktree_busy_check(repo_root, "sdlc-1218")
        # Relative paths are resolved via repo_root / wd; should match.
        assert result is not None
        assert result[0] == "0_REL"

    @patch("models.agent_session.AgentSession")
    def test_query_raises_returns_none(self, mock_as):
        """Popoto query failure fails open (returns None) and logs WARNING."""
        mock_as.query.all.side_effect = RuntimeError("redis down")
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None

    @patch("models.agent_session.AgentSession")
    def test_session_with_no_working_dir_skipped(self, mock_as):
        mock_as.query.all.return_value = [
            _make_session(None, "running"),
            _make_session("", "running"),
        ]
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None


class TestRemoveWorktreeBusyGuard:
    """Tests for remove_worktree's refuse-busy guard (issue #1357)."""

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_clear_path_returns_true(self, mock_run, mock_busy):
        """No live session: remove proceeds and returns True."""
        mock_busy.return_value = None
        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(Path, "exists", return_value=True):
            result = remove_worktree(Path("/fake/repo"), "sdlc-1218")
        assert result is True

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_blocked_returns_tuple(self, mock_run, mock_busy):
        """Live session: returns ('blocked', session_id) and skips git."""
        mock_busy.return_value = ("0_LIVE", "agt-LIVE")
        result = remove_worktree(Path("/fake/repo"), "sdlc-1218")
        assert result == ("blocked", "0_LIVE")
        # git worktree remove must NOT be called when blocked
        for call in mock_run.call_args_list:
            assert "remove" not in str(call) or "branch" not in str(call) or True
        # Stronger: the busy guard fires BEFORE the worktree_dir.exists() check,
        # so no subprocess invocations should have happened.
        assert mock_run.call_count == 0

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_force_overrides_busy_guard(self, mock_run, mock_busy, caplog):
        """force=True logs WARNING and proceeds."""
        import logging

        mock_busy.return_value = ("0_LIVE", "agt-LIVE")
        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(Path, "exists", return_value=True):
            with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
                result = remove_worktree(Path("/fake/repo"), "sdlc-1218", force=True)
        assert result is True
        # Ensure the force WARNING fired
        assert any("force-removing" in rec.message for rec in caplog.records)

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_busy_check_failure_treated_as_clear(self, mock_run, mock_busy):
        """If the busy helper returns None (fail-open path), removal proceeds."""
        mock_busy.return_value = None
        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(Path, "exists", return_value=True):
            result = remove_worktree(Path("/fake/repo"), "sdlc-1218")
        assert result is True


class TestCleanupAfterMergeBusyBlock:
    """Tests for cleanup_after_merge surfacing the busy block (issue #1357)."""

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_blocked_by_live_session(self, mock_remove_wt, mock_branch_exists, mock_run):
        """When remove_worktree returns ('blocked', sid), result reflects it."""
        repo = Path("/fake/repo")
        slug = "sdlc-1218"

        with patch.object(Path, "exists", return_value=True):
            mock_remove_wt.return_value = ("blocked", "0_LIVE")
            mock_branch_exists.return_value = False
            mock_run.return_value = MagicMock(returncode=0)

            result = cleanup_after_merge(repo, slug)

        assert result["worktree_removed"] is False
        assert result["blocked_by_session"] == "0_LIVE"
        # Block is recorded as an error (so post_merge_cleanup.py can decide
        # to emit the distinct exit-2 path).
        assert any("blocked: worktree in use" in e for e in result["errors"])
        assert result["already_clean"] is False


# ---------------------------------------------------------------------------
# Issue #1377: verify_worktree_branch
# ---------------------------------------------------------------------------


def _init_git_worktree(tmp_path: Path, branch: str) -> Path:
    """Create a real git repo at tmp_path checked out to ``branch``.

    Uses subprocess + actual git for fidelity — the behavior under test
    depends on real git semantics (rev-parse, status, checkout). Branch
    ``main`` is created via the initial commit; additional branches are
    created with ``git checkout -b``.
    """
    import subprocess as _sp

    repo = tmp_path / "wt"
    repo.mkdir()
    _sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "seed.txt").write_text("seed\n")
    _sp.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    if branch != "main":
        _sp.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True)
    return repo


class TestVerifyWorktreeBranch:
    """Tests for verify_worktree_branch (issue #1377)."""

    def test_matching_branch_is_noop(self, tmp_path, caplog):
        repo = _init_git_worktree(tmp_path, "main")
        with caplog.at_level("INFO", logger="agent.worktree_manager"):
            verify_worktree_branch(repo, "main")
        assert not any("worktree-branch-recovery" in r.message for r in caplog.records)

    def test_mismatch_clean_auto_checks_out(self, tmp_path, caplog):
        repo = _init_git_worktree(tmp_path, "session/sdlc-1377")
        with caplog.at_level("INFO", logger="agent.worktree_manager"):
            verify_worktree_branch(repo, "main")
        import subprocess as _sp

        head = _sp.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head == "main"
        log_msgs = " ".join(r.message for r in caplog.records)
        assert "worktree-branch-recovery" in log_msgs
        assert "session/sdlc-1377" in log_msgs  # from-branch
        assert "main" in log_msgs  # to-branch

    def test_mismatch_dirty_raises(self, tmp_path):
        repo = _init_git_worktree(tmp_path, "session/sdlc-1377")
        (repo / "dirty.txt").write_text("uncommitted\n")
        with pytest.raises(WorktreeBranchMismatchError) as ei:
            verify_worktree_branch(repo, "main")
        msg = str(ei.value)
        assert "session/sdlc-1377" in msg
        assert "main" in msg
        assert ei.value.dirty_files  # non-empty
        assert ei.value.expected_branch == "main"
        assert ei.value.actual_branch == "session/sdlc-1377"

    def test_missing_path_raises(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(WorktreeBranchMismatchError) as ei:
            verify_worktree_branch(missing, "main")
        assert "does not exist" in str(ei.value)

    def test_empty_expected_branch_raises_value_error(self, tmp_path):
        repo = _init_git_worktree(tmp_path, "main")
        with pytest.raises(ValueError):
            verify_worktree_branch(repo, "")
        with pytest.raises(ValueError):
            verify_worktree_branch(repo, "   ")

    def test_none_path_raises_type_error(self):
        with pytest.raises(TypeError):
            verify_worktree_branch(None, "main")

    def test_mismatch_clean_target_branch_locked_elsewhere_raises(self, tmp_path):
        """Issue #1412: refuse early when expected_branch is held by another worktree."""
        import subprocess as _sp

        # Main repo on `main`.
        main_repo = _init_git_worktree(tmp_path, "main")
        # Create a session branch in the main repo, then add a sibling worktree
        # for it. After the worktree is added, the main repo stays on `main`
        # and the sibling holds `session/x`.
        _sp.run(
            ["git", "-C", str(main_repo), "branch", "session/x"],
            check=True,
            capture_output=True,
        )
        sibling = tmp_path / "sibling"
        _sp.run(
            ["git", "-C", str(main_repo), "worktree", "add", str(sibling), "session/x"],
            check=True,
            capture_output=True,
        )

        # Now `main` is locked by main_repo. verify_worktree_branch on the
        # sibling asking for "main" must raise with the structured cause.
        with pytest.raises(WorktreeBranchMismatchError) as ei:
            verify_worktree_branch(sibling, "main")
        assert ei.value.expected_branch == "main"
        assert ei.value.actual_branch == "session/x"
        cause = str(ei.value)
        assert "already used by worktree at" in cause
        assert str(main_repo.resolve()) in cause or str(main_repo) in cause

    def test_mismatch_clean_target_branch_not_locked_proceeds(self, tmp_path):
        """Issue #1412: when target branch is unlocked, existing recovery path still runs."""
        import subprocess as _sp

        # Main repo on `main`, create a `main2` branch (no worktree holds it),
        # then move the main repo onto `session/x`. Now `main2` is unlocked.
        main_repo = _init_git_worktree(tmp_path, "main")
        _sp.run(
            ["git", "-C", str(main_repo), "branch", "main2"],
            check=True,
            capture_output=True,
        )
        _sp.run(
            ["git", "-C", str(main_repo), "checkout", "-q", "-b", "session/x"],
            check=True,
            capture_output=True,
        )

        # Asking the main_repo (currently on session/x) to verify "main2"
        # should auto-checkout since `main2` is not held by any worktree.
        verify_worktree_branch(main_repo, "main2")
        head = _sp.run(
            ["git", "-C", str(main_repo), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head == "main2"


class TestProvisionWorktreeVenv:
    """Tests for per-worktree venv provisioning (issue #2052)."""

    def test_success_env_construction_and_marker(self, tmp_path):
        """uv sync runs with worktree cwd, VIRTUAL_ENV stripped,
        UV_PROJECT_ENVIRONMENT pinned to the absolute worktree .venv, and
        the .provisioned marker is written only after success."""
        wt = tmp_path / "wt"
        (wt / ".venv").mkdir(parents=True)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured.update(kwargs)
            return MagicMock(returncode=0)

        with (
            patch("agent.worktree_manager.subprocess.run", side_effect=fake_run),
            patch.dict("os.environ", {"VIRTUAL_ENV": "/shared/repo/.venv"}),
        ):
            assert provision_worktree_venv(wt) is True

        assert (wt / ".venv" / PROVISIONED_MARKER).exists()
        assert captured["cmd"] == ["uv", "sync", "--all-extras"]
        assert captured["cwd"] == wt
        env = captured["env"]
        assert "VIRTUAL_ENV" not in env
        assert env["UV_PROJECT_ENVIRONMENT"] == str(wt / ".venv")

    def test_skips_when_marker_present(self, tmp_path):
        wt = tmp_path / "wt"
        (wt / ".venv").mkdir(parents=True)
        (wt / ".venv" / PROVISIONED_MARKER).touch()
        with patch("agent.worktree_manager.subprocess.run") as mock_run:
            assert provision_worktree_venv(wt) is True
        mock_run.assert_not_called()

    def test_fail_open_on_called_process_error(self, tmp_path, caplog):
        wt = tmp_path / "wt"
        wt.mkdir()
        err = subprocess.CalledProcessError(1, ["uv", "sync"], stderr="resolution boom")
        with (
            patch("agent.worktree_manager.subprocess.run", side_effect=err),
            caplog.at_level(logging.WARNING, logger="agent.worktree_manager"),
        ):
            assert provision_worktree_venv(wt) is False
        assert not (wt / ".venv" / PROVISIONED_MARKER).exists()
        assert "[worktree-venv-provision-failed]" in caplog.text
        assert "resolution boom" in caplog.text

    def test_fail_open_on_timeout(self, tmp_path, caplog):
        wt = tmp_path / "wt"
        wt.mkdir()
        err = subprocess.TimeoutExpired(["uv", "sync"], 600)
        with (
            patch("agent.worktree_manager.subprocess.run", side_effect=err),
            caplog.at_level(logging.WARNING, logger="agent.worktree_manager"),
        ):
            assert provision_worktree_venv(wt) is False
        assert "[worktree-venv-provision-failed]" in caplog.text
        assert "timed out" in caplog.text

    def test_fail_open_on_missing_uv(self, tmp_path, caplog):
        wt = tmp_path / "wt"
        wt.mkdir()
        with (
            patch(
                "agent.worktree_manager.subprocess.run",
                side_effect=FileNotFoundError("uv"),
            ),
            caplog.at_level(logging.WARNING, logger="agent.worktree_manager"),
        ):
            assert provision_worktree_venv(wt) is False
        assert "[worktree-venv-provision-failed]" in caplog.text
        assert "not found" in caplog.text

    def test_fail_open_on_nonexistent_worktree_dir(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            assert provision_worktree_venv(tmp_path / "nope") is False
        assert "[worktree-venv-provision-failed]" in caplog.text


class TestCreateWorktreeProvisioningWiring:
    """create_worktree must provision eagerly on create and heal on reuse."""

    def test_reuse_path_reprovisions_when_marker_absent(self, tmp_path):
        repo = tmp_path / "repo"
        wt = repo / ".worktrees" / "my-slug"
        wt.mkdir(parents=True)
        with patch("agent.worktree_manager.provision_worktree_venv") as mock_prov:
            result = create_worktree(repo, "my-slug")
        assert result == wt
        mock_prov.assert_called_once_with(wt)

    def test_reuse_path_skips_when_marker_present(self, tmp_path):
        repo = tmp_path / "repo"
        wt = repo / ".worktrees" / "my-slug"
        (wt / ".venv").mkdir(parents=True)
        (wt / ".venv" / PROVISIONED_MARKER).touch()
        with patch("agent.worktree_manager.provision_worktree_venv") as mock_prov:
            result = create_worktree(repo, "my-slug")
        assert result == wt
        mock_prov.assert_not_called()

    @patch("agent.worktree_manager.provision_worktree_venv")
    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._find_worktree_for_branch")
    @patch("agent.worktree_manager._branch_exists")
    def test_fresh_create_provisions(self, mock_branch_exists, mock_find_wt, mock_run, mock_prov):
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
        mock_prov.assert_called_once_with(repo / ".worktrees" / "my-feature")


def _init_git_repo(tmp_path: Path, name: str = "repo") -> Path:
    """Create a real git repo with an initial commit on ``main``."""
    import subprocess as _sp

    repo = tmp_path / name
    repo.mkdir()
    _sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "seed.txt").write_text("seed\n")
    _sp.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    return repo


def _add_linked_worktree(repo: Path, slug: str) -> Path:
    """Add a linked worktree under ``.worktrees/{slug}`` on ``session/{slug}``."""
    import subprocess as _sp

    wt = repo / ".worktrees" / slug
    _sp.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", f"session/{slug}", str(wt)],
        check=True,
    )
    return wt


def _dirty(wt: Path) -> None:
    """Introduce staged + unstaged + untracked changes in a worktree."""
    import subprocess as _sp

    # Modify a tracked file (unstaged), stage a second edit, and add an untracked file.
    (wt / "seed.txt").write_text("seed\nunstaged change\n")
    (wt / "staged.txt").write_text("staged content\n")
    _sp.run(["git", "-C", str(wt), "add", "staged.txt"], check=True)
    (wt / "untracked.txt").write_text("untracked content\n")


def _git(wt: Path, *args: str):
    import subprocess as _sp

    return _sp.run(["git", "-C", str(wt), *args], capture_output=True, text=True)


class TestPreserveUncommittedChanges:
    """TDD (issue #2137): auto-preserve uncommitted worktree work before teardown."""

    def test_dirty_tree_preserved_in_named_ref_and_wip_commit(self, tmp_path, caplog):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-2137t"
        wt = _add_linked_worktree(repo, slug)
        head_before = _git(wt, "rev-parse", "HEAD").stdout.strip()
        _dirty(wt)

        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        assert result["was_clean"] is False
        sha = result["sha"]
        assert sha and sha != head_before
        assert result["ref"] == f"refs/session-wip/{slug}"

        # Durable named ref resolves in the common ref store to the WIP commit.
        ref_sha = _git(repo, "rev-parse", f"refs/session-wip/{slug}").stdout.strip()
        assert ref_sha == sha
        # HEAD of the worktree advanced to the WIP commit; tree is now clean.
        assert _git(wt, "rev-parse", "HEAD").stdout.strip() == sha
        assert _git(wt, "status", "--porcelain").stdout.strip() == ""

        # Recovery pointer logged with slug, ref, and sha.
        joined = " ".join(r.message for r in caplog.records)
        assert "worktree-wip-preserved" in joined
        assert slug in joined
        assert f"refs/session-wip/{slug}" in joined
        assert sha[:7] in joined

    def test_clean_tree_is_noop_and_creates_no_ref(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-clean"
        wt = _add_linked_worktree(repo, slug)

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is False
        assert result["was_clean"] is True
        # No ref created.
        assert _git(repo, "rev-parse", "--verify", f"refs/session-wip/{slug}").returncode != 0

    def test_untracked_only_is_preserved(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-untr"
        wt = _add_linked_worktree(repo, slug)
        (wt / "brand-new.txt").write_text("only untracked\n")

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        sha = result["sha"]
        # The untracked file is captured in the WIP commit tree.
        listing = _git(repo, "ls-tree", "-r", "--name-only", sha).stdout
        assert "brand-new.txt" in listing

    def test_git_failure_returns_error_dict_and_never_raises(self, tmp_path, caplog):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-fail"
        wt = _add_linked_worktree(repo, slug)
        _dirty(wt)

        # Simulate a git plumbing failure: every git call raises.
        with patch(
            "agent.worktree_manager.subprocess.run",
            side_effect=OSError("git exploded"),
        ):
            with caplog.at_level(logging.ERROR, logger="agent.worktree_manager"):
                result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is False
        assert result.get("errors")
        joined = " ".join(r.message for r in caplog.records)
        assert "worktree-wip-preserve-failed" in joined

    def test_remove_worktree_preserves_dirty_tree_before_force_remove(self, tmp_path):
        from agent import worktree_manager

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-remove"
        wt = _add_linked_worktree(repo, slug)
        _dirty(wt)

        with patch.object(worktree_manager, "worktree_busy_check", return_value=None):
            ok = worktree_manager.remove_worktree(repo, slug, delete_branch=False)

        assert ok is True
        # Worktree directory is gone (force-removed) ...
        assert not wt.exists()
        # ... but the uncommitted work survives in the durable ref.
        assert _git(repo, "rev-parse", "--verify", f"refs/session-wip/{slug}").returncode == 0
