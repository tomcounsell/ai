"""Tests for the ``reflections.yaml`` callable migrations.

Two independent families share one table:

* ``agent.sustainability.*`` -> ``reflections.agents.*`` (issue #2875)
* ``agent.agent_session_queue.*`` -> owning module (issue #2876)

Both ship a registry edit to every machine via ``/update`` because
``config/reflections.yaml`` is gitignored (deliberately untracked in c2af09602)
and therefore cannot carry the change as a file edit.
"""

from __future__ import annotations

import importlib

import pytest

from scripts.migrate_reflections_callables import (
    CALLABLE_MIGRATIONS,
    MigrationError,
    migrate_targets,
    migrate_yaml_callables,
    rewrite_callable_lines,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# The mapping itself must describe reality, not intent.
# --------------------------------------------------------------------------


def test_mapping_covers_both_migration_families():
    """Every historical shim/hub path is migrated, and nothing else."""
    assert set(CALLABLE_MIGRATIONS) == {
        # agent/sustainability.py shim (#2875)
        "agent.sustainability.circuit_health_gate",
        "agent.sustainability.session_count_throttle",
        "agent.sustainability.failure_loop_detector",
        "agent.sustainability.session_recovery_drip",
        "agent.sustainability.sustainability_digest",
        # agent/agent_session_queue.py re-export hub (#2876)
        "agent.agent_session_queue.cleanup_corrupted_agent_sessions",
        "agent.agent_session_queue.cleanup_stale_branches_all_projects",
    }


@pytest.mark.parametrize(("old", "new"), sorted(CALLABLE_MIGRATIONS.items()))
def test_every_migration_target_actually_resolves(old, new):
    """Each new dotted path imports to a real callable.

    This is the guard the scheduler does NOT give us: ``ReflectionEntry.validate``
    only checks the string is non-empty, and ``_resolve_callable`` failures are
    swallowed by a broad ``except Exception`` in ``run_reflection``. A typo here
    would load cleanly and silently stop the reflection forever.
    """
    module_path, _, attr = new.rpartition(".")
    module = importlib.import_module(module_path)
    assert callable(getattr(module, attr)), f"{new} is not callable"


def test_digest_maps_to_system_health_digest_module():
    """Regression guard: the module name does NOT match the old callable name.

    ``agent.sustainability.sustainability_digest`` re-exported
    ``reflections.agents.system_health_digest.run`` — a naive
    ``reflections.agents.sustainability_digest.run`` would be an ImportError
    that the scheduler swallows silently.
    """
    assert (
        CALLABLE_MIGRATIONS["agent.sustainability.sustainability_digest"]
        == "reflections.agents.system_health_digest.run"
    )


# --------------------------------------------------------------------------
# Line rewriting
# --------------------------------------------------------------------------


def test_rewrite_preserves_indent_and_double_quotes():
    text = '    callable: "agent.sustainability.circuit_health_gate"\n'
    out, n, _matched = rewrite_callable_lines(text)
    assert n == 1
    assert out == '    callable: "reflections.agents.circuit_health_gate.run"\n'


def test_rewrite_handles_unquoted_and_single_quoted_and_trailing_comment():
    text = (
        "  callable: agent.sustainability.failure_loop_detector\n"
        "  callable: 'agent.sustainability.session_recovery_drip'  # keep me\n"
    )
    out, n, _matched = rewrite_callable_lines(text)
    assert n == 2
    assert "callable: reflections.agents.failure_loop_detector.run\n" in out
    assert "callable: 'reflections.agents.session_recovery_drip.run'  # keep me\n" in out


def test_rewrite_leaves_unrelated_callables_untouched():
    # Both lines must name callables absent from CALLABLE_MIGRATIONS. The hub
    # paths that used to serve as the example here are migrated as of #2876.
    text = (
        '    callable: "reflections.maintenance.run_disk_space_check"\n'
        '    callable: "reflections.stall_advisory.run_stall_advisory"\n'
    )
    out, n, matched = rewrite_callable_lines(text)
    assert n == 0
    assert matched == set()
    assert out == text


def test_rewrite_is_idempotent():
    text = '    callable: "agent.sustainability.session_count_throttle"\n'
    once, n1, _m1 = rewrite_callable_lines(text)
    twice, n2, _m2 = rewrite_callable_lines(once)
    assert n1 == 1
    assert n2 == 0
    assert twice == once


# --------------------------------------------------------------------------
# File-level migration
# --------------------------------------------------------------------------


def _write_registry(path, callables):
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


def test_migrate_yaml_rewrites_and_is_a_noop_on_rerun(tmp_path):
    target = _write_registry(
        tmp_path / "reflections.yaml",
        [
            "agent.sustainability.circuit_health_gate",
            "reflections.maintenance.run_disk_space_check",
            "agent.sustainability.sustainability_digest",
        ],
    )

    first = migrate_yaml_callables(target)
    assert first.rewrote is True
    assert first.rewrites_count == 2

    text = target.read_text()
    assert "agent.sustainability" not in text
    assert "reflections.agents.circuit_health_gate.run" in text
    assert "reflections.agents.system_health_digest.run" in text
    # Untouched entry survives verbatim.
    assert "reflections.maintenance.run_disk_space_check" in text

    second = migrate_yaml_callables(target)
    assert second.rewrote is False
    assert second.rewrites_count == 0
    assert target.read_text() == text


def test_dry_run_does_not_touch_disk(tmp_path):
    target = _write_registry(
        tmp_path / "reflections.yaml", ["agent.sustainability.failure_loop_detector"]
    )
    before = target.read_text()

    result = migrate_yaml_callables(target, dry_run=True)

    assert result.rewrote is True
    assert result.rewrites_count == 1
    assert target.read_text() == before


def test_missing_target_raises(tmp_path):
    with pytest.raises(MigrationError, match="does not exist"):
        migrate_yaml_callables(tmp_path / "nope.yaml")


def test_no_temp_file_left_behind(tmp_path):
    target = _write_registry(
        tmp_path / "reflections.yaml", ["agent.sustainability.circuit_health_gate"]
    )
    migrate_yaml_callables(target)
    assert list(tmp_path.iterdir()) == [target]


# --------------------------------------------------------------------------
# The mtime hazard: both copies get rewritten.
# --------------------------------------------------------------------------


def test_migrate_targets_rewrites_both_vault_and_config(tmp_path):
    """A vault-only rewrite is invisible when the config copy is NEWER.

    ``env_sync.sync_reflections_yaml`` copies only when
    ``config.mtime < vault.mtime``, so a config copy that is newer masks the
    vault indefinitely. The migration therefore rewrites both files rather
    than relying on the sync to propagate.
    """
    vault = _write_registry(tmp_path / "vault.yaml", ["agent.sustainability.circuit_health_gate"])
    config = _write_registry(tmp_path / "config.yaml", ["agent.sustainability.circuit_health_gate"])
    # Make the config copy strictly newer, reproducing the masking condition.
    import os

    vault_mtime = vault.stat().st_mtime
    os.utime(config, (vault_mtime + 100, vault_mtime + 100))

    results = migrate_targets([vault, config])

    assert len(results) == 2
    assert all(r.rewrote for r in results)
    assert "agent.sustainability" not in vault.read_text()
    assert "agent.sustainability" not in config.read_text()


def test_migrate_targets_skips_missing_paths(tmp_path):
    """A machine with no vault (or no materialized config) is not an error."""
    present = _write_registry(
        tmp_path / "present.yaml", ["agent.sustainability.session_recovery_drip"]
    )

    results = migrate_targets([tmp_path / "absent.yaml", present])

    assert len(results) == 1
    assert results[0].target == present
    assert results[0].rewrote is True


# --------------------------------------------------------------------------
# Import-check scoping (#2876).
#
# The table spans two independent migration families. The import check must be
# scoped to the entries a given file actually contains, or one family's broken
# target blocks the other's safe migration -- silently, because Step 1.659 only
# WARNs on failure and `run_reflection` swallows resolution errors per-tick.
# --------------------------------------------------------------------------


def test_matched_unimportable_target_aborts_without_writing(tmp_path, monkeypatch):
    """A broken target for a callable THIS file names must abort before any write."""
    target = _write_registry(
        tmp_path / "reflections.yaml",
        ["agent.agent_session_queue.cleanup_stale_branches_all_projects"],
    )
    before = target.read_text()

    monkeypatch.setitem(
        CALLABLE_MIGRATIONS,
        "agent.agent_session_queue.cleanup_stale_branches_all_projects",
        "agent.no_such_module_xyz.nope",
    )

    with pytest.raises(MigrationError):
        migrate_yaml_callables(target)

    assert target.read_text() == before, "aborted migration must leave the file untouched"
    assert list(tmp_path.iterdir()) == [target], "no temp file may survive an abort"


def test_unmatched_unimportable_target_does_not_block_migration(tmp_path, monkeypatch):
    """A broken target the file does NOT name must not abort the migration.

    This is the regression guard for the scoping fix. Before it,
    ``verify_targets_importable()`` walked the whole table, so breaking any
    entry broke every file's migration regardless of relevance.
    """
    target = _write_registry(
        tmp_path / "reflections.yaml",
        ["agent.agent_session_queue.cleanup_stale_branches_all_projects"],
    )

    # Break a member of the OTHER migration family, which this file never names.
    monkeypatch.setitem(
        CALLABLE_MIGRATIONS,
        "agent.sustainability.circuit_health_gate",
        "reflections.no_such_module_xyz.nope",
    )

    result = migrate_yaml_callables(target)

    assert result.rewrote is True
    assert result.rewrites_count == 1
    assert "agent.session_revival.cleanup_stale_branches_all_projects" in target.read_text()
    assert "agent.agent_session_queue" not in target.read_text()


def test_rewrite_reports_only_the_keys_the_file_contained(tmp_path):
    """``matched`` is the scoping input for the import check -- it must be exact."""
    text = (
        '    callable: "agent.agent_session_queue.cleanup_corrupted_agent_sessions"\n'
        '    callable: "agent.agent_session_queue.cleanup_corrupted_agent_sessions"\n'
        '    callable: "reflections.maintenance.run_disk_space_check"\n'
    )
    _out, n, matched = rewrite_callable_lines(text)

    # Two substitutions, but one distinct key: the registry names this callable
    # twice, which is why the table needs one key rather than two.
    assert n == 2
    assert matched == {"agent.agent_session_queue.cleanup_corrupted_agent_sessions"}


def test_hub_migration_targets_resolve_on_their_owning_modules():
    """The #2876 targets must actually exist where the table says they do.

    `_resolve_callable` getattrs these off the named module at reflection time
    and the failure is swallowed, so a wrong module path here would silently
    disable a daily job.
    """
    for shim_path in (
        "agent.agent_session_queue.cleanup_corrupted_agent_sessions",
        "agent.agent_session_queue.cleanup_stale_branches_all_projects",
    ):
        target = CALLABLE_MIGRATIONS[shim_path]
        module_path, _, attr = target.rpartition(".")
        module = importlib.import_module(module_path)
        assert callable(getattr(module, attr, None)), f"{target} does not resolve"
