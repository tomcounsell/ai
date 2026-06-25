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

# Set to True when SIGTERM is received; causes main() to exit with code 1
# so launchd applies ThrottleInterval (10s) instead of the default ~10-minute throttle.
_shutdown_via_signal = False

# Heartbeat thread interval: how often the dedicated daemon thread writes
# data/last_worker_connected. Env-tunable for conservative rollout.
WORKER_HEARTBEAT_INTERVAL = int(os.environ.get("WORKER_HEARTBEAT_INTERVAL", "30"))

# Stop event for the heartbeat daemon thread — set on worker shutdown.
_heartbeat_stop_event = threading.Event()


def _heartbeat_thread_main() -> None:
    """Dedicated daemon thread for worker heartbeat writes.

    Runs independently of the asyncio event loop so PTY/thread-pool
    saturation (incident 2026-06-23, issue #1767) cannot prevent heartbeat
    writes. The loop wakes every WORKER_HEARTBEAT_INTERVAL seconds and
    writes data/last_worker_connected via the existing _write_worker_heartbeat().

    Ref: #1055 for the pattern of moving blocking calls off the hot path.
    """
    # Import deferred to after module-level code runs (avoids circular imports
    # at the top of the file where agent packages are not yet on sys.path).
    from agent.agent_session_queue import _write_worker_heartbeat  # noqa: PLC0415

    logger.info("Heartbeat thread started (interval=%ds)", WORKER_HEARTBEAT_INTERVAL)
    while not _heartbeat_stop_event.wait(timeout=WORKER_HEARTBEAT_INTERVAL):
        try:
            _write_worker_heartbeat()
        except Exception as exc:
            logger.warning("Heartbeat thread: write failed: %s", exc)
    logger.info("Heartbeat thread stopped")


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
        _sweep_dead_worker_sessions,
        _write_worker_heartbeat,
        cleanup_corrupted_agent_sessions,
        register_callbacks,
        register_worker_pid,
        request_shutdown,
    )
    from agent.output_handler import FileOutputHandler, TelegramRelayOutputHandler
    from agent.session_health import _agent_session_tool_timeout_loop

    # Initialize global concurrency semaphore BEFORE any worker loops are created.
    # Clamp to minimum 1 to prevent deadlock if MAX_CONCURRENT_SESSIONS=0.
    _max_sessions = max(1, int(os.environ.get("MAX_CONCURRENT_SESSIONS", "8")))
    import agent.session_state as _ss  # noqa: PLC0415

    _ss._global_session_semaphore = asyncio.Semaphore(_max_sessions)
    logger.info(f"Global session semaphore initialized: MAX_CONCURRENT_SESSIONS={_max_sessions}")

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

    # Step 2c: Heal future-dated updated_at values written before fix #1645.
    # Clamps any session whose updated_at is in the future (caused by popoto
    # auto_now minting naive local time on non-UTC hosts). Idempotent.
    try:
        from models.agent_session import AgentSession as _AgentSession

        count = _AgentSession._heal_future_updated_at()
        logger.info(f"_heal_future_updated_at: healed {count} records")
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
    # Granite is the routing brain of the PTY container — every PM/Dev turn is
    # routed by an ollama call against it. Without granite the worker would come
    # up and silently mis-route every session, so this is a HARD startup
    # precondition (the granite PTY path is all-or-nothing; there is no runtime
    # fallback). Every restart path funnels through here — /update's inline
    # restart, the cron deferred restart-flag → SIGTERM → launchd respawn
    # (agent_session_queue._trigger_restart), and manual worker-restart — so
    # gating at startup covers them all, not just the interactive /update path.
    # Pulls once on miss; exits non-zero if granite still can't be made
    # available (launchd respawns after ThrottleInterval, self-healing once
    # granite becomes reachable).
    from agent.granite_container.granite_classifier import ensure_granite_model

    granite_ok, granite_detail = await asyncio.to_thread(ensure_granite_model)
    if not granite_ok:
        logger.critical(
            "Granite classifier unavailable: %s. The PTY container cannot route "
            "sessions without it — exiting. Fix with 'ollama pull granite4.1:3b'.",
            granite_detail,
        )
        sys.exit(1)
    logger.info("Granite classifier ready: %s", granite_detail)

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

    # Start dedicated heartbeat daemon thread (issue #1767).
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

    # Start per-tool timeout sub-loop (issue #1270) — parallel to the main 5-min
    # health loop on its own 30s cadence so the 30s internal-tier budget can fire
    # within one tick of expiry. Independent done-callback so a crash here is
    # logged distinctly from the main monitor.
    tool_timeout_task = asyncio.create_task(
        _agent_session_tool_timeout_loop(), name="session-tool-timeout-monitor"
    )

    def _tool_timeout_task_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("Tool-timeout sub-loop exited unexpectedly: %s", exc)

    tool_timeout_task.add_done_callback(_tool_timeout_task_done)

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

    # Start idle SDK-client sweeper (issue #1128). Worker-internal because
    # `_active_clients` is process-local — the session-watchdog (separate
    # process) cannot reach the registry. See `worker/idle_sweeper.py`.
    idle_sweep_task = None
    try:
        from worker.idle_sweeper import run_idle_sweep

        idle_sweep_task = asyncio.create_task(run_idle_sweep(), name="idle-sweeper")

        def _idle_sweep_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("Idle sweeper exited unexpectedly: %s", exc)

        idle_sweep_task.add_done_callback(_idle_sweep_done)
        logger.info("Idle SDK-client sweeper started")
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

    # Set environment hint that we're running as standalone worker
    os.environ.setdefault("VALOR_WORKER_MODE", "standalone")

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
