"""Service management for update system."""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Service label prefix is install-time configurable via .env. Defaults to
# com.valor for the canonical fork; downstream forks override via SERVICE_LABEL_PREFIX.
SERVICE_PREFIX = os.environ.get("SERVICE_LABEL_PREFIX", "com.valor")

# Launchd job SUFFIXES for features that have been fully removed from the
# codebase. Their plists linger on every already-provisioned machine forever
# (launchd keeps loading and failing them) because nothing else deletes them —
# the per-job "remove stale plist" gates in install_nightly_tests /
# install_reflection_worker only cover role-gated jobs that still EXIST, not
# jobs whose install script and code are gone. This is the launchd analog of
# RENAMED_REMOVALS in hardlinks.py: when you delete a launchd-backed feature,
# add its suffix here so `/update` boots it out and removes its plist on every
# machine. Full labels are built with SERVICE_PREFIX so downstream forks are
# covered too. Suffix only (no "com.valor." prefix, no ".plist").
OBSOLETE_SERVICE_SUFFIXES: list[str] = [
    # issue_poller.py deleted in commit 71190e8a7 ("Remove deprecated issue
    # poller feature entirely"); the com.valor.issue-poller LaunchAgent kept
    # firing every 300s and failing "No such file or directory" on every
    # already-provisioned machine.
    "issue-poller",
]


def remove_obsolete_services() -> list[str]:
    """Boot out and delete launchd jobs for fully-removed features.

    Idempotent and fail-soft: for each label in OBSOLETE_SERVICE_SUFFIXES,
    bootout the job if it is currently loaded, then unlink its plist if present.
    A machine that never had the job (or already cleaned it) is a no-op. Per-job
    failures are swallowed and logged so one wedged job never aborts the sweep.

    Returns the list of full labels that were actually removed (loaded job
    booted out OR plist deleted) — empty when nothing needed cleanup.
    """
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    uid = os.getuid()
    try:
        launchctl_list = run_cmd(["launchctl", "list"]).stdout or ""
    except Exception:
        launchctl_list = ""

    removed: list[str] = []
    for suffix in OBSOLETE_SERVICE_SUFFIXES:
        label = f"{SERVICE_PREFIX}.{suffix}"
        plist_path = launch_agents / f"{label}.plist"
        acted = False

        if label in launchctl_list:
            try:
                run_cmd(["launchctl", "bootout", f"gui/{uid}/{label}"])
                acted = True
            except Exception as exc:
                logger.warning("remove_obsolete_services: bootout %s failed: %s", label, exc)

        if plist_path.exists():
            try:
                plist_path.unlink()
                acted = True
            except Exception as exc:
                logger.warning("remove_obsolete_services: unlink %s failed: %s", plist_path, exc)

        if acted:
            logger.info("remove_obsolete_services: removed obsolete launchd job %s", label)
            removed.append(label)

    return removed


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
        logger.warning(
            "install_worker: could not parse %s (%s) — skipping env injection", env_file, e
        )
        return 0

    try:
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
    except Exception as e:
        logger.warning(
            "install_worker: could not load plist %s (%s) — skipping env injection", plist_path, e
        )
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
        logger.warning(
            "install_worker: could not write plist %s (%s) — env injection rolled back",
            plist_path,
            e,
        )
        return 0

    logger.info(
        "install_worker: injected %d env vars from %s into %s", injected, env_file, plist_path
    )
    return injected


