"""Data access layer for the Reflections dashboard.

All functions are synchronous (def, not async def) because Popoto uses
synchronous Redis calls. FastAPI runs sync route handlers in a threadpool,
which avoids blocking the event loop.
"""

import logging
import math
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
RUNS_PER_PAGE = 20


def _load_registry() -> list[dict]:
    """Load the reflections registry from config/reflections.yaml."""
    try:
        with open(REGISTRY_PATH) as f:
            data = yaml.safe_load(f)
        return data.get("reflections", []) if data else []
    except Exception as e:
        logger.warning(f"Failed to load reflections registry: {e}")
        return []


def _get_registry_map() -> dict[str, dict]:
    """Return a dict mapping reflection name -> registry entry."""
    return {r["name"]: r for r in _load_registry() if "name" in r}


def get_all_reflections() -> list[dict]:
    """Get all registered reflections with their current state from Redis.

    Merges registry config (description, interval, etc.) with live Redis
    state (last_run, status, run_count, etc.).

    Returns:
        List of dicts with merged config + state for each reflection.
    """
    from models.reflection import Reflection

    registry = _get_registry_map()
    states = {r.name: r for r in Reflection.get_all_states() if r.name}

    reflections = []
    for name, config in registry.items():
        state = states.get(name)
        reflections.append(
            {
                "name": name,
                "description": config.get("description", ""),
                "interval": config.get("interval", 0),
                "priority": config.get("priority", "normal"),
                "enabled": config.get("enabled", True),
                "execution_type": config.get("execution_type", "unknown"),
                "last_run": state.last_run if state else None,
                "next_due": state.next_due if state else None,
                "run_count": state.run_count if state else 0,
                "last_status": state.last_status if state else "pending",
                "last_error": state.last_error if state else None,
                "last_duration": state.last_duration if state else None,
                "has_history": bool(
                    state and isinstance(state.run_history, list) and state.run_history
                ),
            }
        )

    return reflections


def get_schedule() -> list[dict]:
    """Get reflections ordered by next-due timestamp.

    Returns:
        List of dicts sorted by next_due (soonest first). Entries
        with no next_due are placed at the end.
    """
    reflections = get_all_reflections()
    now = time.time()

    for r in reflections:
        if r["next_due"]:
            r["due_in_seconds"] = r["next_due"] - now
            r["overdue"] = r["due_in_seconds"] < 0
        else:
            r["due_in_seconds"] = None
            r["overdue"] = False

    # Sort: entries with next_due first (by time), then entries without
    with_due = [r for r in reflections if r["next_due"] is not None]
    without_due = [r for r in reflections if r["next_due"] is None]
    with_due.sort(key=lambda r: r["next_due"])

    return with_due + without_due


def get_active_ignores() -> list[dict]:
    """Get all active (non-expired) ignore patterns.

    Returns:
        List of dicts with pattern, reason, created_at, expires_at, and
        time_remaining fields.
    """
    from models.reflections import ReflectionIgnore

    now = time.time()
    ignores = []
    for entry in ReflectionIgnore.get_active():
        ignores.append(
            {
                "pattern": entry.pattern,
                "reason": entry.reason,
                "created_at": entry.created_at,
                "expires_at": entry.expires_at,
                "time_remaining": entry.expires_at - now if entry.expires_at else 0,
            }
        )

    # Sort by expiry (soonest first)
    ignores.sort(key=lambda x: x["expires_at"] or 0)
    return ignores


def get_run_history(name: str, page: int = 1) -> dict:
    """Get paginated run history for a specific reflection.

    Args:
        name: Reflection name
        page: Page number (1-indexed)

    Returns:
        Dict with 'runs' (list of run dicts, newest first),
        'total_pages', and 'total_runs'.
    """
    from models.reflection import Reflection

    states = Reflection.query.filter(name=name)
    if not states:
        return {"runs": [], "total_pages": 1, "total_runs": 0}

    state = states[0]
    history = state.run_history if isinstance(state.run_history, list) else []

    # Reverse to show newest first
    history = list(reversed(history))
    total_runs = len(history)
    total_pages = max(1, math.ceil(total_runs / RUNS_PER_PAGE))

    # Paginate
    start = (page - 1) * RUNS_PER_PAGE
    end = start + RUNS_PER_PAGE
    page_runs = history[start:end]

    # Add index for detail links (original index in forward order)
    for i, run in enumerate(page_runs):
        run["index"] = total_runs - 1 - (start + i)

    return {
        "runs": page_runs,
        "total_pages": total_pages,
        "total_runs": total_runs,
    }


def get_run_detail(name: str, run_index: int) -> dict | None:
    """Get detail for a specific run by index.

    Args:
        name: Reflection name
        run_index: Zero-based index into run_history (forward order)

    Returns:
        Run dict with full details, or None if not found.
    """
    from models.reflection import Reflection

    states = Reflection.query.filter(name=name)
    if not states:
        return None

    state = states[0]
    history = state.run_history if isinstance(state.run_history, list) else []

    if run_index < 0 or run_index >= len(history):
        return None

    run = dict(history[run_index])
    run["index"] = run_index
    run["name"] = name

    # Try to read log content if log_path is set
    log_path = run.get("log_path")
    if log_path:
        run["log_content"] = _read_log_file(log_path)
    else:
        run["log_content"] = None

    return run


def get_log_content(name: str, run_index: int) -> str:
    """Get log file content for a specific run.

    Args:
        name: Reflection name
        run_index: Zero-based index into run_history

    Returns:
        Log file content string, or an error/not-found message.
    """
    run = get_run_detail(name, run_index)
    if not run:
        return "Run not found."

    if run.get("log_content"):
        return run["log_content"]

    log_path = run.get("log_path")
    if not log_path:
        return "No log file associated with this run."

    return _read_log_file(log_path)


def _read_log_file(path: str) -> str:
    """Safely read a log file, returning an error message if unavailable."""
    try:
        p = Path(path)
        if not p.exists():
            return f"Log file not found: {path}"
        content = p.read_text(errors="replace")
        # Cap at 100KB to avoid huge responses
        if len(content) > 100_000:
            content = content[:100_000] + "\n\n... (truncated at 100KB)"
        return content
    except Exception as e:
        return f"Error reading log file: {e}"
