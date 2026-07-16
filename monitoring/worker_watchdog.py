#!/usr/bin/env python3
"""Worker watchdog — external health monitor for the standalone worker.

Runs as a separate launchd service (StartInterval: 120s) so it can detect
and recover from a hung worker (process alive but event loop frozen) AND
from a missing worker (process gone, launchd KeepAlive failed to restart).

Two recovery paths:

1. Stale-heartbeat recovery (issue #1767): when the worker process is alive but
   `data/last_worker_connected` is older than HEARTBEAT_THRESHOLD, escalate via
   a verified-kill ladder:
     W1: SIGTERM → poll 5s
     W2: SIGKILL → poll 10s  (queued against U-state; may not deliver immediately)
     W3: launchctl bootout → poll 10s  (removes launchd job; cleans fd table on exit)
     W4: Write CRITICAL Redis key for operator visibility
     W5: Final alert — stop; launchd will respawn once U-state frees

   A genuine U-state (uninterruptible sleep) process cannot be killed from
   userspace on macOS — SIGKILL queues until the blocking syscall returns.
   The ladder converts that into a loud operator signal rather than a hang.

2. Missing-worker active recovery (new, issue #1311): when the worker process
   is gone for >2 consecutive ticks, escalate via:
     L1 (count == 1): log and wait one tick — give launchd a chance.
     L2 (count >= 2): `launchctl kickstart -k gui/<uid>/com.valor.worker`,
                      then verify a PID returns within 10s.
     L2.5 (issue #1407): if L2 failed with rc=113 / "Could not find service"
                         AND the worker plist exists on disk, run
                         `launchctl bootstrap gui/<uid> <plist>` to re-register
                         the service in the gui domain, then retry kickstart.
                         Handles the regression where `start_worker()` used the
                         legacy `launchctl load` path and left the service
                         invisible to `gui/<uid>/` queries.
     L3 (L2/L2.5 verify failed): `launchctl enable` + kickstart + verify
                                 (handles sticky-disable from `worker-disable`).
     L4 (L3 verify failed, count >= 3): write `worker:watchdog:critical:{host}`
                                        Redis key + log CRITICAL.

Down-tick counter: Redis key `worker:watchdog:down_ticks:{hostname}` maintained
via `POPOTO_REDIS_DB.incr` + `expire(3600)` (atomic by Redis semantics).
Reset by `DEL` on a healthy tick. Each watchdog tick is a fresh launchd
invocation so the counter lives outside the process — Redis is the natural fit.

Operator-disable short-circuit: detects sticky-disable via
`launchctl print-disabled gui/<uid>` — checks whether
`"com.valor.worker" => disabled` appears in the output. This is the only
source of truth; `worker-disable` in valor-service.sh calls `launchctl disable`
directly (no sidecar flag file). When disabled, clear the down-tick counter
(so a future re-enable starts fresh) and return before any L1/L2/L3 dispatch.

Usage:
    python monitoring/worker_watchdog.py           # Run once (for launchd)
    python monitoring/worker_watchdog.py --check   # Print status and exit 0/1
"""

import argparse
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

from config.settings import settings  # noqa: E402

HEARTBEAT_FILE = PROJECT_DIR / "data" / "last_worker_connected"
# 180s = 6× the 30s heartbeat write interval (plan: ≥6× guard).
# Safe to tighten because the heartbeat is now on its own thread (issue #1767)
# and cannot be starved by thread-pool saturation. Env-tunable for
# conservative rollout (e.g. HEARTBEAT_THRESHOLD=300 on initial deploy).
HEARTBEAT_THRESHOLD = int(os.environ.get("HEARTBEAT_THRESHOLD", "180"))
LOG_FILE = PROJECT_DIR / "logs" / "worker_watchdog.log"

# launchd service label (mirrors scripts/install_worker.sh)
SERVICE_LABEL_PREFIX = os.environ.get("SERVICE_LABEL_PREFIX", "com.valor")
WORKER_LAUNCHD_LABEL = f"{SERVICE_LABEL_PREFIX}.worker"

# Plist path (mirrors scripts/valor-service.sh:55-56 and scripts/install_worker.sh).
# Used by L2.5 bootstrap-recovery to re-register the worker service in
# `gui/<uid>/` when `launchctl kickstart` fails with rc=113 / "Could not find
# service". If this constant ever drifts from the install scripts, the L2.5
# branch becomes a no-op (plist-existence gate falls through to L3).
WORKER_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{WORKER_LAUNCHD_LABEL}.plist"

