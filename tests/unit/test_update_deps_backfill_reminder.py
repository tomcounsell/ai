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

import json
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


@pytest.mark.unit
class TestLlmGateProbeSkippedReporting:
    """`run_gate_phases` must distinguish "unverifiable" from "incompatible".

    Review round 3 (PR #3089): a keyless machine's `llm` gate fails exactly
    like an incompatible pair -- both exit 1 -- but `CompatResult` now
    carries `probe_skipped=True` on the no-key branch, and `run_gate_phases`
    is the one place that reads the gate subprocess's `--json` output, so it
    is the one place that can turn that field into an operator-facing
    distinction. Exit status must stay fail-closed either way -- only the
    message differs.
    """

    def _llm_only_set(self) -> deps.CoupledSet:
        return deps.CoupledSet(
            members=["anthropic", "pydantic-ai-slim"],
            import_names=("anthropic", "pydantic_ai"),
            gates=("llm",),
            reason="test-only coupled set exercising only the llm gate phase",
        )

    def _venv(self, tmp_path: Path) -> Path:
        project_dir = tmp_path
        (project_dir / ".venv" / "bin").mkdir(parents=True)
        (project_dir / ".venv" / "bin" / "python").touch()
        return project_dir

    def test_probe_skipped_true_reports_unverifiable_not_incompatible(
        self, tmp_path: Path, monkeypatch
    ):
        project_dir = self._venv(tmp_path)
        payload = json.dumps({"compatible": False, "loader_ok": True, "probe_skipped": True})

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=payload, stderr="")

        monkeypatch.setattr(deps, "run_cmd", fake_run)

        passed, failed_phase, output = deps.run_gate_phases(project_dir, self._llm_only_set())

        assert passed is False
        assert failed_phase == "llm"
        assert deps.GATE_UNVERIFIABLE_MARKER in output
        assert "incompatible" not in output.split(":")[0]

    def test_the_marker_constant_is_what_run_gate_phases_actually_emits(
        self, tmp_path: Path, monkeypatch
    ):
        """The two halves of the deps -> run.py contract must not drift apart.

        `_bump_coupled_set` sets `AutoBumpResult.gate_unverifiable` by looking
        for `GATE_UNVERIFIABLE_MARKER` in the detail text, and `run.py` reads
        only that field. Reword the message without the constant and the flag
        silently stops being set, so `/update` would report an unverifiable
        host as an incompatible pair again with every test still green. This
        asserts the message is built FROM the constant.
        """
        project_dir = self._venv(tmp_path)
        payload = json.dumps({"compatible": False, "loader_ok": True, "probe_skipped": True})

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=payload, stderr="")

        monkeypatch.setattr(deps, "run_cmd", fake_run)

        _, _, output = deps.run_gate_phases(project_dir, self._llm_only_set())

        result = deps.AutoBumpResult()
        assert result.gate_unverifiable is False, "must default to False"
        result.gate_unverifiable = deps.GATE_UNVERIFIABLE_MARKER in output
        assert result.gate_unverifiable is True

    def test_probe_skipped_false_reports_gate_failed(self, tmp_path: Path, monkeypatch):
        project_dir = self._venv(tmp_path)
        payload = json.dumps(
            {"compatible": False, "loader_ok": True, "probe_skipped": False, "reason": "boom"}
        )

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=payload, stderr="")

        monkeypatch.setattr(deps, "run_cmd", fake_run)

        passed, failed_phase, output = deps.run_gate_phases(project_dir, self._llm_only_set())

        assert passed is False
        assert failed_phase == "llm"
        assert "llm gate failed" in output
        assert "unverifiable" not in output

    def test_probe_skipped_helper_is_best_effort_on_bad_json(self):
        """Non-JSON stdout must fall back to False, never raise."""
        assert deps._llm_probe_was_skipped("not json at all") is False
        assert deps._llm_probe_was_skipped("") is False
