"""Data access layer for the Reflections dashboard.

All functions are synchronous (def, not async def) because Popoto uses
synchronous Redis calls. FastAPI runs sync route handlers in a threadpool,
which avoids blocking the event loop.
"""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"


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