def _launchctl_label_running(label: str) -> bool:
    """Return True if ``label`` appears in ``launchctl list`` with a live PID.

    ``launchctl list`` prints three columns: PID, Status, Label. A service that
    is loaded but not running shows ``-`` in the PID column. Issue #2089: a bare
    label match is NOT sufficient proof of a running service — a stale
    registration left behind by a failed bootstrap still lists the label with a
    ``-`` PID, which is exactly the silent-success case this guards against. Only
    a numeric PID counts as running.
    """
    try:
        out = run_cmd(["launchctl", "list"]).stdout
    except Exception:
        return False
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == label:
            return parts[0].isdigit()
    return False


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

        bootstrap = run_cmd(["launchctl", "bootstrap", f"gui/{uid}", str(plist_dst)])
        if bootstrap.returncode != 0:
            # Bootstrap can fail transiently when the label is still registered
            # after bootout (EIO / "service already loaded" — #2013/#2018).
            # kickstart -k force-restarts the registered label; per #2018 this
            # is the correct recovery for that dominant failure class.
            logger.warning(
                "install_worker: bootstrap failed (rc=%s): %s — trying kickstart -k",
                bootstrap.returncode,
                (bootstrap.stderr or "").strip(),
            )
            run_cmd(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"], timeout=10)

        # Verify the worker is actually running with a live PID before claiming
        # success (#2089). Previously this returned True unconditionally, so a
        # silent bootstrap failure let /update report "Worker running" against a
        # stale pre-restart PID while the process was gone — queued sessions
        # then stopped executing until a human noticed.
        if _launchctl_label_running(label):
            return True
        logger.error(
            "install_worker: worker label %s not running after bootstrap/kickstart "
            "(bootstrap rc=%s, stderr=%s)",
            label,
            bootstrap.returncode,
            (bootstrap.stderr or "").strip(),
        )
        return False
    except Exception:
        return False


def install_nightly_tests(project_dir: Path) -> bool:
    """Install/reload nightly-tests plist via the self-gating install script.

    Delegates to scripts/install_nightly_tests.sh which contains a has_bridge_role()
    gate — it skips install on non-bridge machines and removes any stale plist.
    Returns True if the script exits 0 (installed or cleanly skipped).
    """
    install_script = project_dir / "scripts" / "install_nightly_tests.sh"
    if not install_script.exists():
        logger.warning("install_nightly_tests: install script not found at %s", install_script)
        return False

    try:
        result = run_cmd(
            ["/bin/bash", str(install_script)],
            cwd=project_dir,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("install_nightly_tests: script completed (rc=0)")
            return True
        logger.warning(
            "install_nightly_tests: script exited with rc=%d; stdout=%s",
            result.returncode,
            (result.stdout or "").strip()[:200],
        )
        return False
    except Exception as exc:
        logger.warning("install_nightly_tests: failed to run install script: %s", exc)
        return False


def install_reflection_worker(project_dir: Path) -> bool:
    """Install/reload the reflection-scheduler subprocess via its self-gating script.

    Delegates to scripts/install_reflection_worker.sh which contains a has_worker_role()
    gate — it installs wherever the worker installs (any owned project), skips install on
    machines owning no project, and removes any stale plist. This mirrors
    install_nightly_tests() but the caller in run.py invokes it UNCONDITIONALLY (not under
    `if has_bridge:`) because the reflection subprocess must run everywhere the worker does.
    Returns True if the script exits 0 (installed or cleanly skipped).
    """
    install_script = project_dir / "scripts" / "install_reflection_worker.sh"
    if not install_script.exists():
        logger.warning("install_reflection_worker: install script not found at %s", install_script)
        return False

    try:
        result = run_cmd(
            ["/bin/bash", str(install_script)],
            cwd=project_dir,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("install_reflection_worker: script completed (rc=0)")
            return True
        logger.warning(
            "install_reflection_worker: script exited with rc=%d; stdout=%s",
            result.returncode,
            (result.stdout or "").strip()[:200],
        )
        return False
    except Exception as exc:
        logger.warning("install_reflection_worker: failed to run install script: %s", exc)
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


def get_email_pid() -> int | None:
    """Get PID of running email bridge process."""
    try:
        result = run_cmd(["pgrep", "-f", "bridge.email_bridge"])
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split()[0])
    except Exception:
        pass
    return None


def is_email_running() -> bool:
    """Check if email bridge is running."""
    return get_email_pid() is not None


def is_email_configured(project_dir: Path) -> bool:
    """Return True if IMAP_PASSWORD is set (non-placeholder) in .env."""
    env_file = project_dir / ".env"
    if not env_file.exists():
        return False
    try:
        text = env_file.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("IMAP_PASSWORD="):
                value = line.split("=", 1)[1].strip()
                return bool(value) and "your-gmail" not in value and value != "placeholder"
    except Exception:
        pass
    return False


def stop_email(project_dir: Path) -> bool:
    """Stop the email bridge. Returns True if it stopped."""
    service_script = project_dir / "scripts" / "valor-service.sh"
    if not service_script.exists():
        pid = get_email_pid()
        if pid:
            import os
            import signal

            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        return not is_email_running()
    try:
        run_cmd([str(service_script), "email-stop"], cwd=project_dir, timeout=15)
    except Exception:
        pass
    return not is_email_running()


def ensure_email_running(project_dir: Path) -> bool:
    """Start email bridge if configured and not already running. Returns True if running."""
    if not is_email_configured(project_dir):
        return False
    if is_email_running():
        return True
    service_script = project_dir / "scripts" / "valor-service.sh"
    if not service_script.exists():
        return False
    try:
        run_cmd([str(service_script), "email-start"], cwd=project_dir, timeout=30)
        import time

        time.sleep(3)
        return is_email_running()
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


def get_webui_pids() -> list[int]:
    """Get all PIDs using port 8500 (uvicorn spawns main + worker)."""
    try:
        result = run_cmd(["lsof", "-ti", ":8500"])
        if result.returncode == 0 and result.stdout.strip():
            return [int(p) for p in result.stdout.strip().split()]
    except Exception:
        pass
    return []


def get_webui_pid() -> int | None:
    """Get PID of running web UI process (ui.app on port 8500)."""
    pids = get_webui_pids()
    return pids[0] if pids else None


def is_webui_running() -> bool:
    """Check if web UI is running."""
    return bool(get_webui_pids())


def restart_webui(project_dir: Path, force: bool = False) -> bool:
    """Ensure the web UI server is running. Returns True if running.

    When force=False (default), this is idempotent: if the web UI is already
    listening on port 8500, return True without killing the process. Pass
    force=True to kill+restart (used after git pull pulls new code).
    """

    pids = get_webui_pids()
    if pids and not force:
        return True

    if pids:
        try:
            # Kill all PIDs (main + uvicorn worker) so the port is fully released
            run_cmd(["kill", "-9"] + [str(p) for p in pids])
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
        # Poll up to 10 seconds; uvicorn needs a few seconds to bind the port.
        for _ in range(20):
            time.sleep(0.5)
            if is_webui_running():
                return True
        return False
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
                    reload_res = run_cmd(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)])
                    if reload_res.returncode != 0:
                        # Best-effort PATH heal (#2089): don't fail the heal, but
                        # surface the bootstrap failure so a silently-down service
                        # after a PATH reload isn't invisible.
                        logger.warning(
                            "heal_path: bootstrap after PATH reload failed for %s (rc=%s): %s",
                            label,
                            reload_res.returncode,
                            (reload_res.stderr or "").strip(),
                        )

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
        bootstrap = run_cmd(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)])
        if bootstrap.returncode != 0:
            # Same silent-bootstrap class as install_worker (#2089): recover via
            # kickstart -k, then verify the label is loaded before reporting
            # success. caffeinate is non-critical (idle-sleep inhibitor), so a
            # bare label match is sufficient here — no live-PID assertion.
            logger.warning(
                "install_caffeinate: bootstrap failed (rc=%s): %s — trying kickstart -k",
                bootstrap.returncode,
                (bootstrap.stderr or "").strip(),
            )
            run_cmd(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"], timeout=10)
        return label in run_cmd(["launchctl", "list"]).stdout
    except Exception:
        return False


# === Release verification via boot-SHA beacons (issue #1898) ===

# Per-process relevant path sets — IDENTICAL to the restart gates' diff sets in
# scripts/remote-update.sh (#1091 relevant-diff design). Classifier and restart
# gate must agree by construction: a process is `stale` only when commits
# touching ITS OWN paths landed after its boot SHA. Raw `boot_sha != HEAD`
# equality is deliberately never used — docs-only commits legitimately advance
# HEAD past healthy, correctly-un-restarted processes.
BRIDGE_RELEVANT_PATHS = [
    "bridge/",
    "agent/",
    "mcp_servers/",
    "models/",
    "tools/",
    "config/",
    "pyproject.toml",
]
WORKER_RELEVANT_PATHS = [
    "worker/",
    "agent/",
    "mcp_servers/",
    "models/",
    "tools/",
    "bridge/",
    "reflections/",
    "pyproject.toml",
]

# Bridge eligibility uses the same on-disk plist signal as the restart gate in
# remote-update.sh (Decision 23, #1898): a machine with the bridge role but no
# installed plist has no restart path, so it must not enter a permanent
# stale-FAILED loop — it is skipped entirely.
BRIDGE_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_PREFIX}.bridge.plist"

