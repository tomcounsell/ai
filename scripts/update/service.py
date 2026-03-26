"""Service management for update system."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ServiceStatus:
    """Status of a service."""

    running: bool
    pid: int | None = None
    uptime: str | None = None
    memory_mb: float | None = None
    launchd_installed: bool = False


@dataclass
class CaffeinateStatus:
    """Status of caffeinate service."""

    installed: bool
    running: bool


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def get_bridge_pid() -> int | None:
    """Get PID of running bridge process."""
    try:
        result = run_cmd(["pgrep", "-f", "telegram_bridge.py"])
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split()[0])
    except Exception:
        pass
    return None


def is_bridge_running() -> bool:
    """Check if bridge is running."""
    return get_bridge_pid() is not None


def get_service_status(project_dir: Path) -> ServiceStatus:
    """Get current bridge service status."""
    pid = get_bridge_pid()

    status = ServiceStatus(
        running=pid is not None,
        pid=pid,
    )

    if pid:
        # Get uptime
        try:
            result = run_cmd(["ps", "-o", "etime=", "-p", str(pid)])
            if result.returncode == 0:
                status.uptime = result.stdout.strip()
        except Exception:
            pass

        # Get memory
        try:
            result = run_cmd(["ps", "-o", "rss=", "-p", str(pid)])
            if result.returncode == 0:
                rss_kb = int(result.stdout.strip())
                status.memory_mb = rss_kb / 1024
        except Exception:
            pass

    # Check launchd
    try:
        result = run_cmd(["launchctl", "list"])
        status.launchd_installed = "com.valor.bridge" in result.stdout
    except Exception:
        pass

    return status


def install_service(project_dir: Path) -> bool:
    """Install bridge and update cron services. Returns True if successful."""
    service_script = project_dir / "scripts" / "valor-service.sh"

    if not service_script.exists():
        return False

    try:
        result = run_cmd(
            [str(service_script), "install"],
            cwd=project_dir,
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def restart_service(project_dir: Path) -> bool:
    """Restart bridge service. Returns True if successful."""
    service_script = project_dir / "scripts" / "valor-service.sh"

    if not service_script.exists():
        return False

    try:
        result = run_cmd(
            [str(service_script), "restart"],
            cwd=project_dir,
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def install_reflections(project_dir: Path) -> bool:
    """Install/reload reflections plist. Returns True if successful."""
    plist_src = project_dir / "com.valor.reflections.plist"
    plist_dst = Path.home() / "Library" / "LaunchAgents" / "com.valor.reflections.plist"
    label = "com.valor.reflections"
    old_label = "com.valor.daydream"
    old_plist_dst = Path.home() / "Library" / "LaunchAgents" / "com.valor.daydream.plist"

    if not plist_src.exists():
        return False

    try:
        uid = os.getuid()
        result = run_cmd(["launchctl", "list"])

        # Unload old daydream service if present (migration)
        if old_label in result.stdout:
            run_cmd(["launchctl", "bootout", f"gui/{uid}/{old_label}"])
            old_plist_dst.unlink(missing_ok=True)

        # Unload current if loaded
        if label in result.stdout:
            run_cmd(["launchctl", "bootout", f"gui/{uid}/{label}"])

        # Copy and bootstrap
        plist_dst.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(str(plist_src), str(plist_dst))
        run_cmd(["launchctl", "bootstrap", f"gui/{uid}", str(plist_dst)])
        return True
    except Exception:
        return False


def is_reflections_installed() -> bool:
    """Check if reflections scheduler is installed."""
    try:
        result = run_cmd(["launchctl", "list"])
        return "com.valor.reflections" in result.stdout
    except Exception:
        return False


def is_update_cron_installed() -> bool:
    """Check if update cron is installed."""
    try:
        result = run_cmd(["launchctl", "list"])
        return "com.valor.update" in result.stdout
    except Exception:
        return False


def get_caffeinate_status() -> CaffeinateStatus:
    """Get caffeinate service status."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.valor.caffeinate.plist"

    installed = plist_path.exists()

    running = False
    try:
        result = run_cmd(["pgrep", "caffeinate"])
        running = result.returncode == 0
    except Exception:
        pass

    return CaffeinateStatus(installed=installed, running=running)


def get_webui_pid() -> int | None:
    """Get PID of running web UI process (ui.app on port 8500)."""
    try:
        result = run_cmd(["lsof", "-ti", ":8500"])
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split()[0])
    except Exception:
        pass
    return None


def is_webui_running() -> bool:
    """Check if web UI is running."""
    return get_webui_pid() is not None


def restart_webui(project_dir: Path) -> bool:
    """Restart the web UI server. Returns True if successfully started."""
    import time

    # Kill existing process
    pid = get_webui_pid()
    if pid:
        try:
            run_cmd(["kill", "-9", str(pid)])
            time.sleep(1)
        except Exception:
            pass

    # Start new process
    try:
        venv_python = project_dir / ".venv" / "bin" / "python"
        subprocess.Popen(
            [str(venv_python), "-m", "ui.app"],
            cwd=project_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Wait briefly and verify
        time.sleep(2)
        return is_webui_running()
    except Exception:
        return False


def install_caffeinate() -> bool:
    """Install caffeinate service. Returns True if successful."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.valor.caffeinate.plist"

    plist_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.valor.caffeinate</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>-s</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""

    try:
        uid = os.getuid()
        label = "com.valor.caffeinate"

        # Unload existing service if loaded
        result = run_cmd(["launchctl", "list"])
        if label in result.stdout:
            run_cmd(["launchctl", "bootout", f"gui/{uid}/{label}"])

        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        run_cmd(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)])
        return True
    except Exception:
        return False
