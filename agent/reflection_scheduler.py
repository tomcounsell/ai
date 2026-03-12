"""Reflection Scheduler - unified scheduling for all recurring non-issue work.

Replaces the scattered scheduling mechanisms (launchd plists, asyncio loops,
startup hooks) with a single lightweight scheduler that reads from
config/reflections.yaml and enqueues due reflections as jobs.

Architecture:
- Registry: config/reflections.yaml declares all reflections
- State: models/reflection.py (Popoto/Redis) tracks last_run, next_due, etc.
- Scheduler: This module - asyncio loop that ticks every 60s
- Execution: Two modes - function (direct callable) and agent (full session)

See docs/features/reflections.md for full documentation.
"""

import asyncio
import importlib
import inspect
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from models.reflection import Reflection

logger = logging.getLogger(__name__)

# Scheduler tick interval in seconds
SCHEDULER_TICK_INTERVAL = 60

# Path to the reflections registry
REGISTRY_PATH = Path(__file__).parent.parent / "config" / "reflections.yaml"


@dataclass
class ReflectionEntry:
    """A parsed reflection declaration from the registry."""

    name: str
    description: str
    interval: int  # seconds between runs
    priority: str  # urgent | high | normal | low
    execution_type: str  # "function" or "agent"
    callable: str | None = None  # dotted Python path for function type
    command: str | None = None  # shell command for agent type
    enabled: bool = True

    def validate(self) -> list[str]:
        """Validate this entry, returning a list of error messages."""
        errors = []
        if not self.name:
            errors.append("name is required")
        if not self.interval or self.interval <= 0:
            errors.append(f"interval must be positive, got {self.interval}")
        if self.priority not in ("urgent", "high", "normal", "low"):
            errors.append(f"invalid priority: {self.priority}")
        if self.execution_type not in ("function", "agent"):
            errors.append(f"invalid execution_type: {self.execution_type}")
        if self.execution_type == "function" and not self.callable:
            errors.append("callable is required for execution_type: function")
        if self.execution_type == "agent" and not self.command:
            errors.append("command is required for execution_type: agent")
        return errors


def load_registry(path: Path | None = None) -> list[ReflectionEntry]:
    """Load and validate the reflections registry from YAML.

    Args:
        path: Path to the YAML file. Defaults to config/reflections.yaml.

    Returns:
        List of validated ReflectionEntry objects. Invalid entries are
        logged and skipped.
    """
    path = path or REGISTRY_PATH
    if not path.exists():
        logger.warning("Reflections registry not found at %s", path)
        return []

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error("Failed to parse reflections registry %s: %s", path, e)
        return []

    if not data or "reflections" not in data:
        logger.warning("Reflections registry %s has no 'reflections' key", path)
        return []

    entries = []
    for raw in data["reflections"]:
        if not isinstance(raw, dict):
            logger.warning("Skipping non-dict registry entry: %s", raw)
            continue

        try:
            entry = ReflectionEntry(
                name=raw.get("name", ""),
                description=raw.get("description", ""),
                interval=int(raw.get("interval", 0)),
                priority=raw.get("priority", "low"),
                execution_type=raw.get("execution_type", "function"),
                callable=raw.get("callable"),
                command=raw.get("command"),
                enabled=raw.get("enabled", True),
            )
        except (TypeError, ValueError) as e:
            logger.warning("Skipping malformed registry entry %s: %s", raw.get("name", "?"), e)
            continue

        errors = entry.validate()
        if errors:
            logger.warning(
                "Skipping invalid reflection '%s': %s",
                entry.name,
                "; ".join(errors),
            )
            continue

        if not entry.enabled:
            logger.debug("Skipping disabled reflection: %s", entry.name)
            continue

        entries.append(entry)

    logger.info("Loaded %d reflection(s) from registry", len(entries))
    return entries


def _resolve_callable(dotted_path: str) -> Any:
    """Resolve a dotted Python path to a callable.

    Args:
        dotted_path: e.g. "agent.job_queue._job_health_check"

    Returns:
        The callable object.

    Raises:
        ImportError: If the module cannot be imported.
        AttributeError: If the function doesn't exist in the module.
    """
    parts = dotted_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ImportError(f"Invalid callable path: {dotted_path}")
    module_path, func_name = parts
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def is_reflection_due(entry: ReflectionEntry, state: Reflection, now: float) -> bool:
    """Check if a reflection is due to run.

    A reflection is due if:
    - It has never run (last_run is None), OR
    - last_run + interval <= now

    Args:
        entry: The registry entry with the interval
        state: The Redis state record with last_run
        now: Current timestamp

    Returns:
        True if the reflection should be enqueued.
    """
    if state.last_run is None:
        return True
    return (state.last_run + entry.interval) <= now


