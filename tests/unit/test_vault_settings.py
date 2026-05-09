"""
Tests for the VaultSettings cascade resolver and vault-relative properties.

Cascade order: VALOR_VAULT_DIR env var > ~/.valor/.env > ~/Desktop/Valor/.env > raise.
Per-path env var overrides (GOOGLE_CREDENTIALS_DIR, PROJECTS_CONFIG_PATH,
REFLECTIONS_YAML) are checked at property access time and win over the master
vault dir.

Validation rejects vault paths inside the repo root or under ephemeral roots
(/tmp, /var/folders). Tests that legitimately need ephemeral paths (because
pytest's tmp_path lives there) opt in via the ``allow_ephemeral_vault`` fixture.
"""

import sys
from pathlib import Path

import pytest

from config.settings import (
    VaultNotResolved,
    VaultPathInvalid,
    VaultSettings,
)

# `config.settings` (the attribute) is shadowed by the Settings() singleton
# re-exported from `config/__init__.py`. Reach into sys.modules for the
# actual module so monkeypatch works on module-level constants.
_VAULT_MODULE = sys.modules["config.settings"]


@pytest.fixture(autouse=True)
def _reset_vault_singleton(monkeypatch):
    """Reset the module-level vault singleton before every test.

    Without this, the first test to touch ``config.settings.vault`` caches a
    resolution that persists across tests and silently masks regressions in
    later tests that change env vars or fake home.
    """
    monkeypatch.setattr(_VAULT_MODULE, "_vault_singleton", None)


@pytest.fixture
def allow_ephemeral_vault(monkeypatch):
    """Disable the ephemeral-root rejection so tests can use tmp_path."""
    monkeypatch.setattr(_VAULT_MODULE, "_EPHEMERAL_PATH_PREFIXES", ())


# ---------------------------------------------------------------------------
# M1: cascade resolution
# ---------------------------------------------------------------------------


