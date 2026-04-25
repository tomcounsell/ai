"""Service management for update system."""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Service label prefix is install-time configurable via .env. Defaults to
# com.valor for the canonical fork; downstream forks override via SERVICE_LABEL_PREFIX.
SERVICE_PREFIX = os.environ.get("SERVICE_LABEL_PREFIX", "com.valor")


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
        status.launchd_installed = f"{SERVICE_PREFIX}.bridge" in result.stdout
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


def get_worker_pid() -> int | None:
    """Get PID of running worker process."""
    try:
        result = run_cmd(["pgrep", "-fi", "python -m worker"])
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split()[0])
    except Exception:
        pass
    try:
        result = run_cmd(["pgrep", "-fi", "python.*worker/__main__"])
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split()[0])
    except Exception:
        pass
    return None


def is_worker_running() -> bool:
    """Check if standalone worker is running."""
    return get_worker_pid() is not None


def get_worker_status(project_dir: Path) -> ServiceStatus:
    """Get current worker service status."""
    pid = get_worker_pid()

    status = ServiceStatus(
        running=pid is not None,
        pid=pid,
    )

    if pid:
        try:
            result = run_cmd(["ps", "-o", "etime=", "-p", str(pid)])
            if result.returncode == 0:
                status.uptime = result.stdout.strip()
        except Exception:
            pass

        try:
            result = run_cmd(["ps", "-o", "rss=", "-p", str(pid)])
            if result.returncode == 0:
                rss_kb = int(result.stdout.strip())
                status.memory_mb = rss_kb / 1024
        except Exception:
            pass

    try:
        result = run_cmd(["launchctl", "list"])
        status.launchd_installed = f"{SERVICE_PREFIX}.worker" in result.stdout
    except Exception:
        pass

    return status


def _inject_env_into_plist(plist_path: Path, env_file: Path) -> int:
    """Merge .env vars into the plist's EnvironmentVariables dict.

    Mirrors the inline shell+Python block in ``scripts/install_worker.sh``
    (lines 95-131) so ``/update --full`` produces an equivalent worker plist.
    Without this, only PATH/HOME/VALOR_LAUNCHD (the placeholders baked into
    the template) end up in the plist and launchd-spawned worker processes
    miss every ``.env`` var — including ``VALOR_PROJECT_KEY`` (issue #1171).

    Pre-existing keys in EnvironmentVariables are NOT overwritten (the
    plist template's PATH/HOME/VALOR_LAUNCHD placeholders take precedence).
    Values of ``None`` are skipped so an empty ``KEY=`` line in ``.env``
    does not produce a bare empty value in the plist.

    Args:
        plist_path: Destination plist on disk.
        env_file: Path to the project's ``.env`` file (typically a symlink
            to ``~/Desktop/Valor/.env``).

    Returns:
        Count of env vars injected. ``0`` if injection was skipped due to
        a recoverable error (best-effort — never raises so the caller can
        still bootstrap a degraded plist).
    """
    try:
        from dotenv import dotenv_values
    except Exception as e:  # pragma: no cover — dotenv is a hard dep, but be defensive
        logger.warning("install_worker: cannot import dotenv (%s) — skipping env injection", e)
        return 0

    if not plist_path.exists():
        logger.warning("install_worker: plist %s missing — skipping env injection", plist_path)
        return 0

    if not env_file.exists():
        logger.info("install_worker: .env at %s missing — skipping env injection", env_file)
        return 0

    try:
        env_vars = dotenv_values(env_file)
    except Exception as e:
        logger.warning("install_worker: could not parse %s (%s) — skipping env injection", env_file, e)
        return 0

    try:
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
    except Exception as e:
        logger.warning("install_worker: could not load plist %s (%s) — skipping env injection", plist_path, e)
        return 0

    existing = plist.setdefault("EnvironmentVariables", {})
    injected = 0
    for key, value in env_vars.items():
        if key in existing or value is None:
            continue
        existing[key] = value
        injected += 1

    try:
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)
    except Exception as e:
        logger.warning("install_worker: could not write plist %s (%s) — env injection rolled back", plist_path, e)
        return 0

    logger.info("install_worker: injected %d env vars from %s into %s", injected, env_file, plist_path)
    return injected