def is_reflection_running(state: Reflection) -> bool:
    """Check if a reflection is currently running (skip-if-running guard).

    Args:
        state: The Redis state record

    Returns:
        True if the reflection is currently marked as running.
    """
    return state.last_status == "running"


async def execute_function_reflection(entry: ReflectionEntry) -> None:
    """Execute a function-type reflection by calling its Python callable.

    Handles both sync and async callables.

    Args:
        entry: The registry entry with the callable path.
    """
    func = _resolve_callable(entry.callable)

    if inspect.iscoroutinefunction(func):
        await func()
    else:
        # Run sync functions in a thread to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, func)


async def run_reflection(entry: ReflectionEntry, state: Reflection) -> None:
    """Execute a single reflection and update its state.

    Args:
        entry: The registry entry describing the reflection
        state: The Redis state record to update
    """
    logger.info("[reflection] Starting: %s (%s)", entry.name, entry.execution_type)
    state.mark_started()

    start_time = time.time()
    try:
        if entry.execution_type == "function":
            await execute_function_reflection(entry)
        else:
            # Agent-type reflections are enqueued to the job queue
            # instead of executed directly
            await _enqueue_agent_reflection(entry)

        duration = time.time() - start_time
        state.mark_completed(duration)
        logger.info(
            "[reflection] Completed: %s (%.1fs)",
            entry.name,
            duration,
        )
    except Exception as e:
        duration = time.time() - start_time
        error_msg = f"{type(e).__name__}: {e}"
        state.mark_completed(duration, error=error_msg)
        logger.error(
            "[reflection] Failed: %s after %.1fs: %s",
            entry.name,
            duration,
            error_msg,
            exc_info=True,
        )


async def _enqueue_agent_reflection(entry: ReflectionEntry) -> None:
    """Execute an agent-type reflection by running its command as a subprocess.

    Agent-type reflections run shell commands (e.g., scripts that need a full
    Claude session). They run in a subprocess to avoid blocking the scheduler.

    Args:
        entry: The registry entry with the command to run.
    """
    import subprocess
    from pathlib import Path

    if not entry.command:
        logger.error("[reflection] Agent reflection '%s' has no command", entry.name)
        return

    # Run from the project root
    project_root = Path(__file__).parent.parent

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                entry.command,
                shell=True,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour max for agent reflections
            ),
        )
        if result.returncode != 0:
            raise RuntimeError(f"Command exited {result.returncode}: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Agent reflection '{entry.name}' timed out after 1 hour")


