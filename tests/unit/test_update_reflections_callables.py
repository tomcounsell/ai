"""Tests for the Step 1.659 update hook that repoints reflection callables.

Issue #2875.

Every test passes explicit ``targets`` — without it the wrapper resolves the
REAL vault registry (``~/Desktop/Valor/reflections.yaml``) and rewrites it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.update.reflections_callables import run_reflections_callables_migration

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _write_registry(path: Path, callables: list[str]) -> Path:
    body = ["reflections:"]
    for i, c in enumerate(callables):
        body += [
            f"  - name: entry-{i}",
            "    every: 60s",
            "    execution_type: function",
            f'    callable: "{c}"',
        ]
    path.write_text("\n".join(body) + "\n")
    return path


def test_rewrites_registry_and_reports_counts(tmp_path):
    target = _write_registry(
        tmp_path / "reflections.yaml",
        [
            "agent.sustainability.circuit_health_gate",
            "agent.sustainability.session_recovery_drip",
            "reflections.maintenance.run_disk_space_check",
        ],
    )

    result = run_reflections_callables_migration(_REPO_ROOT, targets=[target])

    assert result.success is True
    assert result.action == "rewrote"
    assert result.rewrites_count == 2
    assert result.targets == [str(target)]
    assert "agent.sustainability" not in target.read_text()


def test_second_run_is_a_noop(tmp_path):
    target = _write_registry(
        tmp_path / "reflections.yaml", ["agent.sustainability.failure_loop_detector"]
    )

    run_reflections_callables_migration(_REPO_ROOT, targets=[target])
    after_first = target.read_text()
    result = run_reflections_callables_migration(_REPO_ROOT, targets=[target])

    assert result.success is True
    assert result.action == "noop"
    assert result.rewrites_count == 0
    assert target.read_text() == after_first


def test_absent_registry_is_a_noop_not_an_error(tmp_path):
    """A machine with no vault and no materialized config must not warn."""
    result = run_reflections_callables_migration(_REPO_ROOT, targets=[tmp_path / "absent.yaml"])

    assert result.success is True
    assert result.action == "noop"


def test_missing_migration_script_is_a_soft_error(tmp_path, monkeypatch):
    """A partial checkout surfaces a warning rather than crashing /update."""
    monkeypatch.setattr(
        "scripts.update.reflections_callables.Path.exists", lambda self: False, raising=False
    )

    result = run_reflections_callables_migration(tmp_path, targets=[tmp_path / "x.yaml"])

    assert result.success is False
    assert result.action == "error"
    assert "migration script missing" in (result.error or "")