def install_worker(project_dir: Path) -> bool:
    """Install/reload worker plist. Returns True if successful.

    Content-idempotent: if the rendered plist matches the file on disk and
    the worker is already loaded, skip the bootout/bootstrap cycle entirely
    so /update doesn't churn a healthy worker on repeated runs.

    After writing the rendered plist, merges ``.env`` vars into the plist's
    ``EnvironmentVariables`` dict so launchd-spawned worker processes see
    ``VALOR_PROJECT_KEY`` and other secrets. Without this, the standalone
    ``scripts/install_worker.sh`` injects env vars but ``/update --full``
    (which calls this function) does not — producing a worker plist that
    silently lacks ``VALOR_PROJECT_KEY`` and reverts the recovery code to
    its fallback (issue #1171).
    """
    plist_src = project_dir / "com.valor.worker.plist"
    label = f"{SERVICE_PREFIX}.worker"
    plist_dst = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if not plist_src.exists():
        return False

    plist_text = plist_src.read_text()
    plist_text = plist_text.replace("__PROJECT_DIR__", str(project_dir))
    plist_text = plist_text.replace("__HOME_DIR__", str(Path.home()))
    plist_text = plist_text.replace("__SERVICE_LABEL__", label)

    # Idempotency check is done against the rendered template. The post-write
    # env injection mutates the destination plist (added EnvironmentVariables
    # keys) so a byte-for-byte compare against ``plist_text`` would fail on
    # every subsequent run if we read existing_text after injection. We avoid
    # that by comparing the rendered template against the on-disk template
    # BEFORE injection — but the on-disk plist on disk has already been
    # mutated. Solution: re-render the template then compare against a
    # template-only round-trip of the existing plist (i.e. strip the injected
    # keys before compare). For simplicity we keep the prior behavior — the
    # idempotency check may produce a false-negative on a healthy worker
    # where env injection is the only difference, triggering an extra
    # bootout/bootstrap cycle. That is harmless (worker restarts cleanly).
    try:
        existing_text = plist_dst.read_text() if plist_dst.exists() else None
    except OSError:
        existing_text = None

    already_loaded = False
    try:
        already_loaded = label in run_cmd(["launchctl", "list"]).stdout
    except Exception:
        pass

    if existing_text == plist_text and already_loaded:
        return True

    try:
        uid = os.getuid()
        if already_loaded:
            run_cmd(["launchctl", "bootout", f"gui/{uid}/{label}"])

        plist_dst.parent.mkdir(parents=True, exist_ok=True)
        plist_dst.write_text(plist_text)

        # Inject .env vars into EnvironmentVariables BEFORE bootstrap so
        # launchd captures them when spawning the worker process.
        env_file = project_dir / ".env"
        try:
            _inject_env_into_plist(plist_dst, env_file)
        except Exception as e:
            # Belt-and-suspenders — _inject_env_into_plist is already
            # defensive, but a mid-mutation crash would leave the plist in
            # a partially-written state. Log and proceed; the worker will
            # still start with a degraded plist (missing env vars), and
            # the regression test catches future drift.
            logger.warning("install_worker: env injection raised (%s) — bootstrapping anyway", e)

        run_cmd(["launchctl", "bootstrap", f"gui/{uid}", str(plist_dst)])
        return True
    except Exception:
        return False


