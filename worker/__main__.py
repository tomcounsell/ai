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
import logging
import os
import signal
import sys
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

# Set to True when SIGTERM is received; causes main() to exit with code 1
# so launchd applies ThrottleInterval (10s) instead of the default ~10-minute throttle.
_shutdown_via_signal = False


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
        _write_worker_heartbeat,
        cleanup_corrupted_agent_sessions,
        register_callbacks,
        request_shutdown,
    )
    from agent.output_handler import FileOutputHandler, TelegramRelayOutputHandler

    # Initialize global concurrency semaphore BEFORE any worker loops are created.
    # Clamp to minimum 1 to prevent deadlock if MAX_CONCURRENT_SESSIONS=0.
    _max_sessions = max(1, int(os.environ.get("MAX_CONCURRENT_SESSIONS", "8")))
    import agent.session_state as _ss  # noqa: PLC0415

    _ss._global_session_semaphore = asyncio.Semaphore(_max_sessions)
    logger.info(f"Global session semaphore initialized: MAX_CONCURRENT_SESSIONS={_max_sessions}")

    handler = TelegramRelayOutputHandler(file_handler=FileOutputHandler())
    from bridge.email_bridge import EmailOutputHandler as _EmailOutputHandler

    email_handler = _EmailOutputHandler()

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
    try:
        cleaned = cleanup_corrupted_agent_sessions()
        if cleaned:
            logger.info(f"Cleaned up {cleaned} corrupted session(s)")
    except Exception as e:
        logger.warning(f"Corrupted session cleanup failed (non-fatal): {e}")

    # Step 3: Recover any sessions that were running when the previous process died
    try:
        recovered = _recover_interrupted_agent_sessions_startup()
        if recovered:
            logger.info(f"Recovered {recovered} interrupted session(s)")
    except Exception as e:
        logger.warning(f"Session recovery failed (non-fatal): {e}")

    # Step 4: Kill orphaned Claude Code CLI subprocesses from prior runs
    try:
        orphans_killed = _cleanup_orphaned_claude_processes()
        if orphans_killed:
            logger.info(f"Killed {orphans_killed} orphaned Claude Code subprocess(es)")
    except Exception as e:
        logger.warning(f"Orphaned process cleanup failed (non-fatal): {e}")

    # Step 5: Start worker loops -- one per project's known chat_ids
    # Workers are started on-demand by _ensure_worker when sessions are enqueued.
    # For startup, we need to kick workers for any pending sessions.
    from models.agent_session import AgentSession

    pending_sessions = list(AgentSession.query.filter(status="pending"))
    started_workers: set[str] = set()
    for session in pending_sessions:
        wk = session.worker_key
        if wk not in started_workers:
            _ensure_worker(wk, is_project_keyed=session.is_project_keyed)
            started_workers.add(wk)

    logger.info(
        f"Worker started: {len(projects)} project(s), "
        f"{len(pending_sessions)} pending session(s), "
        f"{len(started_workers)} worker loop(s)"
    )

    # Write heartbeat immediately so dashboard shows green without waiting
    # for the first 5-minute health loop tick.
    _write_worker_heartbeat()

    # Start health monitor as background task
    health_task = asyncio.create_task(_agent_session_health_loop(), name="session-health-monitor")

    def _health_task_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return  # Normal shutdown path
        exc = t.exception()
        if exc is not None:
            # Guards against unexpected task exit — ordinary exceptions are already caught
            # inside the loop's own try-except.
            logger.error("Health monitor task exited unexpectedly: %s", exc)

    health_task.add_done_callback(_health_task_done)

    def _health_task_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return  # Normal shutdown path
        exc = t.exception()
        if exc is not None:
            logger.error("Health monitor exited unexpectedly: %s", exc)

    health_task.add_done_callback(_health_task_done)

    # Start unified reflection scheduler (moved from bridge — processing belongs in worker)
    reflection_task = None
    try:
        from agent.reflection_scheduler import ReflectionScheduler

        _reflection_scheduler = ReflectionScheduler()
        reflection_task = asyncio.create_task(
            _reflection_scheduler.start(), name="reflection-scheduler"
        )

        def _reflection_task_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("Reflection scheduler exited unexpectedly: %s", exc)

        reflection_task.add_done_callback(_reflection_task_done)
        logger.info("Reflection scheduler started")
    except Exception as e:
        logger.error(f"Failed to start reflection scheduler: {e}")

    # Start pub/sub listener — delivers ~1s session pickup vs 5-minute health check
    notify_task = asyncio.create_task(_session_notify_listener(), name="session-notify-listener")

    def _notify_task_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return  # Normal shutdown path
        exc = t.exception()
        if exc is not None:
            logger.error("Session notify listener exited unexpectedly: %s", exc)

    notify_task.add_done_callback(_notify_task_done)

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

    # Cancel health monitor
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass

    # Cancel pub/sub listener
    notify_task.cancel()
    try:
        await notify_task
    except asyncio.CancelledError:
        pass

    # Cancel reflection scheduler
    if reflection_task is not None:
        reflection_task.cancel()
        try:
            await reflection_task
        except asyncio.CancelledError:
            pass

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

    # Set environment hint that we're running as standalone worker
    os.environ.setdefault("VALOR_WORKER_MODE", "standalone")

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
