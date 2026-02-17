"""Tests for daydream scheduling infrastructure (plist and install script)."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


class TestDaydreamPlist:
    """Tests for the launchd plist configuration."""

    def test_plist_is_valid_xml(self):
        plist_path = PROJECT_ROOT / "com.valor.daydream.plist"
        assert plist_path.exists(), "Plist file must exist in project root"
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
        assert isinstance(data, dict)

    def test_plist_label(self):
        with open(PROJECT_ROOT / "com.valor.daydream.plist", "rb") as f:
            data = plistlib.load(f)
        assert data["Label"] == "com.valor.daydream"

    def test_plist_schedule_6am(self):
        with open(PROJECT_ROOT / "com.valor.daydream.plist", "rb") as f:
            data = plistlib.load(f)
        schedule = data["StartCalendarInterval"]
        assert schedule["Hour"] == 6
        assert schedule["Minute"] == 0

    def test_plist_points_to_daydream_script(self):
        with open(PROJECT_ROOT / "com.valor.daydream.plist", "rb") as f:
            data = plistlib.load(f)
        args = data["ProgramArguments"]
        # Uses bash -c to source .env before running python
        assert args[0] == "/bin/bash"
        assert "daydream.py" in args[-1]
        assert ".env" in args[-1]

    def test_plist_log_paths(self):
        with open(PROJECT_ROOT / "com.valor.daydream.plist", "rb") as f:
            data = plistlib.load(f)
        assert "daydream.log" in data["StandardOutPath"]
        assert "daydream_error.log" in data["StandardErrorPath"]

    def test_plist_has_environment_variables(self):
        with open(PROJECT_ROOT / "com.valor.daydream.plist", "rb") as f:
            data = plistlib.load(f)
        env = data["EnvironmentVariables"]
        assert "PATH" in env
        assert "HOME" in env

    def test_plist_has_working_directory(self):
        with open(PROJECT_ROOT / "com.valor.daydream.plist", "rb") as f:
            data = plistlib.load(f)
        assert "WorkingDirectory" in data


class TestInstallScript:
    """Tests for the install script."""

    def test_install_script_exists_and_executable(self):
        script = PROJECT_ROOT / "scripts" / "install_daydream.sh"
        assert script.exists()
        assert script.stat().st_mode & 0o111, "Script must be executable"

    def test_install_script_syntax_valid(self):
        script = PROJECT_ROOT / "scripts" / "install_daydream.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_install_script_references_plist(self):
        script = PROJECT_ROOT / "scripts" / "install_daydream.sh"
        content = script.read_text()
        assert "com.valor.daydream" in content
        assert "launchctl load" in content
        assert "launchctl unload" in content


class TestRemoteUpdateScript:
    """Tests for daydream integration in remote-update.sh."""

    def test_remote_update_includes_daydream_reload(self):
        script = PROJECT_ROOT / "scripts" / "remote-update.sh"
        content = script.read_text()
        assert "com.valor.daydream" in content
        assert "launchctl" in content

    def test_remote_update_syntax_valid(self):
        script = PROJECT_ROOT / "scripts" / "remote-update.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"