class ReflectionScheduler:
    """Lightweight scheduler that checks reflections registry and enqueues due work.

    Usage:
        scheduler = ReflectionScheduler()
        await scheduler.start()  # Runs forever, ticking every 60s
    """

    def __init__(self, registry_path: Path | None = None):
        self._registry_path = registry_path
        self._entries: list[ReflectionEntry] = []
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._started = False

    def load(self) -> None:
        """Load/reload the registry from disk."""
        self._entries = load_registry(self._registry_path)

    async def tick(self) -> int:
        """Run one scheduler tick: check all reflections and enqueue due ones.

        Returns:
            Number of reflections enqueued this tick.
        """
        now = time.time()
        enqueued = 0

        for entry in self._entries:
            try:
                state = Reflection.get_or_create(entry.name)

                # Skip if already running
                if is_reflection_running(state):
                    # Check for stuck reflections (running > 2x interval)
                    if state.last_run and (now - state.last_run) > (entry.interval * 2):
                        logger.warning(
                            "[reflection] %s appears stuck (running for %.0fs, interval=%ds). "
                            "Resetting status.",
                            entry.name,
                            now - state.last_run,
                            entry.interval,
                        )
                        state.last_status = "error"
                        state.last_error = "Reset: appeared stuck (exceeded 2x interval)"
                        state.save()
                    else:
                        logger.debug("[reflection] Skipping %s: already running", entry.name)
                        state.mark_skipped("already running")
                        continue

                # Check if due
                if not is_reflection_due(entry, state, now):
                    continue

                # Execute or enqueue
                logger.info("[reflection] %s is due, executing", entry.name)

                if entry.execution_type == "function":
                    # Run function-type reflections as background tasks
                    task = asyncio.create_task(
                        run_reflection(entry, state),
                        name=f"reflection-{entry.name}",
                    )
                    self._running_tasks[entry.name] = task
                    # Clean up completed tasks
                    task.add_done_callback(
                        lambda t, name=entry.name: self._running_tasks.pop(name, None)
                    )
                else:
                    # Agent-type reflections are enqueued to job queue
                    await run_reflection(entry, state)

                enqueued += 1

            except Exception as e:
                logger.error(
                    "[reflection] Error processing reflection '%s': %s",
                    entry.name,
                    e,
                    exc_info=True,
                )

        return enqueued

    async def start(self) -> None:
        """Start the scheduler loop. Runs forever, ticking every SCHEDULER_TICK_INTERVAL seconds."""
        if self._started:
            logger.warning("[reflection] Scheduler already started")
            return

        self._started = True
        self.load()
        logger.info(
            "[reflection] Scheduler started with %d reflection(s), tick interval=%ds",
            len(self._entries),
            SCHEDULER_TICK_INTERVAL,
        )

        while True:
            try:
                enqueued = await self.tick()
                if enqueued > 0:
                    logger.info("[reflection] Tick complete: %d reflection(s) enqueued", enqueued)
            except Exception as e:
                logger.error("[reflection] Scheduler tick error: %s", e, exc_info=True)

            await asyncio.sleep(SCHEDULER_TICK_INTERVAL)

    def get_status(self) -> list[dict]:
        """Get current status of all registered reflections.

        Returns:
            List of dicts with reflection name, state, and schedule info.
            Used by /queue-status for observability.
        """
        now = time.time()
        statuses = []

        for entry in self._entries:
            try:
                state = Reflection.get_or_create(entry.name)
                time_until_due = None
                if state.last_run:
                    next_due = state.last_run + entry.interval
                    time_until_due = max(0, next_due - now)

                statuses.append(
                    {
                        "name": entry.name,
                        "description": entry.description,
                        "interval": entry.interval,
                        "priority": entry.priority,
                        "execution_type": entry.execution_type,
                        "last_run": state.last_run,
                        "last_status": state.last_status,
                        "last_error": state.last_error,
                        "last_duration": state.last_duration,
                        "run_count": state.run_count,
                        "time_until_due": time_until_due,
                        "is_running": entry.name in self._running_tasks,
                    }
                )
            except Exception as e:
                statuses.append(
                    {
                        "name": entry.name,
                        "description": entry.description,
                        "error": str(e),
                    }
                )

        return statuses

    def format_status(self) -> str:
        """Format reflection status for display (e.g., in /queue-status).

        Returns:
            Human-readable multi-line string showing all reflections.
        """
        statuses = self.get_status()
        if not statuses:
            return "No reflections registered."

        lines = ["Reflections:"]
        for s in statuses:
            if "error" in s:
                lines.append(f"  {s['name']}: ERROR - {s['error']}")
                continue

            status_icon = {
                "success": "ok",
                "error": "ERR",
                "running": "RUN",
                "skipped": "skip",
                "pending": "new",
            }.get(s["last_status"], "?")

            due_str = ""
            if s["time_until_due"] is not None:
                if s["time_until_due"] <= 0:
                    due_str = " (due NOW)"
                else:
                    mins = int(s["time_until_due"] / 60)
                    if mins >= 60:
                        due_str = f" (due in {mins // 60}h {mins % 60}m)"
                    else:
                        due_str = f" (due in {mins}m)"

            duration_str = ""
            if s["last_duration"] is not None:
                duration_str = f" [{s['last_duration']:.1f}s]"

            error_str = ""
            if s["last_error"] and s["last_status"] == "error":
                error_str = f" - {s['last_error'][:80]}"

            lines.append(
                f"  [{status_icon}] {s['name']} ({s['priority']}){due_str}{duration_str}"
                f" (ran {s['run_count']}x){error_str}"
            )

        return "\n".join(lines)
