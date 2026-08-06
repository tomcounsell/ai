"""Tests for the Pre-requisite Bug 1 hook-registration migration.

Seeds ``~/.claude/settings.json`` (via a fake, monkeypatched HOME) with the
**real deployed 3-entry shape** described in
``docs/plans/hook-registration-manifest-dispatcher.md`` -- not a synthetic
4-distinct-command fixture. The old hardcoded ``_SDLC_HOOK_DEFS`` path
collapsed the two ``sdlc_reminder.py`` (Write/Edit) tuples into a single
``Edit``-matched command-string dedupe, so only 3 legacy entries ever
actually land on disk:

  1. ``PreToolUse`` / ``Bash``  -> ``python <hooks>/sdlc/validate_commit_message.py``
  2. ``PostToolUse`` / ``Edit`` -> ``python <hooks>/sdlc/sdlc_reminder.py`` (no ``|| true``)
  3. ``Stop`` / ``""``          -> ``python <hooks>/sdlc/validate_sdlc_on_stop.py``
     (no ``|| true``)

These tests run the migration against this shape and verify: no duplicates,
the Stop guard is restored, the ``sdlc_reminder.py`` matcher becomes
``"Write|Edit"``, a pre-existing non-SDLC hook survives untouched,
idempotency, the ``.bak`` snapshot, and that invalid JSON is never written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.update import hardlinks, migrations
from scripts.update.hardlinks import _extract_manifest_id

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() so ~/.claude/settings.json points into tmp_path.

    Critically, this must never touch the live operator ~/.claude/settings.json.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


def _legacy_settings_json(hooks_root: Path) -> dict:
    """The real deployed 3-entry shape (Write/Edit collapsed under Edit)."""
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python {hooks_root}/sdlc/validate_commit_message.py",
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python {hooks_root}/sdlc/sdlc_reminder.py",
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python {hooks_root}/sdlc/validate_sdlc_on_stop.py",
                            "timeout": 15,
                        }
                    ],
                }
            ],
        }
    }


def _write_legacy_settings(home: Path, *, extra_non_sdlc_hook: bool = False) -> Path:
    hooks_root = home / ".claude" / "hooks"
    settings = _legacy_settings_json(hooks_root)

    if extra_non_sdlc_hook:
        # A pre-existing, hand-added, non-SDLC user hook that must survive
        # completely untouched (hardlinks.py's "never clobbers non-SDLC
        # hooks" promise).
        settings["hooks"]["PreToolUse"].append(
            {
                "matcher": "Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python /opt/my-own-tool/guard.py",
                        "timeout": 5,
                    }
                ],
            }
        )

    settings_path = home / ".claude" / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return settings_path


def _all_hook_entries(settings: dict) -> list[dict]:
    entries = []
    for event_hooks in settings.get("hooks", {}).values():
        for block in event_hooks:
            entries.extend(block.get("hooks", []))
    return entries


def _entries_by_manifest_id(settings: dict) -> dict[str, list[dict]]:
    by_id: dict[str, list[dict]] = {}
    for entry in _all_hook_entries(settings):
        mid = _extract_manifest_id(entry.get("command", ""))
        if mid:
            by_id.setdefault(mid, []).append(entry)
    return by_id


def test_migration_no_op_when_settings_missing(fake_home):
    """A fresh machine with no ~/.claude/settings.json is a no-op, not an error."""
    (fake_home / ".claude" / "settings.json").unlink(missing_ok=True)
    error = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error is None


def test_migration_rewrites_three_legacy_entries_no_duplicates(fake_home):
    settings_path = _write_legacy_settings(fake_home)

    error = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error is None

    settings = json.loads(settings_path.read_text())
    by_id = _entries_by_manifest_id(settings)

    for mid in (
        "validate_commit_message_fork",
        "sdlc_reminder_fork",
        "validate_sdlc_on_stop_fork",
    ):
        assert mid in by_id, f"missing manifest_id {mid} after migration"
        assert len(by_id[mid]) == 1, f"{mid} has duplicate entries: {by_id[mid]}"

    # Exactly one entry per event overall (no phantom appends).
    assert len(settings["hooks"]["PreToolUse"]) == 1
    assert len(settings["hooks"]["PreToolUse"][0]["hooks"]) == 1
    assert len(settings["hooks"]["PostToolUse"]) == 1
    assert len(settings["hooks"]["PostToolUse"][0]["hooks"]) == 1
    assert len(settings["hooks"]["Stop"]) == 1
    assert len(settings["hooks"]["Stop"][0]["hooks"]) == 1


def test_migrated_commands_point_at_scripts_that_exist(fake_home):
    """Every migrated command must name a script that is actually on disk.

    Regression guard: the migration originally preserved the *legacy* command
    string verbatim, but ``sdlc/validate_commit_message.py`` was renamed to
    ``validate_commit_message_sdlc.py`` in this change and its old hardlink is
    removed by ``RENAMED_REMOVALS`` during sync (run.py Step 1.5), which runs
    *before* this migration (Step 3.6). That left a dangling path in a
    blocking ``PreToolUse``/``Bash`` hook -- ``python <missing>.py`` exits 2,
    and exit 2 from a PreToolUse hook blocks the tool call, so every Bash
    call on every fleet machine would have been blocked until the next
    /update. Matching on the legacy name is fine; writing it back is not.
    """
    settings_path = _write_legacy_settings(fake_home)

    error = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error is None

    settings = json.loads(settings_path.read_text())
    repo_hooks = _REPO_ROOT / ".claude" / "hooks"

    migrated = _entries_by_manifest_id(settings)
    assert migrated, "migration produced no marked entries"

    for manifest_id, entries in migrated.items():
        for entry in entries:
            command = entry["command"]
            # command shape: python <hooks_root>/<script> [args] [|| true] # hook:<id>
            script_token = command.split()[1]
            relative = Path(script_token).relative_to(Path.home() / ".claude" / "hooks")
            assert (repo_hooks / relative).exists(), (
                f"{manifest_id} migrated to {script_token}, which does not exist "
                f"in the repo hooks tree ({relative})"
            )


def test_migration_stop_entry_gains_guard(fake_home):
    settings_path = _write_legacy_settings(fake_home)
    migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)

    settings = json.loads(settings_path.read_text())
    stop_command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "|| true" in stop_command
    assert "validate_sdlc_on_stop_fork" in stop_command


def test_migration_sdlc_reminder_matcher_becomes_write_or_edit(fake_home):
    """The Write coverage silently dropped by the old dedupe is restored.

    Deliberately NOT a phantom-Write append -- there is still exactly one
    on-disk PostToolUse block, its matcher is upgraded in place.
    """
    settings_path = _write_legacy_settings(fake_home)
    migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)

    settings = json.loads(settings_path.read_text())
    post_tool_use_blocks = settings["hooks"]["PostToolUse"]
    assert len(post_tool_use_blocks) == 1
    assert post_tool_use_blocks[0]["matcher"] == "Write|Edit"


def test_migration_preserves_non_sdlc_hook(fake_home):
    settings_path = _write_legacy_settings(fake_home, extra_non_sdlc_hook=True)
    before = json.loads(settings_path.read_text())
    non_sdlc_block = next(b for b in before["hooks"]["PreToolUse"] if b["matcher"] == "Write")

    error = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error is None

    after = json.loads(settings_path.read_text())
    after_block = next(b for b in after["hooks"]["PreToolUse"] if b["matcher"] == "Write")
    assert after_block == non_sdlc_block


def test_migration_idempotent(fake_home):
    settings_path = _write_legacy_settings(fake_home)

    error1 = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error1 is None
    first_pass = json.loads(settings_path.read_text())

    error2 = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error2 is None
    second_pass = json.loads(settings_path.read_text())

    assert first_pass == second_pass


def test_migration_collapses_pre_existing_duplicate(fake_home):
    """Robust to a manifest_id-marked copy already existing (e.g. ordering
    where the regular sync_user_hooks() ran before this migration within the
    same /update invocation and appended a fresh copy alongside the legacy,
    unmarked one)."""
    hooks_root = fake_home / ".claude" / "hooks"
    settings = _legacy_settings_json(hooks_root)
    # Simulate a fresh, already-marked duplicate appended by a regular sync. A
    # regular sync now emits the RESOLVED GLOBAL INTERPRETER, not a bare
    # `python` (issue #2503), so this synthesized generator output uses it too;
    # otherwise the fixture would misrepresent what _merge_hook_settings writes.
    interpreter = hardlinks.resolve_global_interpreter()
    assert interpreter is not None, "no system python3 resolved on this machine"
    settings["hooks"]["Stop"][0]["hooks"].append(
        {
            "type": "command",
            "command": (
                f"{interpreter} {hooks_root}/sdlc/validate_sdlc_on_stop.py "
                "|| true # hook:validate_sdlc_on_stop_fork"
            ),
            "timeout": 15,
        }
    )
    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    error = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error is None

    after = json.loads(settings_path.read_text())
    stop_entries = after["hooks"]["Stop"][0]["hooks"]
    assert len(stop_entries) == 1
    assert "|| true" in stop_entries[0]["command"]


def test_migration_writes_timestamped_backup_before_write(fake_home):
    settings_path = _write_legacy_settings(fake_home)
    original_text = settings_path.read_text()

    error = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error is None

    claude_dir = fake_home / ".claude"
    backups = list(claude_dir.glob("settings.json.bak.*"))
    assert len(backups) == 1, f"expected exactly one .bak file, found {backups}"
    assert backups[0].read_text() == original_text


def test_migration_no_op_run_produces_no_backup(fake_home):
    """A settings.json where every legacy entry already carries its marker is
    a true no-op -- no backup, no rewrite."""
    settings_path = _write_legacy_settings(fake_home)
    migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)

    claude_dir = fake_home / ".claude"
    backups_after_first_run = list(claude_dir.glob("settings.json.bak.*"))
    assert len(backups_after_first_run) == 1

    error = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error is None

    backups_after_second_run = list(claude_dir.glob("settings.json.bak.*"))
    assert len(backups_after_second_run) == 1, "idempotent re-run must not write a 2nd backup"

    settings_path.stat()  # still present


def test_migration_rejects_invalid_json_before_write(fake_home, monkeypatch):
    """A result that fails a JSON-validity round-trip must never be written."""
    settings_path = _write_legacy_settings(fake_home)
    original_text = settings_path.read_text()

    def _boom(*args, **kwargs):
        raise TypeError("simulated non-serializable object")

    monkeypatch.setattr(migrations.json, "dumps", _boom)

    error = migrations._migrate_hook_registration_manifest_ids(_REPO_ROOT)
    assert error is not None
    assert "JSON" in error or "json" in error

    # Original file untouched -- the bad write never happened.
    assert settings_path.read_text() == original_text


def test_migration_registered_in_migrations_dict():
    assert "hook_registration_manifest_ids" in migrations.MIGRATIONS
    fn, description = migrations.MIGRATIONS["hook_registration_manifest_ids"]
    assert fn is migrations._migrate_hook_registration_manifest_ids
    assert description


# ---------------------------------------------------------------------------
# Verification table: "Stop hook keeps guard in generated user scope"
# ---------------------------------------------------------------------------


def test_stop_guard_user_scope(tmp_path, monkeypatch):
    """Every Stop hook in a freshly generated user-scope settings.json must
    carry ``|| true`` -- generated straight from the manifest via
    sync_user_hooks(), never touching the live operator ~/.claude/settings.json.
    """
    from scripts.update import hardlinks

    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))

    result = hardlinks.sync_user_hooks(_REPO_ROOT)
    assert result.errors == 0, [a.error for a in result.actions if a.error]

    generated_settings_path = home / ".claude" / "settings.json"
    assert generated_settings_path.exists()
    settings = json.loads(generated_settings_path.read_text())

    stop_blocks = settings.get("hooks", {}).get("Stop", [])
    assert stop_blocks, "expected at least one generated Stop hook block"
    for block in stop_blocks:
        for hook_entry in block.get("hooks", []):
            assert "|| true" in hook_entry["command"], (
                f"Stop hook missing || true guard: {hook_entry['command']}"
            )


# ---------------------------------------------------------------------------
# Legacy UNMARKED global-registration sweep (issue #2503, spike-3)
#
# _migrate_sweep_legacy_unmarked_global_hooks removes the stale pre-manifest,
# UNMARKED ~/.claude/settings.json entry for validate_no_raw_redis_delete.py.
# It is registration-only (deletes no files) and gated on a PRECISE-MATCH
# allowlist (_LEGACY_UNMARKED_SWEEP_TARGETS) so a hand-added user hook living
# inside ~/.claude/hooks/ is NOT swept -- closing the gap the /opt/ fixture at
# :106 (outside the directory) does not cover.
# ---------------------------------------------------------------------------


def _sweep_target_command(hooks_root: Path) -> str:
    """The exact deployed-reality command spike-3 found in the live settings.

        PreToolUse / matcher "Bash" / timeout 5
        python ~/.claude/hooks/validators/validate_no_raw_redis_delete.py

    Bare ``python`` here is the pre-manifest legacy shape actually on disk.
    """
    return f"python {hooks_root}/validators/validate_no_raw_redis_delete.py"


def _write_sweep_settings(
    home: Path,
    *,
    marked: bool = False,
    foreign_in_dir: bool = False,
    foreign_outside_dir: bool = False,
    marker_id: str = "",
) -> Path:
    """Seed ~/.claude/settings.json with the sweep-target entry plus optional
    survivors. Returns the settings path."""
    hooks_root = home / ".claude" / "hooks"
    target_command = _sweep_target_command(hooks_root)
    if marked:
        target_command = f"{target_command} # hook:{marker_id or 'validate_no_raw_redis_delete'}"

    pre_tool_use = [
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": target_command, "timeout": 5}],
        }
    ]

    if foreign_in_dir:
        # A hand-added user hook living INSIDE ~/.claude/hooks/, unmarked, whose
        # script is NOT in _LEGACY_UNMARKED_SWEEP_TARGETS -- must survive.
        pre_tool_use.append(
            {
                "matcher": "Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python {hooks_root}/validators/my_own_guard.py",
                        "timeout": 5,
                    }
                ],
            }
        )
    if foreign_outside_dir:
        # A foreign hook OUTSIDE ~/.claude/hooks/ -- must survive (mirrors :106).
        pre_tool_use.append(
            {
                "matcher": "Edit",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python /opt/my-own-tool/guard.py",
                        "timeout": 5,
                    }
                ],
            }
        )

    settings = {"hooks": {"PreToolUse": pre_tool_use}}
    settings_path = home / ".claude" / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return settings_path


def _sweep_commands(settings: dict) -> list[str]:
    return [e.get("command", "") for e in _all_hook_entries(settings)]


def test_sweep_removes_unmarked_target_entry(fake_home):
    settings_path = _write_sweep_settings(fake_home)

    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None

    after = json.loads(settings_path.read_text())
    commands = _sweep_commands(after)
    assert not any("validate_no_raw_redis_delete.py" in c for c in commands), (
        f"sweep target survived: {commands}"
    )
    # The whole PreToolUse event is emptied and pruned.
    assert "PreToolUse" not in after.get("hooks", {})


def test_sweep_leaves_marked_target_untouched(fake_home):
    """A MARKED entry is already converged by the sibling migration; the sweep
    only claims UNMARKED pre-manifest entries."""
    settings_path = _write_sweep_settings(fake_home, marked=True)
    before = settings_path.read_text()

    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None

    assert settings_path.read_text() == before, "marked entry must be left untouched"


def test_sweep_preserves_unmarked_hook_not_in_target_allowlist(fake_home):
    """CONCERN closer: a hand-added UNMARKED hook living INSIDE ~/.claude/hooks/
    whose script is NOT in _LEGACY_UNMARKED_SWEEP_TARGETS must survive. This is
    the case the /opt/ fixture at :106 (outside the directory) does not cover."""
    settings_path = _write_sweep_settings(fake_home, foreign_in_dir=True)

    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None

    commands = _sweep_commands(json.loads(settings_path.read_text()))
    assert any("my_own_guard.py" in c for c in commands), (
        f"hand-added in-directory hook was wrongly swept: {commands}"
    )
    # And the actual target is still gone.
    assert not any("validate_no_raw_redis_delete.py" in c for c in commands)


def test_sweep_preserves_foreign_hook_outside_directory(fake_home):
    settings_path = _write_sweep_settings(fake_home, foreign_outside_dir=True)

    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None

    commands = _sweep_commands(json.loads(settings_path.read_text()))
    assert any("/opt/my-own-tool/guard.py" in c for c in commands), (
        f"foreign hook outside ~/.claude/hooks/ was wrongly swept: {commands}"
    )


def test_sweep_deletes_no_files(fake_home):
    """Registration-only proof: an unrecognized FILE placed under a fake
    ~/.claude/hooks/ is left ON DISK, even the swept target's own script."""
    hooks_root = fake_home / ".claude" / "hooks"
    target_file = hooks_root / "validators" / "validate_no_raw_redis_delete.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("# stub validator\n")
    stray_file = hooks_root / "validators" / "some_unrecognized_file.py"
    stray_file.write_text("# stray\n")

    _write_sweep_settings(fake_home)

    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None

    # Registration is gone, but NO file was removed.
    assert target_file.exists(), "sweep deleted the target's script file"
    assert stray_file.exists(), "sweep deleted an unrelated file"


