"""Tests for config consolidation: vault-aware credentials path resolution."""

import sys
from pathlib import Path

import pytest

import config.settings  # noqa: F401  (force import so sys.modules has the entry)

_VAULT_MODULE = sys.modules["config.settings"]


@pytest.fixture(autouse=True)
def _reset_vault_singleton(monkeypatch):
    """Reset the vault singleton before every test in this module."""
    monkeypatch.setattr(_VAULT_MODULE, "_vault_singleton", None)


class TestGoogleAuthSettingsDefault:
    """GoogleAuthSettings.credentials_dir resolves through the vault cascade."""

    def test_default_credentials_dir_uses_vault(self, monkeypatch, tmp_path):
        """When VALOR_VAULT_DIR is set, the default routes through it."""
        monkeypatch.setattr(_VAULT_MODULE, "_EPHEMERAL_PATH_PREFIXES", ())
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
        monkeypatch.delenv("GOOGLE_CREDENTIALS_DIR", raising=False)

        from config.settings import GoogleAuthSettings

        settings = GoogleAuthSettings()
        assert settings.credentials_dir == tmp_path

    def test_default_credentials_dir_falls_back_to_desktop_when_vault_unresolved(
        self, monkeypatch, tmp_path
    ):
        """No VALOR_VAULT_DIR + no ~/Desktop/Valor/.env → desktop literal fallback."""
        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.delenv("GOOGLE_CREDENTIALS_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        # Fake home has no Desktop/Valor/.env, so cascade raises VaultNotResolved.

        from config.settings import GoogleAuthSettings

        settings = GoogleAuthSettings()
        assert settings.credentials_dir == tmp_path / "Desktop" / "Valor"

    def test_default_credentials_dir_honors_google_credentials_dir_env(
        self, monkeypatch, tmp_path
    ):
        """GOOGLE_CREDENTIALS_DIR override wins via vault.google_credentials_dir."""
        monkeypatch.setattr(_VAULT_MODULE, "_EPHEMERAL_PATH_PREFIXES", ())
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path / "vault"))
        (tmp_path / "vault").mkdir()
        override = tmp_path / "creds-elsewhere"
        monkeypatch.setenv("GOOGLE_CREDENTIALS_DIR", str(override))

        from config.settings import GoogleAuthSettings

        settings = GoogleAuthSettings()
        assert settings.credentials_dir == override

    def test_explicit_credentials_dir_arg_still_works(self):
        """Passing credentials_dir= explicitly still wins."""
        from config.settings import GoogleAuthSettings

        settings = GoogleAuthSettings(credentials_dir=Path("/custom/path"))
        assert settings.credentials_dir == Path("/custom/path")


class TestGoogleAuthNoFallback:
    """tools/google_workspace/auth.py should not have fallback chains."""

    def test_no_legacy_path_reference(self):
        """Auth module should not reference claude_code anywhere."""
        auth_file = (
            Path(__file__).resolve().parent.parent.parent / "tools" / "google_workspace" / "auth.py"
        )
        content = auth_file.read_text()
        assert "claude_code" not in content
        assert "_legacy" not in content
        assert "_primary" not in content

    def test_no_fallback_logic(self):
        """Auth module should not have if-exists fallback logic for paths."""
        auth_file = (
            Path(__file__).resolve().parent.parent.parent / "tools" / "google_workspace" / "auth.py"
        )
        content = auth_file.read_text()
        # Should not have conditional path selection
        assert "if (" not in content or "exists()" not in content


class TestTelegramUsersNoFallback:
    """tools/telegram_users.py should use ~/Desktop/Valor/ directly."""

    def test_no_legacy_path_reference(self):
        """telegram_users should not reference claude_code."""
        users_file = Path(__file__).resolve().parent.parent.parent / "tools" / "telegram_users.py"
        content = users_file.read_text()
        assert "claude_code" not in content

    def test_uses_valor_path(self):
        """telegram_users should reference ~/Desktop/Valor/."""
        users_file = Path(__file__).resolve().parent.parent.parent / "tools" / "telegram_users.py"
        content = users_file.read_text()
        assert "Valor" in content


class TestValorCalendarNoFallback:
    """tools/valor_calendar.py should not have legacy fallback."""

    def test_no_legacy_path_reference(self):
        """valor_calendar should not reference claude_code."""
        cal_file = Path(__file__).resolve().parent.parent.parent / "tools" / "valor_calendar.py"
        content = cal_file.read_text()
        assert "claude_code" not in content
        assert "_legacy_dir" not in content


class TestBridgeNoFallback:
    """bridge/telegram_bridge.py should use ~/Desktop/Valor/ directly."""

    def test_no_legacy_path_reference(self):
        """Bridge should not reference claude_code for dm_whitelist."""
        bridge_file = (
            Path(__file__).resolve().parent.parent.parent / "bridge" / "telegram_bridge.py"
        )
        content = bridge_file.read_text()
        assert "claude_code" not in content


class TestPathsNoSecretsDir:
    """config/paths.py should not export SECRETS_DIR."""

    def test_no_secrets_dir_in_paths(self):
        """SECRETS_DIR should be removed from config/paths.py."""
        paths_file = Path(__file__).resolve().parent.parent.parent / "config" / "paths.py"
        content = paths_file.read_text()
        assert "SECRETS_DIR" not in content

    def test_valor_dir_resolves_via_vault(self, monkeypatch, tmp_path):
        """`config.paths.VALOR_DIR` resolves through the vault cascade.

        The constant is computed at import time. We force a fresh resolution
        by clearing the singleton and re-importing the module.
        """
        import importlib

        monkeypatch.setattr(_VAULT_MODULE, "_EPHEMERAL_PATH_PREFIXES", ())
        monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))

        import config.paths

        importlib.reload(config.paths)
        assert config.paths.VALOR_DIR == tmp_path

    def test_valor_dir_falls_back_to_desktop_when_vault_unresolved(
        self, monkeypatch, tmp_path
    ):
        """No VALOR_VAULT_DIR + no ~/Desktop/Valor/.env → desktop literal."""
        import importlib

        monkeypatch.delenv("VALOR_VAULT_DIR", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        import config.paths

        importlib.reload(config.paths)
        assert config.paths.VALOR_DIR == tmp_path / "Desktop" / "Valor"


class TestNoConfigSecrets:
    """config/secrets/ directory should not exist in the repo."""

    def test_no_secrets_directory(self):
        """config/secrets/ should be removed."""
        secrets_dir = Path(__file__).resolve().parent.parent.parent / "config" / "secrets"
        assert not secrets_dir.exists()

    def test_no_config_dm_whitelist(self):
        """config/dm_whitelist.json should not exist in repo."""
        f = Path(__file__).resolve().parent.parent.parent / "config" / "dm_whitelist.json"
        assert not f.exists()

    def test_no_config_calendar_config(self):
        """config/calendar_config.json should not exist in repo."""
        f = Path(__file__).resolve().parent.parent.parent / "config" / "calendar_config.json"
        assert not f.exists()
