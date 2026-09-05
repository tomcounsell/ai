"""Unit tests for agent/worktree_manager.py — worktree cleanup and lookup."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import agent.worktree_manager as wm
from agent.worktree_manager import (
    _cleanup_stale_worktree,
    _find_worktree_for_branch,
    _validate_slug,
    cleanup_after_merge,
    reap_idle_worktree,
    stale_nightly_triage_slug,
)


class TestStaleNightlyTriageSlug:
    """Selection for the nightly-triage worktree sweep (issue #3162)."""

    WINDOW = 72.0

    def test_old_nightly_triage_branch_is_selected(self):
        assert (
            stale_nightly_triage_slug("session/nightly-triage-1a2b3c4d", 73.0, self.WINDOW)
            == "nightly-triage-1a2b3c4d"
        )

    def test_young_nightly_triage_branch_is_not_selected(self):
        assert (
            stale_nightly_triage_slug("session/nightly-triage-1a2b3c4d", 71.0, self.WINDOW) is None
        )

    def test_branch_exactly_at_window_is_not_selected(self):
        assert (
            stale_nightly_triage_slug("session/nightly-triage-1a2b3c4d", self.WINDOW, self.WINDOW)
            is None
        )

    def test_seed_dispatch_suffix_is_selected(self):
        """Re-baseline dispatches use a named suffix instead of a hash."""
        assert (
            stale_nightly_triage_slug("session/nightly-triage-idempotency-3075", 100.0, self.WINDOW)
            == "nightly-triage-idempotency-3075"
        )

    @pytest.mark.parametrize(
        "branch",
        [
            "session/dev-a4e15370",
            "session/sdlc-3162",
            "session/nightly-baseline",
            "session/dev-nightly-triage-1a2b3c4d",
            "nightly-triage-1a2b3c4d",
            "session/nightly-triage-",
        ],
    )
    def test_other_namespaces_are_never_selected(self, branch):
        assert stale_nightly_triage_slug(branch, 1000.0, self.WINDOW) is None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def lane_repo(tmp_path):
    """A real repo with one clean nightly-triage lane checked out under .worktrees/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("initial\n")
    (repo / "tracked.py").write_text("print('hi')\n")
    _git(repo, "add", "README.md", "tracked.py")
    _git(repo, "commit", "-m", "initial")
    slug = "nightly-triage-1a2b3c4d"
    lane = repo / ".worktrees" / slug
    _git(repo, "worktree", "add", "-b", f"session/{slug}", str(lane), "main")
    return repo, slug, lane


@pytest.fixture
def idle(monkeypatch):
    """Neutralize the process and session guards so a test can trip exactly one."""
    monkeypatch.setattr(wm, "_worktree_has_live_process", lambda _p: None)
    monkeypatch.setattr(wm, "worktree_busy_probe", lambda _r, _s: ("clear", ""))


class TestReapIdleWorktree:
    """Fail-closed teardown of a nightly-triage lane (issue #3162)."""

    def test_clean_idle_lane_is_removed_without_preserve(self, lane_repo, idle, monkeypatch):
        repo, slug, lane = lane_repo
        preserve_calls = []
        monkeypatch.setattr(
            wm,
            "preserve_uncommitted_worktree_changes",
            lambda *a, **k: preserve_calls.append(a),
        )

        assert reap_idle_worktree(repo, slug) == (True, "removed")

        assert not lane.exists()
        assert _find_worktree_for_branch(repo, f"session/{slug}") is None
        assert preserve_calls == [], "a clean tree has nothing to preserve"

    def test_half_deleted_tree_is_kept_and_never_preserved(self, lane_repo, idle, monkeypatch):
        """The #3167 shape: tracked files vanished from disk.

        The lane reads as dirty and is skipped. The preserve path must not
        run, because committing that state is the data-loss mechanism.
        """
        repo, slug, lane = lane_repo
        (lane / "tracked.py").unlink()
        preserve_calls = []
        monkeypatch.setattr(
            wm,
            "preserve_uncommitted_worktree_changes",
            lambda *a, **k: preserve_calls.append(a),
        )

        assert reap_idle_worktree(repo, slug) == (False, "uncommitted_changes")

        assert lane.is_dir()
        assert preserve_calls == []

    def test_untracked_file_keeps_the_lane(self, lane_repo, idle):
        repo, slug, lane = lane_repo
        (lane / "scratch.txt").write_text("notes\n")

        assert reap_idle_worktree(repo, slug) == (False, "uncommitted_changes")
        assert lane.is_dir()

    def test_live_process_keeps_the_lane(self, lane_repo, monkeypatch):
        repo, slug, lane = lane_repo
        monkeypatch.setattr(wm, "_worktree_has_live_process", lambda _p: 4242)
        monkeypatch.setattr(wm, "worktree_busy_probe", lambda _r, _s: ("clear", ""))

        assert reap_idle_worktree(repo, slug) == (False, "live_process:4242")
        assert lane.is_dir()

    def test_live_session_keeps_the_lane(self, lane_repo, monkeypatch):
        repo, slug, lane = lane_repo
        monkeypatch.setattr(wm, "_worktree_has_live_process", lambda _p: None)
        monkeypatch.setattr(wm, "worktree_busy_probe", lambda _r, _s: ("busy", "sess-1"))

        assert reap_idle_worktree(repo, slug) == (False, "live_session:sess-1")
        assert lane.is_dir()

    def test_busy_probe_error_fails_closed(self, lane_repo, monkeypatch):
        repo, slug, lane = lane_repo
        monkeypatch.setattr(wm, "_worktree_has_live_process", lambda _p: None)
        monkeypatch.setattr(
            wm, "worktree_busy_probe", lambda _r, _s: ("error", "query_failed:ConnectionError")
        )

        assert reap_idle_worktree(repo, slug) == (
            False,
            "busy_check_error:query_failed:ConnectionError",
        )
        assert lane.is_dir()

    def test_branch_checked_out_elsewhere_is_left_alone(self, lane_repo, idle, tmp_path):
        """A directory at the lane path that is not the branch's worktree stays."""
        repo, slug, lane = lane_repo
        elsewhere = tmp_path / "elsewhere"
        _git(repo, "worktree", "remove", "--force", str(lane))
        _git(repo, "worktree", "add", str(elsewhere), f"session/{slug}")
        lane.mkdir()
        (lane / "leftover").write_text("x\n")

        assert reap_idle_worktree(repo, slug) == (False, "not_registered_at_lane_path")
        assert lane.is_dir()
        assert elsewhere.is_dir()

    def test_missing_lane_reports_missing(self, tmp_path, idle):
        assert reap_idle_worktree(tmp_path, "nightly-triage-ffffffff") == (False, "missing")


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
    @patch("agent.worktree_manager.preserve_uncommitted_worktree_changes")
    def test_force_removes_existing_directory(self, mock_preserve, mock_run, mock_prune):
        """When the worktree directory exists, force-remove it.

        Preservation (#2137) is patched to a no-op here so the single
        ``subprocess.run`` assertion isolates the force-remove call.
        """
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
    @patch("agent.worktree_manager.preserve_uncommitted_worktree_changes")
    def test_fallback_does_not_pass_ignore_errors(
        self, mock_preserve, mock_logger, mock_run, mock_prune, mock_rmtree
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