def test_sweep_no_op_when_settings_missing(fake_home):
    (fake_home / ".claude" / "settings.json").unlink(missing_ok=True)
    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None
    assert not (fake_home / ".claude" / "settings.json").exists()


def test_sweep_no_op_on_empty_settings(fake_home):
    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.write_text("{}\n")

    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None
    assert settings_path.read_text() == "{}\n", "empty {} must be left byte-identical"


def test_sweep_no_op_when_no_hooks_key(fake_home):
    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.write_text(json.dumps({"permissions": {"allow": []}}, indent=2) + "\n")
    before = settings_path.read_text()

    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None
    assert settings_path.read_text() == before


def test_sweep_writes_timestamped_backup_before_write(fake_home):
    settings_path = _write_sweep_settings(fake_home)
    original_text = settings_path.read_text()

    error = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error is None

    backups = list((fake_home / ".claude").glob("settings.json.bak.*"))
    assert len(backups) == 1, f"expected exactly one .bak file, found {backups}"
    assert backups[0].read_text() == original_text


def test_sweep_idempotent(fake_home):
    settings_path = _write_sweep_settings(fake_home)

    error1 = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error1 is None
    first = settings_path.read_text()

    error2 = migrations._migrate_sweep_legacy_unmarked_global_hooks(_REPO_ROOT)
    assert error2 is None
    second = settings_path.read_text()

    assert first == second
    # No second backup on the no-op re-run.
    backups = list((fake_home / ".claude").glob("settings.json.bak.*"))
    assert len(backups) == 1, "idempotent re-run must not write a 2nd backup"


