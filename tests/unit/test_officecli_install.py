"""Tests for scripts/update/officecli.py module."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

from scripts.update.officecli import (
    PINNED_VERSION,
    _verify_sha256,
    get_asset_name,
    get_installed_version,
    install_or_update,
)


class TestGetAssetName:
    """Test platform detection and asset name mapping."""

    @patch("scripts.update.officecli.platform")
    def test_macos_arm64(self, mock_platform):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "arm64"
        assert get_asset_name() == "officecli-mac-arm64"

    @patch("scripts.update.officecli.platform")
    def test_macos_x64(self, mock_platform):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "x86_64"
        assert get_asset_name() == "officecli-mac-x64"

    @patch("scripts.update.officecli.platform")
    def test_linux_arm64(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "aarch64"
        assert get_asset_name() == "officecli-linux-arm64"

    @patch("scripts.update.officecli.platform")
    def test_linux_x64(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "x86_64"
        assert get_asset_name() == "officecli-linux-x64"

    @patch("scripts.update.officecli.platform")
    def test_linux_amd64(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "amd64"
        assert get_asset_name() == "officecli-linux-x64"

    @patch("scripts.update.officecli.platform")
    def test_unsupported_platform(self, mock_platform):
        mock_platform.system.return_value = "Windows"
        mock_platform.machine.return_value = "x86_64"
        assert get_asset_name() is None

    @patch("scripts.update.officecli.platform")
    def test_unsupported_arch(self, mock_platform):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "i386"
        assert get_asset_name() is None


class TestVerifySha256:
    """Test SHA256 verification logic."""

    def test_matching_hash(self, tmp_path):
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert _verify_sha256(test_file, expected) is True

    def test_mismatched_hash(self, tmp_path):
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")
        assert _verify_sha256(test_file, "deadbeef" * 8) is False

    def test_empty_file(self, tmp_path):
        test_file = tmp_path / "empty.bin"
        test_file.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert _verify_sha256(test_file, expected) is True


class TestGetInstalledVersion:
    """Test version detection from installed binary."""

    @patch("scripts.update.officecli.INSTALL_DIR")
    def test_binary_not_found(self, mock_dir, tmp_path):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        # tmp_path/officecli does not exist
        result = get_installed_version()
        assert result is None

    @patch("subprocess.run")
    @patch("scripts.update.officecli.INSTALL_DIR")
    def test_version_output(self, mock_dir, mock_run, tmp_path):
        binary = tmp_path / "officecli"
        binary.touch()
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_run.return_value = MagicMock(returncode=0, stdout="officecli version 1.0.29")
        result = get_installed_version()
        assert result == "1.0.29"

    @patch("subprocess.run")
    @patch("scripts.update.officecli.INSTALL_DIR")
    def test_bare_version_output(self, mock_dir, mock_run, tmp_path):
        binary = tmp_path / "officecli"
        binary.touch()
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.29")
        result = get_installed_version()
        assert result == "1.0.29"


class TestInstallOrUpdate:
    """Test the main install/update orchestration."""

    @patch("scripts.update.officecli.get_asset_name")
    def test_unsupported_platform(self, mock_asset):
        mock_asset.return_value = None
        result = install_or_update()
        assert result.success is False
        assert result.action == "failed"
        assert "Unsupported platform" in result.error

    @patch("scripts.update.officecli.get_installed_version")
    @patch("scripts.update.officecli.get_asset_name")
    def test_already_up_to_date(self, mock_asset, mock_version):
        mock_asset.return_value = "officecli-mac-arm64"
        mock_version.return_value = PINNED_VERSION.lstrip("v")
        result = install_or_update()
        assert result.success is True
        assert result.action == "skipped"

    @patch("scripts.update.officecli._download_file")
    @patch("scripts.update.officecli._fetch_sha256sums")
    @patch("scripts.update.officecli.get_installed_version")
    @patch("scripts.update.officecli.get_asset_name")
    def test_download_failure(self, mock_asset, mock_version, mock_sums, mock_dl):
        mock_asset.return_value = "officecli-mac-arm64"
        mock_version.return_value = None  # not installed
        mock_sums.return_value = {}
        mock_dl.side_effect = Exception("network error")
        result = install_or_update()
        assert result.success is False
        assert result.action == "failed"
        assert "Download failed" in result.error


class TestPinnedVersion:
    """Test pinned version is valid."""

    def test_pinned_version_format(self):
        assert PINNED_VERSION.startswith("v")
        parts = PINNED_VERSION.lstrip("v").split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)
