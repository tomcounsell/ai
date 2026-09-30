"""python -m reflections — long-lived launchd subprocess for the reflection scheduler.

This process is the sole owner of the reflection scheduler (issue #1828, "Fix #5" of
the #1816 Worker Fault Containment slug). It was moved out of the worker's event loop
so a reflection defect (memory leak, CPU spin, synchronous freeze) can no longer degrade
the worker that runs customer-facing sessions. launchd (``com.valor.reflection-worker``,
``KeepAlive=true`` + ``ThrottleInterval``) is the supervisor; this module is a thin
entry that reuses ``ReflectionScheduler`` verbatim.

Runtime contract:
  - ``VALOR_LAUNCHD=1`` MUST be set (plist sets it) so the scheduler skips the iCloud/TCC
    vault path (``agent/reflection_scheduler.py``) and reads the local
    ``config/reflections.yaml`` the installer copies. Reading a ~/Desktop iCloud file from
    a launchd agent hangs on TCC.

Two operator-visibility heartbeat files under ``data/`` (mtime/content, never a DB row):
  - ``data/last_reflection_tick`` — written at the top of every tick (``time.time()``).
    Drives the dashboard ``status`` (is the scheduler ticking?). Mirrors the
    ``data/last_worker_connected`` freshness convention.
  - ``data/reflection_worker_starts`` — ``{count, last_start_ts}`` written ONCE per process
    boot. The crash-loop indicator is ``last_start_age_s`` (now - last_start_ts) staying
    near-zero: a scheduler that crashes right after each tick keeps the tick file fresh but
    keeps resetting ``last_start_ts`` to ~now as launchd respawns it. ``count`` is
    informational-only (every /update bootout->bootstrap inflates it), NOT an alarm source.
    The write is ATOMIC (temp-file + ``os.replace``) so a SIGKILL mid-write during a crash
    storm never truncates the file and destroys the signal it targets; a CORRUPT file is
    preserved best-effort (logged WARNING), never silently reset to 1.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path

from agent.reflection_scheduler import ReflectionScheduler

_DATA = Path(__file__).parent.parent / "data"
_HEARTBEAT = _DATA / "last_reflection_tick"
_STARTS = _DATA / "reflection_worker_starts"  # crash-loop signal (last_start_age_s)

_log = logging.getLogger("reflections")


def _configure_logging() -> None:
    """Configure logging for the standalone subprocess.

    Called only from ``main()`` — never at import time — so importing this
    module (e.g. from tests) never mutates the root logger / global logging
    config as a side effect.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _write_heartbeat() -> None:
    """Write the per-tick freshness heartbeat. Failure is logged, never fatal."""
    try:
        _HEARTBEAT.parent.mkdir(exist_ok=True)
        _HEARTBEAT.write_text(str(time.time()))
    except OSError as e:
        _log.warning("heartbeat write failed: %s", e)


def _record_boot() -> None:
    """Record boot timestamp + bump the boot counter once per process start, atomically.

    ABSENT file  -> first boot, count starts at 1.
    CORRUPT file -> preserve the best-effort prior count, log a WARNING; NEVER reset to 1
                    (that would zero the crash-loop signal during the storm it targets).
    ATOMIC write -> temp-file + os.replace(): a SIGKILL mid-write must not truncate the
                    file. The temp name is PID-suffixed so two writers never clobber one
                    another's partial temp file.
    """
    try:
        _STARTS.parent.mkdir(exist_ok=True)
        prior = 0
        if _STARTS.exists():
            # The file existing at all proves at least one prior boot already
            # happened, so an unparseable file must never collapse back to
            # the same prior=0 an ABSENT file would produce — that would
            # silently reset count to 1 and erase the crash-loop signal.
            # Default to 1 here; a successful parse below overrides it with
            # the real count.
            prior = 1
            try:
                prior = int(json.loads(_STARTS.read_text()).get("count", prior))
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError, OSError):
                _log.warning(
                    "reflection_worker_starts corrupt/unreadable; preserving best-effort "
                    "count (%d) instead of resetting to 1 (not resetting the crash-loop "
                    "signal)",
                    prior,
                )
        payload = json.dumps({"count": prior + 1, "last_start_ts": time.time()})
        tmp = _STARTS.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(payload)
        os.replace(tmp, _STARTS)  # atomic rename — never a truncated file
    except OSError as e:
        _log.warning("start-record write failed: %s", e)


def _wrap_tick_with_heartbeat(scheduler: ReflectionScheduler):
    """Return a ``tick()`` replacement that writes the heartbeat before ticking.

    Kept as a standalone helper (rather than inlined in ``_run``) so the wrap
    itself is independently testable without driving the full signal/loop
    machinery in ``_run``. This is the ONLY place process-specific file I/O
    touches the scheduler — ``ReflectionScheduler`` itself stays untouched
    (it is imported by tests and other callers).
    """
    orig_tick = scheduler.tick

    async def _tick_with_heartbeat() -> int:
        _write_heartbeat()
        return await orig_tick()

    return _tick_with_heartbeat


async def _run(dry_run: bool) -> None:
    scheduler = ReflectionScheduler()

    if dry_run:
        scheduler.load()
        print(scheduler.format_status())
        return

    _record_boot()  # once per boot, before the tick loop (atomic start-timestamp write)

    # Wrap tick to emit the per-tick heartbeat WITHOUT threading process-specific file I/O
    # into the shared ReflectionScheduler class (it is imported by tests and other callers).
    scheduler.tick = _wrap_tick_with_heartbeat(scheduler)  # type: ignore[method-assign]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    task = asyncio.create_task(scheduler.start())
    stop_task = asyncio.create_task(stop.wait())
    # Exit on either a clean shutdown signal OR the scheduler task ending on its own.
    await asyncio.wait({task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _log.info("reflection scheduler shut down cleanly")


def main() -> None:
    # #2098: tag this process as the out-of-process reflection worker so the
    # session-health actuation guard (agent/session_health.py) knows it is NOT
    # the owning worker. Its process-local `_active_workers`/`_active_sessions`
    # registries are empty relative to the real worker, so running the
    # actuation branches here false-recovers live sessions and spawns competing
    # workers (the confirmed #2091 double-owner race). Set before any reflection
    # callable runs.
    os.environ["VALOR_REFLECTION_WORKER"] = "1"
    _configure_logging()
    p = argparse.ArgumentParser(prog="reflections")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Load the registry, print status, and exit 0 (no scheduling loop).",
    )
    args = p.parse_args()
    asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    main()
