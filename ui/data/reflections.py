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
    state (ran_at, status, run_count, etc.). Computes next_due from
    ran_at + config interval (not stored as a field).

    Returns:
        List of dicts with merged config + state for each reflection.
    """
    from models.reflection import Reflection

    registry = _get_registry_map()
    states = {r.name: r for r in Reflection.get_all_states() if r.name}

    now = time.time()
    reflections = []
    for name, config in registry.items():
        state = states.get(name)
        interval = config.get("interval", 0)

        # Compute next_due from ran_at + interval (not stored as a field)
        next_due = None
        if state and state.ran_at and interval:
            next_due = state.ran_at + interval

        due_in_seconds = (next_due - now) if next_due else None
        reflections.append(
            {
                "name": name,
                "description": config.get("description", ""),
                "interval": interval,
                "priority": config.get("priority", "normal"),
                "enabled": config.get("enabled", True),
                "execution_type": config.get("execution_type", "unknown"),
                "last_run": state.ran_at if state else None,
                "next_due": next_due,
                "due_in_seconds": due_in_seconds,
                "overdue": due_in_seconds < 0 if due_in_seconds is not None else False,
                "run_count": state.run_count if state else 0,
                "last_status": state.last_status if state else "pending",
                "last_error": state.last_error if state else None,
                "last_duration": state.last_duration if state else None,
                "has_history": bool(
                    state and isinstance(state.run_history, list) and state.run_history
                ),
            }
        )

    # Sort: entries with next_due first (soonest first), then entries without
    with_due = [r for r in reflections if r["next_due"] is not None]
    without_due = [r for r in reflections if r["next_due"] is None]
    with_due.sort(key=lambda r: r["next_due"])

    return with_due + without_due


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

    return run
