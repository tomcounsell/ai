"""File-based coordination lock for full-suite pytest invocations.

This module prevents concurrent full-suite pytest runs from oversubscribing
CPU and causing cross-run Redis contention. It uses an atomic file-creation
lock protocol:

  1. ``acquire(timeout=1800)`` attempts to atomically create
     ``data/full-suite-running.lock`` via ``os.open(path, O_CREAT | O_EXCL)``.
     If the file already exists, it falls through to ``wait_for_lock()``
     which polls until the lock is released or the holder's PID is dead.

  2. The lock file is JSON: ``{"pid": <PID>, "started_at": <unix_ts>,
     "host": <hostname>}``. The hostname (via ``socket.gethostname()``)
     prevents cross-machine false positives when a shared filesystem is
     mounted (e.g. NFS ``data/``).

  3. ``release()`` only removes the lock if the stored PID matches
     ``os.getpid()``, so a timeout-proceed caller never clobbers the
     original holder's lock.

  4. Stale lock detection: a dead PID (``os.kill(pid, 0)`` raises) or
     corrupt JSON is treated as a stale lock and removed.

  5. Permission errors (read-only filesystem) are caught and logged:
     the caller proceeds without the lock rather than crashing.

CLI usage::

    python scripts/full_suite_lock.py acquire --timeout 1800
    python scripts/full_suite_lock.py release

Exit codes for ``acquire``: 0 if the lock was acquired, 1 if the timeout
expired (proceed without the lock), 2 on unexpected errors.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import time

logger = logging.getLogger("full_suite_lock")

# Lock file lives in data/ which is gitignored — never committed.
DEFAULT_LOCK_PATH = os.path.join("data", "full-suite-running.lock")

# Polling interval for wait_for_lock (seconds).
_POLL_INTERVAL = 2.0


def _hostname() -> str:
    """Return the current hostname for cross-machine disambiguation."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* is a running process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but we can't signal it — it's alive.
        return True
    except OSError:
        return False
    return True


def _read_lock(path: str) -> dict | None:
    """Read and parse the lock file. Return None if missing or corrupt."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        # Corrupt JSON = stale lock
        return {"_corrupt": True}
    return data


def _write_lock(path: str) -> None:
    """Write a lock file with the current process's metadata."""
    data = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "host": _hostname(),
    }
    with open(path, "w") as f:
        json.dump(data, f)


def _remove_lock_if_ours(path: str) -> bool:
    """Remove the lock if its PID matches ours. Return True if removed."""
    data = _read_lock(path)
    if data is None:
        return False
    if data.get("_corrupt"):
        return False
    if data.get("pid") != os.getpid():
        return False
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning("Could not remove lock %s: %s", path, e)
        return False
    return True


def wait_for_lock(path: str, timeout: float) -> bool:
    """Poll the lock file until it is released or the holder is dead.

    Returns True if the lock was released / stale (caller can acquire).
    Returns False if the timeout expired.
    """
    deadline = time.time() + timeout
    host = _hostname()
    last_logged = 0.0

    while time.time() < deadline:
        data = _read_lock(path)

        # Lock file gone — released by the holder.
        if data is None:
            return True

        # Corrupt JSON — stale lock, remove and return.
        if data.get("_corrupt"):
            logger.info("Lock file at %s has corrupt JSON — treating as stale.", path)
            try:
                os.unlink(path)
            except OSError:
                pass
            return True

        lock_pid = data.get("pid")
        lock_host = data.get("host", "unknown")
        started_at = data.get("started_at")

        # Different host — stale lock from a machine that no longer runs.
        if lock_host != host:
            logger.info(
                "Lock at %s held by host=%s (we are %s) — treating as stale.",
                path,
                lock_host,
                host,
            )
            try:
                os.unlink(path)
            except OSError:
                pass
            return True

        # Check if the holder is alive.
        if lock_pid and not _pid_alive(lock_pid):
            logger.info("Lock at %s held by dead PID=%s — removing stale lock.", path, lock_pid)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            return True

        # Lock is held by a live process — log and wait.
        now = time.time()
        if now - last_logged >= 10.0:
            if started_at:
                age_min = (now - started_at) / 60.0
                logger.info(
                    "Waiting for full-suite run PID=%s (started %.1fm ago)...",
                    lock_pid,
                    age_min,
                )
            else:
                logger.info("Waiting for full-suite run PID=%s...", lock_pid)
            last_logged = now

        time.sleep(_POLL_INTERVAL)

    return False


