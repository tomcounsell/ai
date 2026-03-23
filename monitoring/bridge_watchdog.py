#!/usr/bin/env python3
"""Bridge watchdog - external health monitor for the Telegram bridge.

This runs as a SEPARATE process from the bridge (via launchd) so it can
detect and recover from bridge crashes. It implements a 5-level recovery
escalation chain:

1. Simple restart (launchd handles this automatically)
2. Kill stale processes + restart
3. Clear lock files + restart
4. Revert recent commit + restart (if enabled)
5. Alert human with diagnostics

Usage:
    python monitoring/bridge_watchdog.py        # Run once (for launchd)
    python monitoring/bridge_watchdog.py --loop # Run continuously (testing)
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from monitoring.crash_tracker import (  # noqa: E402
    detect_crash_pattern,
    get_recent_crashes,
    log_crash,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Files and directories
LOG_FILE = PROJECT_DIR / "logs" / "bridge.log"
ERROR_LOG = PROJECT_DIR / "logs" / "bridge.error.log"
DATA_DIR = PROJECT_DIR / "data"
RECOVERY_LOCK = DATA_DIR / "recovery-in-progress"
AUTO_REVERT_ENABLED_FILE = DATA_DIR / "auto-revert-enabled"

# Thresholds
LOG_STALENESS_THRESHOLD = 300  # 5 minutes - logs older than this are stale
WATCHDOG_INTERVAL = 60  # Check every 60 seconds (hardcoded per plan)
ZOMBIE_THRESHOLD_SECONDS = 7200  # 2 hours - processes older than this are zombies
SOFT_INSTANCE_LIMIT = 5  # Warn when more than this many active claude processes

# Process name patterns to scan for zombies (CLI invocations only)
# NOTE: "claude" alone matches Claude Desktop app processes (false positives).
# We match specific CLI patterns instead.
ZOMBIE_PROCESS_PATTERNS = ("claude --", "pyright")


@dataclass
class HealthStatus:
    """Bridge health assessment."""

    healthy: bool
    process_running: bool
    logs_fresh: bool
    no_crash_pattern: bool
    issues: list[str]
    recovery_level: int  # 0 = healthy, 1-5 = escalation level needed
    zombie_count: int = 0
    zombie_pids: list[int] | None = None
    zombie_memory_mb: float = 0.0
    active_claude_count: int = 0

    def __post_init__(self):
        if self.zombie_pids is None:
            self.zombie_pids = []


def is_bridge_running() -> tuple[bool, int | None]:
    """Check if bridge process is running. Returns (running, pid)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "telegram_bridge.py"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = int(result.stdout.strip().split("\n")[0])
            return True, pid
        return False, None
    except Exception as e:
        logger.debug(f"Error checking bridge process: {e}")
        return False, None


def are_logs_fresh() -> bool:
    """Check if bridge logs have been written recently."""
    if not LOG_FILE.exists():
        return False

    try:
        mtime = LOG_FILE.stat().st_mtime
        age = time.time() - mtime
        return age < LOG_STALENESS_THRESHOLD
    except Exception:
        return False


