"""Unit tests for worker heartbeat pre-flight in tools/valor_session.py.

Covers:
- _check_worker_health(): healthy/stale/missing/boundary against the 600s
  WORKER_DOWN_THRESHOLD_S, negative-age clamping, and the #980 never-raise
  contract (permission errors, git subprocess failures).
- _resolve_heartbeat_path(): worktree-aware resolution via
  `git rev-parse --path-format=absolute --git-common-dir`, relative-output
  guarding (anchored to repo_root, never process cwd), and the
  __file__-relative fallback on any git failure.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure repo root is on path before importing from tools/
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from agent.constants import WORKER_DOWN_THRESHOLD_S  # noqa: E402
from tools import valor_session  # noqa: E402
from tools.valor_session import (  # noqa: E402
    _check_worker_health,
    _resolve_heartbeat_path,
    _worker_down_message,
)


def _patch_resolver(monkeypatch, path: Path) -> None:
    """Point the resolver seam at a test-controlled heartbeat path."""
    monkeypatch.setattr(valor_session, "_resolve_heartbeat_path", lambda *a, **k: path)


# ---------------------------------------------------------------------------
# _check_worker_health() unit tests
# ---------------------------------------------------------------------------


class TestCheckWorkerHealth:
    """Tests for _check_worker_health() against WORKER_DOWN_THRESHOLD_S (600s)."""

    def test_healthy_worker(self, tmp_path, monkeypatch):
        """Returns (True, age_s) when heartbeat file is recent."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        _patch_resolver(monkeypatch, hb)

        healthy, age_s = _check_worker_health()

        assert healthy is True
        assert age_s is not None
        assert age_s < WORKER_DOWN_THRESHOLD_S

    def test_age_between_old_and_new_threshold_is_healthy(self, tmp_path, monkeypatch):
        """A 360-599s age is healthy under the 600s threshold (was stale at 360s)."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        mtime = time.time() - 450
        os.utime(hb, (mtime, mtime))
        _patch_resolver(monkeypatch, hb)

        healthy, age_s = _check_worker_health()

        assert healthy is True
        assert age_s is not None
        assert 360 <= age_s < WORKER_DOWN_THRESHOLD_S

    def test_stale_worker(self, tmp_path, monkeypatch):
        """Returns (False, age_s) when heartbeat is older than 600s."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        stale_mtime = time.time() - (WORKER_DOWN_THRESHOLD_S + 60)
        os.utime(hb, (stale_mtime, stale_mtime))
        _patch_resolver(monkeypatch, hb)

        healthy, age_s = _check_worker_health()

        assert healthy is False
        assert age_s is not None
        assert age_s >= WORKER_DOWN_THRESHOLD_S

    def test_missing_heartbeat_file(self, tmp_path, monkeypatch):
        """Returns (False, None) when heartbeat file does not exist."""
        _patch_resolver(monkeypatch, tmp_path / "nonexistent_last_worker_connected")

        healthy, age_s = _check_worker_health()

        assert healthy is False
        assert age_s is None

    def test_exact_threshold_boundary(self, tmp_path, monkeypatch):
        """Age == 600s is treated as down (strict <)."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        boundary_mtime = time.time() - WORKER_DOWN_THRESHOLD_S
        os.utime(hb, (boundary_mtime, boundary_mtime))
        _patch_resolver(monkeypatch, hb)

        healthy, _age_s = _check_worker_health()

        assert healthy is False

    def test_future_mtime_clamped_to_zero(self, tmp_path, monkeypatch):
        """Future-dated mtime (clock skew / iCloud) clamps age to 0 == healthy."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        future_mtime = time.time() + 500
        os.utime(hb, (future_mtime, future_mtime))
        _patch_resolver(monkeypatch, hb)

        healthy, age_s = _check_worker_health()

        assert healthy is True
        assert age_s == 0
        assert "-" not in _worker_down_message(age_s).split("(")[1].split(")")[0]

    def test_does_not_raise_on_permission_error(self, tmp_path, monkeypatch):
        """Never raises — OSError is caught silently (#980 contract)."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        hb.chmod(0o000)
        _patch_resolver(monkeypatch, hb)

        try:
            result = _check_worker_health()
            assert isinstance(result, tuple)
            assert len(result) == 2
        finally:
            hb.chmod(0o644)  # restore so tmp_path cleanup works

    def test_git_failure_falls_back_and_reports_down(self, tmp_path, monkeypatch):
        """Git subprocess failure → fallback path, (False, None) — never a raise.

        Pins the resolver's anchor to tmp_path (which has no data/ dir) and
        makes the git binary unfindable; the real resolver must swallow the
        error, fall back to the anchor-relative path, and the health check
        must report down/None.
        """
        real_resolver = _resolve_heartbeat_path
        monkeypatch.setattr(
            valor_session,
            "_resolve_heartbeat_path",
            lambda *a, **k: real_resolver(repo_root=tmp_path),
        )

        def _boom(*args, **kwargs):
            raise FileNotFoundError("git binary missing")

        monkeypatch.setattr(subprocess, "run", _boom)

        healthy, age_s = _check_worker_health()

        assert healthy is False
        assert age_s is None


# ---------------------------------------------------------------------------
# _resolve_heartbeat_path() unit tests
# ---------------------------------------------------------------------------


class TestResolveHeartbeatPath:
    """Tests for the worktree-aware heartbeat path resolver."""

    @staticmethod
    def _git(*args, cwd):
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=test@test",
                "-c",
                "user.name=test",
                *args,
            ],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    def test_worktree_resolves_to_main_checkout_data_dir(self, tmp_path):
        """From a real git worktree, the path lands under the MAIN checkout."""
        main = tmp_path / "main"
        main.mkdir()
        self._git("init", cwd=main)
        (main / "f.txt").write_text("x")
        self._git("add", "f.txt", cwd=main)
        self._git("commit", "-m", "init", cwd=main)
        wt = tmp_path / "wt"
        self._git("worktree", "add", str(wt), cwd=main)

        result = _resolve_heartbeat_path(repo_root=wt)

        assert result == main.resolve() / "data" / "last_worker_connected"

    def test_relative_git_output_anchored_to_repo_root_not_cwd(self, tmp_path, monkeypatch):
        """Relative common-dir output resolves under repo_root, never process cwd."""
        anchor = tmp_path / "repo"
        anchor.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=".git\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        result = _resolve_heartbeat_path(repo_root=anchor)

        assert result == anchor.resolve() / "data" / "last_worker_connected"
        assert not str(result).startswith(str(elsewhere))

    def test_git_nonzero_exit_falls_back_to_anchor(self, tmp_path, monkeypatch):
        """Non-zero git exit → __file__-relative (anchor-relative) fallback."""

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=128, stdout="", stderr="fatal: not a git repository"
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)

        result = _resolve_heartbeat_path(repo_root=tmp_path)

        assert result == tmp_path / "data" / "last_worker_connected"

    def test_git_binary_missing_falls_back_to_anchor(self, tmp_path, monkeypatch):
        """FileNotFoundError from subprocess → anchor-relative fallback, no raise."""

        def _boom(*args, **kwargs):
            raise FileNotFoundError("git binary missing")

        monkeypatch.setattr(subprocess, "run", _boom)

        result = _resolve_heartbeat_path(repo_root=tmp_path)

        assert result == tmp_path / "data" / "last_worker_connected"

    def test_default_anchor_is_repo_root_of_module(self, monkeypatch):
        """With no repo_root, the anchor is the module's __file__-relative root."""

        def _boom(*args, **kwargs):
            raise FileNotFoundError("git binary missing")

        monkeypatch.setattr(subprocess, "run", _boom)

        result = _resolve_heartbeat_path()

        expected_anchor = Path(valor_session.__file__).parent.parent
        assert result == expected_anchor / "data" / "last_worker_connected"
