#!/usr/bin/env python3
"""Worker watchdog — external health monitor for the standalone worker.

Runs as a separate launchd service (StartInterval: 120s) so it can detect
and recover from a hung worker (process alive but event loop frozen).

The worker writes data/last_worker_connected every 300s (health loop interval).
If that file is older than HEARTBEAT_THRESHOLD, the worker is considered hung:
kill the process so launchd restarts it cleanly.

Usage:
    python monitoring/worker_watchdog.py           # Run once (for launchd)
    python monitoring/worker_watchdog.py --check   # Print status and exit 0/1
"""

import argparse
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

HEARTBEAT_FILE = PROJECT_DIR / "data" / "last_worker_connected"
HEARTBEAT_THRESHOLD = 600  # 10 min — 2× health-loop interval (300s) with buffer
LOG_FILE = PROJECT_DIR / "logs" / "worker_watchdog.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
_fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)


def _get_worker_pid() -> int | None:
    """Return PID of running worker process, or None."""
    for pattern in ("python -m worker", "python.*worker/__main__"):
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                pids = [int(p) for p in result.stdout.split() if p.strip().isdigit()]
                if pids:
                    return pids[0]
        except Exception:
            pass
    return None


def _heartbeat_age() -> float | None:
    """Return seconds since last heartbeat write, or None if file missing."""
    try:
        return time.time() - HEARTBEAT_FILE.stat().st_mtime
    except FileNotFoundError:
        return None


def check() -> dict:
    """Assess worker health. Returns a status dict."""
    pid = _get_worker_pid()
    age = _heartbeat_age()

    if pid is None:
        return {
            "status": "down",
            "pid": None,
            "heartbeat_age": age,
            "message": "worker process not running (launchd will restart)",
        }

    if age is None:
        return {
            "status": "starting",
            "pid": pid,
            "heartbeat_age": None,
            "message": "heartbeat file missing — worker may be starting up",
        }

    if age > HEARTBEAT_THRESHOLD:
        return {
            "status": "stale",
            "pid": pid,
            "heartbeat_age": age,
            "message": (
                f"heartbeat is {age:.0f}s old (threshold {HEARTBEAT_THRESHOLD}s) — worker hung"
            ),
        }

    return {
        "status": "ok",
        "pid": pid,
        "heartbeat_age": age,
        "message": f"worker healthy (heartbeat {age:.0f}s ago)",
    }


def recover(status: dict) -> None:
    """Kill a stale worker so launchd restarts it."""
    pid = status["pid"]
    logger.warning(
        "Worker hung (PID %s, heartbeat %ss old) — killing so launchd restarts",
        pid,
        f"{status['heartbeat_age']:.0f}",
    )
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(3)
        # If still alive after SIGTERM, force kill
        try:
            os.kill(pid, 0)  # check if alive
            os.kill(pid, signal.SIGKILL)
            logger.warning("Worker did not exit after SIGTERM — sent SIGKILL")
        except ProcessLookupError:
            pass  # Already gone
        logger.info("Worker killed — launchd will restart within ThrottleInterval")
    except ProcessLookupError:
        logger.info("Worker PID %s already gone", pid)
    except Exception as e:
        logger.error("Failed to kill worker PID %s: %s", pid, e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker watchdog")
    parser.add_argument(
        "--check", action="store_true", help="Print status and exit (0=ok, 1=stale/down)"
    )
    args = parser.parse_args()

    status = check()

    if args.check:
        print(f"Worker status: {status['status']} — {status['message']}")
        sys.exit(0 if status["status"] in ("ok", "starting") else 1)

    if status["status"] == "ok":
        logger.debug("Worker healthy (heartbeat %ss ago)", f"{status['heartbeat_age']:.0f}")
        return

    if status["status"] == "down":
        logger.info("Worker not running — launchd handles restart")
        return

    if status["status"] == "starting":
        logger.info("Worker starting (no heartbeat yet) — skipping")
        return

    # status == "stale"
    logger.warning(status["message"])
    recover(status)


if __name__ == "__main__":
    main()
