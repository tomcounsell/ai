"""Reflection Scheduler - unified scheduling for all recurring non-issue work.

Replaces the scattered scheduling mechanisms (launchd plists, asyncio loops,
startup hooks) with a single lightweight scheduler that reads from
config/reflections.yaml and enqueues due reflections as sessions.

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
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.reflection_schedule import (
    compute_next_due,
    is_legacy_interval_format,
    parse_every_duration,
)
from models.reflection import Reflection

# Memory warning threshold in bytes (100MB)
MEMORY_DELTA_WARNING_BYTES = 100 * 1024 * 1024

# Default timeouts in seconds
DEFAULT_FUNCTION_TIMEOUT = 1800  # 30 minutes
DEFAULT_AGENT_TIMEOUT = 3600  # 1 hour

logger = logging.getLogger(__name__)

# Scheduler tick interval in seconds
SCHEDULER_TICK_INTERVAL = 60


# Path to the reflections registry.
# Resolution order: REFLECTIONS_YAML env var → ~/Desktop/Valor/reflections.yaml → config/
def _resolve_registry_path() -> Path:
    """Resolve the reflections YAML path using vault-first fallback logic.

    Priority:
    1. REFLECTIONS_YAML env var (explicit override, e.g., for testing)
    2. ~/Desktop/Valor/reflections.yaml (iCloud-synced vault, private config)
    3. config/reflections.yaml (in-repo fallback, always present)
    """
    import os

    env_path = os.environ.get("REFLECTIONS_YAML")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p
        logger.warning("REFLECTIONS_YAML env var points to non-existent path: %s", env_path)

    # When running under launchd (VALOR_LAUNCHD=1), skip the iCloud-synced Desktop
    # path entirely. macOS TCC blocks stat()/open() on ~/Desktop files from launchd
    # agents — even exists() hangs indefinitely and blocks the asyncio event loop.
    # install_worker.sh copies reflections.yaml → config/reflections.yaml at install time.
    if not os.environ.get("VALOR_LAUNCHD"):
        vault_path = Path.home() / "Desktop" / "Valor" / "reflections.yaml"
        if vault_path.exists():
            return vault_path

    return Path(__file__).parent.parent / "config" / "reflections.yaml"


REGISTRY_PATH = _resolve_registry_path()


@dataclass
class ReflectionEntry:
    """A parsed reflection declaration from the registry.

    Carries the unified ``schedule`` string (``every:`` / ``cron:`` / ``at:``).
    For backward compatibility during the migration window, callers may still
    construct an entry with ``interval=N`` (legacy seconds-only) and the
    constructor will normalize it to ``schedule="every: Ns"`` so the rest of
    the scheduler doesn't have to handle two shapes.
    """

    name: str
    description: str
    priority: str  # urgent | high | normal | low
    execution_type: str  # "function" or "agent"
    schedule: str = ""  # unified grammar — every:/cron:/at:
    interval: int = 0  # legacy seconds-only field; auto-normalized to schedule
    callable: str | None = None  # dotted Python path for function type
    command: str | None = None  # shell command for agent type
    enabled: bool = True
    timeout: int | None = None  # per-reflection timeout in seconds (None = use default)

    def __post_init__(self) -> None:
        """Normalize legacy ``interval=N`` to ``schedule='every: Ns'``."""
        if not self.schedule and self.interval and self.interval > 0:
            self.schedule = f"every: {self.interval}s"
        # If schedule was provided in `every:` form, keep ``interval`` populated
        # so the existing stale-detection logic (``state.ran_at + 2 * interval``)
        # continues to work without a separate code path.
        if self.schedule and not self.interval:
            try:
                self.interval = self._derive_interval_seconds(self.schedule)
            except ValueError:
                # Cron / at: schedules don't have an interval — leave 0 and let
                # `interval_seconds()` synthesize a sensible default.
                self.interval = 0

    @staticmethod
    def _derive_interval_seconds(schedule: str) -> int:
        """Best-effort interval estimate from a unified-grammar schedule.

        Returns the parsed seconds for ``every:``; raises for ``cron:``/``at:``.
        """
        prefix, _, body = schedule.partition(":")
        if prefix.strip().lower() != "every":
            raise ValueError("interval is only derivable from `every:` schedules")
        return parse_every_duration(body.strip())

    def interval_seconds(self) -> int:
        """Return a numeric interval (seconds) for stale-detection thresholds.

        For ``every:`` schedules this is exact. For ``cron:`` and ``at:``
        schedules this returns the per-execution-type timeout as a sensible
        upper bound — used only by the stale-running reaper.
        """
        if self.interval and self.interval > 0:
            return self.interval
        return self.effective_timeout()

    def validate(self) -> list[str]:
        """Validate this entry, returning a list of error messages."""
        errors: list[str] = []
        if not self.name:
            errors.append("name is required")
        if not self.schedule:
            errors.append("schedule is required (use every:/cron:/at:)")
        else:
            if is_legacy_interval_format(self.schedule):
                errors.append(
                    f"schedule {self.schedule!r} uses legacy `interval:` form; "
                    "rewrite to `every: Ns`"
                )
            else:
                try:
                    compute_next_due(self.schedule, last_run=None)
                except ValueError as e:
                    errors.append(f"schedule grammar error: {e}")
        if self.priority not in ("urgent", "high", "normal", "low"):
            errors.append(f"invalid priority: {self.priority}")
        if self.execution_type not in ("function", "agent"):
            errors.append(f"invalid execution_type: {self.execution_type}")
        if self.execution_type == "function" and not self.callable:
            errors.append("callable is required for execution_type: function")
        if self.execution_type == "agent" and not self.command:
            errors.append("command is required for execution_type: agent")
        if self.timeout is not None and self.timeout <= 0:
            errors.append(f"timeout must be positive, got {self.timeout}")
        return errors

    def effective_timeout(self) -> int:
        """Return the effective timeout for this reflection.

        Uses the explicit timeout if set, otherwise falls back to
        type-based defaults (30 min for function, 60 min for agent).
        """
        if self.timeout is not None:
            return self.timeout
        if self.execution_type == "agent":
            return DEFAULT_AGENT_TIMEOUT
        return DEFAULT_FUNCTION_TIMEOUT


def load_registry(path: Path | None = None) -> list[ReflectionEntry]:
    """Load and validate the reflections registry from YAML.

    Args:
        path: Path to the YAML file. Defaults to vault-first resolution:
              REFLECTIONS_YAML env var → ~/Desktop/Valor/reflections.yaml →
              config/reflections.yaml.

    Returns:
        List of validated ReflectionEntry objects. Invalid entries are
        logged and skipped.
    """
    path = path or _resolve_registry_path()
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
            raw_timeout = raw.get("timeout")
            # Unified-grammar fields: `every`, `cron`, or `at` map directly to
            # the corresponding schedule prefix; the older `schedule:` field
            # passes through verbatim. Legacy `interval: N` is normalized in
            # ReflectionEntry.__post_init__ for the migration window.
            schedule = raw.get("schedule", "") or ""
            if not schedule:
                if raw.get("every"):
                    schedule = f"every: {raw['every']}"
                elif raw.get("cron"):
                    schedule = f"cron: {raw['cron']}"
                    if raw.get("cron_tz"):
                        schedule = f"{schedule}; tz={raw['cron_tz']}"
                elif raw.get("at"):
                    schedule = f"at: {raw['at']}"

            entry = ReflectionEntry(
                name=raw.get("name", ""),
                description=raw.get("description", ""),
                schedule=schedule,
                interval=int(raw.get("interval", 0)),
                priority=raw.get("priority", "low"),
                execution_type=raw.get("execution_type", "function"),
                callable=raw.get("callable"),
                command=raw.get("command"),
                enabled=raw.get("enabled", True),
                timeout=int(raw_timeout) if raw_timeout is not None else None,
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
        dotted_path: e.g. "agent.agent_session_queue._agent_session_health_check"

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


def _latest_run_timestamp(name: str) -> float | None:
    """Return the most recent ``ReflectionRun`` timestamp for a reflection name.

    History-based fallback for ``is_reflection_due`` (burst-fire hotfix). When a
    Reflection record's ``ran_at`` has been lost — e.g. a fresh duplicate record
    spawned by ``get_or_create`` during a Redis index-rebuild window, where the
    name index is transiently empty — the record looks "never run" and an
    ``every:`` schedule would fire on every tick. ``ReflectionRun`` rows are NOT
    in ``models.__all__`` and so are never destructively rebuilt, making their
    timestamps a reliable record of when the job actually last ran.

    Returns None if no history rows exist or on any error (fail-open to the
    existing ``ran_at``-based behavior).
    """
    try:
        from models.reflection_run import ReflectionRun

        timestamps = [
            r.timestamp
            for r in ReflectionRun.query.filter(name=name)
            if isinstance(r.timestamp, (int, float)) and r.timestamp > 0
        ]
        return max(timestamps) if timestamps else None
    except Exception:
        return None


def is_reflection_due(entry: ReflectionEntry, state: Reflection, now: float) -> bool:
    """Check if a reflection is due to run.

    Delegates to the unified ``compute_next_due`` so the same parser handles
    every schedule shape (``every:``, ``cron:``, ``at:``). Falls back to the
    legacy ``state.ran_at + entry.interval`` check only when the entry has no
    schedule string and a positive interval — the migration window.

    Args:
        entry: The registry entry (carries the unified schedule).
        state: The Redis state record with last_run.
        now: Current timestamp.

    Returns:
        True if the reflection should be enqueued.
    """
    # Guard against Popoto returning the Field descriptor when value is None.
    ran_at = state.ran_at if isinstance(state.ran_at, (int, float)) else None

    if entry.schedule:
        # Burst-fire guard: a blank ``every:`` record (ran_at lost during an
        # index-rebuild race) would be treated as "never run" and fire on every
        # tick. Recover the true last-run from ReflectionRun history so the job
        # stays suppressed until its real interval elapses. Scoped to ``every:``
        # because ``cron:`` anchors on ``now`` (never immediately-due on a blank
        # record) and ``at:`` is a one-shot.
        if ran_at is None and entry.schedule.partition(":")[0].strip().lower() == "every":
            ran_at = _latest_run_timestamp(entry.name)
        try:
            next_due = compute_next_due(entry.schedule, last_run=ran_at, now=now)
        except ValueError as e:
            logger.warning(
                "[reflection] %s has invalid schedule %r: %s",
                entry.name,
                entry.schedule,
                e,
            )
            return False
        return next_due <= now

    # Legacy fallback (pre-migration entries with `interval:` only).
    if ran_at is None:
        return True
    return (ran_at + entry.interval) <= now


def is_reflection_running(state: Reflection) -> bool:
    """Check if a reflection is currently running (skip-if-running guard).

    Args:
        state: The Redis state record

    Returns:
        True if the reflection is currently marked as running.
    """
    return state.last_status == "running"


async def execute_function_reflection(entry: ReflectionEntry) -> Any:
    """Execute a function-type reflection by calling its Python callable.

    Handles both sync and async callables. Captures and returns the callable's
    return value so per-project audits can surface their {projects: [...]}
    breakdown to ``run_reflection`` for ``mark_completed(projects=...)``.

    Args:
        entry: The registry entry with the callable path.

    Returns:
        Whatever the underlying callable returns. For per-project audits
        this is a dict with a ``"projects"`` key; for legacy single-repo
        callables that return ``None`` it is ``None`` (safely ignored).
    """
    func = _resolve_callable(entry.callable)

    if inspect.iscoroutinefunction(func):
        return await func()
    else:
        # Run sync functions in a thread to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func)


def _get_memory_rss() -> int | None:
    """Return current process RSS in bytes, or None if psutil unavailable."""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss
    except Exception:
        return None


async def run_reflection(entry: ReflectionEntry, state: Reflection) -> None:
    """Execute a single reflection and update its state.

    Includes memory instrumentation (before/after RSS snapshots) and
    timeout enforcement via asyncio.wait_for().

    Args:
        entry: The registry entry describing the reflection
        state: The Redis state record to update
    """
    logger.info("[reflection] Starting: %s (%s)", entry.name, entry.execution_type)
    state.mark_started()

    # Memory snapshot before execution
    mem_before = _get_memory_rss()
    if mem_before is not None:
        logger.info(
            "[reflection] Memory before %s: %.1fMB",
            entry.name,
            mem_before / (1024 * 1024),
        )

    timeout = entry.effective_timeout()
    start_time = time.time()
    try:
        if entry.execution_type == "function":
            # Wrap in asyncio.wait_for for timeout enforcement
            # Note: for sync callables in run_in_executor, wait_for raises
            # TimeoutError but cannot cancel the thread (detection-only).
            # For async callables, cancellation works correctly.
            result = await asyncio.wait_for(
                execute_function_reflection(entry),
                timeout=timeout,
            )
        else:
            # Agent-type reflections are enqueued to the session queue
            # instead of executed directly
            result = await asyncio.wait_for(
                _enqueue_agent_reflection(entry),
                timeout=timeout,
            )

        duration = time.time() - start_time
        # Per-project audits return {projects: [...]}; non-audit callables
        # return None (or non-dict). Guard with isinstance to keep legacy
        # callables fully backward-compatible.
        projects_list = result.get("projects") if isinstance(result, dict) else None
        state.mark_completed(duration, projects=projects_list)
        logger.info(
            "[reflection] Completed: %s (%.1fs)",
            entry.name,
            duration,
        )

        # Auto-delete one-shot ``at:`` reflections after a successful run
        # (Q2 cycle-4 fix). Failed one-shots are preserved for diagnosis.
        if state.auto_delete_after_run and (entry.schedule or "").strip().lower().startswith("at:"):
            logger.info(
                "[reflection] Auto-deleting one-shot reflection %s after success", entry.name
            )
            try:
                state.delete()
            except Exception as e:
                logger.warning("[reflection] auto-delete failed for %s: %s", entry.name, e)
    except TimeoutError:
        duration = time.time() - start_time
        error_msg = f"TimeoutError: reflection '{entry.name}' exceeded {timeout}s timeout"
        state.mark_completed(duration, error=error_msg)
        logger.error(
            "[reflection] Timeout: %s after %.1fs (limit: %ds)",
            entry.name,
            duration,
            timeout,
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
    finally:
        # Memory snapshot after execution
        mem_after = _get_memory_rss()
        if mem_before is not None and mem_after is not None:
            delta = mem_after - mem_before
            delta_mb = delta / (1024 * 1024)
            logger.info(
                "[reflection] Memory after %s: %.1fMB (delta: %+.1fMB)",
                entry.name,
                mem_after / (1024 * 1024),
                delta_mb,
            )
            if delta > MEMORY_DELTA_WARNING_BYTES:
                logger.warning(
                    "[reflection] HIGH MEMORY DELTA for %s: %+.1fMB (before=%.1fMB, after=%.1fMB)",
                    entry.name,
                    delta_mb,
                    mem_before / (1024 * 1024),
                    mem_after / (1024 * 1024),
                )


async def _enqueue_agent_reflection(entry: ReflectionEntry) -> None:
    """Enqueue an agent-type reflection as a PM session in the session queue.

    Agent-type reflections use the `command` field as a natural-language prompt
    sent to a PM session. The session runs asynchronously; this function returns
    once the session is enqueued (not when it completes).

    Args:
        entry: The registry entry with the command (prompt) to enqueue.
    """
    import os

    from agent.agent_session_queue import _push_agent_session
    from bridge.utc import utc_now

    if not entry.command:
        logger.error("[reflection] Agent reflection '%s' has no command", entry.name)
        return

    project_root = Path(__file__).parent.parent

    # Note: SENTRY_AUTH_TOKEN is injected by sdk_client.py at PM session launch
    # time (line ~1031), so no env setup is needed here — _push_agent_session()
    # only creates a Redis entry; the worker handles env injection later.

    # Resolve project key from the repo root.
    # On this machine project_root=~/src/ai reliably matches the 'valor' key
    # in projects.json, so the fallback is defensive-only. We catch the new
    # typed errors specifically (instead of a blanket Exception) so any other
    # failure surfaces as a crash rather than being silently swallowed.
    try:
        from tools.valor_session import (
            ProjectKeyResolutionError,
            ProjectsConfigUnavailableError,
            resolve_project_key,
        )

        project_key = resolve_project_key(str(project_root))
    except (ProjectKeyResolutionError, ProjectsConfigUnavailableError) as e:
        logger.warning("[reflection] could not resolve project_key via projects.json: %s", e)
        project_key = os.environ.get("PROJECT_KEY", "valor")

    ts_suffix = str(int(utc_now().timestamp() * 1000))
    session_id = f"0_{ts_suffix}"

    await _push_agent_session(
        project_key=project_key,
        session_id=session_id,
        working_dir=str(project_root),
        message_text=entry.command.strip(),
        sender_name=f"reflection ({entry.name})",
        chat_id="0",
        telegram_message_id=0,
        session_type="pm",
    )
    logger.info(
        "[reflection] Enqueued agent reflection '%s' as session %s",
        entry.name,
        session_id,
    )


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
                state = Reflection.get_or_create(entry.name, schedule=entry.schedule)

                # Paused reflections are skipped BEFORE due-checking (Q6).
                if state.is_paused(now=now):
                    logger.debug(
                        "[reflection] Skipping %s: paused until %s",
                        entry.name,
                        state.paused_until,
                    )
                    continue

                # Skip if already running
                if is_reflection_running(state):
                    interval = entry.interval_seconds()
                    if state.ran_at and (now - state.ran_at) > (interval * 2):
                        logger.warning(
                            "[reflection] %s appears stuck (running for %.0fs, interval=%ds). "
                            "Resetting status.",
                            entry.name,
                            now - state.ran_at,
                            interval,
                        )
                        state.last_status = "error"
                        state.last_error = "Reset: appeared stuck (exceeded 2x interval)"
                        state.save()
                    else:
                        logger.debug("[reflection] Skipping %s: already running", entry.name)
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
                    # Agent-type reflections are enqueued to session queue
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

    def reap_stale_running(self) -> int:
        """Force-mark Reflection records that have been ``last_status="running"`` past
        a sane threshold (Q6 / Race 2 cycle-4 fix).

        Called once at worker startup, after ``register_worker_pid`` and before the
        first scheduler tick. A stale "running" record is one whose ``ran_at`` is
        older than ``max(2 * entry.interval_seconds(), entry.effective_timeout())``.

        Returns the number of records reaped.
        """
        now = time.time()
        reaped = 0
        for entry in self._entries:
            try:
                rows = list(Reflection.query.filter(name=entry.name))
                if not rows:
                    continue
                state = rows[0]
                if state.last_status != "running":
                    continue
                ran_at = state.ran_at if isinstance(state.ran_at, (int, float)) else None
                if ran_at is None:
                    continue
                threshold = max(
                    2 * entry.interval_seconds(),
                    entry.effective_timeout(),
                )
                if (now - ran_at) <= threshold:
                    continue
                logger.warning(
                    "[reflection] Reaping stale-running record %s "
                    "(ran_at=%.0fs ago, threshold=%ds).",
                    entry.name,
                    now - ran_at,
                    threshold,
                )
                state.last_status = "stale_running"
                state.last_error = "stale running status cleared on worker restart"
                state.failure_count_consecutive = (state.failure_count_consecutive or 0) + 1
                state.save()
                reaped += 1
            except Exception as e:
                logger.error(
                    "[reflection] reap_stale_running failed for %s: %s",
                    entry.name,
                    e,
                    exc_info=True,
                )
        if reaped:
            logger.info("[reflection] reap_stale_running cleared %d stale record(s)", reaped)
        return reaped

    async def start(self) -> None:
        """Start the scheduler loop. Runs forever, ticking every SCHEDULER_TICK_INTERVAL seconds."""
        if self._started:
            logger.warning("[reflection] Scheduler already started")
            return

        self._started = True
        self.load()
        # Reap any reflections that were left in ``last_status="running"`` at
        # the previous worker exit. Idempotent across restarts.
        try:
            self.reap_stale_running()
        except Exception as e:
            logger.error("[reflection] reap_stale_running on startup failed: %s", e)
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
                if state.ran_at:
                    next_due = state.ran_at + entry.interval
                    time_until_due = max(0, next_due - now)

                statuses.append(
                    {
                        "name": entry.name,
                        "description": entry.description,
                        "interval": entry.interval,
                        "priority": entry.priority,
                        "execution_type": entry.execution_type,
                        "last_run": state.ran_at,
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
