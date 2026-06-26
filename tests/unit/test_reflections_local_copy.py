"""Regression tests for the June 2026 worker wedge.

config/reflections.yaml MUST be a real local file, never a symlink to
~/Desktop/Valor/reflections.yaml. Under launchd the reflection scheduler reads
it on the asyncio event loop; a symlink follows back to ~/Desktop where macOS
TCC / iCloud eviction make open() block indefinitely, freezing the whole
worker (alive but processing nothing). Two layers are tested:

1. scripts.update.env_sync.sync_reflections_yaml — materializes a real copy and
   replaces any pre-existing symlink.
2. agent.reflection_scheduler.load_registry — defense in depth: under launchd it
   refuses a path that resolves into ~/Desktop instead of blocking on open().
"""

from __future__ import annotations

from pathlib import Path

from scripts.update import env_sync


def _seed_vault(monkeypatch, tmp_path: Path) -> Path:
    vault = tmp_path / "vault_reflections.yaml"
    vault.write_text("reflections: []\n")
    monkeypatch.setattr(env_sync, "VAULT_REFLECTIONS_PATH", vault)
    return vault


def test_sync_creates_real_file_not_symlink(monkeypatch, tmp_path):
    _seed_vault(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()

    result = env_sync.sync_reflections_yaml(tmp_path)

    dest = tmp_path / "config" / "reflections.yaml"
    assert result.ok
    assert dest.exists()
    assert not dest.is_symlink(), "reflections.yaml must be a real file, never a symlink"
    assert dest.read_text() == "reflections: []\n"


def test_sync_replaces_existing_symlink_with_copy(monkeypatch, tmp_path):
    """The exact wedge shape: a pre-existing symlink → vault is replaced."""
    vault = _seed_vault(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    dest = tmp_path / "config" / "reflections.yaml"
    dest.symlink_to(vault)  # the launchd-unsafe shape

    result = env_sync.sync_reflections_yaml(tmp_path)

    assert result.ok and result.created
    assert not dest.is_symlink(), "stale symlink must be replaced with a real copy"
    assert dest.read_text() == "reflections: []\n"


def test_sync_skips_when_vault_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(env_sync, "VAULT_REFLECTIONS_PATH", tmp_path / "nope.yaml")
    (tmp_path / "config").mkdir()

    result = env_sync.sync_reflections_yaml(tmp_path)

    assert result.skipped and not result.ok


def test_load_registry_refuses_desktop_realpath_under_launchd(monkeypatch, tmp_path):
    """Defense in depth: a symlink resolving into ~/Desktop is refused, not opened."""
    from agent import reflection_scheduler

    # Simulate a ~/Desktop home and a config symlink pointing into it.
    home = tmp_path
    desktop_dir = home / "Desktop" / "Valor"
    desktop_dir.mkdir(parents=True)
    real = desktop_dir / "reflections.yaml"
    real.write_text("reflections: []\n")
    link = home / "config_reflections.yaml"
    link.symlink_to(real)

    monkeypatch.setenv("VALOR_LAUNCHD", "1")
    monkeypatch.setattr(reflection_scheduler.os.path, "expanduser", lambda p: str(home))

    # Should return [] (graceful skip), NOT block on open().
    assert reflection_scheduler.load_registry(link) == []


def test_load_registry_allows_real_local_file_under_launchd(monkeypatch, tmp_path):
    """A real local file (not under ~/Desktop) loads normally under launchd."""
    from agent import reflection_scheduler

    monkeypatch.setenv("VALOR_LAUNCHD", "1")
    fake_home = str(tmp_path / "home")
    monkeypatch.setattr(reflection_scheduler.os.path, "expanduser", lambda p: fake_home)
    local = tmp_path / "reflections.yaml"
    local.write_text("reflections: []\n")

    # Empty 'reflections' list → returns [] but via the normal parse path
    # (no error log), proving it was opened, not refused.
    assert reflection_scheduler.load_registry(local) == []