# Verification poll budget
VERIFY_GRACE_SECONDS = 10
VERIFY_POLL_INTERVAL = 0.5

# Critical Redis key TTL (1 hour — written on every L4 tick, refresh keeps it live)
CRITICAL_KEY_TTL = 3600

# Down-tick Redis key TTL (1 hour — auto-clears stale state across launchd restarts)
DOWN_TICKS_KEY_TTL = 3600

# Respawn circuit breaker (issue #2100): trip when the worker restarts too many
# times inside a short window — i.e. launchd (KeepAlive=true, ThrottleInterval=10)
# is tightly crash-looping the worker. worker/__main__.py records each startup in
# the `worker:starts:{host}` sorted set; the watchdog counts starts in the window
# via ZCOUNT and, when the count crosses the threshold, `launchctl disable`s the
# worker (halting the fixed-cadence loop) and writes a dedicated operator-visible
# critical key. Break-glass recovery: `launchctl enable` + `worker-start`, then
# delete `worker:watchdog:critical:breaker:{host}`.
#
# Provisional/tunable: 5 starts in 120s is a grain-of-salt default chosen high
# enough that only a genuine tight crash-loop trips it (a scripted restart is one
# start and is additionally guarded by the restart-suppression marker). Override
# via WORKER_RESPAWN_CIRCUIT_THRESHOLD / WORKER_RESPAWN_CIRCUIT_WINDOW_S. Named
# locally (#1968 promote-vs-name-locally) — single-file knobs, not promoted to
# config.settings.
WORKER_RESPAWN_CIRCUIT_THRESHOLD = int(os.environ.get("WORKER_RESPAWN_CIRCUIT_THRESHOLD", "5"))
WORKER_RESPAWN_CIRCUIT_WINDOW_S = int(os.environ.get("WORKER_RESPAWN_CIRCUIT_WINDOW_S", "120"))

# Breaker critical key TTL — refreshed on every tripping tick so it stays live
# while the crash-loop condition persists. The launchctl-disable is the
# persistent signal (survives this TTL); the key is only the operator-visible
# annotation ("held disabled by respawn breaker").
BREAKER_CRITICAL_KEY_TTL = 3600


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
    # Use case-insensitive flag (-i) to match both `python` and `Python`
    # (macOS launchd spawns with the full path: .../Python.app/.../Python)
    for pattern in ("python -m worker", "python.*worker/__main__"):
        try:
            result = subprocess.run(
                ["pgrep", "-if", pattern],
                capture_output=True,
                text=True,
                timeout=settings.timeouts.subprocess_default_s,
            )
            if result.returncode == 0:
                pids = [int(p) for p in result.stdout.split() if p.strip().isdigit()]
                if pids:
                    return pids[0]
        except Exception:  # noqa: S110 -- falls through to next pgrep pattern
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


