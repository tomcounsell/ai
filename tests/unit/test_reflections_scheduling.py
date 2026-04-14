"""Tests for reflections scheduling infrastructure in remote-update.sh.

Note: com.valor.reflections.plist and scripts/install_reflections.sh were deleted
as part of the monolith removal (Phase C). Tests for those files are removed here.
The reflections scheduler now runs via agent/reflection_scheduler.py as a subprocess
managed by the standalone worker, with no launchd plist of its own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


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
        """remote-update.sh must not use || true on launchctl calls, except
        for intentional migration guards (daydream and reflections legacy unload)."""
        script = PROJECT_ROOT / "scripts" / "remote-update.sh"
        content = script.read_text()
        # Find all launchctl bootstrap/bootout lines and ensure none have || true
        for line in content.splitlines():
            if "launchctl bootstrap" in line or "launchctl bootout" in line:
                # Migration guards: these services may not exist on all machines
                # during the transition period, so || true is intentional.
                if "com.valor.daydream" in line:
                    continue
                if "REFLECTIONS_LABEL" in line or "com.valor.reflections" in line:
                    # NOTE: reflections legacy-unload bootout uses || true as a
                    # migration guard — the old service may not be loaded on all
                    # machines. This is intentional, not a silent failure swallower.
                    continue
                assert "|| true" not in line, (
                    f"launchctl call should not swallow errors with || true: {line}"
                )

    def test_service_install_reflections_deleted(self):
        """service.py install_reflections() must be absent — reflections run
        inside the worker process now; there is no launchd plist for them."""
        service_path = PROJECT_ROOT / "scripts" / "update" / "service.py"
        content = service_path.read_text()
        assert "def install_reflections(" not in content, (
            "install_reflections() was deleted in the monolith removal — "
            "reflections are now scheduled by agent/reflection_scheduler.py "
            "as a subprocess managed by the worker, not a launchd service"
        )

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
