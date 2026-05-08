"""Reflection Scheduler - unified scheduling for all recurring non-issue work.

Replaces the scattered scheduling mechanisms (launchd plists, asyncio loops,
startup hooks) with a single lightweight scheduler that reads from
config/reflections.yaml and enqueues due reflections as sessions.

Architecture:
- Registry: config/reflections.yaml declares all reflections
- State: models/reflection.py (Popoto/Redis) tracks last_run, next_due, etc.
- Scheduler: This module - asyncio loop that ticks every 60s
- Execution: Two modes - function (direct callable) and agent (full session)

Schedule grammar (fazm-style triplet, see Q2 of the unify-recurring-tasks plan):
- ``cron:<expr>``  — standard 5-field cron (timezone via ``cron_tz`` field).
- ``every:<N><suffix>`` — interval, suffix is ``s``/``m``/``h``/``d``.
- ``at:<ISO8601>`` — one-shot fire-once schedule.

See docs/features/reflections.md for full documentation.
"""

import asyncio
import importlib
import inspect
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

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


_DURATION_SUFFIX_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def _parse_every(expr: str) -> int:
    """Parse ``<N><suffix>`` (e.g. ``60s``, ``5m``) into seconds.

    Raises ``ValueError`` on malformed input.
    """
    expr = expr.strip()
    if not expr:
        raise ValueError("every: expression is empty")
    suffix = expr[-1].lower()
    if suffix not in _DURATION_SUFFIX_SECONDS:
        raise ValueError(f"every: suffix must be one of s/m/h/d, got '{suffix}' in '{expr}'")
    head = expr[:-1]
    try:
        n = int(head)
    except ValueError as e:
        raise ValueError(f"every: numeric prefix must be int, got '{head}'") from e
    if n <= 0:
        raise ValueError(f"every: value must be positive, got {n}")
    return n * _DURATION_SUFFIX_SECONDS[suffix]


def _resolve_tz(cron_tz: str):
    """Return a tzinfo for the given timezone name (UTC fallback)."""
    if not cron_tz or cron_tz.upper() == "UTC":
        return UTC
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(cron_tz)
    except Exception:
        logger.warning("Unknown cron_tz '%s', falling back to UTC", cron_tz)
        return UTC


def compute_next_due(schedule_str: str, last_run: float | None, cron_tz: str = "UTC") -> float:
    """Compute the next-fire Unix timestamp for a schedule string.

    Args:
        schedule_str: Schedule in fazm grammar:
            - ``cron:<expr>`` (5-field cron)
            - ``every:<N><suffix>`` (suffix s/m/h/d)
            - ``at:<ISO8601>`` (one-shot)
        last_run: Unix timestamp of the previous fire, or ``None`` if never.
        cron_tz: IANA timezone name for ``cron:`` schedules. Ignored otherwise.

    Returns:
        Unix timestamp of the next fire after ``last_run`` (or after now if
        last_run is None). Returns ``math.inf`` for one-shot ``at:`` schedules
        whose fire time has already passed.

    Raises:
        ValueError: For empty/unknown schedule strings, including legacy
        ``interval:`` entries that should have been migrated.
    """
    if not schedule_str or not isinstance(schedule_str, str):
        raise ValueError(f"schedule must be a non-empty string, got {schedule_str!r}")

    schedule_str = schedule_str.strip()
    now = time.time()
    base = last_run if (isinstance(last_run, (int, float)) and last_run) else now

    if schedule_str.startswith("cron:"):
        from croniter import croniter

        expr = schedule_str[len("cron:") :].strip()
        if not expr:
            raise ValueError("cron: expression is empty")
        if not croniter.is_valid(expr):
            raise ValueError(f"cron: invalid expression '{expr}'")
        tz = _resolve_tz(cron_tz)
        base_dt = datetime.fromtimestamp(base, tz=tz)
        itr = croniter(expr, base_dt)
        nxt = itr.get_next(datetime)
        return nxt.timestamp()

    if schedule_str.startswith("every:"):
        seconds = _parse_every(schedule_str[len("every:") :])
        if last_run is None or not last_run:
            return now
        return float(last_run) + seconds

    if schedule_str.startswith("at:"):
        iso = schedule_str[len("at:") :].strip()
        if not iso:
            raise ValueError("at: ISO timestamp is empty")
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError as e:
            raise ValueError(f"at: invalid ISO8601 '{iso}': {e}") from e
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        target_ts = dt.timestamp()
        # One-shot: if it's already fired (last_run set) OR target is in the past,
        # never fire again.
        if last_run is not None and last_run > 0:
            return math.inf
        if target_ts <= now:
            return math.inf
        return target_ts

    if schedule_str.startswith("interval:"):
        raise ValueError(
            f"legacy 'interval:' schedule no longer supported: {schedule_str!r}; "
            "migrate to 'every:<N>s' (see scripts/migrate_reflections_yaml.py)"
        )

    raise ValueError(
        f"unknown schedule prefix in {schedule_str!r}; expected one of 'cron:', 'every:', 'at:'"
    )