def _parse_elapsed_time(etime_str: str) -> int:
    """Convert ps etime format to seconds.

    Handles formats:
    - MM:SS (e.g., "05:23")
    - HH:MM:SS (e.g., "01:05:23")
    - D-HH:MM:SS (e.g., "2-01:05:23")
    - DD-HH:MM:SS (e.g., "12-01:05:23")
    """
    etime_str = etime_str.strip()
    days = 0

    if "-" in etime_str:
        day_part, time_part = etime_str.split("-", 1)
        days = int(day_part)
    else:
        time_part = etime_str

    parts = time_part.split(":")
    if len(parts) == 2:
        hours, minutes, seconds = 0, int(parts[0]), int(parts[1])
    elif len(parts) == 3:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        raise ValueError(f"Unexpected etime format: {etime_str}")

    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _enumerate_claude_processes() -> list[dict]:
    """Enumerate all claude and pyright processes system-wide.

    Returns list of dicts with keys: pid, etime_seconds, rss_mb, command
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,etime,rss,command"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(f"ps command failed: {result.stderr}")
            return []
    except Exception as e:
        logger.warning(f"Failed to enumerate processes: {e}")
        return []

    processes = []
    for line in result.stdout.strip().split("\n")[1:]:  # Skip header
        line = line.strip()
        if not line:
            continue

        # Check if this line matches any of our target patterns
        matched = False
        for pattern in ZOMBIE_PROCESS_PATTERNS:
            if pattern in line.lower():
                matched = True
                break
        if not matched:
            continue

        # Parse: PID ETIME RSS COMMAND...
        parts = line.split(None, 3)
        if len(parts) < 4:
            logger.debug(f"Skipping malformed ps line: {line}")
            continue

        try:
            pid = int(parts[0])
            etime_seconds = _parse_elapsed_time(parts[1])
            rss_kb = int(parts[2])
            command = parts[3]

            # Skip the watchdog itself and grep processes
            if "bridge_watchdog" in command or "grep" in command:
                continue

            processes.append(
                {
                    "pid": pid,
                    "etime_seconds": etime_seconds,
                    "rss_mb": round(rss_kb / 1024, 1),
                    "command": command[:200],  # Truncate long commands
                }
            )
        except (ValueError, IndexError) as e:
            logger.debug(f"Skipping unparseable ps line: {line} ({e})")
            continue

    return processes


def classify_zombies(
    processes: list[dict],
    threshold_seconds: int = ZOMBIE_THRESHOLD_SECONDS,
) -> tuple[list[dict], list[dict]]:
    """Classify processes as zombies or active.

    Returns (zombies, active) where each is a list of process dicts.
    Zombies are processes with elapsed time exceeding the threshold.
    """
    zombies = []
    active = []

    for proc in processes:
        if proc["etime_seconds"] >= threshold_seconds:
            zombies.append(proc)
        else:
            active.append(proc)

    return zombies, active


def kill_zombie_processes(zombies: list[dict]) -> int:
    """Kill identified zombie processes with SIGTERM -> SIGKILL escalation.

    Returns count of processes successfully killed.
    """
    import signal

    killed = 0
    for proc in zombies:
        pid = proc["pid"]
        try:
            # First try SIGTERM for graceful shutdown
            os.kill(pid, signal.SIGTERM)
            logger.info(
                f"Sent SIGTERM to zombie process {pid} "
                f"(age: {proc['etime_seconds']}s, mem: {proc['rss_mb']}MB)"
            )

            # Wait up to 3 seconds for process to exit
            for _ in range(6):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)  # Check if still alive
                except ProcessLookupError:
                    killed += 1
                    logger.info(f"Zombie process {pid} exited after SIGTERM")
                    break
            else:
                # Process still alive, escalate to SIGKILL
                os.kill(pid, signal.SIGKILL)
                killed += 1
                logger.info(f"Sent SIGKILL to zombie process {pid}")

        except ProcessLookupError:
            # Process already gone between detection and kill
            logger.debug(f"Zombie process {pid} already exited")
            killed += 1
        except PermissionError:
            logger.warning(f"Permission denied killing zombie process {pid}")
        except Exception as e:
            logger.error(f"Error killing zombie process {pid}: {e}")

    return killed


def check_bridge_health() -> HealthStatus:
    """Assess bridge health and determine recovery level needed."""
    issues = []
    recovery_level = 0

    # Check 1: Process running
    running, pid = is_bridge_running()
    if not running:
        issues.append("Bridge process not running")
        recovery_level = max(recovery_level, 1)

    # Check 2: Logs fresh (only if process is "running")
    logs_fresh = are_logs_fresh()
    if running and not logs_fresh:
        issues.append("Bridge logs stale (no activity in 5+ minutes)")
        recovery_level = max(recovery_level, 2)

    # Check 3: Crash pattern detection
    crash_pattern, suspect_commit = detect_crash_pattern()
    if crash_pattern:
        issues.append(f"Crash pattern detected (commit: {suspect_commit})")
        # Only escalate to level 4 if auto-revert is enabled
        if AUTO_REVERT_ENABLED_FILE.exists():
            recovery_level = max(recovery_level, 4)
        else:
            recovery_level = max(recovery_level, 3)

    # Check recent crash count
    recent_crashes = get_recent_crashes(1800)  # 30 min
    if len(recent_crashes) >= 5:
        issues.append(f"{len(recent_crashes)} crashes in last 30 minutes")
        recovery_level = max(recovery_level, 5)  # Alert human

    # Check 4: Zombie process detection
    all_processes = _enumerate_claude_processes()
    zombies, active = classify_zombies(all_processes)

    zombie_count = len(zombies)
    zombie_pids = [z["pid"] for z in zombies]
    zombie_memory_mb = round(sum(z["rss_mb"] for z in zombies), 1)
    active_claude_count = len(active)

    if zombie_count > 0:
        if running and logs_fresh:
            # Bridge is healthy — just kill zombies directly, don't restart
            killed = kill_zombie_processes(zombies)
            issues.append(
                f"{zombie_count} zombie process(es) cleaned up "
                f"({killed} killed, memory freed: {zombie_memory_mb}MB)"
            )
            # Do NOT escalate — bridge is fine, zombies are handled
        else:
            issues.append(
                f"{zombie_count} zombie process(es) detected "
                f"(PIDs: {zombie_pids}, memory: {zombie_memory_mb}MB)"
            )
            recovery_level = max(recovery_level, 2)

    if active_claude_count > SOFT_INSTANCE_LIMIT:
        logger.warning(
            f"High concurrent claude instance count: {active_claude_count} "
            f"(soft limit: {SOFT_INSTANCE_LIMIT})"
        )

    healthy = len(issues) == 0

    return HealthStatus(
        healthy=healthy,
        process_running=running,
        logs_fresh=logs_fresh,
        no_crash_pattern=not crash_pattern,
        issues=issues,
        recovery_level=recovery_level,
        zombie_count=zombie_count,
        zombie_pids=zombie_pids,
        zombie_memory_mb=zombie_memory_mb,
        active_claude_count=active_claude_count,
    )


def kill_stale_processes() -> int:
    """Kill any stale bridge processes. Returns count killed."""
    killed = 0
    try:
        result = subprocess.run(
            ["pgrep", "-f", "telegram_bridge.py"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for pid_str in result.stdout.strip().split("\n"):
                if pid_str:
                    try:
                        pid = int(pid_str)
                        os.kill(pid, 9)
                        killed += 1
                        logger.info(f"Killed stale bridge process {pid}")
                    except (ValueError, ProcessLookupError, PermissionError):
                        pass
    except Exception as e:
        logger.error(f"Error killing stale processes: {e}")
    return killed


def clear_lock_files() -> int:
    """Clear session lock files. Returns count cleared."""
    cleared = 0
    for pattern in ["*.session-journal", "*.session-wal", "*.session-shm"]:
        for f in DATA_DIR.glob(pattern):
            try:
                f.unlink()
                cleared += 1
                logger.info(f"Cleared lock file: {f.name}")
            except Exception:
                pass
    return cleared


def restart_bridge() -> bool:
    """Restart the bridge via launchctl kickstart. Returns success."""
    try:
        uid = os.getuid()
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/com.valor.bridge"],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Failed to restart bridge: {e}")
        return False


def revert_last_commit() -> bool:
    """Revert HEAD commit and restart. Returns success."""
    try:
        # Create revert commit
        result = subprocess.run(
            ["git", "revert", "HEAD", "--no-edit"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error(f"Git revert failed: {result.stderr}")
            return False

        logger.info("Created revert commit")
        return True
    except Exception as e:
        logger.error(f"Failed to revert commit: {e}")
        return False


def send_telegram_alert(message: str) -> bool:
    """Send alert via Telegram (to supervisor DM)."""
    try:
        # Use the bridge's Telegram session to send a message
        # This is a simple approach - spawn a quick script
        script = f'''
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
import os

load_dotenv("{PROJECT_DIR}/.env")
from pathlib import Path as _P
load_dotenv(_P.home() / "Desktop" / "Valor" / ".env")

async def send():
    client = TelegramClient(
        "{DATA_DIR}/valor_bridge",
        int(os.getenv("TELEGRAM_API_ID")),
        os.getenv("TELEGRAM_API_HASH"),
    )
    await client.start()
    # Send to supervisor (Tom's user ID from whitelist)
    await client.send_message(179144806, """{message}""")
    await client.disconnect()

asyncio.run(send())
'''
        result = subprocess.run(
            [f"{PROJECT_DIR}/.venv/bin/python", "-c", script],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return False


def _kill_detected_zombies() -> int:
    """Detect and kill zombie claude/pyright processes. Returns count killed."""
    processes = _enumerate_claude_processes()
    zombies, _ = classify_zombies(processes)
    if zombies:
        logger.info(f"Found {len(zombies)} zombie process(es) to kill")
        return kill_zombie_processes(zombies)
    return 0


def execute_recovery(level: int, issues: list[str]) -> bool:
    """Execute recovery at the specified escalation level."""
    if level == 0:
        logger.warning(f"Recovery called with level 0 (no action needed): {issues}")
        return True

    logger.info(f"Executing recovery level {level}")

    # Create recovery lock
    RECOVERY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    RECOVERY_LOCK.write_text(
        json.dumps(
            {
                "level": level,
                "started": datetime.now().isoformat(),
                "issues": issues,
            }
        )
    )

    try:
        if level == 1:
            # Simple restart (launchd should handle, but we can help)
            return restart_bridge()

        elif level == 2:
            # Kill stale + zombie processes + restart
            kill_stale_processes()
            _kill_detected_zombies()
            time.sleep(2)
            return restart_bridge()

        elif level == 3:
            # Clear locks + kill zombies + restart
            kill_stale_processes()
            _kill_detected_zombies()
            clear_lock_files()
            time.sleep(2)
            return restart_bridge()

        elif level == 4:
            # Revert commit + restart
            if not AUTO_REVERT_ENABLED_FILE.exists():
                logger.warning("Auto-revert not enabled, escalating to level 5")
                return execute_recovery(5, issues)

            kill_stale_processes()
            _kill_detected_zombies()
            clear_lock_files()

            if revert_last_commit():
                send_telegram_alert(
                    f"🔄 Auto-revert triggered\n\n"
                    f"Issues: {', '.join(issues)}\n\n"
                    f"HEAD reverted to previous commit. Bridge restarting."
                )
                time.sleep(2)
                return restart_bridge()
            else:
                return execute_recovery(5, issues)

        elif level == 5:
            # Alert human
            diagnostic = (
                "🚨 Bridge Recovery Failed\n\n"
                "Issues:\n" + "\n".join(f"• {i}" for i in issues) + "\n\n"
                "Recovery levels 1-4 exhausted.\n"
                "Manual intervention required."
            )
            send_telegram_alert(diagnostic)
            log_crash("Recovery exhausted - alerting human")
            return False

    finally:
        # Remove recovery lock
        try:
            RECOVERY_LOCK.unlink()
        except Exception:
            pass

    return False


def run_health_check() -> bool:
    """Run a single health check cycle. Returns True if healthy."""
    # Skip if recovery already in progress
    if RECOVERY_LOCK.exists():
        try:
            lock_data = json.loads(RECOVERY_LOCK.read_text())
            lock_time = datetime.fromisoformat(lock_data.get("started", ""))
            age = (datetime.now() - lock_time).total_seconds()
            if age < 300:  # 5 minute recovery timeout
                logger.info("Recovery in progress, skipping check")
                return True
            else:
                logger.warning("Stale recovery lock, removing")
                RECOVERY_LOCK.unlink()
        except Exception:
            pass

    status = check_bridge_health()

    if status.healthy:
        logger.debug("Bridge healthy")
        return True

    logger.warning(f"Bridge unhealthy: {', '.join(status.issues)}")
    logger.info(f"Recovery level needed: {status.recovery_level}")

    # Execute recovery
    success = execute_recovery(status.recovery_level, status.issues)

    if success:
        logger.info("Recovery successful")
    else:
        logger.error("Recovery failed")

    return success


def main():
    parser = argparse.ArgumentParser(description="Bridge health watchdog")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously (for testing)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check health only, don't execute recovery",
    )
    args = parser.parse_args()

    if args.check_only:
        status = check_bridge_health()
        print(f"Healthy: {status.healthy}")
        print(f"Process running: {status.process_running}")
        print(f"Logs fresh: {status.logs_fresh}")
        print(f"No crash pattern: {status.no_crash_pattern}")
        if status.issues:
            print(f"Issues: {', '.join(status.issues)}")
        print(f"Recovery level: {status.recovery_level}")
        print(f"Zombie processes: {status.zombie_count}")
        if status.zombie_pids:
            print(f"Zombie PIDs: {status.zombie_pids}")
            print(f"Zombie memory: {status.zombie_memory_mb}MB")
        print(f"Active claude instances: {status.active_claude_count}")
        if status.active_claude_count > SOFT_INSTANCE_LIMIT:
            print(
                f"WARNING: Active instances exceed soft limit "
                f"({status.active_claude_count} > {SOFT_INSTANCE_LIMIT})"
            )
        return 0 if status.healthy else 1

    if args.loop:
        logger.info(f"Starting watchdog loop (interval: {WATCHDOG_INTERVAL}s)")
        while True:
            try:
                run_health_check()
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
            time.sleep(WATCHDOG_INTERVAL)
    else:
        # Single check (for launchd StartInterval)
        try:
            run_health_check()
        except Exception as e:
            logger.error(f"Watchdog error: {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