def test_sweep_registered_in_migrations_dict():
    assert "sweep_legacy_unmarked_global_hooks" in migrations.MIGRATIONS
    fn, description = migrations.MIGRATIONS["sweep_legacy_unmarked_global_hooks"]
    assert fn is migrations._migrate_sweep_legacy_unmarked_global_hooks
    assert description


# ---------------------------------------------------------------------------
# Issue #2521: _migrate_prune_orphaned_premanifest_hook_files
#
# The FILE prune that #2503 deliberately carved out. Delete-by-inventory,
# keep-by-default: only paths named in _PREMANIFEST_HOOK_ORPHANS may go, and
# only if they are not in _USER_HOOK_KEEP_PATHS and not named by a surviving
# settings.json command. Every gate compares the path RELATIVE to
# ~/.claude/hooks/ -- top-level sdlc_reminder.py (residue) and
# sdlc/sdlc_reminder.py (live global script) share a basename.
# ---------------------------------------------------------------------------

# Files the migration must remove, relative to ~/.claude/hooks/.
_EXPECTED_REMOVED = (
    "validators/validate_no_raw_redis_delete.py",
    "validators/validate_merge_guard.py",
    "validators/__pycache__/validate_no_raw_redis_delete.cpython-313.pyc",
    "dispatch/pre_tool_use_bash.py",
    "hook_utils/constants.py",
    "__pycache__/stop.cpython-313.pyc",
    "format_file.py",
    "hook_python",
    "manifest.toml",
    "post_compact.py",
    "post_tool_use.py",
    "pre_tool_use.py",
    "sdlc_reminder.py",
    "stop.py",
    "subagent_stop.py",
    "user_prompt_submit.py",
)

