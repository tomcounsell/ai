"""
Standalone worker entry point for processing AgentSession records.

Processes sessions from Redis without requiring a Telegram connection.
Developer workstations run just the worker. Bridge machines run
bridge + embedded worker (backward compatible).

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
from pathlib import Path

logger = logging.getLogger("worker")


def _configure_logging() -> None:
    """Set up logging for the standalone worker."""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "worker.log"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(str(log_file)),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )


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

    1. Registers FileOutputHandler for each project
    2. Recovers interrupted sessions
    3. Starts worker loops per project
    4. Starts health monitor
    5. Waits for shutdown signal
    """
    from agent.agent_session_queue import (
        _active_workers,
        _agent_session_health_loop,
        _ensure_worker,
        _recover_interrupted_agent_sessions_startup,
        register_callbacks,
        request_shutdown,
    )
    from agent.output_handler import FileOutputHandler

    handler = FileOutputHandler()

    # Verify Redis is reachable by attempting to list sessions
    try:
        from models.agent_session import AgentSession

        _ = list(AgentSession.query.filter(status="pending"))
        logger.info("Redis connection verified")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        sys.exit(1)

    if dry_run:
        logger.info(
            f"Dry run: config loaded with {len(projects)} project(s): "
            f"{', '.join(projects.keys())}. Worker ready."
        )
        return

    # Register FileOutputHandler for each project
    for project_key in projects:
        register_callbacks(project_key, handler=handler)
        logger.info(f"[{project_key}] Registered FileOutputHandler")

    # Recover any sessions that were running when the previous process died
    recovered = _recover_interrupted_agent_sessions_startup()
    if recovered:
        logger.info(f"Recovered {recovered} interrupted session(s)")

    # Start worker loops -- one per project's known chat_ids
    # Workers are started on-demand by _ensure_worker when sessions are enqueued.
    # For startup, we need to kick workers for any pending sessions.
    from models.agent_session import AgentSession

    pending_sessions = list(AgentSession.query.filter(status="pending"))
    started_chats: set[str] = set()
    for session in pending_sessions:
        chat_id = session.chat_id or session.project_key
        if chat_id not in started_chats:
            _ensure_worker(chat_id)
            started_chats.add(chat_id)

    logger.info(
        f"Worker started: {len(projects)} project(s), "
        f"{len(pending_sessions)} pending session(s), "
        f"{len(started_chats)} worker loop(s)"
    )

    # Start health monitor as background task
    health_task = asyncio.create_task(_agent_session_health_loop())

    # Set up graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down gracefully...")
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


if __name__ == "__main__":
    main()