def acquire(
    lock_path: str = DEFAULT_LOCK_PATH,
    timeout: float = 1800,
) -> bool:
    """Attempt to acquire the full-suite coordination lock.

    Returns True if the lock was acquired (caller should release it later).
    Returns False if the timeout expired (caller proceeds without the lock).

    On permission errors (read-only filesystem), logs a warning and returns
    True so the caller can proceed without crashing.
    """
    lock_dir = os.path.dirname(lock_path)
    if lock_dir:
        try:
            os.makedirs(lock_dir, exist_ok=True)
        except PermissionError:
            logger.warning(
                "Cannot create lock directory %s (read-only fs) — proceeding without lock.",
                lock_dir,
            )
            return True
        except OSError as e:
            logger.warning("Lock directory error (%s) — proceeding without lock.", e)
            return True

    # Try atomic creation first.
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        _write_lock(lock_path)
        logger.info("Acquired full-suite lock at %s (PID=%s)", lock_path, os.getpid())
        return True
    except FileExistsError:
        pass  # Lock exists — fall through to wait path.
    except PermissionError:
        logger.warning(
            "Cannot create lock at %s (permission denied) — proceeding without lock.",
            lock_path,
        )
        return True
    except OSError as e:
        logger.warning("Lock creation error (%s) — proceeding without lock.", e)
        return True

    # Lock exists — wait for it to be released or go stale.
    if wait_for_lock(lock_path, timeout):
        # Lock was released or stale — try to acquire again.
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            _write_lock(lock_path)
            logger.info("Acquired full-suite lock at %s (PID=%s)", lock_path, os.getpid())
            return True
        except (FileExistsError, OSError) as e:
            logger.warning(
                "Could not re-acquire lock after wait (%s) — proceeding without lock.",
                e,
            )
            return False
    else:
        load = _safe_loadavg()
        workers = recommended_workers()
        logger.warning(
            "Timed out waiting for lock; proceeding with %d workers (load average: %.2f)",
            workers,
            load,
        )
        return False


def release(lock_path: str = DEFAULT_LOCK_PATH) -> None:
    """Release the full-suite lock if it belongs to the current process.

    Does not raise if the lock file is already gone.
    Does not remove a lock held by a different PID.
    """
    _remove_lock_if_ours(lock_path)


def _safe_loadavg() -> float:
    """Return the 1-minute load average, or 0.0 if unavailable."""
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return 0.0


def recommended_workers() -> int:
    """Return a recommended worker count based on CPU count and load.

    Formula: ``max(1, min(cpu_count, cpu_count - int(load_1min)))``.
    Never returns less than 1.
    """
    cpu = os.cpu_count() or 1
    load = _safe_loadavg()
    return max(1, min(cpu, cpu - int(load)))


def _cli() -> int:
    """CLI entry point for ``python scripts/full_suite_lock.py``."""
    parser = argparse.ArgumentParser(
        description="Coordinate full-suite pytest runs via a file lock."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire_parser = subparsers.add_parser("acquire", help="Acquire the full-suite lock.")
    acquire_parser.add_argument(
        "--timeout",
        type=float,
        default=1800,
        help="Seconds to wait for the lock before proceeding (default: 1800).",
    )
    acquire_parser.add_argument(
        "--lock-path",
        type=str,
        default=DEFAULT_LOCK_PATH,
        help="Path to the lock file (default: data/full-suite-running.lock).",
    )

    release_parser = subparsers.add_parser("release", help="Release the full-suite lock.")
    release_parser.add_argument(
        "--lock-path",
        type=str,
        default=DEFAULT_LOCK_PATH,
        help="Path to the lock file.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    if args.command == "acquire":
        try:
            got_lock = acquire(lock_path=args.lock_path, timeout=args.timeout)
        except Exception as e:
            logger.error("Unexpected error during acquire: %s", e)
            return 2
        if got_lock:
            return 0
        return 1

    if args.command == "release":
        release(lock_path=args.lock_path)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_cli())
