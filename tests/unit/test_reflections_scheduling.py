"""Tests for reflections scheduling infrastructure (plist and install script)."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestReflectionsPlist:
    """Tests for the launchd plist configuration."""

    def test_plist_is_valid_xml(self):
        plist_path = PROJECT_ROOT / "com.valor.reflections.plist"
        assert plist_path.exists(), "Plist file must exist in project root"
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
        assert isinstance(data, dict)

    def test_plist_label(self):
        with open(PROJECT_ROOT / "com.valor.reflections.plist", "rb") as f:
            data = plistlib.load(f)
        assert data["Label"] == "com.valor.reflections"

    def test_plist_schedule_6am(self):
        with open(PROJECT_ROOT / "com.valor.reflections.plist", "rb") as f:
            data = plistlib.load(f)
        schedule = data["StartCalendarInterval"]
        assert schedule["Hour"] == 6
        assert schedule["Minute"] == 0

    def test_plist_points_to_reflections_script(self):
        with open(PROJECT_ROOT / "com.valor.reflections.plist", "rb") as f:
            data = plistlib.load(f)
        args = data["ProgramArguments"]
        # Uses bash -c to source .env before running python
        assert args[0] == "/bin/bash"
        assert "reflections.py" in args[-1]
        assert ".env" in args[-1]

    def test_plist_log_paths(self):
        with open(PROJECT_ROOT / "com.valor.reflections.plist", "rb") as f:
            data = plistlib.load(f)
        assert "reflections.log" in data["StandardOutPath"]
        assert "reflections_error.log" in data["StandardErrorPath"]

    def test_plist_has_environment_variables(self):
        with open(PROJECT_ROOT / "com.valor.reflections.plist", "rb") as f:
            data = plistlib.load(f)
        env = data["EnvironmentVariables"]
        assert "PATH" in env
        assert "HOME" in env

    def test_plist_has_working_directory(self):
        with open(PROJECT_ROOT / "com.valor.reflections.plist", "rb") as f:
            data = plistlib.load(f)
        assert "WorkingDirectory" in data


class TestInstallScript:
    """Tests for the install script."""

    def test_install_script_exists_and_executable(self):
        script = PROJECT_ROOT / "scripts" / "install_reflections.sh"
        assert script.exists()
        assert script.stat().st_mode & 0o111, "Script must be executable"

    def test_install_script_syntax_valid(self):
        script = PROJECT_ROOT / "scripts" / "install_reflections.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_install_script_references_plist(self):
        script = PROJECT_ROOT / "scripts" / "install_reflections.sh"
        content = script.read_text()
        assert "com.valor.reflections" in content
        assert "launchctl bootstrap" in content
        assert "launchctl bootout" in content


class TestRemoteUpdateScript:
    """Tests for reflections integration in remote-update.sh."""

    def test_remote_update_includes_reflections_reload(self):
        script = PROJECT_ROOT / "scripts" / "remote-update.sh"
        content = script.read_text()
        assert "com.valor.reflections" in content
        assert "launchctl" in content

    def test_remote_update_syntax_valid(self):
        script = PROJECT_ROOT / "scripts" / "remote-update.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"


class TestInstallMechanism:
    """Tests that all launchctl calls use the modern bootstrap/bootout API."""

    def test_install_script_uses_bootstrap(self):
        """install_reflections.sh must use launchctl bootstrap, not load."""
        script = PROJECT_ROOT / "scripts" / "install_reflections.sh"
        content = script.read_text()
        assert "launchctl bootstrap" in content, "Must use launchctl bootstrap"
        assert "launchctl load" not in content, "Must not use deprecated launchctl load"

    def test_install_script_uses_bootout(self):
        """install_reflections.sh must use launchctl bootout, not unload."""
        script = PROJECT_ROOT / "scripts" / "install_reflections.sh"
        content = script.read_text()
        assert "launchctl bootout" in content, "Must use launchctl bootout"
        assert "launchctl unload" not in content, "Must not use deprecated launchctl unload"

    def test_remote_update_uses_bootstrap(self):
        """remote-update.sh must use launchctl bootstrap, not load."""
        script = PROJECT_ROOT / "scripts" / "remote-update.sh"
        content = script.read_text()
        assert "launchctl bootstrap" in content, "Must use launchctl bootstrap"
        assert "launchctl load" not in content, "Must not use deprecated launchctl load"

    def test_remote_update_uses_bootout(self):
        """remote-update.sh must use launchctl bootout, not unload."""
        script = PROJECT_ROOT / "scripts" / "remote-update.sh"
        content = script.read_text()
        assert "launchctl bootout" in content, "Must use launchctl bootout"
        assert "launchctl unload" not in content, "Must not use deprecated launchctl unload"

    def test_remote_update_no_silent_failures(self):
        """remote-update.sh must not use || true on launchctl calls."""
        script = PROJECT_ROOT / "scripts" / "remote-update.sh"
        content = script.read_text()
        # Find all launchctl bootstrap/bootout lines and ensure none have || true
        for line in content.splitlines():
            if "launchctl bootstrap" in line or "launchctl bootout" in line:
                # The old daydream bootout is allowed to have || true (migration guard)
                if "com.valor.daydream" in line:
                    continue
                assert "|| true" not in line, (
                    f"launchctl call should not swallow errors with || true: {line}"
                )

    def test_service_install_reflections_uses_bootstrap(self):
        """service.py install_reflections() must use bootstrap/bootout."""
        service_path = PROJECT_ROOT / "scripts" / "update" / "service.py"
        content = service_path.read_text()
        # Extract the install_reflections function body
        func_start = content.index("def install_reflections(")
        # Find the next def or end of file
        next_def = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_def] if next_def != -1 else content[func_start:]

        assert "bootout" in func_body, "install_reflections must use bootout"
        assert "bootstrap" in func_body, "install_reflections must use bootstrap"
        assert '"unload"' not in func_body, "install_reflections must not use unload"
        assert '"load"' not in func_body, "install_reflections must not use load"

    def test_service_install_caffeinate_uses_bootstrap(self):
        """service.py install_caffeinate() must use bootstrap, not load."""
        service_path = PROJECT_ROOT / "scripts" / "update" / "service.py"
        content = service_path.read_text()
        # Extract the install_caffeinate function body
        func_start = content.index("def install_caffeinate(")
        next_def = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_def] if next_def != -1 else content[func_start:]

        assert "bootstrap" in func_body, "install_caffeinate must use bootstrap"
        assert '"load"' not in func_body, "install_caffeinate must not use load"
