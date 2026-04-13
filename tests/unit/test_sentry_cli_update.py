"""Unit tests for scripts/update/sentry_cli.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.update.sentry_cli import (
    InstallResult,
    check_sentry_cli,
    install_or_update,
    install_sentry_cli,
)

SENTRY_BIN = "/usr/local/bin/sentry-cli"


class TestCheckSentryCli:
    """Tests for check_sentry_cli()."""

    def test_not_found_on_path(self):
        """Returns failed when sentry-cli is not on PATH."""
        with patch("scripts.update.sentry_cli.shutil.which", return_value=None):
            result = check_sentry_cli()
        assert not result.success
        assert result.action == "failed"
        assert "not found" in result.error

    def test_found_and_version_returned(self):
        """Returns skipped with version when sentry-cli is present."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "sentry-cli 2.33.1\n"

        with (
            patch("scripts.update.sentry_cli.shutil.which", return_value=SENTRY_BIN),
            patch("scripts.update.sentry_cli.subprocess.run", return_value=mock_result),
        ):
            result = check_sentry_cli()
        assert result.success
        assert result.action == "skipped"
        assert result.version == "2.33.1"

    def test_version_check_timeout(self):
        """Returns failed on timeout."""
        import subprocess

        with (
            patch("scripts.update.sentry_cli.shutil.which", return_value=SENTRY_BIN),
            patch(
                "scripts.update.sentry_cli.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="sentry-cli", timeout=10),
            ),
        ):
            result = check_sentry_cli()
        assert not result.success
        assert result.action == "failed"
        assert "timed out" in result.error.lower() or "timeout" in result.error.lower()

    def test_nonzero_exit_code(self):
        """Returns failed when sentry-cli exits with error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with (
            patch("scripts.update.sentry_cli.shutil.which", return_value=SENTRY_BIN),
            patch("scripts.update.sentry_cli.subprocess.run", return_value=mock_result),
        ):
            result = check_sentry_cli()
        assert not result.success
        assert result.action == "failed"


class TestInstallSentryCli:
    """Tests for install_sentry_cli()."""

    def test_installer_succeeds(self):
        """Returns installed with version on success."""
        install_result = MagicMock()
        install_result.returncode = 0
        install_result.stdout = ""

        check_result = InstallResult(success=True, action="skipped", version="2.33.1")

        with (
            patch("scripts.update.sentry_cli.subprocess.run", return_value=install_result),
            patch("scripts.update.sentry_cli.check_sentry_cli", return_value=check_result),
        ):
            result = install_sentry_cli()
        assert result.success
        assert result.action == "installed"
        assert result.version == "2.33.1"

    def test_installer_fails(self):
        """Returns failed when curl installer exits with error."""
        install_result = MagicMock()
        install_result.returncode = 1
        install_result.stderr = "Network error"
        install_result.stdout = ""

        with patch("scripts.update.sentry_cli.subprocess.run", return_value=install_result):
            result = install_sentry_cli()
        assert not result.success
        assert result.action == "failed"
        assert "Network error" in result.error

    def test_installer_timeout(self):
        """Returns failed on installer timeout."""
        import subprocess

        with patch(
            "scripts.update.sentry_cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="bash", timeout=120),
        ):
            result = install_sentry_cli()
        assert not result.success
        assert result.action == "failed"
        assert "timed out" in result.error.lower()

    def test_installer_succeeds_but_binary_not_found(self):
        """Returns failed when installer exits 0 but binary isn't on PATH."""
        install_result = MagicMock()
        install_result.returncode = 0
        install_result.stdout = ""

        check_result = InstallResult(
            success=False, action="failed", error="sentry-cli not found on PATH"
        )

        with (
            patch("scripts.update.sentry_cli.subprocess.run", return_value=install_result),
            patch("scripts.update.sentry_cli.check_sentry_cli", return_value=check_result),
        ):
            result = install_sentry_cli()
        assert not result.success
        assert result.action == "failed"
        assert "not found" in result.error


class TestInstallOrUpdate:
    """Tests for install_or_update()."""

    def test_already_installed_skips(self):
        """Skips installation when sentry-cli is already present."""
        check_result = InstallResult(success=True, action="skipped", version="2.33.1")

        with patch("scripts.update.sentry_cli.check_sentry_cli", return_value=check_result):
            result = install_or_update()
        assert result.success
        assert result.action == "skipped"
        assert result.version == "2.33.1"

    def test_not_installed_triggers_install(self):
        """Runs installer when sentry-cli is not found."""
        check_result = InstallResult(
            success=False, action="failed", error="sentry-cli not found on PATH"
        )
        install_result = InstallResult(success=True, action="installed", version="2.33.1")

        with (
            patch("scripts.update.sentry_cli.check_sentry_cli", return_value=check_result),
            patch("scripts.update.sentry_cli.install_sentry_cli", return_value=install_result),
        ):
            result = install_or_update()
        assert result.success
        assert result.action == "installed"

    def test_install_failure_returns_failed(self):
        """Returns failed result when installation fails."""
        check_result = InstallResult(
            success=False, action="failed", error="sentry-cli not found on PATH"
        )
        install_result = InstallResult(success=False, action="failed", error="Network error")

        with (
            patch("scripts.update.sentry_cli.check_sentry_cli", return_value=check_result),
            patch("scripts.update.sentry_cli.install_sentry_cli", return_value=install_result),
        ):
            result = install_or_update()
        assert not result.success
        assert result.action == "failed"
