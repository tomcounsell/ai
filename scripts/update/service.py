"""Service management for update system."""

from __future__ import annotations

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


def install_daydream(project_dir: Path) -> bool:
    """Install/reload daydream plist. Returns True if successful."""
    plist_src = project_dir / "com.valor.daydream.plist"
    plist_dst = Path.home() / "Library" / "LaunchAgents" / "com.valor.daydream.plist"
    label = "com.valor.daydream"

    if not plist_src.exists():
        return False

    try:
        # Unload if currently loaded
        result = run_cmd(["launchctl", "list"])
        if label in result.stdout:
            run_cmd(["launchctl", "unload", str(plist_dst)])

        # Copy and load
        plist_dst.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(str(plist_src), str(plist_dst))
        run_cmd(["launchctl", "load", str(plist_dst)])
        return True
    except Exception:
        return False


def is_daydream_installed() -> bool:
    """Check if daydream scheduler is installed."""
    try:
        result = run_cmd(["launchctl", "list"])
        return "com.valor.daydream" in result.stdout
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


def install_caffeinate() -> bool:
    """Install caffeinate service. Returns True if successful."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.valor.caffeinate.plist"

    if plist_path.exists():
        return True  # Already installed

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
    <true/>
</dict>
</plist>
"""

    try:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        run_cmd(["launchctl", "load", str(plist_path)])
        return True
    except Exception:
        return False
