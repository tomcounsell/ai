"""Tests for scripts/update/hook_manifest.py::load_hook_manifest.

Covers the real repo manifest (shape/count) plus the fail-closed contract:
a missing, malformed, or empty manifest must raise HookManifestError rather
than silently returning an empty list (Failure Path Test Strategy in
docs/plans/hook-registration-manifest-dispatcher.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.update.hook_manifest import (
    DEFAULT_MANIFEST_PATH,
    HookDeclaration,
    HookManifestError,
    load_hook_manifest,
)


def test_default_manifest_path_points_at_repo_manifest():
    assert DEFAULT_MANIFEST_PATH.name == "manifest.toml"
    assert DEFAULT_MANIFEST_PATH.parent.name == "hooks"
    assert DEFAULT_MANIFEST_PATH.exists()


def test_load_real_manifest_returns_typed_declarations():
    declarations = load_hook_manifest()

    assert len(declarations) > 0
    assert all(isinstance(d, HookDeclaration) for d in declarations)

    # Every declaration carries the full field set with sane types.
    for d in declarations:
        assert d.manifest_id and isinstance(d.manifest_id, str)
        assert d.event and isinstance(d.event, str)
        assert isinstance(d.matcher, str)
        assert d.script and isinstance(d.script, str)
        assert isinstance(d.timeout, int) and not isinstance(d.timeout, bool)
        assert d.scope in ("project", "global")
        assert d.exit_policy in ("propagate", "deny-only", "suppress")
        assert isinstance(d.args, tuple)

    # manifest_id is unique (it's the generators' add/update/remove key).
    ids = [d.manifest_id for d in declarations]
    assert len(ids) == len(set(ids))


def test_load_real_manifest_covers_both_scopes():
    declarations = load_hook_manifest()
    scopes = {d.scope for d in declarations}
    assert scopes == {"project", "global"}

    global_ids = {d.manifest_id for d in declarations if d.scope == "global"}
    # The 3 deployed SDLC-fork entries from _SDLC_HOOK_DEFS.
    assert len(global_ids) == 3


def test_load_real_manifest_declares_sdlc_reminder_once_with_write_edit_matcher():
    """Deployed-reality baseline fix: sdlc_reminder.py must appear as a single
    declaration per scope with matcher "Write|Edit", not two separate Write
    and Edit entries — restoring Write coverage the old dedup quirk dropped.
    """
    declarations = load_hook_manifest()

    project_reminders = [
        d for d in declarations if d.script == "sdlc_reminder.py" and d.scope == "project"
    ]
    assert len(project_reminders) == 1
    assert project_reminders[0].matcher == "Write|Edit"

    global_reminders = [
        d for d in declarations if d.script == "sdlc/sdlc_reminder.py" and d.scope == "global"
    ]
    assert len(global_reminders) == 1
    assert global_reminders[0].matcher == "Write|Edit"


def test_load_real_manifest_drops_uv_run_for_validate_file_contains():
    """Pre-requisite Bug 4: no entry should carry a uv-run marker; the script
    path itself has no invocation prefix baked in (generators emit bare
    `python`), and args preserve the original CLI flags.
    """
    declarations = load_hook_manifest()
    entry = next(d for d in declarations if d.manifest_id == "validate_file_contains")
    assert entry.script == "validators/validate_file_contains.py"
    assert "-d" in entry.args
    assert "docs/plans" in entry.args


def test_missing_manifest_raises(tmp_path: Path):
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(HookManifestError, match="not found"):
        load_hook_manifest(missing)


def test_malformed_toml_raises(tmp_path: Path):
    bad = tmp_path / "manifest.toml"
    bad.write_text("this is not [ valid toml")
    with pytest.raises(HookManifestError, match="not valid TOML"):
        load_hook_manifest(bad)


def test_empty_manifest_raises(tmp_path: Path):
    empty = tmp_path / "manifest.toml"
    empty.write_text("# no [[hook]] entries here\n")
    with pytest.raises(HookManifestError, match="no \\[\\[hook\\]\\] entries"):
        load_hook_manifest(empty)


def test_entry_missing_required_field_raises(tmp_path: Path):
    bad = tmp_path / "manifest.toml"
    bad.write_text(
        """
[[hook]]
manifest_id = "incomplete"
event = "Stop"
matcher = ""
script = "stop.py"
timeout = 10
scope = "project"
# exit_policy field missing
"""
    )
    with pytest.raises(HookManifestError, match="missing required field"):
        load_hook_manifest(bad)


def test_entry_invalid_scope_raises(tmp_path: Path):
    bad = tmp_path / "manifest.toml"
    bad.write_text(
        """
[[hook]]
manifest_id = "bad_scope"
event = "Stop"
matcher = ""
script = "stop.py"
timeout = 10
scope = "nonsense"
exit_policy = "suppress"
"""
    )
    with pytest.raises(HookManifestError, match="invalid scope"):
        load_hook_manifest(bad)


def test_duplicate_manifest_id_raises(tmp_path: Path):
    bad = tmp_path / "manifest.toml"
    bad.write_text(
        """
[[hook]]
manifest_id = "dup"
event = "Stop"
matcher = ""
script = "stop.py"
timeout = 10
scope = "project"
exit_policy = "suppress"

[[hook]]
manifest_id = "dup"
event = "SubagentStop"
matcher = ""
script = "subagent_stop.py"
timeout = 5
scope = "project"
exit_policy = "suppress"
"""
    )
    with pytest.raises(HookManifestError, match="duplicate manifest_id"):
        load_hook_manifest(bad)


def test_valid_minimal_manifest_loads(tmp_path: Path):
    ok = tmp_path / "manifest.toml"
    ok.write_text(
        """
[[hook]]
manifest_id = "one"
event = "Stop"
matcher = ""
script = "stop.py"
timeout = 10
scope = "project"
exit_policy = "suppress"
"""
    )
    declarations = load_hook_manifest(ok)
    assert len(declarations) == 1
    assert declarations[0].manifest_id == "one"
    assert declarations[0].args == ()


def test_entry_invalid_exit_policy_raises(tmp_path: Path):
    """An unrecognized exit_policy is fail-closed, not silently treated as
    "suppress" — that default is what made every deny inert (#2527)."""
    bad = tmp_path / "manifest.toml"
    bad.write_text(
        """
[[hook]]
manifest_id = "bad_policy"
event = "PreToolUse"
matcher = ""
script = "hook.py"
timeout = 10
scope = "project"
exit_policy = "blocking"
"""
    )
    with pytest.raises(HookManifestError, match="invalid exit_policy"):
        load_hook_manifest(bad)