class TestVaultDirCascade:
    def test_vault_dir_explicit_env_var(self, monkeypatch, tmp_path, allow_ephemeral_vault):
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
        v = VaultSettings()
        assert v.dir == tmp_path
        assert v.source == "env"

    def test_vault_dir_default_valor_home(self, monkeypatch, tmp_path, allow_ephemeral_vault):
        """~/.valor/.env existence picks ~/.valor before falling back to Desktop."""
        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        valor_home = tmp_path / ".valor"
        valor_home.mkdir(parents=True)
        (valor_home / ".env").write_text("STUB=1\n")

        v = VaultSettings()
        assert v.dir == valor_home
        assert v.source == "default_valor_home"

    def test_vault_dir_valor_home_wins_over_desktop(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        """When both ~/.valor/.env and ~/Desktop/Valor/.env exist, ~/.valor wins."""
        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        valor_home = tmp_path / ".valor"
        valor_home.mkdir(parents=True)
        (valor_home / ".env").write_text("STUB=1\n")

        desktop_valor = tmp_path / "Desktop" / "Valor"
        desktop_valor.mkdir(parents=True)
        (desktop_valor / ".env").write_text("STUB=1\n")

        v = VaultSettings()
        assert v.dir == valor_home
        assert v.source == "default_valor_home"

    def test_vault_dir_default_desktop_legacy_fallback(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        """When ~/.valor is empty and ~/Desktop/Valor/.env exists, fall through to Desktop."""
        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        desktop_valor = tmp_path / "Desktop" / "Valor"
        desktop_valor.mkdir(parents=True)
        (desktop_valor / ".env").write_text("STUB=1\n")

        v = VaultSettings()
        assert v.dir == desktop_valor
        assert v.source == "default_desktop"

    def test_vault_dir_no_resolution_raises(self, monkeypatch, tmp_path, allow_ephemeral_vault):
        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        # Neither ~/.valor nor ~/Desktop/Valor exists under fake home.

        with pytest.raises(VaultNotResolved) as exc:
            VaultSettings()

        msg = str(exc.value)
        assert "VALOR_VAULT_DIR" in msg, "error must enumerate the env-var tier"
        assert ".valor" in msg, "error must enumerate the ~/.valor tier"
        assert "Desktop/Valor" in msg, "error must enumerate the desktop tier"


class TestVaultDirValidation:
    def test_vault_dir_validation_rejects_repo_subdir(self, monkeypatch):
        repo_root = Path(__file__).resolve().parent.parent.parent
        bad = repo_root / "config" / "fake_vault"
        monkeypatch.setenv("VALOR_VAULT_DIR", str(bad))

        with pytest.raises(VaultPathInvalid, match="inside repo"):
            VaultSettings()

    def test_vault_dir_validation_rejects_tmp(self, monkeypatch):
        monkeypatch.setenv("VALOR_VAULT_DIR", "/tmp/foo")
        with pytest.raises(VaultPathInvalid, match="ephemeral"):
            VaultSettings()

    def test_vault_dir_validation_rejects_var_folders(self, monkeypatch):
        monkeypatch.setenv("VALOR_VAULT_DIR", "/var/folders/foo/bar")
        with pytest.raises(VaultPathInvalid, match="ephemeral"):
            VaultSettings()

    def test_vault_dir_validation_runs_on_explicit_construction(self):
        """Explicit dir= construction (e.g. --vault-dir CLI flag) must validate."""
        with pytest.raises(VaultPathInvalid, match="ephemeral"):
            VaultSettings(dir=Path("/tmp/badpath"), source="explicit")


# ---------------------------------------------------------------------------
# M2: vault-relative properties + per-path env overrides
# ---------------------------------------------------------------------------


class TestVaultProperties:
    def test_vault_properties_return_correct_paths(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
        # Strip per-path overrides so we observe pure vault.dir derivation.
        monkeypatch.delenv("GOOGLE_CREDENTIALS_DIR", raising=False)
        monkeypatch.delenv("PROJECTS_CONFIG_PATH", raising=False)
        monkeypatch.delenv("REFLECTIONS_YAML", raising=False)

        v = VaultSettings()
        assert v.env_path == tmp_path / ".env"
        assert v.projects_path == tmp_path / "projects.json"
        assert v.personas_dir == tmp_path / "personas"
        assert v.identity_path == tmp_path / "identity.json"
        assert v.google_credentials_dir == tmp_path
        assert v.reflections_yaml == tmp_path / "reflections.yaml"

    def test_per_path_env_var_overrides_master(self, monkeypatch, tmp_path, allow_ephemeral_vault):
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
        override = tmp_path / "creds-elsewhere"
        monkeypatch.setenv("GOOGLE_CREDENTIALS_DIR", str(override))

        v = VaultSettings()
        assert v.google_credentials_dir == override
        # Other properties unaffected.
        assert v.env_path == tmp_path / ".env"

    def test_per_path_overrides_for_projects_path_and_reflections_yaml(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
        proj_override = tmp_path / "alt-projects.json"
        ref_override = tmp_path / "alt-reflections.yaml"
        monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(proj_override))
        monkeypatch.setenv("REFLECTIONS_YAML", str(ref_override))

        v = VaultSettings()
        assert v.projects_path == proj_override
        assert v.reflections_yaml == ref_override


# ---------------------------------------------------------------------------
# M3: public singleton + log-once
# ---------------------------------------------------------------------------


class TestVaultSingleton:
    def test_vault_singleton_lazy_load(self, monkeypatch, tmp_path, allow_ephemeral_vault):
        """`from config.settings import vault; vault.dir` resolves lazily.

        Set VALOR_VAULT_DIR explicitly so the test does not depend on the host
        machine having a configured vault. The autouse `_reset_vault_singleton`
        fixture guarantees we observe a fresh resolution.
        """
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))

        from config.settings import vault

        assert vault.dir == tmp_path
        assert vault.source == "env"

    def test_get_vault_dir_logs_resolution_source(
        self, monkeypatch, tmp_path, caplog, allow_ephemeral_vault
    ):
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))

        with caplog.at_level("INFO", logger="config.settings"):
            VaultSettings()

        info_records = [r for r in caplog.records if "Vault directory resolved" in r.message]
        assert info_records, "expected an INFO log on resolution"
        assert "source: env" in info_records[0].message


# ---------------------------------------------------------------------------
# load_vault_env helper
# ---------------------------------------------------------------------------


