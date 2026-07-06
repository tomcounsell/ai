"""
Standalone worker entry point for processing AgentSession records.

Processes sessions from Redis without requiring a Telegram connection.
Developer workstations run just the worker. Bridge machines run
bridge + worker as separate processes (bridge handles I/O only).

Usage:
    python -m worker                    # Process all projects
    python -m worker --project valor    # Process one project only
    python -m worker --dry-run          # Validate config and exit
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# Load .env for direct invocations (python -m worker from terminal).
# When running via launchd, env vars are injected into the plist by
# install_worker.sh — VALOR_LAUNCHD=1 is set to signal this. We skip
# dotenv entirely in that case: macOS TCC blocks open() on iCloud-synced
# ~/Desktop/Valor/.env (which .env symlinks to), causing the process to
# hang indefinitely.
if not os.environ.get("VALOR_LAUNCHD"):
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass

logger = logging.getLogger("worker")

# Shared mutable state (granite_available, shutdown_requested, etc.)

# Set to True when SIGTERM is received; causes main() to exit with code 1
# so launchd applies ThrottleInterval (10s) instead of the default ~10-minute throttle.
_shutdown_via_signal = False

# Heartbeat thread interval: how often the dedicated daemon thread writes
# data/last_worker_connected. Env-tunable for conservative rollout.
WORKER_HEARTBEAT_INTERVAL = int(os.environ.get("WORKER_HEARTBEAT_INTERVAL", "30"))

# Dead-man's-switch constants (all env-overridable for conservative rollout).
# Grain of salt: every default below is PROVISIONAL — tune after observing real
# freeze / false-positive rates in logs/worker.log (per the plan's pre-merge spike).
# On-loop bump cadence — how often the beacon task refreshes last_loop_tick.
WORKER_DEADMAN_TICK_INTERVAL: float = float(os.environ.get("WORKER_DEADMAN_TICK_INTERVAL", "5"))
# Abort once armed if the beacon is older than this. Generous (several multiples
# of the tick + 30s watchdog cycle) so legitimate on-loop sync blocks don't trip it.
# Provisional — tune after observing real freeze/false-positive rates.
WORKER_DEADMAN_STALENESS_THRESHOLD: float = float(
    os.environ.get("WORKER_DEADMAN_STALENESS_THRESHOLD", "90")
)
# Unarmed-grace ceiling: if the beacon is still None this long after the watchdog
# thread starts, the loop wedged before the first tick (startup freeze) — abort
# anyway. Provisional — tune after observing real freeze/false-positive rates.
WORKER_DEADMAN_STARTUP_GRACE_MAX: float = float(
    os.environ.get("WORKER_DEADMAN_STARTUP_GRACE_MAX", "300")
)
# Conservative rollback kill switch: false restores #1767's unconditional green
# write (the switch only logs, never aborts). Provisional default — tune after
# observing real freeze/false-positive rates.
WORKER_DEADMAN_ENABLED: bool = os.environ.get(
    "WORKER_DEADMAN_ENABLED", "true"
).strip().lower() not in ("", "0", "false")

# Stop event for the heartbeat daemon thread — set on worker shutdown.
_heartbeat_stop_event = threading.Event()

# Stop event for the session-archive periodic export daemon thread (issue #1825)
# — set on worker shutdown.
_session_archive_stop_event = threading.Event()

# Granite re-probe / circuit breaker — provisional, tunable via env.
# Grain of salt: defaults below are PROVISIONAL — tune after observing real
# ollama outage / false-positive rates in logs/worker.log.
# How often to probe granite when the circuit is closed.
GRANITE_REPROBE_INTERVAL_S: float = float(os.environ.get("GRANITE_REPROBE_INTERVAL_S", "30"))
# Consecutive probe timeouts/failures before the circuit trips to OPEN.
GRANITE_BREAKER_OPEN_THRESHOLD: int = int(os.environ.get("GRANITE_BREAKER_OPEN_THRESHOLD", "3"))
# How long the circuit stays OPEN before allowing a half-open re-probe.
GRANITE_BREAKER_COOLDOWN_S: float = float(os.environ.get("GRANITE_BREAKER_COOLDOWN_S", "120"))

# Module-level alias — the authoritative flag lives in agent.session_state
# (so agent_session_queue._worker_loop can read it without a circular import).
# This alias is provided for readability in worker.__main__ code that writes the flag.
# GRANITE_AVAILABLE is not a constant; it is mutated by _granite_reprobe_loop.

# Background-task supervisor constants (Fix #4, #1816) — all env-overridable.
# Grain of salt: defaults below are PROVISIONAL / conservative — tune after
# observing real respawn cadence in logs/worker.log.
# Max number of restarts within WORKER_SUPERVISOR_WINDOW_S before the storm
# cap fires and recycles the process via SIGKILL. Erring toward NOT killing
# legitimate work — a task that crashes once every few minutes is fine.
WORKER_SUPERVISOR_MAX_RESTARTS: int = int(os.environ.get("WORKER_SUPERVISOR_MAX_RESTARTS", "5"))
# Rolling window (seconds) for the restart-count denominator.
# Provisional — tune based on longest legitimate startup transient.
WORKER_SUPERVISOR_WINDOW_S: float = float(os.environ.get("WORKER_SUPERVISOR_WINDOW_S", "300"))
# Base backoff (seconds) before the first respawn; doubles each restart.
# Provisional — minimum viable grace window before hammering a failing factory.
WORKER_SUPERVISOR_BASE_BACKOFF_S: float = float(
    os.environ.get("WORKER_SUPERVISOR_BASE_BACKOFF_S", "1.0")
)


def _self_kill() -> None:
    """Hard-kill this process (uncatchable, signal-based) so launchd respawns it.

    Dumps all thread stacks to stderr first — a real production wedge then leaves
    forensic evidence in logs/worker_error.log (better than the macOS .ips C-frame
    report for a Python-level wedge; see #1808). Then delivers SIGKILL: equally
    unswallowable as the former abort-based kill, but produces NO macOS crash-report
    dialog and NO Python-*.ips file. Extracted as a seam so unit tests can assert the
    call without killing the test process.

    The dump is best-effort; the SIGKILL is in a `finally` so it fires even if the
    dump raises (e.g. stderr closed/monkeypatched) — otherwise, inside the storm-cap
    asyncio done-callback the exception would be swallowed and the guard would
    silently fail to recycle, the exact trap the subprocess test exists to catch.
    """
    try:
        faulthandler.dump_traceback(all_threads=True)
        sys.stderr.flush()
    finally:
        os.kill(os.getpid(), signal.SIGKILL)


def supervise(
    name: str,
    factory,
    *,
    max_restarts: int = WORKER_SUPERVISOR_MAX_RESTARTS,
    window_s: float = WORKER_SUPERVISOR_WINDOW_S,
    base_backoff_s: float = WORKER_SUPERVISOR_BASE_BACKOFF_S,
) -> asyncio.Task:
    """Create and supervise a background asyncio Task with exponential-backoff respawn.

    Wraps asyncio.create_task(factory()) and installs a done-callback that
    respawns the task on unexpected death (any exit that is not a cancellation).
    Restart timestamps are tracked in a rolling window; if the task crashes more
    than ``max_restarts`` times within ``window_s`` seconds, the process is
    recycled unconditionally via ``_self_kill()`` (SIGKILL) so launchd can
    respawn it clean.

    Backoff: the first respawn waits ``base_backoff_s``, the second waits
    ``base_backoff_s * 2``, and so on (capped at ``window_s / 2``).

    Storm-cap recycle is an UNCONDITIONAL ``_self_kill()`` (a faulthandler thread
    dump then SIGKILL) — the same seam used by the dead-man's-switch (#1815).
    Never a bare ``sys.exit(1)``: a ``SystemExit`` raised inside an asyncio done-callback is
    swallowed by the event loop's callback-exception handler, so the process
    would keep running and the cap would silently fail to recycle.

    Shutdown guard: cancelled tasks are never respawned (cancellation is the
    normal shutdown signal). The ``agent.session_state._shutdown_requested``
    flag is also checked so in-flight tasks during a graceful SIGTERM shutdown
    are not respawned.
    """
    restart_times: list[float] = []
    restart_count = [0]

    def _done_callback(t: asyncio.Task) -> None:
        import agent.session_state as _ss_state  # noqa: PLC0415

        if t.cancelled():
            return  # Normal shutdown — never respawn a cancelled task.
        if _ss_state._shutdown_requested:
            return  # Graceful SIGTERM shutdown — suppress respawn.

        exc = t.exception()
        if exc is not None:
            logger.warning(
                "[supervisor] Task %r exited unexpectedly: %s(%s)",
                name,
                type(exc).__name__,
                exc,
            )

        # Prune restart times outside the rolling window.
        now = time.monotonic()
        cutoff = now - window_s
        while restart_times and restart_times[0] < cutoff:
            restart_times.pop(0)

        # Storm-cap check — UNCONDITIONAL _self_kill() if exceeded.
        if len(restart_times) >= max_restarts:
            logger.critical(
                "[supervisor] Task %r hit storm cap (%d restarts in %.0fs) — "
                "recycling process via SIGKILL so launchd can respawn clean.",
                name,
                max_restarts,
                window_s,
            )
            # _self_kill() dumps threads then SIGKILLs — every branch reaches this, no exceptions.
            # Never sys.exit(1): a SystemExit in a done-callback is swallowed by the
            # event loop so the process would keep running with the cap silently failed.
            _self_kill()
            return  # Unreachable — _self_kill() (SIGKILL) terminates the process.

        # Exponential backoff: 1s, 2s, 4s … capped at window_s/2.
        backoff = min(base_backoff_s * (2 ** len(restart_times)), window_s / 2)
        restart_count[0] += 1
        restart_times.append(now)
        logger.warning(
            "[supervisor] Respawning task %r (restart #%d, backoff=%.1fs)",
            name,
            restart_count[0],
            backoff,
        )

        async def _delayed_respawn() -> None:
            await asyncio.sleep(backoff)
            import agent.session_state as _ss_state  # noqa: PLC0415

            if _ss_state._shutdown_requested:
                logger.debug(
                    "[supervisor] Shutdown requested; cancelling delayed respawn of %r", name
                )
                return
            new_task = asyncio.create_task(factory(), name=name)
            new_task.add_done_callback(_done_callback)

        asyncio.create_task(_delayed_respawn())

    task = asyncio.create_task(factory(), name=name)
    task.add_done_callback(_done_callback)
    return task


def _asyncio_debug_enabled(env_value: str | None) -> bool:
    """Return True if asyncio debug mode should be enabled.

    Parses the ``WORKER_ASYNCIO_DEBUG`` environment variable value.
    Only ``"1"`` and other non-empty, non-``"0"`` / non-``"false"`` strings
    enable debug; ``None``, ``""``, ``"0"``, and ``"false"`` are all off.

    This helper is a pure, always-shipping module-level function so test
    assertions have a stable import target on **both** investigation outcome
    branches (root-cause-found or not-reproducible) — resolving the B2
    orphan-helper concern from revision 4 of issue #1808.

    Ref: #1808 (wedged-but-alive worker investigation).
    """
    if env_value is None:
        return False
    stripped = env_value.strip().lower()
    return stripped not in ("", "0", "false")


def _green_heartbeat_write() -> None:
    """Write data/last_worker_connected, swallowing FS errors.

    The dead-man's switch NEVER aborts on a write failure — a transient
    filesystem error must not be confused with a frozen event loop. Refreshes
    the Redis worker PID as a side effect (issue #1271).
    """
    from agent.agent_session_queue import _write_worker_heartbeat  # noqa: PLC0415

    try:
        _write_worker_heartbeat()
    except Exception as exc:
        logger.warning("Heartbeat thread: write failed: %s", exc)


def _heartbeat_cycle(
    armed: bool, thread_start: float, beacon_log_next: float
) -> tuple[bool, float]:
    """Run ONE dead-man's-switch cycle and return ``(new_armed, new_beacon_log_next)``.

    Pure per-cycle body extracted from :func:`_heartbeat_thread_main` so unit
    tests can drive a single cycle deterministically (no threads/sleeps). The
    only side effects are the green heartbeat write, logging, and — on a wedge —
    the :func:`_self_kill` seam (gated by ``WORKER_DEADMAN_ENABLED``).

    Semantics (issue #1815 fix #1):

    - **Unarmed:** the switch stays unarmed until the first beacon tick newer
      than ``thread_start`` is observed (writing green on process liveness, the
      #1767 behaviour). ``None`` is unarmed, NEVER stale.
    - **Startup-freeze guard:** while still unarmed, if the beacon is ``None``
      past ``WORKER_DEADMAN_STARTUP_GRACE_MAX`` the event loop wedged before the
      tick task could initialise — abort (the wedge would otherwise stay silent
      forever, since ``None`` is never stale).
    - **Armed:** if ``now - tick <= WORKER_DEADMAN_STALENESS_THRESHOLD`` the loop
      is ticking → write green + low-cadence beacon-age audit. Otherwise the loop
      is synchronously frozen → CRITICAL + ``_self_kill`` (SIGKILL) so launchd
      respawns a healthy worker.

    ``WORKER_DEADMAN_ENABLED=false`` is the rollback kill switch: stale/None-past-
    ceiling beacons only log, then fall through to an unconditional green write
    (restoring #1767).
    """
    from agent.session_state import get_loop_tick  # noqa: PLC0415

    tick = get_loop_tick()
    now = time.monotonic()

    if not armed:
        if tick is not None and tick > thread_start:
            # First tick after thread start — arm the switch and fall through to
            # the armed staleness check this same cycle.
            armed = True
            logger.info(
                "[deadman] armed: first beacon tick observed %.1fs after thread start",
                tick - thread_start,
            )
        elif tick is None and (now - thread_start) > WORKER_DEADMAN_STARTUP_GRACE_MAX:
            # Beacon never ticked past the grace ceiling — startup-window freeze.
            logger.critical(
                "[deadman] beacon never ticked %.1fs after start; startup-window freeze — aborting",
                now - thread_start,
            )
            if WORKER_DEADMAN_ENABLED:
                _self_kill()
            else:
                logger.warning("[deadman] WORKER_DEADMAN_ENABLED=false — logging only, not killing")
            # Rollback path (or _self_kill stubbed in tests): write green so the
            # dashboard still reflects process liveness.
            _green_heartbeat_write()
            return armed, beacon_log_next
        else:
            # Still within startup grace, beacon not yet ticked — green write on
            # process liveness (the #1767 behaviour).
            _green_heartbeat_write()
            return armed, beacon_log_next

    # Armed (possibly just-armed this cycle): tick is non-None here.
    beacon_age = now - tick  # type: ignore[operator]
    if beacon_age <= WORKER_DEADMAN_STALENESS_THRESHOLD:
        _green_heartbeat_write()
        # Beacon-age audit: low cadence (~once/min) so operators can watch the
        # live margin without spamming the log every cycle.
        if now >= beacon_log_next:
            logger.info("[deadman] beacon age=%.1fs", beacon_age)
            beacon_log_next = now + 60
        return armed, beacon_log_next

    # Beacon stale — the event loop is synchronously frozen.
    logger.critical(
        "[deadman] loop beacon stale: age=%.1fs > %.1fs threshold — aborting for launchd respawn",
        beacon_age,
        WORKER_DEADMAN_STALENESS_THRESHOLD,
    )
    if WORKER_DEADMAN_ENABLED:
        _self_kill()
    else:
        logger.warning("[deadman] WORKER_DEADMAN_ENABLED=false — logging only, not killing")
    return armed, beacon_log_next


def _heartbeat_thread_main() -> None:
    """Dedicated daemon thread for worker heartbeat writes, inverted into a
    dead-man's switch (issue #1815 fix #1).

    Runs independently of the asyncio event loop so PTY/thread-pool
    saturation (incident 2026-06-23, issue #1767) cannot prevent heartbeat
    writes. The loop wakes every WORKER_HEARTBEAT_INTERVAL seconds and delegates
    each cycle to :func:`_heartbeat_cycle`.

    When the on-loop beacon (last_loop_tick) is fresh, writes the green
    heartbeat as before. When the beacon goes stale beyond
    WORKER_DEADMAN_STALENESS_THRESHOLD (or never ticks past the startup grace
    ceiling), logs a CRITICAL and self-kills via SIGKILL so launchd can respawn
    a healthy worker.

    WORKER_DEADMAN_ENABLED=false restores the unconditional green-write
    behaviour of issue #1767 (rollback kill switch).

    Ref: #1055 for the executor-isolation pattern.
    Ref: #1767 for the original off-loop thread design.
    Ref: #1815 for the dead-man's-switch inversion.
    """
    thread_start = time.monotonic()
    armed = False
    beacon_log_next = 0.0

    logger.info(
        "Heartbeat thread started (interval=%ds, deadman=%s, threshold=%ds, grace=%ds)",
        WORKER_HEARTBEAT_INTERVAL,
        WORKER_DEADMAN_ENABLED,
        WORKER_DEADMAN_STALENESS_THRESHOLD,
        WORKER_DEADMAN_STARTUP_GRACE_MAX,
    )

    # wait() returns True only when the stop event is set, so the loop exits on
    # graceful shutdown and never aborts during teardown (Race 3).
    while not _heartbeat_stop_event.wait(timeout=WORKER_HEARTBEAT_INTERVAL):
        armed, beacon_log_next = _heartbeat_cycle(armed, thread_start, beacon_log_next)

    logger.info("Heartbeat thread stopped")


def _session_archive_thread_main() -> None:
    """Dedicated daemon thread for the periodic session-archive export (issue #1825).

    Mirrors :func:`_heartbeat_thread_main`'s pattern exactly: runs off the
    asyncio event loop (both the Redis `query.all()` scan and the SQLite write
    are blocking), wakes every SESSION_ARCHIVE_INTERVAL seconds, and wraps each
    cycle's call to :func:`agent.session_archive.export_all` in its own
    try/except so one failed export can never kill the thread or block the
    next cycle.

    See `docs/plans/session-archive-sqlite.md` Data Flow point 2.
    """
    from agent.constants import SESSION_ARCHIVE_INTERVAL
    from agent.session_archive import export_all

    logger.info("Session-archive export thread started (interval=%ds)", SESSION_ARCHIVE_INTERVAL)

    # wait() returns True only when the stop event is set, so the loop exits on
    # graceful shutdown and never aborts mid-export during teardown.
    while not _session_archive_stop_event.wait(timeout=SESSION_ARCHIVE_INTERVAL):
        try:
            export_all()
        except Exception:
            logger.warning("session_archive export_all failed (non-fatal)", exc_info=True)

    logger.info("Session-archive export thread stopped")


class _UTCFormatter(logging.Formatter):
    """Log formatter that always uses UTC for timestamps."""

    converter = staticmethod(time.gmtime)


def _configure_logging() -> None:
    """Set up logging for the standalone worker."""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "worker.log"

    formatter = _UTCFormatter(
        fmt="%(asctime)s UTC %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(str(log_file)),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in handlers:
        root_logger.addHandler(handler)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="worker",
        description="Standalone worker for processing AgentSession records from Redis.",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Process only this project key (default: all projects from config).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load config, verify Redis connection, then exit without processing.",
    )
    return parser.parse_args()


def _should_register_email_handler(project_cfg: dict) -> bool:
    """Return True if the project has any email routing configured.

    Registers EmailOutputHandler when either email.contacts OR email.domains
    is non-empty, matching the inbound routing logic in bridge.routing which
    builds both an address map and a domain map.

    Args:
        project_cfg: Project configuration dict from projects.json.

    Returns:
        True if the project should have an EmailOutputHandler registered.
    """
    email_cfg = project_cfg.get("email", {}) or {}
    return bool(email_cfg.get("contacts") or email_cfg.get("domains"))


def _load_projects(project_filter: str | None = None) -> dict:
    """Load project configurations from projects.json.

    Uses bridge.routing.load_config() which handles path resolution
    (iCloud, env var override, fallback to config/projects.json).

    Args:
        project_filter: If set, return only this project. None returns all.

    Returns:
        Dict of project_key -> project_config.
    """
    try:
        from bridge.routing import load_config

        config = load_config()
    except ImportError:
        logger.error(
            "Could not import bridge.routing.load_config. Ensure the project root is on PYTHONPATH."
        )
        sys.exit(1)

    all_projects = config.get("projects", {})

    if project_filter:
        if project_filter not in all_projects:
            logger.error(
                f"Project '{project_filter}' not found in config. "
                f"Available: {', '.join(all_projects.keys())}"
            )
            sys.exit(1)
        return {project_filter: all_projects[project_filter]}

    return all_projects


async def _granite_reprobe_loop() -> None:
    """Background circuit breaker that re-probes granite and updates granite_available.

    States:
      CLOSED  — probe every GRANITE_REPROBE_INTERVAL_S; success keeps it closed;
                failure increments the consecutive-failure counter.
      OPEN    — entered when consecutive failures reach GRANITE_BREAKER_OPEN_THRESHOLD;
                waits GRANITE_BREAKER_COOLDOWN_S then moves to HALF_OPEN.
      HALF_OPEN — tries a single probe; success → CLOSED + granite_available=True;
                  failure → back to OPEN (reset cooldown).

    Logs every state transition. CancelledError exits cleanly (normal shutdown path).
    The probe call uses asyncio.to_thread so the synchronous ensure_granite_model
    does not block the event loop.
    """
    import agent.session_state as _ss  # noqa: PLC0415
    from agent.granite_container.granite_classifier import ensure_granite_model  # noqa: PLC0415

    # Circuit state strings (lowercase as required by ruff N806)
    state_closed = "closed"
    state_open = "open"
    state_half_open = "half_open"

    # Start closed; initial availability already set by the startup probe.
    state = state_closed
    consecutive_failures = 0 if _ss.granite_available else 1  # count startup failure

    logger.debug(
        "Granite reprobe loop started (state=%s, granite_available=%s)",
        state,
        _ss.granite_available,
    )

    try:
        while True:
            if state == state_closed:
                await asyncio.sleep(GRANITE_REPROBE_INTERVAL_S)
                ok, detail = await asyncio.to_thread(ensure_granite_model)
                if ok:
                    if not _ss.granite_available:
                        logger.info(
                            "Granite classifier recovered: %s. Resuming deferred sessions.",
                            detail,
                        )
                        _ss.granite_available = True
                        _resume_deferred_granite_sessions()
                    consecutive_failures = 0
                    # Stay closed
                else:
                    consecutive_failures += 1
                    logger.warning(
                        "Granite probe failed (%d/%d): %s",
                        consecutive_failures,
                        GRANITE_BREAKER_OPEN_THRESHOLD,
                        detail,
                    )
                    if _ss.granite_available:
                        _ss.granite_available = False
                        logger.warning(
                            "Granite unavailable — ENG sessions will be deferred to paused_circuit."
                        )
                    if consecutive_failures >= GRANITE_BREAKER_OPEN_THRESHOLD:
                        state = state_open
                        logger.warning(
                            "Granite circuit breaker OPEN after %d consecutive failures. "
                            "Cooling down for %.0fs before next probe.",
                            consecutive_failures,
                            GRANITE_BREAKER_COOLDOWN_S,
                        )

            elif state == state_open:
                await asyncio.sleep(GRANITE_BREAKER_COOLDOWN_S)
                state = state_half_open
                logger.info("Granite circuit breaker HALF_OPEN — attempting re-probe.")

            elif state == state_half_open:
                ok, detail = await asyncio.to_thread(ensure_granite_model)
                if ok:
                    state = state_closed
                    consecutive_failures = 0
                    _ss.granite_available = True
                    logger.info(
                        "Granite circuit breaker CLOSED after half-open success: %s. "
                        "Resuming deferred sessions.",
                        detail,
                    )
                    _resume_deferred_granite_sessions()
                else:
                    state = state_open
                    logger.warning(
                        "Granite half-open probe failed: %s. Circuit back to OPEN, "
                        "cooling down for %.0fs.",
                        detail,
                        GRANITE_BREAKER_COOLDOWN_S,
                    )

    except asyncio.CancelledError:
        logger.debug("Granite reprobe loop cancelled (shutdown).")


def _resume_deferred_granite_sessions() -> None:
    """Set the worker:recovering Redis flag so session_recovery_drip re-queues
    paused_circuit sessions that were deferred due to granite unavailability.

    Uses a best-effort approach: if Redis is unreachable the drip will simply
    not fire on the next tick, but granite will be available again at that point
    so the next enqueue will proceed normally anyway.
    """
    try:
        import os  # noqa: PLC0415

        from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

        project_key = os.environ.get("VALOR_PROJECT_KEY", "valor").strip() or "valor"
        r = POPOTO_REDIS_DB
        rec_key = f"{project_key}:worker:recovering"
        r.set(rec_key, "1", ex=3600)  # TTL: 1 hour, drip clears it when queues drain
        logger.info(
            "Set %s flag — session_recovery_drip will re-queue paused_circuit sessions.",
            rec_key,
        )
    except Exception as e:
        logger.warning("Could not set worker:recovering flag for granite recovery: %s", e)


def _any_pty_role_configured() -> bool:
    """D1b (issue #1817): True if at least one role (globally or per-project)
    resolves to PTY transport, anywhere across the fleet.

    Runs at worker startup, before any specific session/project is known, so
    this scans EVERY project's ``transport.pm``/``transport.dev`` override in
    projects.json in addition to the global default
    (``settings.granite.pm_transport`` / ``dev_transport``). A single
    project overriding to "pty" while the global default is "headless" (or
    vice versa) still counts — the D1b contract-check only needs ONE
    PTY-transport role anywhere in the fleet to become load-bearing; a fully
    headless fleet must not be hard-failed by a check that only matters for
    the interactive TUI (#1842 — a headless role's ``claude -p`` one-shot
    carries no PTY bytes and is immune to TUI-marker drift by construction).

    Fails OPEN (returns True) on any config-read error: a false "no PTY role
    configured" would silently disarm a load-bearing contract-check, which
    is worse than running one extra fingerprint check unnecessarily.
    """
    from config.settings import settings

    if settings.granite.pm_transport == "pty" or settings.granite.dev_transport == "pty":
        return True

    try:
        from bridge.routing import load_config  # noqa: PLC0415

        cfg = load_config()
        projects_cfg = cfg.get("projects", {}) if isinstance(cfg, dict) else {}
        for project in projects_cfg.values():
            if not isinstance(project, dict):
                continue
            transport = project.get("transport")
            if not isinstance(transport, dict):
                continue
            if transport.get("pm") == "pty" or transport.get("dev") == "pty":
                return True
    except Exception as e:
        logger.debug(
            "[worker-startup] projects.json transport scan failed (fail-open, "
            "assuming a PTY role IS configured): %s",
            e,
        )
        return True

    return False


def _evaluate_contract_check_gate(contract_ok: bool, pty_configured: bool, enforce: bool) -> str:
    """Pure decision function for the D1b startup contract-check gate (issue #1817).

    Separated from `_run_worker`'s I/O (logging, sys.exit) so the branching
    logic is directly unit-testable without mocking worker startup.

    Returns one of:
      - "pass": markers matched their golden sample — nothing to report.
      - "skip_headless": markers mismatched, but no PTY-transport role is
        configured (fully headless fleet) — a headless `claude -p` one-shot
        carries no PTY bytes and is immune to TUI-marker drift by
        construction (#1842), so this is NOT an operator-actionable failure.
      - "warn": markers mismatched, a PTY-transport role IS configured, but
        CLAUDE_CONTRACT_CHECK_ENFORCE is not set — log CRITICAL and continue.
      - "hard_fail": markers mismatched, a PTY-transport role IS configured,
        AND CLAUDE_CONTRACT_CHECK_ENFORCE=1 — the caller must refuse to
        start (sys.exit) rather than let sessions silently hang to timeout.
    """
    if contract_ok:
        return "pass"
    if not pty_configured:
        return "skip_headless"
    return "hard_fail" if enforce else "warn"


async def _run_worker(projects: dict, dry_run: bool = False) -> None:
    """Main worker coroutine.

    Startup sequence (deterministic):
    1. Registers TelegramRelayOutputHandler for each project
    2. Rebuilds AgentSession indexes (idempotent SCAN-based, production-safe)
    3. Cleans up corrupted sessions
    4. Recovers interrupted sessions
    5. Kills orphaned Claude subprocesses from prior runs
    6. Starts worker loops for pending sessions
    7. Starts health monitor
    8. Waits for shutdown signal
    """
    from agent.agent_session_queue import (
        _active_workers,
        _agent_session_health_loop,
        _cleanup_orphaned_claude_processes,
        _ensure_worker,
        _recover_interrupted_agent_sessions_startup,
        _session_notify_listener,
        _sweep_dead_worker_sessions,
        _sweep_stranded_waiting_for_children_parents,
        _write_worker_heartbeat,
        cleanup_corrupted_agent_sessions,
        register_callbacks,
        register_worker_pid,
        request_shutdown,
    )
    from agent.output_handler import FileOutputHandler, TelegramRelayOutputHandler
    from agent.session_health import _agent_session_tool_timeout_loop

    # Initialize the global concurrency slot registry BEFORE any worker loops
    # are created. Clamp to minimum 1 to prevent deadlock if
    # MAX_CONCURRENT_SESSIONS=0.
    _max_sessions = max(1, int(os.environ.get("MAX_CONCURRENT_SESSIONS", "8")))
    import agent.session_state as _ss  # noqa: PLC0415
    from agent.slot_lease import SlotLeaseRegistry  # noqa: PLC0415

    _ss._slot_registry = SlotLeaseRegistry(_max_sessions)
    logger.info(f"Slot lease registry initialized: MAX_CONCURRENT_SESSIONS={_max_sessions}")

    # Opt-in asyncio debug mode (issue #1808 investigation — Deliverable B / C-rev4).
    # Catches *synchronous* loop blocks (hypotheses 2/3 of the wedge investigation)
    # by logging any callback that exceeds slow_callback_duration.
    # Default-off (WORKER_ASYNCIO_DEBUG unset / "0" / "" → no-op); fail-open.
    # Ships on BOTH investigation outcome branches because the env flag makes it
    # zero steady-state cost (C-rev4: the helper always has a production caller).
    # IMPORTANT — C1 LIMITATION: set_debug only detects *synchronous* blocking
    # callbacks (CPU-bound work, blocking syscall). It is structurally blind to
    # coroutines cleanly parked at ``await semaphore.acquire()`` (hypothesis 1).
    # For the suspension-wedge detection surface see the always-on slot-exhaustion
    # forensic line in agent/session_health.py (Deliverable D, issue #1808).
    try:
        if _asyncio_debug_enabled(os.environ.get("WORKER_ASYNCIO_DEBUG")):
            _loop = asyncio.get_event_loop()
            _loop.set_debug(True)
            _loop.slow_callback_duration = 0.1  # 100 ms; tune lower for finer resolution
            logger.info(
                "[worker-startup] asyncio debug mode enabled (WORKER_ASYNCIO_DEBUG): "
                "slow callbacks logged above %.0f ms. "
                "Note C1 limitation — does NOT detect await-suspension wedge; "
                "use the slot-exhaustion forensic log in session_health.py for that.",
                _loop.slow_callback_duration * 1000,
            )
    except Exception as _dbg_exc:
        logger.warning(
            "[worker-startup] WORKER_ASYNCIO_DEBUG set_debug failed (fail-open): %s", _dbg_exc
        )

    handler = TelegramRelayOutputHandler(file_handler=FileOutputHandler())
    from bridge.email_bridge import EmailOutputHandler as _EmailOutputHandler

    email_handler = _EmailOutputHandler()

    # Register the worker's PID in Redis (issue #1271). The cross-process
    # orphan reaper reads `worker:registered_pid:*` keys to build a positive-ID
    # skip-set so a live worker is never reaped even if a future code change
    # re-adds the worker pattern to the cmdline regex set. The same key is
    # refreshed on every heartbeat tick by `_write_worker_heartbeat`.
    try:
        register_worker_pid()
    except Exception as e:
        logger.warning(f"register_worker_pid (startup) failed: {e}")

    # Verify Redis is reachable by attempting to list sessions
    try:
        from models.agent_session import AgentSession

        _ = list(AgentSession.query.filter(status="pending"))
        logger.info("Redis connection verified")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        sys.exit(1)

    # CLI harness health check: verify claude binary is available at startup.
    # All session types execute via claude -p — a missing binary is fatal.
    # Binary existence is the hard gate; the subprocess smoke test is advisory
    # because launchd can hang on asyncio.create_subprocess_exec for node binaries
    # (macOS TCC / TTY restrictions). A 30s timeout prevents an infinite hang.
    import shutil as _shutil

    if not _shutil.which("claude"):
        logger.critical(
            "CLI harness 'claude' not found on PATH — "
            "install with: npm install -g @anthropic-ai/claude-code\n"
            "Worker cannot start without the harness binary."
        )
        sys.exit(1)

    logger.info("CLI harness 'claude' found on PATH")

    # D1b (issue #1817): startup contract-check on the scraped TUI markers
    # the PTY driver depends on for idle detection (IDLE_BAR, PROMPT_GLYPH,
    # SPINNER_EVIDENCE_RE, the trust-folder prompt pattern). A routine
    # `claude` auto-update that rewords one of these strings is otherwise a
    # SILENT fleet-wide outage — every PTY session hangs to timeout instead
    # of detecting idle, with no exception and no crash. This converts that
    # into a loud, operator-visible signal. See `_evaluate_contract_check_gate`
    # for the (independently unit-testable) decision logic.
    try:
        from agent.granite_container.pty_driver import verify_tui_marker_contract

        _contract_ok, _failed_markers = verify_tui_marker_contract()
        _pty_configured = True if _contract_ok else _any_pty_role_configured()
        _enforce = os.environ.get("CLAUDE_CONTRACT_CHECK_ENFORCE") == "1"
        _decision = _evaluate_contract_check_gate(_contract_ok, _pty_configured, _enforce)

        if _decision == "pass":
            logger.info("CLI harness contract-check passed (TUI markers OK)")
        elif _decision == "skip_headless":
            logger.info(
                "CLI harness contract-check: marker(s) %s mismatched, but no "
                "PTY-transport role is configured (fully headless fleet) — "
                "skipping. Headless turns carry no PTY bytes and are immune "
                "to TUI-marker drift by construction.",
                ", ".join(_failed_markers),
            )
        elif _decision == "warn":
            logger.critical(
                "CLI harness contract-check FAILED — scraped TUI marker(s) "
                "%s no longer match their golden sample and a PTY-transport "
                "role is configured. A stale marker means every interactive "
                "session silently hangs to timeout instead of detecting idle "
                "(issue #1817 D1b). Continuing anyway — set "
                "CLAUDE_CONTRACT_CHECK_ENFORCE=1 to refuse startup on this "
                "condition.",
                ", ".join(_failed_markers),
            )
        else:  # "hard_fail"
            logger.critical(
                "CLI harness contract-check FAILED — scraped TUI marker(s) "
                "%s no longer match their golden sample and a PTY-transport "
                "role is configured. Refusing to start "
                "(CLAUDE_CONTRACT_CHECK_ENFORCE=1).",
                ", ".join(_failed_markers),
            )
            sys.exit(1)
    except Exception as e:
        logger.warning(f"CLI harness contract-check error (non-fatal): {e}")

    # Under launchd (VALOR_LAUNCHD=1), skip the subprocess smoke test entirely.
    # asyncio.create_subprocess_exec hangs indefinitely under macOS TCC/TTY restrictions
    # before yielding to the event loop, so asyncio.wait_for cannot apply the timeout.
    # Binary existence (shutil.which) is the only reliable check in this environment.
    if os.environ.get("VALOR_LAUNCHD"):
        logger.info("CLI harness smoke test skipped (VALOR_LAUNCHD — TCC restriction)")
    else:
        try:
            from agent.sdk_client import verify_harness_health

            _healthy = await asyncio.wait_for(verify_harness_health("claude-cli"), timeout=30.0)
            if _healthy:
                logger.info("CLI harness 'claude-cli' health check passed")
            else:
                logger.warning(
                    "CLI harness subprocess test failed — binary found, continuing anyway"
                )
        except TimeoutError:
            logger.warning(
                "CLI harness subprocess test timed out (30s) — binary found, skipping smoke test"
            )
        except Exception as e:
            logger.warning(f"CLI harness subprocess test error: {e} — binary found, continuing")

    if dry_run:
        logger.info(
            f"Dry run: config loaded with {len(projects)} project(s): "
            f"{', '.join(projects.keys())}. Worker ready."
        )
        return

    # Guarded cold-start restore (issue #1825): rehydrate data/session_archive.db
    # back into Redis iff Redis is provably empty of AgentSession data. Must run
    # BELOW the dry-run guard above (a dry run must never mutate Redis) and BEFORE
    # handler/callback registration below (so no incoming message can create a new
    # AgentSession while restore is running -- see the plan's Race Condition #2) and
    # BEFORE the Step 1 index rebuild (so the rebuild reindexes any rehydrated rows).
    try:
        from agent import session_archive

        restore_result = session_archive.restore_if_empty()
        logger.info("session_archive restore_if_empty: %s", restore_result)
    except Exception:
        logger.warning("session_archive restore_if_empty failed at startup", exc_info=True)

    # Register TelegramRelayOutputHandler for each project
    for project_key in projects:
        register_callbacks(project_key, handler=handler)
        logger.info(f"[{project_key}] Registered TelegramRelayOutputHandler")

    # Register EmailOutputHandler for projects with any email routing config
    for project_key, project_cfg in projects.items():
        if _should_register_email_handler(project_cfg):
            register_callbacks(project_key, transport="email", handler=email_handler)
            logger.info(f"[{project_key}] Registered EmailOutputHandler (transport=email)")

    # Step 1: Rebuild indexes for ALL Popoto models (SCAN-based, production-safe)
    # Cleans up stale/orphaned index entries across all models, not just AgentSession
    try:
        import time as _time

        from scripts.popoto_index_cleanup import run_cleanup

        _t0 = _time.monotonic()
        cleanup_result = run_cleanup()
        _elapsed = _time.monotonic() - _t0
        logger.info(
            f"Rebuilt indexes for all Popoto models "
            f"({cleanup_result.get('models_processed', 0)} models, "
            f"{cleanup_result.get('total_orphans_found', 0)} orphans cleaned, "
            f"{_elapsed:.1f}s)"
        )
    except Exception as e:
        logger.warning(f"Popoto index rebuild failed (non-fatal): {e}")

    # Step 2: Clean up corrupted sessions before recovery (prevents error spam)
    # Returns dict {"corrupted": int, "orphans": int} as of issue #1271.
    try:
        result = cleanup_corrupted_agent_sessions()
        if isinstance(result, dict):
            cleaned = result.get("corrupted", 0)
            orphans = result.get("orphans", 0)
        else:
            # Defensive: support legacy int return from older session_health.py
            cleaned = int(result) if result is not None else 0
            orphans = 0
        if cleaned:
            logger.info(f"Cleaned up {cleaned} corrupted session(s)")
        if orphans:
            logger.info(f"Reaped {orphans} orphan claude/MCP process(es) at startup")
    except Exception as e:
        logger.warning(f"Corrupted session cleanup failed (non-fatal): {e}")

    # Step 2b: Clean class-set orphans for AgentSession and Memory (#1459)
    # repair_indexes() in step 2 only covers $IndexF (field/status indexes).
    # TTL expiry removes hashes without removing class-set members, which causes
    # continuous Sentry noise. clean_indexes() uses SSCAN (production-safe).
    try:
        from models.agent_session import AgentSession
        from models.memory import Memory

        for model_cls, label in ((AgentSession, "AgentSession"), (Memory, "Memory")):
            try:
                removed = model_cls.clean_indexes()
                if removed:
                    logger.info(
                        f"clean_indexes {label}: removed {removed} orphan class-set entries"
                    )
            except Exception as ci_err:
                logger.warning(f"clean_indexes {label} failed (non-fatal): {ci_err}")
    except Exception as e:
        logger.warning(f"Class-set orphan cleanup failed (non-fatal): {e}")

    # Step 2c: Detect future-dated updated_at values written before fix #1645.
    # C2 (#1817): detection-only -- no longer clamps/re-saves (that reshuffled
    # the created_at-based index; see _heal_future_updated_at's docstring).
    # Purely an operator-visibility log; staleness reads no longer depend on
    # this having run (agent/session_health.py uses a trusted-clock relative
    # age instead of comparing local wall-clock to a possibly-skewed value).
    try:
        from models.agent_session import AgentSession as _AgentSession

        count = _AgentSession._heal_future_updated_at()
        if count:
            logger.info(f"_heal_future_updated_at: detected {count} future-dated record(s)")
    except Exception as e:
        logger.warning(f"_heal_future_updated_at non-fatal: {e}")

    # Step 3a: Sweep running sessions whose claude_pid is dead (issue #1767).
    # MUST run BEFORE _recover_interrupted_agent_sessions_startup (Step 3b) — that
    # function transitions all running→pending without checking PID liveness. If the
    # sweep runs after, there are no running sessions left to inspect.
    # This sweep finds sessions orphaned from a dead/U-state worker (dead claude_pid)
    # and marks them killed so catchup can re-enqueue the unanswered human messages.
    # Contrast: Step 3b re-queues sessions that are genuinely interruptible (alive PID
    # or no PID yet) — the sweep handles the dead-worker subset first.
    try:
        swept = _sweep_dead_worker_sessions()
        if swept:
            logger.info("Startup recovery: swept %d dead-worker running session(s) → killed", swept)
    except Exception as e:
        logger.warning(f"Dead-worker session sweep failed (non-fatal): {e}")

    # Step 3b: Recover any sessions that were running when the previous process died
    try:
        recovered = _recover_interrupted_agent_sessions_startup()
        if recovered:
            logger.info(f"Recovered {recovered} interrupted session(s)")
    except Exception as e:
        logger.warning(f"Session recovery failed (non-fatal): {e}")

    # Step 3c: Re-finalize parents stranded in waiting_for_children by a crash
    # window between the child's finalize save and the parent's own transition
    # (issue #1817, C1). finalize_session() intentionally saves the child
    # independently of the parent's best-effort finalize (see
    # agent/session_health.py::_sweep_stranded_waiting_for_children_parents for
    # the full non-coupling rationale) -- this sweep is what closes the
    # resulting crash-window orphan. Safe to run unconditionally: it only
    # transitions parents whose children are ALL terminal, and is a no-op for
    # a parent still legitimately waiting or already finalized.
    try:
        respawned = _sweep_stranded_waiting_for_children_parents()
        if respawned:
            logger.info(
                "Startup recovery: re-finalized %d stranded waiting_for_children parent(s)",
                respawned,
            )
    except Exception as e:
        logger.warning(f"Stranded waiting_for_children sweep failed (non-fatal): {e}")

    # Step 4: Kill orphaned Claude Code CLI subprocesses from prior runs
    try:
        orphans_killed = _cleanup_orphaned_claude_processes()
        if orphans_killed:
            logger.info(f"Killed {orphans_killed} orphaned Claude Code subprocess(es)")
    except Exception as e:
        logger.warning(f"Orphaned process cleanup failed (non-fatal): {e}")

    # Step 4b: Kill orphaned granite PTY children from prior runs.
    # The PTYPool records spawned PM/Dev PIDs to data/granite_pty_pids.json so
    # a worker-process restart can still kill orphan PTYs (plan #1572, Risk 1
    # / OPS-3). PID-targeted kill avoids pkill -f matching an operator's
    # personal interactive `claude` session on a different project.
    try:
        from agent.granite_container.pty_pool import _kill_orphaned_pty_pids

        killed_pids = _kill_orphaned_pty_pids()
        if killed_pids:
            logger.info(f"Killed {killed_pids} orphaned granite PTY subprocess(es)")
    except Exception as e:
        logger.warning(f"Granite PTY orphan cleanup failed (non-fatal): {e}")

    # Step 4b.5: Verify the granite classifier model is present and responsive.
    # The only ollama call is ensure_granite_model() — a one-time startup probe
    # and a background re-probe loop. Classification of PM prefix tokens is pure
    # regex (classify_pm_prefix); PTY sessions run on Claude OAuth subscription,
    # not ollama. When the probe succeeds, granite_available is set True and ENG
    # sessions are dispatched normally. When it fails, the worker starts in
    # DEGRADED mode: granite_available stays False, ENG sessions are deferred to
    # paused_circuit, and _granite_reprobe_loop retries in the background until
    # the model becomes reachable (circuit breaker: OPEN after
    # GRANITE_BREAKER_OPEN_THRESHOLD consecutive failures, COOLDOWN before
    # half-open re-probe). Non-ENG sessions (TEAMMATE) proceed unaffected.
    import agent.session_state as _ss_granite  # noqa: PLC0415
    from agent.granite_container.granite_classifier import ensure_granite_model  # noqa: PLC0415

    granite_ok, granite_detail = await asyncio.to_thread(ensure_granite_model)
    if not granite_ok:
        logger.warning(
            "Granite classifier unavailable: %s. Starting in degraded mode — "
            "ENG sessions will be deferred to paused_circuit until granite "
            "becomes reachable. Fix with 'ollama pull granite4.1:3b'.",
            granite_detail,
        )
        _ss_granite.granite_available = False
    else:
        logger.info("Granite classifier ready: %s", granite_detail)
        _ss_granite.granite_available = True

    # Start _granite_reprobe_loop as a supervised background task — it doubles as the
    # circuit breaker, toggling granite_available as the model appears/disappears.
    # supervise() respawns with exponential backoff; storm cap → _self_kill() SIGKILL.
    reprobe_task = supervise("granite-reprobe", _granite_reprobe_loop)

    # Step 4c: Initialize the granite PTY pool singleton (plan #1572).
    # The pool pre-warms GRANITE__PTY_POOL_SIZE (default 3) interactive
    # ``claude --permission-mode bypassPermissions`` pairs. Sessions
    # acquire/release pairs via async context manager; over-cap sessions
    # wait in the Redis queue.
    try:
        from agent.granite_container.pty_pool import initialize_pty_pool

        _pty_pool = initialize_pty_pool()
        await _pty_pool.initialize()
        logger.info(f"Granite PTY pool initialized: pool_size={_pty_pool.pool_size}")
    except Exception as e:
        logger.warning(f"Granite PTY pool initialization failed (non-fatal): {e}")

    # Step 5: Start worker loops -- one per project's known chat_ids
    # Workers are started on-demand by _ensure_worker when sessions are enqueued.
    # For startup, we need to kick workers for any pending sessions.
    # Gate: when granite is unavailable, ENG sessions are deferred to paused_circuit
    # so they are not silently mis-routed. Non-ENG sessions (TEAMMATE) proceed.
    from models.agent_session import AgentSession  # noqa: PLC0415
    from models.session_lifecycle import transition_status  # noqa: PLC0415

    pending_sessions = list(AgentSession.query.filter(status="pending"))
    deferred_count = 0
    started_workers: set[str] = set()
    for session in pending_sessions:
        session_type = getattr(session, "session_type", None)
        if not _ss_granite.granite_available and session_type == "eng":
            # Defer ENG sessions until granite becomes reachable.
            try:
                transition_status(
                    session,
                    "paused_circuit",
                    reason="granite-degrade: startup probe failed; resumes when granite recovers",
                )
                deferred_count += 1
                logger.info(
                    "Deferred ENG session %s to paused_circuit (granite unavailable)",
                    getattr(session, "session_id", "?"),
                )
            except Exception as e:
                logger.warning(
                    "Could not defer ENG session %s to paused_circuit: %s",
                    getattr(session, "session_id", "?"),
                    e,
                )
            continue
        wk = session.worker_key
        if wk not in started_workers:
            _ensure_worker(wk, is_project_keyed=session.is_project_keyed)
            started_workers.add(wk)

    logger.info(
        f"Worker started: {len(projects)} project(s), "
        f"{len(pending_sessions)} pending session(s), "
        f"{len(started_workers)} worker loop(s), "
        f"{deferred_count} ENG session(s) deferred (granite degraded)"
    )

    # Write heartbeat immediately so dashboard shows green without waiting
    # for the first 5-minute health loop tick.
    _write_worker_heartbeat()

    # Start on-loop liveness beacon task (issue #1815 fix #1).
    # Must be started BEFORE the heartbeat thread so the thread can observe
    # the first tick within its startup grace window.
    async def _loop_tick_task() -> None:
        """On-loop heartbeat: bumps last_loop_tick so the off-loop watchdog can
        distinguish a ticking loop from a synchronously-frozen one."""
        from agent.session_state import bump_loop_tick  # noqa: PLC0415

        bump_loop_tick()  # Initialize before first sleep so watchdog has a baseline
        while True:
            await asyncio.sleep(WORKER_DEADMAN_TICK_INTERVAL)
            bump_loop_tick()

    loop_tick_task = asyncio.create_task(_loop_tick_task(), name="loop-tick")

    def _loop_tick_task_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return  # Normal shutdown path
        exc = t.exception()
        if exc is not None:
            # The beacon stopping means the watchdog will eventually self-kill;
            # surface the cause so the crash is diagnosable.
            logger.error("Loop-tick beacon task exited unexpectedly: %s", exc)

    loop_tick_task.add_done_callback(_loop_tick_task_done)

    # Start dedicated heartbeat daemon thread (issue #1767, inverted #1815).
    # Runs outside the asyncio event loop so PTY/thread-pool saturation
    # cannot starve heartbeat writes. daemon=True ensures it cannot outlive
    # the worker process even on abnormal exit.
    _heartbeat_stop_event.clear()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_thread_main,
        name="worker-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    # Start dedicated session-archive export daemon thread (issue #1825).
    # Mirrors the heartbeat thread above exactly (own daemon thread, own stop
    # event) so the periodic SQLite export never shares the event loop or the
    # heartbeat's crash domain. Never started under pytest — a test run must
    # not spin up a background thread that writes to data/session_archive.db.
    session_archive_thread = None
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        _session_archive_stop_event.clear()
        session_archive_thread = threading.Thread(
            target=_session_archive_thread_main,
            name="worker-session-archive",
            daemon=True,
        )
        session_archive_thread.start()

    # Start health monitor as a supervised background task (#1816 Fix #4).
    # supervise() respawns on unexpected death with exponential backoff;
    # storm cap exceeds → _self_kill() SIGKILL so launchd can respawn clean.
    health_task = supervise("session-health-monitor", _agent_session_health_loop)

    # Start per-tool timeout sub-loop (issue #1270) — supervised.
    # Parallel to the main 5-min health loop on its own 30s cadence so the
    # 30s internal-tier budget can fire within one tick of expiry.
    tool_timeout_task = supervise("session-tool-timeout-monitor", _agent_session_tool_timeout_loop)

    # The reflection scheduler runs OUT-OF-PROCESS (issue #1828): its own supervised
    # launchd subprocess (`python -m reflections`, com.valor.reflection-worker) owns it,
    # so a reflection defect can no longer share this worker's event loop or crash domain.
    # The worker only executes the AgentSession records that subprocess enqueues.

    # Start pub/sub listener — supervised; delivers ~1s session pickup vs 5-min health check.
    notify_task = supervise("session-notify-listener", _session_notify_listener)

    # Start idle SDK-client sweeper (issue #1128) — supervised. Worker-internal because
    # `_active_clients` is process-local — the session-watchdog (separate process)
    # cannot reach the registry. See `worker/idle_sweeper.py`.
    idle_sweep_task = None
    try:
        from worker.idle_sweeper import run_idle_sweep

        idle_sweep_task = supervise("idle-sweeper", run_idle_sweep)
        logger.info("Idle SDK-client sweeper started (supervised)")
    except Exception as e:
        logger.warning("Failed to start idle sweeper: %s", e)

    # Set up graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        global _shutdown_via_signal
        logger.info(f"Received signal {sig}, shutting down gracefully...")
        if sig == signal.SIGTERM:
            _shutdown_via_signal = True
        request_shutdown()  # Signal all worker loops to finish current sessions
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Wait for active worker loops to finish their current sessions
    active_tasks = [t for t in _active_workers.values() if not t.done()]
    if active_tasks:
        logger.info(f"Waiting for {len(active_tasks)} active worker(s) to finish...")
        done, pending = await asyncio.wait(active_tasks, timeout=60)
        if pending:
            logger.warning(f"{len(pending)} worker(s) did not finish in 60s, cancelling...")
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    # Drain in-flight PM final-delivery completion runners (issue #1058).
    # Ordering: before extraction drain because the completion runner may
    # itself schedule extraction, and we want the completion turn to reach
    # its CancelledError handler (which delivers the "interrupted" message)
    # while Redis and the transport send_cb are still wired up.
    try:
        from agent.session_completion import drain_pending_completions

        await drain_pending_completions(timeout=15.0)
    except Exception as e:
        logger.warning(f"Completion drain failed: {e}")

    # Drain in-flight post-session extractions (hotfix #1055).
    # Ordering: after worker-task wait (so every extraction that will be scheduled
    # has been scheduled), before health/notify/reflection cancels (so the event
    # loop is still running and pending extractions can cooperate with cancel).
    try:
        from agent.session_executor import drain_pending_extractions

        await drain_pending_extractions(timeout=5.0)
    except Exception as e:
        logger.warning(f"Extraction drain failed: {e}")

    # Stop the dedicated heartbeat thread (issue #1767).
    # Setting the event causes _heartbeat_thread_main's wait() to return
    # immediately; the thread exits its loop and the join() completes quickly.
    _heartbeat_stop_event.set()
    heartbeat_thread.join(timeout=5)

    # Stop the dedicated session-archive export thread (issue #1825), mirroring
    # the heartbeat thread shutdown above. Only join if it was actually started
    # (never started under pytest -- see the startup guard).
    if session_archive_thread is not None:
        _session_archive_stop_event.set()
        session_archive_thread.join(timeout=5)

    # Cancel health monitor
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass

    # Cancel per-tool timeout sub-loop
    tool_timeout_task.cancel()
    try:
        await tool_timeout_task
    except asyncio.CancelledError:
        pass

    # Cancel on-loop liveness beacon (issue #1815 fix #1)
    loop_tick_task.cancel()
    try:
        await loop_tick_task
    except asyncio.CancelledError:
        pass

    # Cancel pub/sub listener
    notify_task.cancel()
    try:
        await notify_task
    except asyncio.CancelledError:
        pass

    # (Reflection scheduler runs out-of-process — issue #1828 — so there is no
    # reflection task to cancel here.)

    # Cancel granite re-probe / circuit breaker loop (Fix #1)
    reprobe_task.cancel()
    try:
        await reprobe_task
    except asyncio.CancelledError:
        pass

    # Cancel idle-sweeper (issue #1128)
    if idle_sweep_task is not None:
        idle_sweep_task.cancel()
        try:
            await idle_sweep_task
        except asyncio.CancelledError:
            pass

    # Drain the granite PTY pool's in-flight respawn tasks (POOL-1).
    # The pool's per-slot `event` is only set after `_spawn_slot`
    # completes; if the worker exits before a respawn finishes, the
    # slot is left in `respawning` permanently and the next worker
    # process's `_load_persisted_pids` will not see the in-flight
    # spawn. We drain the asyncio.Tasks here so respawns either
    # complete or are visibly cancelled.
    try:
        from agent.granite_container.pty_pool import get_pty_pool

        _pool = get_pty_pool()
        _pool.shutdown()
        await _pool.drain_respawns()
    except Exception as e:
        logger.warning(f"Granite PTY pool drain failed (non-fatal): {e}")

    logger.info("Worker shutdown complete")


def main() -> None:
    """Entry point for python -m worker."""
    _configure_logging()
    args = _parse_args()

    logger.info("Starting standalone worker...")

    # Ensure project root is on path
    project_root = str(Path(__file__).parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Record the SHA this worker booted at (data/worker_boot_sha) so the update
    # system can verify the running release matches pulled HEAD (issue #1898).
    # Best-effort like _green_heartbeat_write — a failure logs a warning and
    # never crashes startup.
    try:
        from monitoring.boot_beacon import write_boot_beacon  # noqa: PLC0415

        write_boot_beacon("worker")
    except Exception as beacon_err:
        logger.warning(f"Boot beacon write skipped (non-fatal): {beacon_err}")

    # Set environment hint that we're running as standalone worker
    os.environ.setdefault("VALOR_WORKER_MODE", "standalone")

    # Worker-process Sentry (#1877 defect #3). Session execution happens here, so
    # worker exceptions must reach Sentry with the same fidelity as bridge ones.
    # The worker passes before_send=None (no bridge-hibernation coupling); the
    # shared helper is DSN-gated and self-guards against pytest/CI mis-tagging.
    from monitoring.sentry_config import configure_sentry  # noqa: PLC0415

    configure_sentry("worker", before_send=None)
    logger.info(
        "worker sentry: %s",
        "enabled" if os.getenv("SENTRY_DSN") else "disabled (no DSN in worker env)",
    )

    # Configure resilient Redis connection before any Popoto model is accessed.
    # Degrade-don't-die: if Redis is unreachable at boot this logs a warning
    # and returns without raising so operators can start Redis and restart.
    from config.redis_bootstrap import configure_resilient_redis  # noqa: PLC0415

    configure_resilient_redis()

    # Validate agent definition files are usable on disk. Missing, malformed,
    # or unreadable files are not fatal — the SDK falls back gracefully — but
    # we surface warnings early so operators can fix them before users hit
    # degraded prompts. The worker is the actual session execution engine
    # (per CLAUDE.md), so this check must fire here in addition to the bridge
    # startup hook. `_parse_agent_markdown` already logs a precise per-path
    # warning, so we emit a concise startup summary here instead of a
    # misleading "Missing" line for files that may actually be malformed.
    from agent.agent_definitions import validate_agent_files

    problematic_agent_files = validate_agent_files()
    if problematic_agent_files:
        logger.warning(
            "Unusable agent definition files detected at startup (%d): %s",
            len(problematic_agent_files),
            problematic_agent_files,
        )

    projects = _load_projects(args.project)

    if not projects:
        logger.error("No projects found in configuration. Exiting.")
        sys.exit(1)

    asyncio.run(_run_worker(projects, dry_run=args.dry_run))

    if _shutdown_via_signal:
        logger.info("Exiting with code 1 (SIGTERM) so launchd respects ThrottleInterval")
        sys.exit(1)


if __name__ == "__main__":
    main()