def compute_interval_seconds(schedule_str: str) -> int:
    """Estimate the typical interval between fires, in seconds.

    Used by the stale-running reaper to derive a 2× threshold. For ``at:``
    one-shots returns 0. For ``cron:`` estimates from the delta between the
    next two fires.
    """
    if not schedule_str:
        return 0
    schedule_str = schedule_str.strip()

    if schedule_str.startswith("every:"):
        try:
            return _parse_every(schedule_str[len("every:") :])
        except ValueError:
            return 0

    if schedule_str.startswith("cron:"):
        from croniter import croniter

        expr = schedule_str[len("cron:") :].strip()
        if not croniter.is_valid(expr):
            return 0
        try:
            base = datetime.now(tz=UTC)
            itr = croniter(expr, base)
            t1 = itr.get_next(datetime)
            t2 = itr.get_next(datetime)
            return max(0, int((t2 - t1).total_seconds()))
        except Exception:
            return 0

    if schedule_str.startswith("at:"):
        return 0

    return 0


@dataclass
class ReflectionEntry:
    """A parsed reflection declaration from the registry."""

    name: str
    description: str
    schedule: str  # cron:<expr> | every:<N><suffix> | at:<ISO>
    priority: str  # urgent | high | normal | low
    execution_type: str  # "function" or "agent"
    callable: str | None = None  # dotted Python path for function type
    command: str | None = None  # shell command for agent type
    enabled: bool = True
    timeout: int | None = None  # per-reflection timeout (None = use default)
    cron_tz: str = "UTC"
    output_sink: str = "log_only"

    def validate(self) -> list[str]:
        """Validate this entry, returning a list of error messages."""
        errors = []
        if not self.name:
            errors.append("name is required")
        if not self.schedule:
            errors.append("schedule is required (cron:/every:/at:)")
        else:
            try:
                compute_next_due(self.schedule, None, self.cron_tz)
            except ValueError as e:
                errors.append(f"invalid schedule: {e}")
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
        """Return the effective timeout for this reflection."""
        if self.timeout is not None:
            return self.timeout
        if self.execution_type == "agent":
            return DEFAULT_AGENT_TIMEOUT
        return DEFAULT_FUNCTION_TIMEOUT


