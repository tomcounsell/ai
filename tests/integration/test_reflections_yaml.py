"""Integration tests for reflections YAML migration + load.

Uses fixture YAML (not the production vault file) so we can verify migration
and idempotence end-to-end without mutating real config.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _legacy_yaml(p: Path):
    p.write_text(
        yaml.dump(
            {
                "reflections": [
                    {
                        "name": "test-fixture-old",
                        "interval": 300,
                        "priority": "low",
                        "execution_type": "function",
                        "callable": "x.y",
                    },
                    {
                        "name": "test-fixture-old-2",
                        "interval": 3600,
                        "priority": "low",
                        "execution_type": "function",
                        "callable": "x.y",
                    },
                ]
            }
        )
    )


def _run_migration(yaml_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "migrate_reflections_yaml.py"), *args],
        env={"REFLECTIONS_YAML": str(yaml_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def test_migration_rewrites_interval(tmp_path):
    yaml_path = tmp_path / "reflections.yaml"
    _legacy_yaml(yaml_path)
    res = _run_migration(yaml_path)
    assert res.returncode == 0, res.stderr
    data = yaml.safe_load(yaml_path.read_text())
    for entry in data["reflections"]:
        assert "interval" not in entry
        assert entry.get("schedule", "").startswith("every:")


def test_migration_idempotent(tmp_path):
    yaml_path = tmp_path / "reflections.yaml"
    _legacy_yaml(yaml_path)
    _run_migration(yaml_path)
    first = yaml_path.read_text()
    res2 = _run_migration(yaml_path)
    assert res2.returncode == 0
    second = yaml_path.read_text()
    assert first == second, "Migration should be idempotent"


def test_check_idempotent_pass_after_migrate(tmp_path):
    yaml_path = tmp_path / "reflections.yaml"
    _legacy_yaml(yaml_path)
    _run_migration(yaml_path)
    res = _run_migration(yaml_path, "--check-idempotent")
    assert res.returncode == 0, res.stderr


def test_check_idempotent_fails_on_legacy(tmp_path):
    yaml_path = tmp_path / "reflections.yaml"
    _legacy_yaml(yaml_path)
    res = _run_migration(yaml_path, "--check-idempotent")
    assert res.returncode == 1


def test_load_registry_has_only_fazm_schedules(tmp_path, monkeypatch):
    """After migration every entry has cron:/every:/at: prefix."""
    from agent.reflection_scheduler import load_registry

    yaml_path = tmp_path / "reflections.yaml"
    yaml_path.write_text(
        yaml.dump(
            {
                "reflections": [
                    {
                        "name": "every-test",
                        "schedule": "every:60s",
                        "priority": "low",
                        "execution_type": "function",
                        "callable": "x.y",
                    },
                    {
                        "name": "cron-test",
                        "schedule": "cron:0 9 * * *",
                        "priority": "low",
                        "execution_type": "function",
                        "callable": "x.y",
                    },
                ]
            }
        )
    )
    entries = load_registry(yaml_path)
    assert len(entries) == 2
    for e in entries:
        assert e.schedule.startswith(("cron:", "every:", "at:"))
