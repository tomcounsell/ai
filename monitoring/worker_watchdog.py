#!/usr/bin/env python3
"""Worker watchdog — external health monitor for the standalone worker.

Runs as a separate launchd service (StartInterval: 120s) so it can detect
and recover from a hung worker (process alive but event loop frozen) AND
from a missing worker (process gone, launchd KeepAlive failed to restart).

Two recovery paths:

1. Stale-heartbeat recovery (existing): when the worker process is alive but
   `data/last_worker_connected` is older than HEARTBEAT_THRESHOLD, kill it so
   launchd respawns it cleanly via KeepAlive.

2. Missing-worker active recovery (new, issue #1311): when the worker process
   is gone for >2 consecutive ticks, escalate via:
     L1 (count == 1): log and wait one tick — give launchd a chance.
     L2 (count >= 2): `launchctl kickstart -k gui/<uid>/com.valor.worker`,
                      then verify a PID returns within 10s.
     L3 (L2 verify failed): `launchctl enable` + kickstart + verify (handles
                            sticky-disable from `worker-disable`).
     L4 (L3 verify failed, count >= 3): write `worker:watchdog:critical:{host}`
                                        Redis key + log CRITICAL.

Operator-disable short-circuit: if `data/worker-disabled` exists, skip the
check entirely — the operator deliberately took the worker down.

Usage:
    python monitoring/worker_watchdog.py           # Run once (for launchd)
    python monitoring/worker_watchdog.py --check   # Print status and exit 0/1
"""

import argparse
import fcntl
import json
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

HEARTBEAT_FILE = PROJECT_DIR / "data" / "last_worker_connected"
HEARTBEAT_THRESHOLD = 600  # 10 min — 2× health-loop interval (300s) with buffer
LOG_FILE = PROJECT_DIR / "logs" / "worker_watchdog.log"

# Active-recovery state files (issue #1311)
DOWN_TICKS_FILE = PROJECT_DIR / "data" / "worker_watchdog_down_ticks"
LOCK_FILE = PROJECT_DIR / "data" / "worker_watchdog.lock"
OPERATOR_DISABLED_FLAG = PROJECT_DIR / "data" / "worker-disabled"

# launchd service label (mirrors scripts/install_worker.sh)
SERVICE_LABEL_PREFIX = os.environ.get("SERVICE_LABEL_PREFIX", "com.valor")
WORKER_LAUNCHD_LABEL = f"{SERVICE_LABEL_PREFIX}.worker"

# Verification poll budget
VERIFY_GRACE_SECONDS = 10
VERIFY_POLL_INTERVAL = 0.5

# Critical Redis key TTL (1 hour — written on every L4 tick, refresh keeps it live)
CRITICAL_KEY_TTL = 3600


def _configure_logger() -> logging.Logger:
    """Configure the named logger with a single rotating file handler.

    Issue #1311: previously `logging.basicConfig` attached a StreamHandler to
    root, the named logger added another file handler, and the named logger
    propagated to root → every line written twice. The plist also redirects
    stdout/stderr to the same log file, compounding the duplication.

    Fix: configure the named logger explicitly. No basicConfig. Set
    `propagate = False` so root never sees these messages. Attach exactly one
    rotating file handler. The plist's StandardOutPath/StandardErrorPath still
    capture any uncaught Python exceptions (printed by the interpreter, not
    via the logger) — those are operationally rare and not duplicated.
    """
    log = logging.getLogger("monitoring.worker_watchdog")
    # Idempotent: clear any handlers attached by prior imports/test runs.
    for h in list(log.handlers):
        log.removeHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)
    return log


logger = _configure_logger()


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
            "message": "worker process not running",
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


# --- Active-recovery helpers (issue #1311) -------------------------------


def _service_target() -> str:
    """Return the launchctl service target string for the worker."""
    return f"gui/{os.getuid()}/{WORKER_LAUNCHD_LABEL}"


def _read_down_ticks() -> int:
    """Read the down-tick counter. Treat missing/corrupt as 0."""
    try:
        raw = DOWN_TICKS_FILE.read_text().strip()
        return int(raw) if raw else 0
    except FileNotFoundError:
        return 0
    except (ValueError, OSError) as e:
        logger.warning("Down-tick counter unreadable (%s) — resetting to 0", e)
        return 0


def _write_down_ticks(n: int) -> None:
    """Write the down-tick counter. Best-effort."""
    try:
        DOWN_TICKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        DOWN_TICKS_FILE.write_text(str(n))
    except OSError as e:
        logger.warning("Could not write down-tick counter: %s", e)


def _clear_down_ticks() -> None:
    """Delete the down-tick counter file. No-op if missing."""
    try:
        DOWN_TICKS_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not clear down-tick counter: %s", e)