class TestLoadVaultEnv:
    def test_load_vault_env_reads_from_resolved_vault(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        from config.settings import load_vault_env

        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
        (tmp_path / ".env").write_text("VAULT_TEST_VAR=hello-from-vault\n")
        monkeypatch.delenv("VAULT_TEST_VAR", raising=False)

        loaded = load_vault_env()
        assert loaded == tmp_path / ".env"
        import os

        assert os.environ.get("VAULT_TEST_VAR") == "hello-from-vault"

    def test_load_vault_env_falls_back_to_valor_home(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        """When ~/.valor/.env exists, helper picks that as the cascade preferred default."""
        from config.settings import load_vault_env

        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        valor_home_env = tmp_path / ".valor" / ".env"
        valor_home_env.parent.mkdir(parents=True)
        valor_home_env.write_text("VAULT_FALLBACK_VAR=hello-from-valor-home\n")
        monkeypatch.delenv("VAULT_FALLBACK_VAR", raising=False)

        loaded = load_vault_env()
        assert loaded == valor_home_env
        import os

        assert os.environ.get("VAULT_FALLBACK_VAR") == "hello-from-valor-home"

    def test_load_vault_env_falls_back_to_legacy_desktop(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        """When ~/.valor missing but ~/Desktop/Valor/.env exists, fall through to Desktop."""
        from config.settings import load_vault_env

        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        desktop_env = tmp_path / "Desktop" / "Valor" / ".env"
        desktop_env.parent.mkdir(parents=True)
        desktop_env.write_text("VAULT_FALLBACK_VAR=hello-from-desktop\n")
        monkeypatch.delenv("VAULT_FALLBACK_VAR", raising=False)

        loaded = load_vault_env()
        assert loaded == desktop_env
        import os

        assert os.environ.get("VAULT_FALLBACK_VAR") == "hello-from-desktop"

    def test_load_vault_env_returns_none_when_nothing_reachable(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        from config.settings import load_vault_env

        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        # Neither ~/.valor nor ~/Desktop/Valor/.env exists under fake home.

        assert load_vault_env() is None

    def test_explicit_vault_dir_does_not_fall_back_to_defaults(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        """Explicit VALOR_VAULT_DIR with no .env must return None, not probe defaults.

        Regression test for silent secret inheritance: if the user sets
        VALOR_VAULT_DIR to a typo or unmounted external disk, falling back
        to ~/.valor / ~/Desktop/Valor would load somebody else's secrets
        into the process. Fail-loud (None) is correct.
        """
        import os as _os

        from config.settings import load_vault_env

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        explicit_vault = tmp_path / "user_picked_this_path"
        explicit_vault.mkdir()
        # No .env file at the explicit path.
        monkeypatch.setenv("VALOR_VAULT_DIR", str(explicit_vault))

        # Both default tiers have a .env that should NOT be loaded.
        valor_home_env = tmp_path / ".valor" / ".env"
        valor_home_env.parent.mkdir()
        valor_home_env.write_text("WRONG_FALLBACK=valor-home\n")
        desktop_env = tmp_path / "Desktop" / "Valor" / ".env"
        desktop_env.parent.mkdir(parents=True)
        desktop_env.write_text("WRONG_FALLBACK=desktop\n")

        monkeypatch.delenv("WRONG_FALLBACK", raising=False)

        result = load_vault_env()
        assert result is None, (
            "explicit VALOR_VAULT_DIR with missing .env must return None — "
            "falling back would silently inherit secrets from a different vault"
        )
        assert _os.environ.get("WRONG_FALLBACK") is None, (
            "default-tier .env must NOT be loaded when VALOR_VAULT_DIR is set"
        )

    def test_explicit_vault_dir_loads_its_own_env(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        """Explicit VALOR_VAULT_DIR with a real .env loads that and only that."""
        import os as _os

        from config.settings import load_vault_env

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        explicit_vault = tmp_path / "user_picked_this_path"
        explicit_vault.mkdir()
        explicit_env = explicit_vault / ".env"
        explicit_env.write_text("EXPLICIT_VAR=correct-value\n")
        monkeypatch.setenv("VALOR_VAULT_DIR", str(explicit_vault))

        # Decoy at default tier — must NOT be loaded.
        decoy = tmp_path / ".valor" / ".env"
        decoy.parent.mkdir()
        decoy.write_text("EXPLICIT_VAR=wrong-decoy-value\n")

        monkeypatch.delenv("EXPLICIT_VAR", raising=False)

        result = load_vault_env()
        assert result == explicit_env
        assert _os.environ.get("EXPLICIT_VAR") == "correct-value"


# ---------------------------------------------------------------------------
# TCC restriction check
# ---------------------------------------------------------------------------


class TestTccRestriction:
    """``path_is_tcc_restricted`` decides whether secrets get baked into plists."""

    def test_path_under_desktop_is_restricted(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert VaultSettings.path_is_tcc_restricted(tmp_path / "Desktop" / "Valor") is True

    def test_path_under_documents_is_restricted(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert VaultSettings.path_is_tcc_restricted(tmp_path / "Documents" / "Valor") is True

    def test_path_under_icloud_drive_is_restricted(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert (
            VaultSettings.path_is_tcc_restricted(tmp_path / "iCloud Drive" / "Valor") is True
        )

    def test_path_at_restricted_root_itself_is_restricted(self, monkeypatch, tmp_path):
        """Exact match (e.g. vault at ~/Desktop directly) counts as restricted."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert VaultSettings.path_is_tcc_restricted(tmp_path / "Desktop") is True

    def test_valor_home_is_not_restricted(self, monkeypatch, tmp_path):
        """The preferred default ~/.valor is explicitly non-TCC."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert VaultSettings.path_is_tcc_restricted(tmp_path / ".valor") is False

    def test_arbitrary_home_subdir_is_not_restricted(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert VaultSettings.path_is_tcc_restricted(tmp_path / "work" / "vault") is False

    def test_absolute_non_home_path_is_not_restricted(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert VaultSettings.path_is_tcc_restricted(Path("/opt/valor")) is False

    def test_instance_property_reflects_dir(
        self, monkeypatch, tmp_path, allow_ephemeral_vault
    ):
        """``vault.is_tcc_restricted`` is True for a TCC vault and False otherwise."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        tcc_dir = tmp_path / "Desktop" / "Valor"
        tcc_dir.mkdir(parents=True)
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tcc_dir))
        assert VaultSettings().is_tcc_restricted is True

        safe_dir = tmp_path / ".valor"
        safe_dir.mkdir()
        monkeypatch.setenv("VALOR_VAULT_DIR", str(safe_dir))
        assert VaultSettings().is_tcc_restricted is False

    def test_symlink_into_restricted_dir_is_caught(self, monkeypatch, tmp_path):
        """A symlink with a benign name pointing into ~/Desktop must be restricted.

        Regression test for the silent-fail mode where ``~/.valor`` is a
        symlink to ``~/Documents/Valor``: bare path-prefix comparison missed
        this and launchd-spawned services hung on the iCloud-synced target.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        real_dir = tmp_path / "Desktop" / "actually_here"
        real_dir.mkdir(parents=True)
        symlink = tmp_path / ".valor"
        symlink.symlink_to(real_dir)

        assert VaultSettings.path_is_tcc_restricted(symlink) is True

    def test_canonical_mobile_documents_is_restricted(self, monkeypatch, tmp_path):
        """Canonical iCloud Drive path (~/Library/Mobile Documents/...) is restricted."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        canonical_icloud = (
            tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Valor"
        )
        assert VaultSettings.path_is_tcc_restricted(canonical_icloud) is True

    def test_cloud_storage_fileprovider_paths_restricted(
        self, monkeypatch, tmp_path
    ):
        """Sonoma+ FileProvider mount under ~/Library/CloudStorage is restricted.

        Catches Dropbox / OneDrive / Google Drive vaults that get
        FileProvider-gated the same way iCloud does — launchd-spawned
        processes can't read them without baking secrets into the plist.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        for provider_subdir in (
            "Dropbox/Valor",
            "OneDrive-Personal/Valor",
            "GoogleDrive-kevin@example.com/My Drive/Valor",
            "iCloud Drive (User)/Valor",
        ):
            path = tmp_path / "Library" / "CloudStorage" / provider_subdir
            assert VaultSettings.path_is_tcc_restricted(path) is True, (
                f"{provider_subdir} should be FileProvider-gated"
            )