def load_registry(path: Path | None = None) -> list[ReflectionEntry]:
    """Load and validate the reflections registry from YAML.

    Rejects legacy ``interval:`` entries with a warning (the migration script
    rewrites them to ``every:Ns`` during ``/update``).
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

        if "interval" in raw and "schedule" not in raw:
            logger.warning(
                "Skipping legacy 'interval:' entry '%s' — run "
                "scripts/migrate_reflections_yaml.py to upgrade to 'every:Ns'",
                raw.get("name", "?"),
            )
            continue

        try:
            raw_timeout = raw.get("timeout")
            entry = ReflectionEntry(
                name=raw.get("name", ""),
                description=raw.get("description", ""),
                schedule=str(raw.get("schedule", "")).strip(),
                priority=raw.get("priority", "low"),
                execution_type=raw.get("execution_type", "function"),
                callable=raw.get("callable"),
                command=raw.get("command"),
                enabled=raw.get("enabled", True),
                timeout=int(raw_timeout) if raw_timeout is not None else None,
                cron_tz=str(raw.get("cron_tz", "UTC")),
                output_sink=str(raw.get("output_sink", "log_only")),
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
    """Resolve a dotted Python path to a callable."""
    parts = dotted_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ImportError(f"Invalid callable path: {dotted_path}")
    module_path, func_name = parts
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def is_reflection_due(entry: ReflectionEntry, state: Reflection, now: float) -> bool:
    """Check if a reflection is due to run.

    Paused reflections (``paused_until > now``) are NEVER due — caller is
    expected to mark them skipped via ``state.mark_skipped("paused")``.
    """
    paused_until = state.paused_until if isinstance(state.paused_until, (int, float)) else 0.0
    if paused_until and paused_until > now:
        return False

    ran_at = state.ran_at if isinstance(state.ran_at, (int, float)) else None
    try:
        next_due = compute_next_due(entry.schedule, ran_at, entry.cron_tz)
    except ValueError as e:
        logger.warning("[reflection] '%s' schedule unparseable, skipping: %s", entry.name, e)
        return False

    if next_due == math.inf:
        return False
    return next_due <= now


def is_reflection_running(state: Reflection) -> bool:
    """Check if a reflection is currently running (skip-if-running guard)."""
    return state.last_status == "running"


def reap_stale_running() -> int:
    """Force-clear ``last_status='running'`` for any stuck reflection.

    Called once at worker startup (after ``register_worker_pid``, before the
    first scheduler tick) to recover from prior-process crashes.

    For each Reflection where ``last_status == 'running'``, computes
    ``threshold = max(2 * compute_interval_seconds(schedule), last_duration or 1800)``.
    If ``ran_at`` is older than ``now - threshold``, sets
    ``last_status='stale_running'``, writes a clear ``last_error``, and
    increments ``failure_count_consecutive`` by 1.

    Returns:
        Number of records reaped.
    """
    now = time.time()
    reaped = 0
    try:
        records = list(Reflection.query.all())
    except Exception as e:
        logger.error("[reflection] reap_stale_running: query failed: %s", e)
        return 0

    for state in records:
        try:
            if state.last_status != "running":
                continue
            ran_at = state.ran_at if isinstance(state.ran_at, (int, float)) else None
            if not ran_at:
                continue
            schedule = state.schedule if isinstance(state.schedule, str) else ""
            interval = compute_interval_seconds(schedule)
            last_duration = (
                state.last_duration
                if isinstance(state.last_duration, (int, float)) and state.last_duration
                else 0
            )
            threshold = max(2 * interval, int(last_duration) if last_duration else 1800)
            if (now - ran_at) <= threshold:
                continue
            state.last_status = "stale_running"
            state.last_error = "stale running status cleared on worker startup"
            state.failure_count_consecutive = (state.failure_count_consecutive or 0) + 1
            state.save()
            reaped += 1
        except Exception as e:
            logger.warning(
                "[reflection] reap_stale_running: error on '%s': %s",
                getattr(state, "name", "?"),
                e,
            )

    logger.info("[reflection] reaped %d stale running record(s)", reaped)
    return reaped


async def execute_function_reflection(entry: ReflectionEntry) -> Any:
    """Execute a function-type reflection by calling its Python callable."""
    func = _resolve_callable(entry.callable)

    if inspect.iscoroutinefunction(func):
        return await func()
    else:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func)


def _get_memory_rss() -> int | None:
    """Return current process RSS in bytes, or None if psutil unavailable."""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss
    except Exception:
        return None


def _lookup_session_cost(session_id: str) -> tuple[float, int, int]:
    """Best-effort lookup of (cost_usd, tokens_input, tokens_output) for a session."""
    try:
        from models.agent_session import AgentSession

        results = AgentSession.query.filter(session_id=session_id)
        if not results:
            return 0.0, 0, 0
        s = results[0] if not hasattr(results, "first") else results.first()
        if s is None:
            return 0.0, 0, 0
        cost = float(getattr(s, "cost_usd", 0.0) or 0.0)
        ti = int(getattr(s, "tokens_input", 0) or 0)
        to = int(getattr(s, "tokens_output", 0) or 0)
        return cost, ti, to
    except Exception:
        return 0.0, 0, 0


async def run_reflection(entry: ReflectionEntry, state: Reflection) -> None:
    """Execute a single reflection and update its state.

    Includes memory instrumentation (before/after RSS snapshots) and
    timeout enforcement via asyncio.wait_for().
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
    spawned_session_id: str | None = None
    try:
        if entry.execution_type == "function":
            result = await asyncio.wait_for(
                execute_function_reflection(entry),
                timeout=timeout,
            )
        else:
            spawned_session_id = await asyncio.wait_for(
                _enqueue_agent_reflection(entry),
                timeout=timeout,
            )
            result = {"session_id": spawned_session_id} if spawned_session_id else None

        duration = time.time() - start_time
        projects_list = result.get("projects") if isinstance(result, dict) else None

        # Cost/token accounting
        cost_usd = 0.0
        tokens_input = 0
        tokens_output = 0
        if entry.execution_type == "agent" and spawned_session_id:
            cost_usd, tokens_input, tokens_output = _lookup_session_cost(spawned_session_id)

        # Stable output summary string for delivery + ReflectionRun.output_summary.
        if isinstance(result, str):
            output_str = result
        elif result is None:
            output_str = None
        else:
            try:
                import json

                output_str = json.dumps(result, default=str)[:1000]
            except Exception:
                output_str = str(result)[:1000]

        state.mark_completed(
            duration,
            projects=projects_list,
            cost_usd=cost_usd,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            output=output_str,
        )
        logger.info(
            "[reflection] Completed: %s (%.1fs)",
            entry.name,
            duration,
        )

        # Output delivery (Q5). Lazy import — Wave-2 ships agent/reflection_output.py.
        try:
            from agent.reflection_output import deliver
            from models.reflection_run import ReflectionRun

            recent = ReflectionRun.recent_for(state.name, limit=1)
            run_row = recent[0] if recent else None
            if run_row is not None:
                deliver(reflection=state, run=run_row, output=output_str)
        except ImportError:
            # Wave-2 builder hasn't shipped agent/reflection_output.py yet.
            logger.debug("[reflection] reflection_output module not available yet")
        except Exception as e:
            logger.warning("[reflection] output delivery failed for '%s': %s", entry.name, e)

        # One-shot at: schedules self-delete on success.
        if entry.schedule.startswith("at:") and getattr(state, "auto_delete_after_run", False):
            try:
                state.delete()
                logger.info(
                    "[reflection] '%s' (one-shot at:) self-deleted after success",
                    entry.name,
                )
            except Exception as e:
                logger.warning(
                    "[reflection] one-shot self-delete failed for '%s': %s",
                    entry.name,
                    e,
                )
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


