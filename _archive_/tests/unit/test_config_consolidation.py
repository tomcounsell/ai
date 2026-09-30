"""Tests for config consolidation: ~/Desktop/Valor/ as single credentials path."""

from pathlib import Path


class TestGoogleAuthSettingsDefault:
    """GoogleAuthSettings.credentials_dir defaults to ~/Desktop/Valor/."""

    def test_default_credentials_dir(self):
        """Default credentials_dir should be ~/Desktop/Valor/ not config/secrets."""
        from config.settings import GoogleAuthSettings

        settings = GoogleAuthSettings()
        assert settings.credentials_dir == Path.home() / "Desktop" / "Valor"

    def test_env_override_still_works(self):
        """GOOGLE_CREDENTIALS_DIR env var should still override the default."""
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

    def test_has_valor_dir(self):
        """config/paths.py should export VALOR_DIR."""
        from config.paths import VALOR_DIR

        assert VALOR_DIR == Path.home() / "Desktop" / "Valor"


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
