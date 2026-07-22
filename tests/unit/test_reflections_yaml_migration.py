"""Unit tests for the reflections.yaml migration script (issue #1273 Q3).

Covers:

- ``interval: N`` rewrite to ``every: Ns`` (atomic).
- Idempotence — running on already-migrated YAML is a no-op.
- Pre-flight rejection — malformed entries abort before writing.
- Atomic temp-file + rename — partial files never appear on disk.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def tmp_yaml(tmp_path: Path) -> Path:
    return tmp_path / "reflections.yaml"


def _write(p: Path, content: str) -> None:
    p.write_text(textwrap.dedent(content).lstrip("\n"))


def _read(p: Path) -> str:
    return p.read_text()


class TestInterval:
    def test_rewrites_interval_to_every(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                interval: 60
                priority: low
                execution_type: function
                callable: x.y
              - name: b
                interval: 3600
                priority: high
                execution_type: function
                callable: x.z
            """,
        )

        result = migrate_yaml(tmp_yaml, dry_run=False)
        assert result.rewrote is True

        text = _read(tmp_yaml)
        # Both interval lines are gone; replaced by every:.
        assert "interval:" not in text
        assert "every: 60s" in text
        assert "every: 3600s" in text

    def test_idempotent_on_already_migrated(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                every: 60s
                priority: low
                execution_type: function
                callable: x.y
            """,
        )

        before = _read(tmp_yaml)
        result = migrate_yaml(tmp_yaml, dry_run=False)
        assert result.rewrote is False  # nothing to do
        after = _read(tmp_yaml)
        assert before == after  # exact byte-equality

    def test_passes_through_cron_at(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                cron: "0 9 * * *"
                priority: low
                execution_type: function
                callable: x.y
              - name: b
                at: "2099-01-01T00:00:00+00:00"
                priority: high
                execution_type: function
                callable: x.z
            """,
        )

        result = migrate_yaml(tmp_yaml, dry_run=False)
        assert result.rewrote is False
        text = _read(tmp_yaml)
        assert "cron:" in text
        assert "at:" in text


class TestPreflight:
    def test_aborts_when_entry_has_no_schedule_after_rewrite(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import MigrationError, migrate_yaml

        # Malformed: no interval, no every/cron/at — entry can't be migrated.
        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                priority: low
                execution_type: function
                callable: x.y
            """,
        )
        with pytest.raises(MigrationError, match="no schedule|missing"):
            migrate_yaml(tmp_yaml, dry_run=False)

        # File remains untouched (no temp-file leak).
        assert "schedule" not in _read(tmp_yaml)

    def test_aborts_when_interval_is_zero(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import MigrationError, migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                interval: 0
                priority: low
                execution_type: function
                callable: x.y
            """,
        )
        with pytest.raises(MigrationError):
            migrate_yaml(tmp_yaml, dry_run=False)


class TestDryRun:
    def test_dry_run_does_not_write(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                interval: 60
                priority: low
                execution_type: function
                callable: x.y
            """,
        )
        before = _read(tmp_yaml)
        result = migrate_yaml(tmp_yaml, dry_run=True)
        # dry_run reports the rewrite would have occurred but does not write.
        assert result.rewrote is True
        assert _read(tmp_yaml) == before


class TestSentryTriageCutover:
    """Guard against reintroducing the local sentry-issue-triage reflection
    entry now that it has migrated to a Claude Code Routine (cloud) — see
    docs/features/cowork-tasks.md. config/reflections.yaml carries only a
    pointer comment where the block used to live; a re-add here (e.g. by a
    parallel/concurrent agent run or a stale merge) would silently double-run
    the triage."""

    def test_sentry_issue_triage_absent_from_repo_registry(self):
        import yaml

        repo_root = Path(__file__).resolve().parent.parent.parent
        registry_path = repo_root / "config" / "reflections.yaml"
        assert registry_path.exists(), "config/reflections.yaml should exist in-repo"

        data = yaml.safe_load(registry_path.read_text())
        names = [r["name"] for r in data["reflections"]]
        assert "sentry-issue-triage" not in names

        # The pointer comment documenting the migration is still present.
        assert "sentry-issue-triage migrated to a Claude Code Routine" in registry_path.read_text()


class TestNoTempFileLeak:
    def test_no_partial_temp_file_on_failure(self, tmp_yaml: Path, monkeypatch):
        """If the rename step fails, the original file must remain unchanged
        and no ``.migrate.tmp`` sibling can be left behind."""
        from scripts import migrate_reflections_yaml as mod

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                interval: 60
                priority: low
                execution_type: function
                callable: x.y
            """,
        )

        original = _read(tmp_yaml)

        def _boom(src, dst):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(mod.os, "replace", _boom)
        with pytest.raises(OSError):
            mod.migrate_yaml(tmp_yaml, dry_run=False)

        # Original is intact.
        assert _read(tmp_yaml) == original
        # No leftover temp file.
        siblings = list(tmp_yaml.parent.glob("*.migrate.tmp"))
        assert siblings == []
        # Cleanup any stray (defensive)
        for s in siblings:
            os.unlink(s)