# Shared TTLs for the planned bridge-restart marker (data/update-restart-in-
# progress) and the staged pending report (data/update-pending-report) —
# issue #1898, Decision 26. Formula: the bridge watchdog's
# STARTUP_GRACE_SECONDS (5 min) + one 60s watchdog cycle. BOTH TTLs share the
# formula so the watchdog-suppression window can never expire before the
# legitimate boot window it protects. Defined here rather than in
# monitoring/bridge_watchdog.py because scripts.update.verify_release must
# import the marker TTL without pulling in the watchdog's module-level side
# effects (log handler creation, redis import); the watchdog re-imports both,
# and a test pins them to STARTUP_GRACE_SECONDS + 60.
UPDATE_REPORT_TTL_SECONDS = 5 * 60 + 60
UPDATE_RESTART_MARKER_TTL_SECONDS = UPDATE_REPORT_TTL_SECONDS


def get_process_start_ts(pid: int) -> float | None:
    """Return a process's start time as a unix timestamp (``ps -o lstart``).

    Generalized from the bridge watchdog's ``get_bridge_process_start_ts``
    (issue #1898) — works for any PID (bridge, worker). Returns None on any
    error or unparseable output. None is treated as inconclusive by callers:
    the release verifier classifies ``unknown`` (fail-safe, never a restart
    or a FAILED report), and the watchdog never authorises a restart on it.

    lstart format example: "Mon Jun 16 09:45:12 2026"
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if not raw:
            return None
        # lstart format: "Mon Jun 16 09:45:12 2026"
        # Note: single-digit days are space-padded, e.g. " 6" not "06".
        # strptime handles both with "%e" on many platforms; use "%d" with strip.
        try:
            dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y")
        except ValueError:
            # Some macOS versions zero-pad; try both forms
            try:
                dt = datetime.strptime(raw.strip(), "%a %b  %d %H:%M:%S %Y")
            except ValueError:
                logger.warning("get_process_start_ts: unparseable lstart=%r", raw)
                return None
        # ps lstart is local time; mktime() interprets it as local and returns a
        # Unix timestamp (seconds since UTC epoch) — no explicit UTC conversion needed.
        local_ts = time.mktime(dt.timetuple())
        return local_ts
    except Exception as e:
        logger.debug("get_process_start_ts failed for pid=%s: %s", pid, e)
        return None


def read_boot_beacon(beacon_path: Path) -> tuple[str, float] | None:
    """Read a boot-SHA beacon file written by ``monitoring.boot_beacon``.

    Returns ``(boot_sha, beacon_ts)`` or None when the beacon is missing,
    empty, or malformed (all of which classify ``unknown``, never ``stale``).
    """
    try:
        if not beacon_path.exists():
            return None
        lines = beacon_path.read_text().strip().splitlines()
        if len(lines) < 2:
            return None
        sha = lines[0].strip()
        if not sha:
            return None
        beacon_ts = datetime.fromisoformat(lines[1].strip()).timestamp()
        return sha, beacon_ts
    except Exception:  # swallow-ok: malformed/missing beacon classifies as unknown downstream
        return None


def _classify_process(
    project_dir: Path,
    head_sha: str,
    process_name: str,
    pid: int | None,
    relevant_paths: list[str],
) -> dict:
    """Classify one process's running release as matches | stale | unknown.

    - ``matches``: the beacon belongs to the current image
      (``beacon_ts > process_start_ts``) AND
      ``git log {boot_sha}..{head_sha} -- <relevant_paths>`` is empty — no
      process-relevant commits landed since it booted (``boot_sha == HEAD``
      is the trivial subcase; docs-only commits ahead still match).
    - ``stale`` (positive staleness only): beacon belongs to the current
      image AND the relevant-range log is non-empty.
    - ``unknown``: beacon missing/empty/malformed, no PID,
      ``process_start_ts`` unavailable, orphaned beacon
      (``beacon_ts <= process_start_ts``), or ``boot_sha`` unresolvable by
      git. ``unknown`` never fails a run and never triggers a restart.
    """
    result = {
        "running": pid is not None,
        "boot_sha": None,
        "beacon_ts": None,
        "process_start_ts": None,
        "classification": "unknown",
    }
    beacon = read_boot_beacon(project_dir / "data" / f"{process_name}_boot_sha")
    if beacon is None:
        return result
    result["boot_sha"], result["beacon_ts"] = beacon
    if pid is None:
        return result
    start_ts = get_process_start_ts(pid)
    result["process_start_ts"] = start_ts
    if start_ts is None:
        return result
    if result["beacon_ts"] <= start_ts:
        # Orphaned beacon: predates the current process image — inconclusive.
        return result
    try:
        log_result = run_cmd(
            ["git", "log", "--oneline", f"{result['boot_sha']}..{head_sha}", "--", *relevant_paths],
            cwd=project_dir,
        )
    except Exception as e:
        logger.debug("verify_running_release: git log failed for %s: %s", process_name, e)
        return result
    if log_result.returncode != 0:
        # boot_sha unresolvable (e.g. history rewrite) — inconclusive.
        return result
    result["classification"] = "matches" if not log_result.stdout.strip() else "stale"
    return result


def verify_running_release(project_dir: Path, head_sha: str, machine_check: dict) -> dict:
    """Verify the running bridge/worker releases against pulled HEAD (#1898).

    Returns ``{process_name: {running, boot_sha, beacon_ts, process_start_ts,
    classification}}`` with classification in ``{matches, stale, unknown}``
    per :func:`_classify_process` (positive staleness against the process's
    OWN relevant path set — never raw HEAD equality).

    Per-process machine-role gating (same gates run.py Step 5 uses):

    - bridge: ``machine_check["bridge_projects"]`` truthy AND the bridge
      plist exists on disk (the same signal the restart gate uses —
      Decision 23), so verify and restart can never diverge.
    - worker: ``machine_check["projects"]`` truthy.

    A machine lacking a role (or plist) skips that process entirely — no
    beacon read, no warning.
    """
    results: dict = {}
    if machine_check.get("bridge_projects") and BRIDGE_PLIST_PATH.exists():
        results["bridge"] = _classify_process(
            project_dir, head_sha, "bridge", get_bridge_pid(), BRIDGE_RELEVANT_PATHS
        )
    if machine_check.get("projects"):
        results["worker"] = _classify_process(
            project_dir, head_sha, "worker", get_worker_pid(), WORKER_RELEVANT_PATHS
        )
    return results