async def _enqueue_agent_reflection(entry: ReflectionEntry) -> str | None:
    """Enqueue an agent-type reflection as a PM session in the session queue.

    Returns the spawned session_id (or None on failure) so the caller can
    look up cost/token totals after the session completes.
    """
    from agent.agent_session_queue import _push_agent_session
    from bridge.utc import utc_now

    if not entry.command:
        logger.error("[reflection] Agent reflection '%s' has no command", entry.name)
        return None

    project_root = Path(__file__).parent.parent

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
    return session_id


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
        """Run one scheduler tick: check all reflections and enqueue due ones."""
        now = time.time()
        enqueued = 0

        for entry in self._entries:
            try:
                # Snapshot the Reflection record once per iteration.
                state = Reflection.get_or_create(entry.name)

                # Paused-check FIRST (Q6): skip entirely while paused_until > now.
                paused_until = (
                    state.paused_until if isinstance(state.paused_until, (int, float)) else 0.0
                )
                if paused_until and paused_until > now:
                    state.mark_skipped("paused")
                    logger.debug(
                        "[reflection] Skipping %s: paused_until=%s > now",
                        entry.name,
                        paused_until,
                    )
                    continue

                # Skip if already running. Stale-running cleanup is the worker
                # startup reaper's job (see reap_stale_running()), not the tick.
                if is_reflection_running(state):
                    logger.debug("[reflection] Skipping %s: already running", entry.name)
                    continue

                if not is_reflection_due(entry, state, now):
                    continue

                logger.info("[reflection] %s is due, executing", entry.name)

                if entry.execution_type == "function":
                    task = asyncio.create_task(
                        run_reflection(entry, state),
                        name=f"reflection-{entry.name}",
                    )
                    self._running_tasks[entry.name] = task
                    task.add_done_callback(
                        lambda t, name=entry.name: self._running_tasks.pop(name, None)
                    )
                else:
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
        """Get current status of all registered reflections (used by /queue-status)."""
        now = time.time()
        statuses = []

        for entry in self._entries:
            try:
                state = Reflection.get_or_create(entry.name)
                time_until_due = None
                ran_at = state.ran_at if isinstance(state.ran_at, (int, float)) else None
                try:
                    next_due = compute_next_due(entry.schedule, ran_at, entry.cron_tz)
                    if next_due != math.inf:
                        time_until_due = max(0, next_due - now)
                except ValueError:
                    time_until_due = None

                statuses.append(
                    {
                        "name": entry.name,
                        "description": entry.description,
                        "schedule": entry.schedule,
                        "priority": entry.priority,
                        "execution_type": entry.execution_type,
                        "last_run": ran_at,
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
        """Format reflection status for display (e.g., in /queue-status)."""
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
                "stale_running": "STALE",
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
