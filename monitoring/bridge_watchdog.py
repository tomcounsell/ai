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

from monitoring.crash_tracker import (
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


@dataclass
class HealthStatus:
    """Bridge health assessment."""

    healthy: bool
    process_running: bool
    logs_fresh: bool
    no_crash_pattern: bool
    issues: list[str]
    recovery_level: int  # 0 = healthy, 1-5 = escalation level needed


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

    healthy = len(issues) == 0

    return HealthStatus(
        healthy=healthy,
        process_running=running,
        logs_fresh=logs_fresh,
        no_crash_pattern=not crash_pattern,
        issues=issues,
        recovery_level=recovery_level,
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
    """Restart the bridge via launchctl. Returns success."""
    try:
        # Unload then load to force restart
        subprocess.run(
            [
                "launchctl",
                "unload",
                f"{Path.home()}/Library/LaunchAgents/com.valor.bridge.plist",
            ],
            capture_output=True,
            timeout=30,
        )
        time.sleep(2)
        result = subprocess.run(
            [
                "launchctl",
                "load",
                f"{Path.home()}/Library/LaunchAgents/com.valor.bridge.plist",
            ],
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

async def send():
    client = TelegramClient(
        "{DATA_DIR}/ai_rebuild_session",
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


def execute_recovery(level: int, issues: list[str]) -> bool:
    """Execute recovery at the specified escalation level."""
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
            # Kill stale + restart
            kill_stale_processes()
            time.sleep(2)
            return restart_bridge()

        elif level == 3:
            # Clear locks + restart
            kill_stale_processes()
            clear_lock_files()
            time.sleep(2)
            return restart_bridge()

        elif level == 4:
            # Revert commit + restart
            if not AUTO_REVERT_ENABLED_FILE.exists():
                logger.warning("Auto-revert not enabled, escalating to level 5")
                return execute_recovery(5, issues)

            kill_stale_processes()
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