def _kickstart_worker() -> bool:
    """Run `launchctl kickstart -k <target>`. Returns True on returncode 0."""
    target = _service_target()
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", target],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info("launchctl kickstart succeeded for %s", target)
            return True
        logger.error(
            "launchctl kickstart failed (rc=%s, stderr=%s)",
            result.returncode,
            result.stderr.strip(),
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error("launchctl kickstart timed out for %s", target)
        return False
    except Exception as e:
        logger.error("launchctl kickstart raised: %s", e)
        return False


def _enable_worker() -> bool:
    """Run `launchctl enable <target>` to clear sticky-disable. Returns success."""
    target = _service_target()
    try:
        result = subprocess.run(
            ["launchctl", "enable", target],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info("launchctl enable succeeded for %s", target)
            return True
        logger.error(
            "launchctl enable failed (rc=%s, stderr=%s)",
            result.returncode,
            result.stderr.strip(),
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error("launchctl enable timed out for %s", target)
        return False
    except Exception as e:
        logger.error("launchctl enable raised: %s", e)
        return False


def _verify_worker_alive(grace_seconds: int = VERIFY_GRACE_SECONDS) -> int | None:
    """Poll for a worker PID until grace_seconds expires. Returns PID or None."""
    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        pid = _get_worker_pid()
        if pid is not None:
            return pid
        time.sleep(VERIFY_POLL_INTERVAL)
    return _get_worker_pid()


def _record_critical_status(reason: str, tick_count: int) -> None:
    """Write `worker:watchdog:critical:{hostname}` Redis key. Best-effort."""
    try:
        import redis  # local import to keep watchdog importable without redis

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        host = socket.gethostname()
        key = f"worker:watchdog:critical:{host}"
        payload = json.dumps(
            {
                "hostname": host,
                "tick_count": tick_count,
                "last_attempt_at": int(time.time()),
                "reason": reason,
            }
        )
        client.set(key, payload, ex=CRITICAL_KEY_TTL)
        logger.info("Wrote critical Redis key %s", key)
    except Exception as e:
        # Never raise — Redis is the secondary surface; CRITICAL log is primary.
        logger.warning("Could not write critical Redis key: %s", e)


def _handle_missing_worker() -> None:
    """Active recovery for `status == down` — escalate L1 → L4."""
    count = _read_down_ticks() + 1
    _write_down_ticks(count)

    if count == 1:
        # L1: give launchd one tick to restart on its own.
        logger.info("Worker missing — giving launchd one tick to restart (count=1)")
        return

    # L2: kickstart + verify
    logger.warning("Worker missing for %s ticks — running launchctl kickstart -k", count)
    if _kickstart_worker():
        pid = _verify_worker_alive()
        if pid is not None:
            logger.info("Worker revived via kickstart (PID=%s) — clearing counter", pid)
            _clear_down_ticks()
            return

    # L3: enable + kickstart + verify (handles sticky-disable)
    logger.warning("Kickstart did not bring worker back — trying launchctl enable + kickstart")
    if _enable_worker() and _kickstart_worker():
        pid = _verify_worker_alive()
        if pid is not None:
            logger.info("Worker revived via enable+kickstart (PID=%s) — clearing counter", pid)
            _clear_down_ticks()
            return

    # L4: critical
    if count >= 3:
        host = socket.gethostname()
        reason = f"kickstart+enable both failed after {count} ticks"
        logger.critical(
            "WORKER WATCHDOG CRITICAL on %s: %s — manual intervention required",
            host,
            reason,
        )
        _record_critical_status(reason, count)
    else:
        # count == 2 and L3 failed — wait one more tick before declaring critical.
        logger.warning(
            "Recovery attempts failed at count=%s — escalating to CRITICAL on next tick",
            count,
        )


def _acquire_tick_lock() -> int | None:
    """Acquire an exclusive flock on the watchdog lock file. Returns fd or None."""
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            os.close(fd)
            return None
    except OSError as e:
        logger.warning("Could not acquire watchdog lock: %s", e)
        return None


def _release_tick_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker watchdog")
    parser.add_argument(
        "--check", action="store_true", help="Print status and exit (0=ok, 1=stale/down)"
    )
    args = parser.parse_args()

    # Operator-disable short-circuit: skip ALL checks if the operator deliberately
    # took the worker down. Verified against scripts/valor-service.sh — the
    # `worker-disable` command runs `launchctl disable`, no flag file is written
    # today, so this is a forward-compatible escape hatch operators can `touch`.
    if not args.check and OPERATOR_DISABLED_FLAG.exists():
        logger.info(
            "Worker disabled by operator (flag %s) — skipping check", OPERATOR_DISABLED_FLAG
        )
        return

    status = check()

    if args.check:
        print(f"Worker status: {status['status']} — {status['message']}")
        sys.exit(0 if status["status"] in ("ok", "starting") else 1)

    # Single-tick lock guards against overlapping invocations (race 1).
    lock_fd = _acquire_tick_lock()
    if lock_fd is None:
        logger.info("Another watchdog tick is in flight — skipping")
        return

    try:
        if status["status"] == "ok":
            logger.debug("Worker healthy (heartbeat %ss ago)", f"{status['heartbeat_age']:.0f}")
            # Reset down-tick counter on any healthy tick.
            if DOWN_TICKS_FILE.exists():
                _clear_down_ticks()
            return

        if status["status"] == "down":
            _handle_missing_worker()
            return

        if status["status"] == "starting":
            logger.info("Worker starting (no heartbeat yet) — skipping")
            return

        # status == "stale" — preserve existing recover() behavior.
        logger.warning(status["message"])
        recover(status)
    finally:
        _release_tick_lock(lock_fd)


if __name__ == "__main__":
    main()