# Files the migration must never touch.
_EXPECTED_KEPT = (
    "sdlc/validate_commit_message_sdlc.py",
    "sdlc/sdlc_reminder.py",
    "sdlc/validate_sdlc_on_stop.py",
    "sdlc/sdlc_context.py",
    "sdlc/__pycache__/sdlc_context.cpython-313.pyc",
)

# Files not on the inventory at all -- kept by default, and they keep their
# containing directory alive with them.
_UNRECOGNIZED = (
    "validators/my_own_validator.py",
    "my_notes.md",
)


def _seed_premanifest_tree(home: Path) -> Path:
    """Build a synthetic pre-manifest ~/.claude/hooks/ tree. Returns hooks_root."""
    hooks_root = home / ".claude" / "hooks"
    for rel in _EXPECTED_REMOVED + _EXPECTED_KEPT + _UNRECOGNIZED:
        path = hooks_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n")
    return hooks_root


def _backups(home: Path) -> list[Path]:
    return list((home / ".claude").glob("hooks.bak.*"))


def _prune(project_dir: Path = _REPO_ROOT) -> str | None:
    return migrations._migrate_prune_orphaned_premanifest_hook_files(project_dir)


class TestPruneOrphanedPremanifestHookFiles:
    def test_removes_inventory_and_keeps_keep_set(self, fake_home):
        hooks_root = _seed_premanifest_tree(fake_home)

        assert _prune() is None

        for rel in _EXPECTED_REMOVED:
            assert not (hooks_root / rel).exists(), f"{rel} should have been removed"
        for rel in _EXPECTED_KEPT:
            assert (hooks_root / rel).exists(), f"{rel} must survive"

    def test_top_level_sdlc_reminder_removed_but_sdlc_subdir_one_survives(self, fake_home):
        """Highest-value assertion: the gates key on the relative path, not the basename."""
        hooks_root = _seed_premanifest_tree(fake_home)
        assert (hooks_root / "sdlc_reminder.py").exists()
        assert (hooks_root / "sdlc" / "sdlc_reminder.py").exists()

        assert _prune() is None

        assert not (hooks_root / "sdlc_reminder.py").exists()
        assert (hooks_root / "sdlc" / "sdlc_reminder.py").exists()

    def test_unrecognized_files_and_their_directory_survive(self, fake_home):
        hooks_root = _seed_premanifest_tree(fake_home)

        assert _prune() is None

        for rel in _UNRECOGNIZED:
            assert (hooks_root / rel).exists(), f"unrecognized {rel} must be kept"
        assert (hooks_root / "validators").is_dir(), (
            "a tree holding an unrecognized user file must survive"
        )
        # Fully-emptied trees are gone.
        assert not (hooks_root / "dispatch").exists()
        assert not (hooks_root / "hook_utils").exists()
        assert not (hooks_root / "__pycache__").exists()

    def test_takes_full_tree_backup_containing_removed_files(self, fake_home):
        hooks_root = _seed_premanifest_tree(fake_home)

        assert _prune() is None

        backups = _backups(fake_home)
        assert len(backups) == 1, f"expected exactly one .bak snapshot, found {backups}"
        for rel in _EXPECTED_REMOVED:
            snapshot = backups[0] / rel
            assert snapshot.exists(), f"{rel} missing from snapshot"
            assert snapshot.read_text() == f"# {rel}\n"
        assert (hooks_root / "sdlc" / "sdlc_context.py").exists()

    def test_second_run_is_a_no_op(self, fake_home):
        hooks_root = _seed_premanifest_tree(fake_home)

        assert _prune() is None
        first_state = sorted(p.relative_to(hooks_root) for p in hooks_root.rglob("*"))

        assert _prune() is None
        assert sorted(p.relative_to(hooks_root) for p in hooks_root.rglob("*")) == first_state
        assert len(_backups(fake_home)) == 1, "idempotent re-run must not write a 2nd .bak"

    def test_absent_hooks_dir_is_a_clean_no_op(self, fake_home):
        assert not (fake_home / ".claude" / "hooks").exists()

        assert _prune() is None

        assert not (fake_home / ".claude" / "hooks").exists()
        assert _backups(fake_home) == []

    @pytest.mark.parametrize("settings_body", [None, "{ this is not json"])
    def test_absent_and_malformed_settings_yield_the_same_removal_set(
        self, fake_home, tmp_path, settings_body
    ):
        """Gate (c) is PERMISSIVE on absent, unreadable, and malformed alike."""
        hooks_root = _seed_premanifest_tree(fake_home)
        settings_path = fake_home / ".claude" / "settings.json"
        if settings_body is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_text(settings_body)

        assert _prune() is None

        survivors = sorted(
            str(p.relative_to(hooks_root)) for p in hooks_root.rglob("*") if p.is_file()
        )
        assert survivors == sorted(_EXPECTED_KEPT + _UNRECOGNIZED)

    def test_path_named_by_surviving_settings_command_is_not_removed(self, fake_home):
        hooks_root = _seed_premanifest_tree(fake_home)
        settings_path = fake_home / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"python {hooks_root}/pre_tool_use.py",
                                        "timeout": 5,
                                    },
                                    {
                                        "type": "command",
                                        "command": "python /opt/my-own-tool/guard.py",
                                        "timeout": 5,
                                    },
                                ],
                            }
                        ]
                    }
                },
                indent=2,
            )
            + "\n"
        )

        assert _prune() is None

        assert (hooks_root / "pre_tool_use.py").exists(), (
            "gate (c) must veto removal of a still-registered path"
        )
        assert not (hooks_root / "post_tool_use.py").exists()

    def test_registered_in_migrations_dict(self):
        assert "prune_orphaned_premanifest_hook_files" in migrations.MIGRATIONS
        fn, description = migrations.MIGRATIONS["prune_orphaned_premanifest_hook_files"]
        assert fn is migrations._migrate_prune_orphaned_premanifest_hook_files
        assert "#2521" in description

    def test_unwritable_backup_aborts_before_any_unlink(self, fake_home, monkeypatch):
        hooks_root = _seed_premanifest_tree(fake_home)
        before = sorted(str(p.relative_to(hooks_root)) for p in hooks_root.rglob("*"))

        def _boom(src, dst, **kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(migrations.shutil, "copytree", _boom)

        error = _prune()
        assert error is not None
        assert "read-only file system" in error
        assert sorted(str(p.relative_to(hooks_root)) for p in hooks_root.rglob("*")) == before

    def test_legacy_dir_symlink_into_repo_is_never_pruned(self, fake_home, tmp_path):
        """~/.claude/hooks may be a legacy SYMLINK to <repo>/.claude/hooks.

        On such a machine the "user tree" and the repo's own git-tracked source
        tree are the same directory, so every inventory path resolves to a live
        repo file. An unguarded run deletes this repo's real hooks -- observed
        on a live machine during the #2521 build. The migration must no-op.
        """
        repo = tmp_path / "repo"
        repo_hooks = repo / ".claude" / "hooks"
        repo_hooks.mkdir(parents=True)
        seeded = _seed_premanifest_tree(fake_home)
        # Re-home the seeded tree inside the fake repo, then symlink the user
        # path at it -- the exact legacy layout.
        for child in sorted(seeded.iterdir()):
            child.rename(repo_hooks / child.name)
        seeded.rmdir()
        (fake_home / ".claude" / "hooks").symlink_to(repo_hooks)

        before = sorted(str(p.relative_to(repo_hooks)) for p in repo_hooks.rglob("*"))

        assert _prune(project_dir=repo) is None

        assert sorted(str(p.relative_to(repo_hooks)) for p in repo_hooks.rglob("*")) == before
        assert _backups(fake_home) == [], "a symlinked user tree must not produce a .bak"
