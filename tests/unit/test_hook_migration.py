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

from scripts.update import migrations
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
    # Simulate a fresh, already-marked duplicate appended by a regular sync.
    settings["hooks"]["Stop"][0]["hooks"].append(
        {
            "type": "command",
            "command": (
                f"python {hooks_root}/sdlc/validate_sdlc_on_stop.py "
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
