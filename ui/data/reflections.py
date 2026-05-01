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

GROUP_AGENTS = "agents"
GROUP_HOUSEKEEPING = "housekeeping"
GROUP_AUDITS = "audits"
GROUP_MEMORY = "memory"

GROUP_DISPLAY_ORDER = [GROUP_AGENTS, GROUP_HOUSEKEEPING, GROUP_AUDITS, GROUP_MEMORY]

GROUP_DESCRIPTIONS: dict[str, str] = {
    GROUP_AGENTS: "Liveness, throttling, circuit health, and recovery for agent sessions",
    GROUP_HOUSEKEEPING: "Redis cleanup, disk checks, analytics rollup, and branch pruning",
    GROUP_AUDITS: "Daily review of code, docs, hooks, skills, and Sentry issues",
    GROUP_MEMORY: "Deduplication, decay pruning, quality audits, and knowledge reindexing",
}

REFLECTION_GROUPS: dict[str, str] = {
    "session-liveness-check": GROUP_AGENTS,
    "agent-session-cleanup": GROUP_AGENTS,
    "circuit-health-gate": GROUP_AGENTS,
    "session-count-throttle": GROUP_AGENTS,
    "failure-loop-detector": GROUP_AGENTS,
    "session-recovery-drip": GROUP_AGENTS,
    "session-intelligence": GROUP_AGENTS,
    "system-health-digest": GROUP_AGENTS,
    "pm-audio-briefing": GROUP_AGENTS,
    "redis-index-cleanup": GROUP_HOUSEKEEPING,
    "redis-ttl-cleanup": GROUP_HOUSEKEEPING,
    "disk-space-check": GROUP_HOUSEKEEPING,
    "analytics-rollup": GROUP_HOUSEKEEPING,
    "merged-branch-cleanup": GROUP_HOUSEKEEPING,
    "stale-branch-cleanup": GROUP_HOUSEKEEPING,
    "behavioral-learning": GROUP_HOUSEKEEPING,
    "tech-debt-scan": GROUP_AUDITS,
    "redis-quality-audit": GROUP_AUDITS,
    "daily-log-review": GROUP_AUDITS,
    "skills-audit": GROUP_AUDITS,
    "hooks-audit": GROUP_AUDITS,
    "docs-auditor": GROUP_AUDITS,
    "do-docs-branch-sweeper": GROUP_HOUSEKEEPING,
    "pr-review-audit": GROUP_AUDITS,
    "task-backlog-check": GROUP_AUDITS,
    "principal-staleness": GROUP_AUDITS,
    "sentry-issue-triage": GROUP_AUDITS,
    "daily-report-and-notify": GROUP_AUDITS,
    "memory-dedup": GROUP_MEMORY,
    "memory-decay-prune": GROUP_MEMORY,
    "memory-quality-audit": GROUP_MEMORY,
}

# Prefix-expanded reflections render per-project rows on the dashboard.
# These reflections write Reflection records named `<prefix>-<project_key>`
# (e.g. `pm-audio-briefing-psyoptimal`). The dashboard surfaces the parent
# registry entry AND each per-project record, all classified into the parent's
# group. Hard-coded as a tuple at module-load so there is exactly one place to
# update when adding a new prefix-expanded reflection (per plan C1-R2).
_PREFIX_EXPANDED_REFLECTIONS: tuple[str, ...] = ("pm-audio-briefing",)


def _classify_group(name: str) -> str:
    return REFLECTION_GROUPS.get(name, GROUP_HOUSEKEEPING)


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


def _build_entry(name: str, config: dict, state, now: float) -> dict:
    """Build a single dashboard row from registry config + live Redis state."""
    interval = config.get("interval", 0)

    # Compute next_due from ran_at + interval (not stored as a field)
    # Guard against Popoto returning the Field descriptor when value is None
    ran_at = state.ran_at if state else None
    if not isinstance(ran_at, (int, float)):
        ran_at = None
    next_due = None
    if ran_at and interval:
        next_due = ran_at + interval

    due_in_seconds = (next_due - now) if next_due else None
    return {
        "name": name,
        "group": _classify_group(name),
        "description": config.get("description", ""),
        "interval": interval,
        "priority": config.get("priority", "normal"),
        "enabled": config.get("enabled", True),
        "execution_type": config.get("execution_type", "unknown"),
        "callable": config.get("callable"),
        "command": config.get("command"),
        "last_run": ran_at,
        "next_due": next_due,
        "due_in_seconds": due_in_seconds,
        "overdue": due_in_seconds < 0 if due_in_seconds is not None else False,
        "run_count": state.run_count if state else 0,
        "last_status": state.last_status if state else "pending",
        "last_error": state.last_error if state else None,
        "last_duration": state.last_duration if state else None,
        "has_history": bool(state and isinstance(state.run_history, list) and state.run_history),
    }


