"""Unit tests for the markitdown backfill-reminder logic in scripts/update/deps.py.

The reminder must fire on the run that *first* installs the markitdown package
into the project venv, but never on subsequent runs. The bug being fixed: the
prior implementation read uv.lock before vs after `uv sync` to detect a
first-time install — but by the time `update_dependencies()` runs, `git pull`
has already updated uv.lock, so both sides match and the reminder never fires.

The fix probes whether `markitdown` is importable in the project venv before
and after `uv sync` to detect actual environment-state transitions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.update import deps


@pytest.mark.unit
class TestBackfillReminderEnvironmentProbe:
    """The reminder fires on first install of markitdown into the venv."""

    def test_first_install_emits_reminder(self, tmp_path: Path, monkeypatch):
        """markitdown absent before sync, present after → reminder fires."""
        project_dir = tmp_path
        (project_dir / ".venv" / "bin").mkdir(parents=True)
        (project_dir / ".venv" / "bin" / "python").touch()

        # Sequence the import probe: first call returns False (pre-sync),
        # second call returns True (post-sync).
        probe_results = iter([False, True])
        monkeypatch.setattr(
            deps,
            "_markitdown_importable",
            lambda pd: next(probe_results),
        )

        # Stub the actual `uv sync` invocation.
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(deps, "run_cmd", fake_run)

        result = deps.sync_with_uv(project_dir)
        assert result.success is True
        assert result.backfill_reminder_needed is True

    def test_already_installed_does_not_emit_reminder(self, tmp_path: Path, monkeypatch):
        """markitdown importable before sync → reminder must not fire."""
        project_dir = tmp_path
        (project_dir / ".venv" / "bin").mkdir(parents=True)
        (project_dir / ".venv" / "bin" / "python").touch()

        # Both probes return True — markitdown was already installed.
        monkeypatch.setattr(deps, "_markitdown_importable", lambda pd: True)

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(deps, "run_cmd", fake_run)

        result = deps.sync_with_uv(project_dir)
        assert result.backfill_reminder_needed is False

    def test_uninstall_does_not_emit_reminder(self, tmp_path: Path, monkeypatch):
        """markitdown removed (present before, absent after) → no reminder."""
        project_dir = tmp_path
        (project_dir / ".venv" / "bin").mkdir(parents=True)
        (project_dir / ".venv" / "bin" / "python").touch()

        probe_results = iter([True, False])
        monkeypatch.setattr(
            deps,
            "_markitdown_importable",
            lambda pd: next(probe_results),
        )

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(deps, "run_cmd", fake_run)

        result = deps.sync_with_uv(project_dir)
        assert result.backfill_reminder_needed is False

    def test_failed_sync_does_not_emit_reminder(self, tmp_path: Path, monkeypatch):
        """If `uv sync` fails, no reminder regardless of probe state."""
        project_dir = tmp_path
        (project_dir / ".venv" / "bin").mkdir(parents=True)
        (project_dir / ".venv" / "bin" / "python").touch()

        # Probe should not even be consulted on a failed sync, but if it is
        # we'd be sequenced to the "first install" pattern. The result must
        # still be backfill_reminder_needed=False because the operation failed.
        monkeypatch.setattr(
            deps,
            "_markitdown_importable",
            lambda pd: False,
        )

        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="boom")

        monkeypatch.setattr(deps, "run_cmd", fake_run)

        result = deps.sync_with_uv(project_dir)
        assert result.success is False
        assert result.backfill_reminder_needed is False


@pytest.mark.unit
class TestMarkitdownImportable:
    """The probe helper checks `python -c 'import markitdown'` in the project venv."""

    def test_returns_true_when_import_succeeds(self, tmp_path: Path, monkeypatch):
        project_dir = tmp_path
        (project_dir / ".venv" / "bin").mkdir(parents=True)
        py = project_dir / ".venv" / "bin" / "python"
        py.touch()

        def fake_run(cmd, **kwargs):
            # Should be invoked with: [py, "-c", "import markitdown"]
            assert str(py) in cmd
            assert "import markitdown" in " ".join(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(deps, "run_cmd", fake_run)
        assert deps._markitdown_importable(project_dir) is True

    def test_returns_false_when_import_fails(self, tmp_path: Path, monkeypatch):
        project_dir = tmp_path
        (project_dir / ".venv" / "bin").mkdir(parents=True)
        py = project_dir / ".venv" / "bin" / "python"
        py.touch()

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'markitdown'",
            )

        monkeypatch.setattr(deps, "run_cmd", fake_run)
        assert deps._markitdown_importable(project_dir) is False

    def test_returns_false_when_no_venv(self, tmp_path: Path):
        """Without a venv python the probe must default to False (pre-sync state)."""
        project_dir = tmp_path
        # No .venv/bin/python exists.
        assert deps._markitdown_importable(project_dir) is False
