"""Unit tests for scripts/update/reflections_yaml.py (Step 3.65 hook).

Verifies the thin update-system wrapper that invokes
``scripts/migrate_reflections_yaml.py`` during ``/update``:

- Idempotent: calling ``run_reflections_yaml_migration()`` twice on an
  already-migrated YAML reports no rewrite the second time.
- Migration is invoked via subprocess against the repo's ``.venv`` python so
  the newly-installed ``croniter`` dep is available (matches the migrations
  module pattern).
- Exposes a ``ReflectionsYamlMigrationResult`` dataclass with ``success``,
  ``action`` (``rewrote`` | ``noop`` | ``error``), ``rewrites_count``, and
  ``error`` fields so ``run.py`` Step 3.65 can render machine-readable status.

The migration target path is parameterized via the ``REFLECTIONS_YAML``
environment variable to keep tests isolated from the operator's vault.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "reflections.yaml"
    p.write_text(
        textwrap.dedent(
            """
            reflections:
              - name: a
                interval: 60
                priority: low
                execution_type: function
                callable: x.y
              - name: b
                every: 3600s
                priority: high
                execution_type: function
                callable: x.z
            """
        ).lstrip("\n")
    )
    # Force the migration script's resolver to the temp file regardless of
    # the operator's vault layout.
    monkeypatch.setenv("REFLECTIONS_YAML", str(p))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")
    return p


def test_first_run_rewrites_interval_lines(tmp_yaml: Path):
    from scripts.update.reflections_yaml import run_reflections_yaml_migration

    result = run_reflections_yaml_migration(tmp_yaml.parent.parent)
    assert result.success is True
    assert result.action == "rewrote"
    assert result.rewrites_count == 1
    assert "every: 60s" in tmp_yaml.read_text()
    assert "interval: 60" not in tmp_yaml.read_text()


def test_second_run_is_noop_after_migration(tmp_yaml: Path):
    """Idempotent — a fully-migrated YAML reports action=noop."""
    from scripts.update.reflections_yaml import run_reflections_yaml_migration

    # Pre-migrate.
    run_reflections_yaml_migration(tmp_yaml.parent.parent)
    result = run_reflections_yaml_migration(tmp_yaml.parent.parent)
    assert result.success is True
    assert result.action == "noop"
    assert result.rewrites_count == 0


def test_missing_target_is_a_clean_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A missing reflections.yaml is not an error; the helper reports skipped."""
    monkeypatch.setenv("REFLECTIONS_YAML", str(tmp_path / "absent.yaml"))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")
    from scripts.update.reflections_yaml import run_reflections_yaml_migration

    result = run_reflections_yaml_migration(tmp_path)
    assert result.success is True
    assert result.action == "skipped"
    assert "not found" in (result.error or "").lower() or result.error is None


def test_malformed_yaml_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A YAML entry without any schedule key surfaces error, success=False."""
    p = tmp_path / "reflections.yaml"
    p.write_text(
        textwrap.dedent(
            """
            reflections:
              - name: bad
                priority: low
                execution_type: function
                callable: x.y
            """
        ).lstrip("\n")
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(p))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")
    from scripts.update.reflections_yaml import run_reflections_yaml_migration

    result = run_reflections_yaml_migration(tmp_path)
    assert result.success is False
    assert result.action == "error"
    assert result.error
