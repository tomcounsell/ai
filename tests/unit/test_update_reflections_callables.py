"""Tests for the Step 1.659 update hook that repoints reflection callables.

Issue #2875.

Every test passes explicit ``targets`` — without it the wrapper resolves the
REAL vault registry (``~/Desktop/Valor/reflections.yaml``) and rewrites it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.update.reflections_callables import (
    PROBE_SENTINEL,
    run_reflections_callables_migration,
    run_registry_probe,
)

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


# ─── Step 4.65 acceptance probe (run_registry_probe) ────────────────────────
#
# The probe is what Step 4.65 actually gates on, because
# `ReflectionsCallablesResult.success` is a weaker proposition than the restart
# needs: it is True for `action="noop"`, and "noop" covers both *no registry at
# all* and *the line-anchored rewrite regex matched nothing*.


def _write_flow_style_registry(path: Path, dotted: str) -> Path:
    """A registry entry the Step 1.659 rewriter provably cannot see.

    `_CALLABLE_LINE_RE` is line-anchored on `    callable: <dotted>`; a
    flow-style mapping puts the key mid-line, so `rewrite_callable_lines`
    returns count=0, `migrate_yaml_callables` returns BEFORE its
    `verify_targets_importable()` call, and the wrapper reports a clean noop.
    """
    path.write_text(
        "reflections:\n"
        f"  - {{name: flow-entry, every: 60s, execution_type: function, callable: {dotted}}}\n"
    )
    return path


def test_probe_catches_the_false_green_the_migration_reports_as_noop(tmp_path, monkeypatch):
    """The exact hazard R2-TD2 names: rewriter says "noop", registry is broken."""
    broken = _write_flow_style_registry(
        tmp_path / "reflections.yaml", "agent.sustainability.circuit_health_gate"
    )

    # The rewriter is happy — this is the false green.
    migration = run_reflections_callables_migration(_REPO_ROOT, targets=[broken])
    assert migration.success is True
    assert migration.action == "noop"
    assert "agent.sustainability" in broken.read_text()

    # The probe is not.
    monkeypatch.setenv("REFLECTIONS_YAML", str(broken))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")  # skip the vault: keeps this hermetic
    probe = run_registry_probe(_REPO_ROOT)

    assert probe.success is False
    assert "agent.sustainability" in probe.detail


def test_probe_failure_stamps_the_sentinel_remote_update_reads(tmp_path, monkeypatch):
    """The verdict must cross the process boundary to the shell half of /update."""
    broken = _write_flow_style_registry(
        tmp_path / "reflections.yaml", "agent.sustainability.failure_loop_detector"
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(broken))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    # project_dir is a tmp dir so the real repo's sentinel is never touched; the
    # probe script itself falls back to the helper's own repo root.
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    result = run_registry_probe(fake_repo)

    assert result.success is False
    assert (fake_repo / PROBE_SENTINEL).exists()


def test_probe_success_clears_a_stale_sentinel(tmp_path, monkeypatch):
    """A green run must un-block the next restart, not block it forever."""
    clean = _write_registry(
        tmp_path / "reflections.yaml", ["reflections.agents.circuit_health_gate.run"]
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(clean))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    fake_repo = tmp_path / "repo"
    (fake_repo / "data").mkdir(parents=True)
    stale = fake_repo / PROBE_SENTINEL
    stale.write_text("from a previous failing run\n")

    result = run_registry_probe(fake_repo)

    assert result.success is True, result.detail
    assert not stale.exists()


def test_missing_probe_script_fails_closed(tmp_path, monkeypatch):
    """The probe not running proves nothing — it must not read as a pass."""
    monkeypatch.setattr(
        "scripts.update.reflections_callables.Path.exists", lambda self: False, raising=False
    )

    result = run_registry_probe(tmp_path)

    assert result.success is False
    assert "probe script missing" in result.detail


def test_remote_update_shell_consults_the_same_sentinel():
    """Pin the cross-language contract: the shell must read the path Python writes.

    These are two files in two languages with no shared constant, so the only
    thing keeping them in agreement is this assertion.
    """
    shell = (_REPO_ROOT / "scripts" / "remote-update.sh").read_text()

    assert PROBE_SENTINEL in shell, "remote-update.sh must check the sentinel run.py stamps"
    assert "REGISTRY_PROBE_OK" in shell
    assert "RESTART BLOCKED" in shell