def _expand_prefix_records(
    registry: dict[str, dict], states: dict[str, "object"], now: float
) -> list[dict]:
    """Return per-project rows for any record matching a prefix-expanded name.

    A record like `pm-audio-briefing-psyoptimal` is rendered using its parent
    registry entry's description, interval, and group classification, but with
    the per-project name and live state. The parent entry's group is reused so
    per-project rows cluster with the parent on the dashboard.
    """
    rows: list[dict] = []
    for prefix in _PREFIX_EXPANDED_REFLECTIONS:
        parent = registry.get(prefix)
        if parent is None:
            # Parent entry isn't registered (e.g. removed from YAML) -- skip
            # to avoid surfacing orphan per-project rows without context.
            continue
        needle = f"{prefix}-"
        for record_name, state in states.items():
            if not record_name.startswith(needle) or record_name == prefix:
                continue
            row = _build_entry(record_name, parent, state, now)
            # Per-project rows reuse the parent's group classification.
            row["group"] = _classify_group(prefix)
            rows.append(row)
    return rows


def get_all_reflections() -> list[dict]:
    """Get all registered reflections with their current state from Redis.

    Merges registry config (description, interval, etc.) with live Redis
    state (ran_at, status, run_count, etc.). Computes next_due from
    ran_at + config interval (not stored as a field).

    Per-project records whose name starts with a prefix in
    `_PREFIX_EXPANDED_REFLECTIONS` (e.g. `pm-audio-briefing-psyoptimal`) are
    appended as additional rows using the parent entry's config.

    Returns:
        List of dicts with merged config + state for each reflection.
    """
    from models.reflection import Reflection

    registry = _get_registry_map()
    states = {r.name: r for r in Reflection.get_all_states() if r.name}

    now = time.time()
    reflections = [
        _build_entry(name, config, states.get(name), now) for name, config in registry.items()
    ]
    # Append per-project rows for any prefix-expanded reflection.
    reflections.extend(_expand_prefix_records(registry, states, now))

    # Sort: entries with next_due first (soonest first), then entries without
    with_due = [r for r in reflections if r["next_due"] is not None]
    without_due = [r for r in reflections if r["next_due"] is None]
    with_due.sort(key=lambda r: r["next_due"])

    return with_due + without_due


def get_grouped_reflections() -> list[dict]:
    """Return reflections bucketed by group, in display order.

    Empty groups are omitted. Within each group, the existing sort from
    `get_all_reflections()` is preserved (soonest next_due first, then
    entries without next_due).

    Returns:
        List of {"group": str, "items": [reflection, ...]} dicts.
    """
    reflections = get_all_reflections()
    by_group: dict[str, list[dict]] = {g: [] for g in GROUP_DISPLAY_ORDER}
    for r in reflections:
        by_group.setdefault(r["group"], []).append(r)
    groups = []
    for g in GROUP_DISPLAY_ORDER:
        items = by_group.get(g) or []
        if not items:
            continue
        cadence_set = sorted({r["interval"] for r in items if r.get("interval")})
        groups.append(
            {
                "group": g,
                "description": GROUP_DESCRIPTIONS.get(g, ""),
                "reflections": items,
                "count": len(items),
                "cadences": cadence_set,
                "any_error": any(
                    r["enabled"] and r["last_status"] in ("error", "failed") for r in items
                ),
                "off_count": sum(1 for r in items if not r["enabled"]),
            }
        )
    return groups


def get_reflection_detail(name: str) -> dict | None:
    """Return a single reflection's merged config + state, or None."""
    for r in get_all_reflections():
        if r["name"] == name:
            return r
    return None


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