def _poll_pid_dead(pid: int, timeout_sec: float, interval: float = 0.5) -> bool:
    """Poll until PID is gone or timeout expires. Returns True if dead.

    Uses os.kill(pid, 0) to probe liveness. A ProcessLookupError or
    PermissionError both indicate the process is gone (or unmonitorable),
    so either counts as dead for recovery purposes.
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(interval)
    # One final check after the deadline
    try:
        os.kill(pid, 0)
        return False
    except (ProcessLookupError, PermissionError):
        return True


def recover(status: dict) -> None:
    """Verified-kill escalation ladder for a stale-heartbeat worker (issue #1767).

    W1: SIGTERM → poll 5s
    W2: SIGKILL → poll 10s  (queued against U-state; may not deliver immediately)
    W3: launchctl bootout → poll 10s  (removes launchd job; cleans fd table on exit)
    W4: Write CRITICAL Redis key for operator visibility
    W5: Final structured alert — stop; launchd will respawn once U-state frees

    Guard: PID-reuse window (~5 min on macOS). Each rung re-checks
    os.kill(pid, 0) immediately before acting to avoid acting on a recycled PID.

    Note: against a genuine U-state process (uninterruptible sleep in a
    blocking syscall — e.g. fd reads against a hung device or filesystem),
    SIGKILL is queued until the syscall returns. W3 (bootout) removes the
    launchd job; once the kernel cleans up the process's fd table on exit,
    the blocked read returns and the process can exit naturally, allowing
    launchd to respawn.
    """
    pid = status.get("pid")
    if not pid:
        logger.error("recover(): no PID in status — cannot kill")
        return

    host = socket.gethostname()
    logger.warning(
        "Worker hung (PID %d, heartbeat %ss old) — starting verified-kill escalation ladder",
        pid,
        f"{status.get('heartbeat_age', 0):.0f}",
    )

    # W1: SIGTERM → poll 5s
    logger.info("Watchdog W1: sending SIGTERM to worker PID %d", pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        logger.info("Watchdog W1: PID %d already gone — recovery complete", pid)
        return
    if _poll_pid_dead(pid, timeout_sec=5.0):
        logger.info("Watchdog W1: worker PID %d exited after SIGTERM", pid)
        return

    # W2: SIGKILL → poll 10s
    logger.warning(
        "Watchdog W2: PID %d survived SIGTERM — sending SIGKILL (may queue in U-state)", pid
    )
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        logger.info("Watchdog W2: PID %d gone — recovery complete", pid)
        return
    if _poll_pid_dead(pid, timeout_sec=10.0):
        logger.info("Watchdog W2: worker PID %d exited after SIGKILL", pid)
        return

    # W3: launchctl bootout → poll 10s
    logger.warning("Watchdog W3: PID %d survived SIGKILL — attempting launchctl bootout", pid)
    try:
        _bootout_worker()
    except Exception as exc:
        logger.warning("Watchdog W3: bootout raised %s — continuing", exc)
    if _poll_pid_dead(pid, timeout_sec=10.0):
        logger.info("Watchdog W3: worker PID %d exited after bootout", pid)
        return

    # W4: CRITICAL alert
    logger.critical(
        "Watchdog W4: worker PID %d is STILL ALIVE after SIGTERM+SIGKILL+bootout — "
        "process is in U-state (uninterruptible sleep in a blocking syscall). "
        "Operator intervention required: investigate the hung fd/device or "
        "reboot the machine. Redis CRITICAL key written.",
        pid,
    )
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.set(f"worker:watchdog:critical:{host}", f"U-state hung PID {pid}", ex=CRITICAL_KEY_TTL)
        logger.info("Watchdog W4: wrote critical Redis key for host %s", host)
    except Exception as exc:
        logger.error("Watchdog W4: failed to write critical Redis key: %s", exc)

    # W5: Final log — launchd will respawn once U-state frees
    logger.critical(
        "Watchdog W5: no further automated action. launchd will restart the worker "
        "once PID %d exits (U-state will free when the blocking syscall returns). "
        "Session sweep will run at next startup.",
        pid,
    )


# --- Active-recovery helpers (issue #1311) -------------------------------


def _service_target() -> str:
    """Return the launchctl service target string for the worker."""
    return f"gui/{os.getuid()}/{WORKER_LAUNCHD_LABEL}"


def _bootout_worker() -> bool:
    """Run `launchctl bootout gui/<uid>/<label>` to evict the worker job.

    Used by W3 recovery (issue #1767) — removes the launchd job entry so the
    kernel cleans up the process's fd table on exit, allowing an fd read
    blocking in U-state to return and the process to exit.
    Returns True on returncode 0.
    """
    target = _service_target()
    try:
        result = subprocess.run(
            ["launchctl", "bootout", target],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        if result.returncode == 0:
            logger.info("launchctl bootout succeeded for %s", target)
            return True
        logger.warning(
            "launchctl bootout failed (rc=%s, stderr=%s)",
            result.returncode,
            result.stderr.strip(),
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("launchctl bootout timed out for %s", target)
        return False
    except Exception as e:
        logger.warning("launchctl bootout raised: %s", e)
        return False


def _down_ticks_key() -> str:
    """Return the Redis key for the per-host down-tick counter."""
    return f"worker:watchdog:down_ticks:{socket.gethostname()}"


def _read_down_ticks() -> int:
    """Read the down-tick counter from Redis. Returns 0 on any failure."""
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        val = _R.get(_down_ticks_key())
        return int(val) if val else 0
    except Exception as e:
        logger.warning("Could not read down-tick counter from Redis (%s) — treating as 0", e)
        return 0


def _increment_down_ticks() -> int:
    """Atomically increment and return the new down-tick counter value."""
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        key = _down_ticks_key()
        count = _R.incr(key)
        _R.expire(key, DOWN_TICKS_KEY_TTL)
        return int(count)
    except Exception as e:
        logger.warning("Could not increment down-tick counter in Redis (%s) — defaulting to 1", e)
        return 1


def _clear_down_ticks() -> None:
    """Delete the down-tick counter key from Redis. Best-effort."""
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.delete(_down_ticks_key())
    except Exception as e:
        logger.warning("Could not clear down-tick counter from Redis: %s", e)


def _is_operator_disabled() -> bool:
    """Return True if the worker service is sticky-disabled via launchctl.

    Parses `launchctl print-disabled gui/<uid>` output for the line:
        "com.valor.worker" => disabled
    This is the only authoritative source — `worker-disable` in valor-service.sh
    calls `launchctl disable` directly (no sidecar flag file exists).
    """
    target_domain = f"gui/{os.getuid()}"
    try:
        result = subprocess.run(
            ["launchctl", "print-disabled", target_domain],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        for line in result.stdout.splitlines():
            if f'"{WORKER_LAUNCHD_LABEL}"' in line and "disabled" in line:
                return True
        return False
    except Exception as e:
        logger.warning("Could not check launchctl print-disabled (%s) — assuming enabled", e)
        return False


def _kickstart_worker_detailed() -> tuple[bool, int, str]:
    """Run `launchctl kickstart -k <target>` and return (ok, returncode, stderr).

    Exposes returncode and stderr so callers can distinguish rc=113 / "Could not
    find service" (which means the service is not registered in the gui domain
    and should trigger L2.5 bootstrap-recovery) from other failures.

    On timeout or unexpected exception, returns (False, -1, error_message).
    """
    target = _service_target()
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", target],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        if result.returncode == 0:
            logger.info("launchctl kickstart succeeded for %s", target)
            return True, 0, ""
        stderr = result.stderr.strip()
        logger.error(
            "launchctl kickstart failed (rc=%s, stderr=%s)",
            result.returncode,
            stderr,
        )
        return False, result.returncode, stderr
    except subprocess.TimeoutExpired:
        logger.error("launchctl kickstart timed out for %s", target)
        return False, -1, "timeout"
    except Exception as e:
        logger.error("launchctl kickstart raised: %s", e)
        return False, -1, str(e)


def _kickstart_worker() -> bool:
    """Thin wrapper over `_kickstart_worker_detailed` for callers that only need ok/fail."""
    ok, _rc, _stderr = _kickstart_worker_detailed()
    return ok


def _bootstrap_worker() -> bool:
    """Run `launchctl bootstrap gui/<uid> <plist>` to register the worker service.

    Used by L2.5 recovery (issue #1407) to heal the case where the service is
    not registered in the gui domain — typically because a prior `start_worker`
    invocation used the legacy `launchctl load` path. Returns True on
    returncode 0.

    Caller MUST gate this on `WORKER_PLIST_PATH.exists()` so the watchdog
    never spuriously bootstraps a nonexistent service (e.g., uninstalled host).
    """
    target_domain = f"gui/{os.getuid()}"
    try:
        result = subprocess.run(
            ["launchctl", "bootstrap", target_domain, str(WORKER_PLIST_PATH)],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        if result.returncode == 0:
            logger.info(
                "launchctl bootstrap succeeded for %s (plist=%s)",
                target_domain,
                WORKER_PLIST_PATH,
            )
            return True
        logger.error(
            "launchctl bootstrap failed (rc=%s, stderr=%s)",
            result.returncode,
            result.stderr.strip(),
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error("launchctl bootstrap timed out for %s", target_domain)
        return False
    except Exception as e:
        logger.error("launchctl bootstrap raised: %s", e)
        return False


def _enable_worker() -> bool:
    """Run `launchctl enable <target>` to clear sticky-disable. Returns success."""
    target = _service_target()
    try:
        result = subprocess.run(
            ["launchctl", "enable", target],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
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
        from popoto.redis_db import POPOTO_REDIS_DB as _R

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
        _R.set(key, payload, ex=CRITICAL_KEY_TTL)
        logger.info("Wrote critical Redis key %s", key)
    except Exception as e:
        # Never raise — Redis is the secondary surface; CRITICAL log is primary.
        logger.warning("Could not write critical Redis key: %s", e)


# --- Respawn circuit breaker (issue #2100) ------------------------------


def _starts_beacon_key() -> str:
    """Return the Redis sorted-set key for the per-host worker start-beacon."""
    return f"worker:starts:{socket.gethostname()}"


def _restart_suppress_key() -> str:
    """Return the Redis key for the per-host operator-restart suppression marker."""
    return f"worker:restart_suppress:{socket.gethostname()}"


def _breaker_critical_key() -> str:
    """Return the DEDICATED Redis key for the tripped respawn-breaker state.

    Distinct from the shared `worker:watchdog:critical:{host}` key owned by
    `_record_critical_status` (the U-state hung path) — the two states must never
    clobber each other's value/TTL (issue #2100 critique blocker 1).
    """
    return f"worker:watchdog:critical:breaker:{socket.gethostname()}"


def _count_recent_starts(window_s: int) -> int:
    """Count worker starts recorded within the last `window_s` seconds.

    Reads `worker:starts:{host}` via `ZCOUNT (now-window) now`. Returns 0 on any
    Redis failure — a breaker that cannot read the beacon must fail open (never
    trip on missing data).
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        now = int(time.time())
        return int(_R.zcount(_starts_beacon_key(), now - window_s, now))
    except Exception as e:
        logger.warning("Could not read respawn start-beacon (%s) — treating as 0", e)
        return 0


def _is_restart_suppressed() -> bool:
    """Return True if the operator-restart suppression marker is set.

    A scripted/manual restart (valor-service.sh, install_worker.sh) sets a
    short-lived `worker:restart_suppress:{host}` marker before cycling the
    worker, so a deliberate restart never masquerades as a crash-loop.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        return _R.exists(_restart_suppress_key()) > 0
    except Exception as e:
        logger.warning("Could not read restart-suppress marker (%s) — assuming absent", e)
        return False


def _disable_worker() -> bool:
    """Run `launchctl disable <target>` to persistently stop the respawn loop.

    Unlike `bootout`, `disable` survives the launchd KeepAlive respawn timer AND
    the breaker's Redis-key TTL, so a tripped breaker holds the worker down until
    an operator runs `launchctl enable` + `worker-start`. Returns success.
    """
    target = _service_target()
    try:
        result = subprocess.run(
            ["launchctl", "disable", target],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
        )
        if result.returncode == 0:
            logger.info("launchctl disable succeeded for %s", target)
            return True
        logger.error(
            "launchctl disable failed (rc=%s, stderr=%s)",
            result.returncode,
            result.stderr.strip(),
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error("launchctl disable timed out for %s", target)
        return False
    except Exception as e:
        logger.error("launchctl disable raised: %s", e)
        return False


def _record_breaker_critical(reason: str, start_count: int) -> None:
    """Write the DEDICATED `worker:watchdog:critical:breaker:{host}` key.

    Best-effort — never raises. Distinct key from `_record_critical_status` so the
    respawn-breaker state and the U-state hung state never clobber each other.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        host = socket.gethostname()
        payload = json.dumps(
            {
                "hostname": host,
                "start_count": start_count,
                "window_s": WORKER_RESPAWN_CIRCUIT_WINDOW_S,
                "threshold": WORKER_RESPAWN_CIRCUIT_THRESHOLD,
                "tripped_at": int(time.time()),
                "reason": reason,
            }
        )
        _R.set(_breaker_critical_key(), payload, ex=BREAKER_CRITICAL_KEY_TTL)
        logger.info("Wrote breaker critical Redis key %s", _breaker_critical_key())
    except Exception as e:
        logger.warning("Could not write breaker critical Redis key: %s", e)


def _check_and_trip_respawn_breaker() -> bool:
    """Read the respawn beacon and trip the circuit breaker if crash-looping.

    Returns True if the breaker tripped this tick (caller must `return`
    immediately — do NOT fall through to the down-tick/missing-worker ladder,
    whose L3 `_enable_worker()` would undo the disable in the same tick, per
    issue #2100 critique follow-up #2).

    Skips tripping when the operator-restart suppression marker is set so a
    scripted deploy/restart is never mistaken for a crash-loop.
    """
    start_count = _count_recent_starts(WORKER_RESPAWN_CIRCUIT_WINDOW_S)
    if start_count < WORKER_RESPAWN_CIRCUIT_THRESHOLD:
        return False

    if _is_restart_suppressed():
        logger.info(
            "Respawn beacon shows %s starts in %ss (>= threshold %s) but restart-suppress "
            "marker is set — treating as a scripted restart, not tripping the breaker",
            start_count,
            WORKER_RESPAWN_CIRCUIT_WINDOW_S,
            WORKER_RESPAWN_CIRCUIT_THRESHOLD,
        )
        return False

    reason = (
        f"respawn circuit breaker tripped: {start_count} worker starts in "
        f"{WORKER_RESPAWN_CIRCUIT_WINDOW_S}s (threshold {WORKER_RESPAWN_CIRCUIT_THRESHOLD}) — "
        "launchd is crash-looping the worker"
    )
    logger.critical("WORKER WATCHDOG CRITICAL on %s: %s", socket.gethostname(), reason)
    _disable_worker()
    _record_breaker_critical(reason, start_count)
    # Clear the down-tick counter so a future operator re-enable starts fresh.
    _clear_down_ticks()
    return True


def _handle_missing_worker() -> None:
    """Active recovery for `status == down` — escalate L1 → L4."""
    count = _increment_down_ticks()

    if count == 1:
        # L1: give launchd one tick to restart on its own.
        logger.info("Worker missing — giving launchd one tick to restart (count=1)")
        return

    # L2: kickstart + verify
    logger.warning("Worker missing for %s ticks — running launchctl kickstart -k", count)
    kickstart_ok, kickstart_rc, kickstart_stderr = _kickstart_worker_detailed()
    if kickstart_ok:
        pid = _verify_worker_alive()
        if pid is not None:
            logger.info("Worker revived via kickstart (PID=%s) — clearing counter", pid)
            _clear_down_ticks()
            return

    # L2.5 (issue #1407): rc=113 / "Could not find service" means the service
    # is not registered in the gui domain — typically because a prior
    # `start_worker` used the legacy `launchctl load` path. Self-heal by
    # bootstrapping the plist and retrying kickstart. Gated on plist-existence
    # so an uninstalled host falls through cleanly to L3.
    bootstrap_attempted = False
    if (
        not kickstart_ok
        and (kickstart_rc == 113 or "Could not find service" in kickstart_stderr)
        and WORKER_PLIST_PATH.exists()
    ):
        logger.warning(
            "Kickstart returned rc=%s (%s) — attempting L2.5 bootstrap recovery",
            kickstart_rc,
            kickstart_stderr,
        )
        bootstrap_attempted = True
        if _bootstrap_worker() and _kickstart_worker():
            pid = _verify_worker_alive()
            if pid is not None:
                logger.info(
                    "Worker revived via bootstrap+kickstart (PID=%s) — clearing counter", pid
                )
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
        if bootstrap_attempted:
            reason = f"bootstrap+kickstart+enable all failed after {count} ticks"
        else:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker watchdog")
    parser.add_argument(
        "--check", action="store_true", help="Print status and exit (0=ok, 1=stale/down)"
    )
    args = parser.parse_args()

    # Operator-disable short-circuit: skip ALL checks if the operator deliberately
    # took the worker down via `worker-disable` (which calls `launchctl disable`).
    # Clear the down-tick counter so a future re-enable starts fresh.
    if not args.check and _is_operator_disabled():
        logger.info("Worker disabled by operator (launchctl print-disabled) — skipping check")
        _clear_down_ticks()
        return

    # Respawn circuit breaker (issue #2100): read the start-beacon and trip BEFORE
    # the check()→_handle_missing_worker dispatch. Must precede the ladder because
    # L3 `_enable_worker()` calls `launchctl enable`, which would undo the breaker's
    # `launchctl disable` in the same tick (critique follow-up #2). On trip we
    # disable the worker + write the dedicated breaker key and return immediately.
    # Subsequent ticks short-circuit at the `_is_operator_disabled()` guard above,
    # since `launchctl disable` is persistent.
    if not args.check and _check_and_trip_respawn_breaker():
        return

    status = check()

    if args.check:
        print(f"Worker status: {status['status']} — {status['message']}")
        sys.exit(0 if status["status"] in ("ok", "starting") else 1)

    if status["status"] == "ok":
        logger.debug("Worker healthy (heartbeat %ss ago)", f"{status['heartbeat_age']:.0f}")
        # Reset down-tick counter on any healthy tick.
        _clear_down_ticks()
        return

    if status["status"] == "down":
        _handle_missing_worker()
        return

    if status["status"] == "starting":
        logger.info("Worker starting (no heartbeat yet) — skipping")
        return

    # status == "stale" — verified-kill escalation ladder (issue #1767).
    logger.warning(status["message"])
    recover(status)


if __name__ == "__main__":
    main()