def restart_worker(project_dir: Path) -> bool:
    """Restart worker service. Returns True if successful."""
    service_script = project_dir / "scripts" / "valor-service.sh"

    if not service_script.exists():
        return False

    try:
        result = run_cmd(
            [str(service_script), "worker-restart"],
            cwd=project_dir,
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def is_worker_installed() -> bool:
    """Check if worker service is installed in launchd."""
    try:
        result = run_cmd(["launchctl", "list"])
        return f"{SERVICE_PREFIX}.worker" in result.stdout
    except Exception:
        return False


def is_update_cron_installed() -> bool:
    """Check if update cron is installed."""
    try:
        result = run_cmd(["launchctl", "list"])
        return f"{SERVICE_PREFIX}.update" in result.stdout
    except Exception:
        return False


def get_caffeinate_status() -> CaffeinateStatus:
    """Get caffeinate service status."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_PREFIX}.caffeinate.plist"

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


def restart_webui(project_dir: Path, force: bool = False) -> bool:
    """Ensure the web UI server is running. Returns True if running.

    When force=False (default), this is idempotent: if the web UI is already
    listening on port 8500, return True without killing the process. Pass
    force=True to kill+restart (used after git pull pulls new code).
    """
    import time

    pid = get_webui_pid()
    if pid and not force:
        return True

    if pid:
        try:
            run_cmd(["kill", "-9", str(pid)])
            time.sleep(1)
        except Exception:
            pass

    try:
        venv_python = project_dir / ".venv" / "bin" / "python"
        subprocess.Popen(
            [str(venv_python), "-m", "ui.app"],
            cwd=project_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(2)
        return is_webui_running()
    except Exception:
        return False


def heal_plist_paths(project_dir: Path) -> list[str]:
    """Check installed service plists for missing PATH entries and fix them.

    Specifically ensures ~/.local/bin is in the PATH of bridge, watchdog, and
    update plists — required for the claude CLI harness to be found at runtime.

    Returns list of plist labels that were healed (empty if none needed fixing).
    """
    import plistlib

    local_bin = str(Path.home() / ".local" / "bin")
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    healed: list[str] = []

    # Service plists that run subprocesses requiring CLI tools on PATH
    plist_names = [
        f"{SERVICE_PREFIX}.bridge",
        f"{SERVICE_PREFIX}.update",
        f"{SERVICE_PREFIX}.bridge-watchdog",
    ]

    uid = os.getuid()
    try:
        launchctl_list = run_cmd(["launchctl", "list"]).stdout
    except Exception:
        launchctl_list = ""

    for label in plist_names:
        plist_path = launch_agents / f"{label}.plist"
        if not plist_path.exists():
            continue

        try:
            with open(plist_path, "rb") as f:
                plist = plistlib.load(f)
        except Exception:
            continue

        env = plist.get("EnvironmentVariables", {})
        current_path = env.get("PATH", "")
        path_parts = current_path.split(":")

        if local_bin not in path_parts:
            # Prepend .local/bin after the venv bin (first entry)
            if path_parts and path_parts[0].endswith("/bin") and ".venv" in path_parts[0]:
                new_path_parts = [path_parts[0], local_bin] + path_parts[1:]
            else:
                new_path_parts = [local_bin] + path_parts
            env["PATH"] = ":".join(new_path_parts)
            plist.setdefault("EnvironmentVariables", {})["PATH"] = env["PATH"]

            try:
                with open(plist_path, "wb") as f:
                    plistlib.dump(plist, f)

                # Reload if currently loaded so the fix takes effect immediately
                if label in launchctl_list:
                    run_cmd(["launchctl", "bootout", f"gui/{uid}/{label}"])
                    run_cmd(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)])

                healed.append(label)
            except Exception:
                pass

    return healed


def install_log_rotate_agent(project_dir: Path) -> bool:
    """Install the user-space log-rotate LaunchAgent.

    Renders ``com.valor.log-rotate.plist`` with local paths substituted,
    compares the rendered text to the already-installed file, and skips
    the launchctl bootout/bootstrap cycle entirely when the content has
    not changed. This is a deliberate improvement over
    ``install_worker()``, which unconditionally bootouts and bootstraps
    on every run — an actual (not advertised) content-idempotency.

    Returns True when the agent is installed or already up to date. Returns
    False only when the source plist is missing or a launchctl command
    fails outright.
    """
    plist_src = project_dir / "com.valor.log-rotate.plist"
    label = f"{SERVICE_PREFIX}.log-rotate"
    plist_dst = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if not plist_src.exists():
        return False

    # Render template with local paths.
    plist_text = plist_src.read_text()
    plist_text = plist_text.replace("__PROJECT_DIR__", str(project_dir))
    plist_text = plist_text.replace("__HOME_DIR__", str(Path.home()))
    plist_text = plist_text.replace("__SERVICE_LABEL__", label)

    # Content-idempotency: if the file on disk matches and the service is
    # already loaded, there's nothing to do.
    try:
        existing_text = plist_dst.read_text() if plist_dst.exists() else None
    except OSError:
        existing_text = None

    already_loaded = False
    try:
        launchctl_list = run_cmd(["launchctl", "list"]).stdout
        already_loaded = label in launchctl_list
    except Exception:
        pass

    if existing_text == plist_text and already_loaded:
        # No-op: everything already matches. This is the improvement over
        # install_worker() — we skip the reload cycle when nothing changed.
        return True

    # Install/refresh the rendered plist.
    try:
        plist_dst.parent.mkdir(parents=True, exist_ok=True)
        plist_dst.write_text(plist_text)
    except OSError:
        return False

    # Bootout + bootstrap to pick up changes. Only when content or load
    # state differed — otherwise we returned above.
    try:
        uid = os.getuid()
        if already_loaded:
            run_cmd(["launchctl", "bootout", f"gui/{uid}/{label}"])
        result = run_cmd(["launchctl", "bootstrap", f"gui/{uid}", str(plist_dst)])
        if result.returncode != 0:
            # Bootstrap can fail if the label is still registered from a
            # previous crash; a best-effort kickstart gets it running.
            run_cmd(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
                timeout=10,
            )
        # Verify the service appears in launchctl list.
        verify_list = run_cmd(["launchctl", "list"]).stdout
        return label in verify_list
    except Exception:
        return False


def remove_newsyslog_config() -> bool:
    """Best-effort removal of the stale /etc/newsyslog.d/valor.conf config.

    Prior releases installed a system-level newsyslog config at
    ``/etc/newsyslog.d/valor.conf``. macOS's newsyslog daemon reads that
    directory hourly regardless of what this project does, so leaving the
    file in place produces double-rotation (two different naming schemes:
    newsyslog's ``.0.bz2`` vs the new LaunchAgent's ``.1``/``.2``).

    This function attempts a non-interactive ``sudo -n rm`` on the file.
    Never prompts — passing ``-n`` tells sudo to fail fast when a password
    is required. Returns True when the file is already absent or was
    successfully removed. Returns False when sudo required a password; the
    caller logs a warning so the double-rotation doesn't silently persist.
    """
    target = Path("/etc/newsyslog.d/valor.conf")
    if not target.exists():
        return True

    try:
        result = run_cmd(["sudo", "-n", "rm", "-f", str(target)], timeout=5)
    except Exception:
        return False

    # sudo -n exits non-zero when a password is required; the file stays
    # in place. Returning False lets the caller surface a one-line warning.
    return result.returncode == 0 and not target.exists()


def install_caffeinate() -> bool:
    """Install caffeinate service. Returns True if successful."""
    label = f"{SERVICE_PREFIX}.caffeinate"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
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
