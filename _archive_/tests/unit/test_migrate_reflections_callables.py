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
import json

import pytest

from scripts.migrate_reflections_callables import (
    CALLABLE_MIGRATIONS,
    MigrationError,
    PartialMigrationError,
    main,
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


# Parametrized over the WHOLE table, so both migration families are covered by
# construction -- including the two #2876 hub entries, whose presence is pinned
# by test_mapping_covers_both_migration_families above. Those two matter
# especially: `_resolve_callable` getattrs them off a module the registry names
# by string, and the registry is untracked vault config no repo grep can see.
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


# --------------------------------------------------------------------------
# Partial application across registry copies.
#
# Scoping the import check per-file (above) means two registry copies with
# divergent content can take different code paths, so one can be written before
# a later one aborts. The write is correct and self-heals on the next /update;
# what must not happen is the abort claiming nothing was written.
# --------------------------------------------------------------------------


def test_abort_after_a_write_reports_what_reached_disk(tmp_path, monkeypatch):
    vault = _write_registry(tmp_path / "vault.yaml", ["agent.sustainability.circuit_health_gate"])
    config = _write_registry(
        tmp_path / "config.yaml",
        ["agent.agent_session_queue.cleanup_stale_branches_all_projects"],
    )
    monkeypatch.setitem(
        CALLABLE_MIGRATIONS,
        "agent.agent_session_queue.cleanup_stale_branches_all_projects",
        "agent.no_such_module_xyz.nope",
    )

    with pytest.raises(PartialMigrationError) as exc:
        migrate_targets([vault, config])

    # The vault write really happened -- this is the state the old bare
    # `rewrote: false` payload misreported.
    assert "reflections.agents.circuit_health_gate.run" in vault.read_text()
    assert "agent.agent_session_queue" in config.read_text(), "config must be untouched"

    written = [r for r in exc.value.completed if r.rewrote]
    assert [r.target for r in written] == [vault]
    assert sum(r.rewrites_count for r in written) == 1


def test_abort_before_any_write_is_not_a_partial(tmp_path, monkeypatch):
    """First-target failure writes nothing, so it stays a plain MigrationError."""
    config = _write_registry(
        tmp_path / "config.yaml",
        ["agent.agent_session_queue.cleanup_stale_branches_all_projects"],
    )
    monkeypatch.setitem(
        CALLABLE_MIGRATIONS,
        "agent.agent_session_queue.cleanup_stale_branches_all_projects",
        "agent.no_such_module_xyz.nope",
    )

    with pytest.raises(MigrationError) as exc:
        migrate_targets([config])

    assert not isinstance(exc.value, PartialMigrationError)
    assert "agent.agent_session_queue" in config.read_text()


def test_dry_run_abort_is_never_a_partial(tmp_path, monkeypatch):
    """A dry run touches no disk, so it can never be partially applied."""
    vault = _write_registry(tmp_path / "vault.yaml", ["agent.sustainability.circuit_health_gate"])
    config = _write_registry(
        tmp_path / "config.yaml",
        ["agent.agent_session_queue.cleanup_stale_branches_all_projects"],
    )
    monkeypatch.setitem(
        CALLABLE_MIGRATIONS,
        "agent.agent_session_queue.cleanup_stale_branches_all_projects",
        "agent.no_such_module_xyz.nope",
    )

    with pytest.raises(MigrationError) as exc:
        migrate_targets([vault, config], dry_run=True)

    assert not isinstance(exc.value, PartialMigrationError)
    assert "agent.sustainability" in vault.read_text(), "dry run must not write"


def test_main_json_abort_reports_the_partial_write(tmp_path, monkeypatch, capsys):
    """The CLI abort payload is the operator-facing artifact, so pin it directly.

    Without this, reverting the abort branch to a bare
    ``{"rewrote": False, "rewrites_count": 0}`` keeps every other test green —
    and that payload is precisely what Step 1.659 surfaces to the human running
    the #2876 propagation check.
    """
    vault = _write_registry(tmp_path / "vault.yaml", ["agent.sustainability.circuit_health_gate"])
    config = _write_registry(
        tmp_path / "config.yaml",
        ["agent.agent_session_queue.cleanup_stale_branches_all_projects"],
    )
    monkeypatch.setitem(
        CALLABLE_MIGRATIONS,
        "agent.agent_session_queue.cleanup_stale_branches_all_projects",
        "agent.no_such_module_xyz.nope",
    )

    rc = main(["--target", str(vault), "--target", str(config), "--json"])
    err = capsys.readouterr().err

    assert rc == 1
    # Select the JSON line by content, not position: coupling the payload
    # assertions to ordering makes an ordering regression fail here with a
    # JSONDecodeError instead of at the endswith() below, which is the
    # assertion that actually owns the "must be LAST" claim.
    payload = json.loads(next(ln for ln in err.splitlines() if ln.startswith("{")))
    assert payload["rewrote"] is True, "abort must not claim nothing was written"
    assert payload["partial"] is True
    assert payload["rewrites_count"] == 1
    assert payload["targets"] == [str(vault)]

    # The plain-text summary must be LAST: the Step 1.659 wrapper keeps only
    # stderr[-500:], which truncates the JSON's leading partial-write evidence.
    assert err.rstrip().endswith(f"ALREADY WRITTEN: {vault} (1 line(s))")


def test_check_idempotent_rejects_dry_run(tmp_path):
    """The safe-looking invocation must fail loudly, not verify nothing.

    `--check-idempotent --dry-run` used to be a silent skip: exit 0, no
    `idempotence OK` line, nothing checked. That made the only invocation that
    does not touch a real registry also the one that proves nothing.
    """
    with pytest.raises(SystemExit) as exc:
        # tmp_path, not a literal /tmp path: the whole point of this test is to
        # fail if the guard regresses, and in exactly that regression the target
        # would start being resolved for real against a machine-global path
        # shared with every other agent testing on this box.
        main(["--target", str(tmp_path / "nope.yaml"), "--check-idempotent", "--dry-run"])
    assert exc.value.code == 2, "argparse.error() exits 2"


def test_check_idempotent_verifies_against_a_target_copy(tmp_path, capsys):
    """The sanctioned safe form: --target a copy, no --dry-run.

    The `idempotence OK` assertion is the one that owns this test's name. Exit 0
    plus a rewritten file both still hold with the `--check-idempotent` block
    deleted entirely, so asserting only those would leave the test green while
    the feature it names is gone — the same hollow shape this PR exists to kill.
    """
    target = _write_registry(
        tmp_path / "refl.yaml",
        ["agent.agent_session_queue.cleanup_stale_branches_all_projects"],
    )

    assert main(["--target", str(target), "--check-idempotent"]) == 0
    assert "agent.session_revival.cleanup_stale_branches_all_projects" in target.read_text()
    assert "idempotence OK" in capsys.readouterr().out


def test_non_import_error_during_target_import_is_still_a_partial(tmp_path, monkeypatch):
    """A target module that raises something other than ImportError still honors the contract.

    Importing a target module executes it, so it can raise anything — a pydantic
    ValidationError out of `config/settings.py` on a machine with a malformed
    `.env`, a RuntimeError, a SyntaxError. Those are just as much "this target is
    unusable" as a missing module, but when the import check caught only
    ImportError they escaped `migrate_targets`' and `main`'s `except
    MigrationError` entirely: `main()` exited on an uncaught traceback, emitting
    no JSON payload and no ALREADY WRITTEN line, while an earlier target was
    already rewritten on disk. Step 1.659 would then WARN with a traceback tail
    and no record of what it had changed.
    """
    vault = _write_registry(tmp_path / "vault.yaml", ["agent.sustainability.circuit_health_gate"])
    config = _write_registry(
        tmp_path / "config.yaml",
        ["agent.agent_session_queue.cleanup_stale_branches_all_projects"],
    )

    real_import = importlib.import_module

    def _boom(name, *a, **kw):
        if name == "agent.session_revival":
            raise RuntimeError("settings blew up while importing the target")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", _boom)

    with pytest.raises(PartialMigrationError) as exc:
        migrate_targets([vault, config])

    # The RuntimeError was converted, not propagated.
    assert "does not import" in str(exc.value)
    assert "settings blew up" in str(exc.value)

    # And the contract still reports what reached disk.
    written = [r for r in exc.value.completed if r.rewrote]
    assert [r.target for r in written] == [vault]
    assert "reflections.agents.circuit_health_gate.run" in vault.read_text()
    assert "agent.agent_session_queue" in config.read_text(), "config must be untouched"
