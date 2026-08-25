"""Unit tests for agent/worktree_manager.py — branch verification and venv provisioning."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.worktree_manager import (
    PROVISIONED_MARKER,
    WorktreeBranchMismatchError,
    create_worktree,
    main_checkout_venv,
    provision_worktree_venv,
    repo_interpreter_pin,
    venv_python_version,
    verify_worktree_branch,
    worktree_interpreter_pin,
)

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


class TestInterpreterPinResolution:
    """Tests for the worktree/main-checkout interpreter pin (issue #2572)."""

    @pytest.mark.parametrize(
        ("recorded", "expected"),
        [
            # uv writes MAJOR.MINOR for an env built from its own managed
            # download and MAJOR.MINOR.PATCH for one built from a system
            # interpreter, so both shapes occur on the same machine.
            ("3.13", "3.13"),
            ("3.12.13", "3.12"),
            ("3.14.3", "3.14"),
            ("not-a-version", None),
        ],
    )
    def test_venv_python_version_parses_both_granularities(self, tmp_path, recorded, expected):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text(
            f"home = /opt/python/bin\nversion_info = {recorded}\nprompt = ai\n"
        )
        assert venv_python_version(venv) == expected

    def test_venv_python_version_none_when_absent(self, tmp_path):
        assert venv_python_version(tmp_path / "nope") is None

    def test_main_checkout_venv_from_main_checkout(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        assert main_checkout_venv(repo) == repo.resolve() / ".venv"

    def test_main_checkout_venv_from_linked_worktree(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git" / "worktrees" / "my-slug").mkdir(parents=True)
        wt = repo / ".worktrees" / "my-slug"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {repo.resolve() / '.git' / 'worktrees' / 'my-slug'}\n")
        assert main_checkout_venv(wt) == repo.resolve() / ".venv"

    def test_main_checkout_venv_none_outside_a_repo(self, tmp_path):
        assert main_checkout_venv(tmp_path) is None

    @pytest.mark.parametrize(
        ("recorded", "expected"),
        [
            ("3.14\n", "3.14"),
            ("3.14.3\n", "3.14"),
            ("cpython@3.14\n", "3.14"),
            ("# comment\n\n3.14\n", "3.14"),
            ("pypy\n", None),
            ("", None),
        ],
    )
    def test_repo_interpreter_pin_parses_committed_file(self, tmp_path, recorded, expected):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".python-version").write_text(recorded)
        assert repo_interpreter_pin(repo) == expected

    def test_repo_interpreter_pin_from_linked_worktree_without_the_file(self, tmp_path):
        """A worktree checked out before the pin landed still reads the checkout's."""
        repo = tmp_path / "repo"
        (repo / ".git" / "worktrees" / "slug").mkdir(parents=True)
        (repo / ".python-version").write_text("3.14\n")
        wt = repo / ".worktrees" / "slug"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {repo.resolve() / '.git' / 'worktrees' / 'slug'}\n")
        assert repo_interpreter_pin(wt) == "3.14"

    def test_repo_interpreter_pin_none_when_unpinned(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        assert repo_interpreter_pin(repo) is None

    def test_worktree_pin_prefers_the_committed_file_over_a_drifted_checkout_venv(self, tmp_path):
        """The committed pin outranks the checkout's own venv (#2617).

        A main checkout venv that itself drifted must not propagate that drift
        into every worktree provisioned from it.
        """
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".python-version").write_text("3.14\n")
        (repo / ".venv").mkdir()
        (repo / ".venv" / "pyvenv.cfg").write_text("version_info = 3.13.2\n")
        assert worktree_interpreter_pin(repo) == "3.14"

    def test_worktree_pin_falls_back_to_the_checkout_venv_when_unpinned(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".venv").mkdir()
        (repo / ".venv" / "pyvenv.cfg").write_text("version_info = 3.13.2\n")
        assert worktree_interpreter_pin(repo) == "3.13"

    def test_this_repo_ships_a_committed_pin(self):
        """The pin must be committed, not gitignored (#2617).

        `.python-version` was gitignored, which is why #2572's pin could only
        ever be host-local. If this test fails, a bare `uv sync` in a fresh
        worktree is once again free to pick whatever interpreter uv has newest.
        """
        repo_root = Path(__file__).resolve().parents[3]
        pin_file = repo_root / ".python-version"
        assert pin_file.exists(), "repo root has no .python-version pin"
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(pin_file)],
            cwd=repo_root,
            capture_output=True,
        )
        assert ignored.returncode != 0, ".python-version is gitignored — it can never ship"
        assert repo_interpreter_pin(repo_root) is not None


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

    def test_pins_interpreter_to_main_checkout(self, tmp_path):
        """uv sync is pinned to the main checkout's MAJOR.MINOR (#2572)."""
        wt = tmp_path / "wt"
        (wt / ".venv").mkdir(parents=True)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return MagicMock(returncode=0)

        with (
            patch("agent.worktree_manager.subprocess.run", side_effect=fake_run),
            patch("agent.worktree_manager.worktree_interpreter_pin", return_value="3.12"),
        ):
            assert provision_worktree_venv(wt) is True

        assert captured["cmd"] == ["uv", "sync", "--all-extras", "--python", "3.12"]

    def test_resyncs_provisioned_venv_that_drifted_off_the_pin(self, tmp_path, caplog):
        """A stale venv on the wrong interpreter is re-synced, not reused.

        This is what heals the worktrees already on disk: their results are
        otherwise incomparable to any main-checkout baseline.
        """
        wt = tmp_path / "wt"
        (wt / ".venv").mkdir(parents=True)
        (wt / ".venv" / PROVISIONED_MARKER).touch()
        (wt / ".venv" / "pyvenv.cfg").write_text("version_info = 3.13.14\n")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return MagicMock(returncode=0)

        with (
            patch("agent.worktree_manager.subprocess.run", side_effect=fake_run),
            patch("agent.worktree_manager.worktree_interpreter_pin", return_value="3.12"),
            caplog.at_level(logging.WARNING, logger="agent.worktree_manager"),
        ):
            assert provision_worktree_venv(wt) is True

        assert captured["cmd"] == ["uv", "sync", "--all-extras", "--python", "3.12"]
        assert "[worktree-venv-interpreter-drift]" in caplog.text
        assert "3.13" in caplog.text

    def test_reuses_provisioned_venv_already_on_the_pin(self, tmp_path):
        wt = tmp_path / "wt"
        (wt / ".venv").mkdir(parents=True)
        (wt / ".venv" / PROVISIONED_MARKER).touch()
        (wt / ".venv" / "pyvenv.cfg").write_text("version_info = 3.12.13\n")
        with (
            patch("agent.worktree_manager.subprocess.run") as mock_run,
            patch("agent.worktree_manager.worktree_interpreter_pin", return_value="3.12"),
        ):
            assert provision_worktree_venv(wt) is True
        mock_run.assert_not_called()

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

    def test_reuse_path_always_delegates_to_provision(self, tmp_path):
        """Even a marker-present worktree goes through provisioning.

        The skip decision lives inside ``provision_worktree_venv``, which also
        has to compare the venv's interpreter against the main checkout's
        (#2572). Short-circuiting here would hide the drift.
        """
        repo = tmp_path / "repo"
        wt = repo / ".worktrees" / "my-slug"
        (wt / ".venv").mkdir(parents=True)
        (wt / ".venv" / PROVISIONED_MARKER).touch()
        with patch("agent.worktree_manager.provision_worktree_venv") as mock_prov:
            result = create_worktree(repo, "my-slug")
        assert result == wt
        mock_prov.assert_called_once_with(wt)

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
