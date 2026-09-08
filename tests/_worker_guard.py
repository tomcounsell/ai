"""Guard: a test must never signal the launchd-managed live worker (#2147).

Worker-lifecycle integration tests (``test_watchdog_recovery``,
``test_crash_auto_resume``, ``test_remote_update``) exercise SIGTERM / kill
paths. On a machine with a live ``python -m worker`` under launchd, a real
kill whose target resolves to that pid would take down production
orchestration — the failure attributed to the unattended suite on 2026-07-17
(see #2146). ``assert_not_live_worker(pid)`` raises BEFORE any such signal, so
a fabricated-PID test can never target the live worker even if a future
refactor drops a probe mock.

The guard resolves "is this the live worker?" from two independent signals so
either alone is sufficient:

1. The target pid's own command line looks like ``python -m worker`` (any
   pid that IS a worker process, self-spawned or not).
2. The pid is registered as the launchd worker — via the
   ``worker:registered_pid:*`` heartbeat keys on production Redis (db=0,
   written by ``agent.session_health.register_worker_pid``) and/or a live
   worker found in the host process table via
   ``tools.process_lookup.find_python_service_pids`` (ancestor-safe; see
   #3164 for why ``pgrep`` cannot be used here).
"""

from __future__ import annotations

import re
import subprocess

from tools.process_lookup import find_python_service_pids

# A command line for the launchd worker joins to `<python> -m worker`. Require
# BOTH a python-ish executable AND the `-m worker` token so an unrelated
# process that merely mentions "worker" never trips the guard. `python -m
# pytest` (the test runner itself) has no `-m worker` token and passes.
_WORKER_MODULE_RE = re.compile(r"-m\s+worker\b")


class LiveWorkerSignalError(AssertionError):
    """Raised when a test would signal the launchd-managed live worker."""


def _looks_like_worker_cmdline(cmdline: str) -> bool:
    if not cmdline:
        return False
    return bool(_WORKER_MODULE_RE.search(cmdline)) and "python" in cmdline.lower()


def _pid_cmdline(pid: int) -> str:
    """Return the full command line of ``pid`` (best-effort, empty on failure)."""
    try:
        out = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _host_worker_pids() -> set[int]:
    """PIDs of live worker processes, read from the host process table.

    Ancestor-safe (#3164). This used to run ``pgrep -f "python -m worker"``,
    which on macOS excludes the caller's own ancestors — and a pytest process
    started inside a worker-hosted agent session IS a descendant of the
    launchd worker. That made this signal silently empty in exactly the
    situation the guard exists for (#2146 / #2147).
    """
    try:
        return set(find_python_service_pids(module="worker", script_suffix="worker/__main__.py"))
    except Exception:
        return set()


def _registered_worker_pids() -> set[int]:
    """PIDs registered as live workers via the production (db=0) heartbeat keys.

    Registrations are written by ``register_worker_pid`` against the worker's
    own ``POPOTO_REDIS_DB`` — which is db=0 for the launchd worker. Under a
    test db the fixture repoints ``POPOTO_REDIS_DB``, so we read db=0
    explicitly (reusing host/port/auth) to see the live worker's registration.
    ``worker:registered_pid:*`` are plain string keys (not Popoto models), so a
    scan/get read here is not touching Popoto-managed state.
    """
    pids: set[int] = set()
    try:
        import redis as _redis
        from popoto.redis_db import POPOTO_REDIS_DB

        from agent.session_health import WORKER_REGISTERED_PID_KEY_PREFIX

        kw = POPOTO_REDIS_DB.connection_pool.connection_kwargs
        conn = _redis.Redis(
            host=kw.get("host", "localhost"),
            port=kw.get("port", 6379),
            db=0,  # registrations always live on production db=0
            username=kw.get("username"),
            password=kw.get("password"),
            decode_responses=True,
        )
        try:
            for key in conn.scan_iter(match=f"{WORKER_REGISTERED_PID_KEY_PREFIX}*"):
                val = conn.get(key)
                if val and str(val).isdigit():
                    pids.add(int(val))
        finally:
            conn.close()
    except Exception:
        pass
    return pids


def live_worker_pids() -> set[int]:
    """Union of host-level (process table) and registered (Redis db=0) worker PIDs."""
    return _host_worker_pids() | _registered_worker_pids()


def assert_not_live_worker(pid) -> None:
    """Raise ``LiveWorkerSignalError`` if ``pid`` is (or looks like) the live worker.

    Call this immediately before any real ``os.kill`` / ``proc.terminate()``
    whose target is derived at runtime, so a fabricated-PID test can never
    signal the launchd-managed production worker.
    """
    pid = int(pid)

    cmdline = _pid_cmdline(pid)
    if _looks_like_worker_cmdline(cmdline):
        raise LiveWorkerSignalError(
            f"Refusing to signal pid {pid}: its command line looks like the live "
            f"worker ({cmdline!r}). Tests must only signal processes they spawned."
        )

    if pid in live_worker_pids():
        raise LiveWorkerSignalError(
            f"Refusing to signal pid {pid}: it is registered as / matches the "
            f"launchd-managed live worker. Tests must only signal self-spawned procs."
        )
