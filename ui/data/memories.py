"""Data access layer for the Memories dashboard.

All functions are synchronous (def, not async def) because Popoto uses
synchronous Redis calls. FastAPI runs sync route handlers in a threadpool,
which avoids blocking the event loop.

The view is read-only — it never writes to Memory records. Mutation lives
in `python -m tools.memory_search`.
"""

import logging
import os
from typing import Any

from config.memory_defaults import (
    DEFAULT_PROJECT_KEY,
    DISMISSAL_DECAY_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Top-N cap protects render time on large corpora (per plan: ≤500ms render budget).
DEFAULT_LIMIT = 200

# Known categories (used for filter UI defaults). Any record category not in this
# set is folded into "default" group at render time.
KNOWN_CATEGORIES = ("correction", "decision", "pattern", "surprise", "default")


def _resolve_project_key(project_key: str | None) -> str:
    """Resolve project_key, falling back to env / default.

    Empty string and None both fall back. Mirrors
    `tools.memory_search._resolve_project_key`.
    """
    if project_key:
        return project_key
    return os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)


def _decorate_record(record: Any) -> dict:
    """Convert a Memory instance into a render-ready dict.

    Uses defensive `.get()` access because legacy records may have
    `metadata = {}` or be missing keys entirely. The template iterates
    these pre-decorated dicts, so it never sees raw `metadata`.
    """
    from agent.memory_extraction import compute_act_rate

    meta = getattr(record, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}

    content = getattr(record, "content", "") or ""
    # Title: first line, truncated to ~80 chars.
    first_line = content.splitlines()[0] if content else ""
    title = first_line[:80] + ("…" if len(first_line) > 80 else "")

    outcome_history = meta.get("outcome_history", [])
    if not isinstance(outcome_history, list):
        outcome_history = []
    acted_count = sum(1 for e in outcome_history if e.get("outcome") == "acted")
    dismissed_count = sum(1 for e in outcome_history if e.get("outcome") == "dismissed")
    act_rate = compute_act_rate(outcome_history)

    dismissal_count = meta.get("dismissal_count", 0)
    if not isinstance(dismissal_count, int):
        dismissal_count = 0
    decay_imminent = dismissal_count >= DISMISSAL_DECAY_THRESHOLD - 1

    category = meta.get("category", "default") or "default"

    superseded_by = getattr(record, "superseded_by", "") or ""
    superseded_by_rationale = getattr(record, "superseded_by_rationale", "") or ""

    # `relevance` is a DecayingSortedField exposed as a numeric score on the
    # instance. Used as the canonical sort key (per plan).
    try:
        relevance = float(getattr(record, "relevance", 0.0) or 0.0)
    except (TypeError, ValueError):
        relevance = 0.0

    return {
        "memory_id": getattr(record, "memory_id", ""),
        "title": title,
        "content": content,
        "category": category,
        "importance": float(getattr(record, "importance", 0.0) or 0.0),
        "relevance": relevance,
        "source": getattr(record, "source", "") or "",
        "agent_id": getattr(record, "agent_id", "") or "",
        "project_key": getattr(record, "project_key", "") or "",
        "confidence": float(getattr(record, "confidence", 0.0) or 0.0),
        "access_count": int(getattr(record, "access_count", 0) or 0),
        # AccessTrackerMixin records last access; fall back gracefully.
        "last_access_at": getattr(record, "last_access_at", None),
        "outcome_history": outcome_history,
        "acted_count": acted_count,
        "dismissed_count": dismissed_count,
        "act_rate": act_rate,
        "last_outcome": meta.get("last_outcome"),
        "dismissal_count": dismissal_count,
        "decay_imminent": decay_imminent,
        "decay_threshold": DISMISSAL_DECAY_THRESHOLD,
        "superseded": bool(superseded_by),
        "superseded_by": superseded_by,
        "superseded_by_rationale": superseded_by_rationale,
        "tags": meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
        "file_paths": meta.get("file_paths", [])
        if isinstance(meta.get("file_paths"), list)
        else [],
    }


def get_memories(
    project_key: str | None = None,
    category: str | None = None,
    decay_only: bool = False,
    include_superseded: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Return decorated, filtered, sorted, and capped memory records.

    Filter ordering: filters apply first, THEN sort the filtered subset
    by `relevance` desc, THEN truncate to `limit`. This protects render
    time on large corpora.

    The query is wrapped in try/except. On any failure, returns an empty
    list and logs a warning -- the dashboard never crashes from this.

    Args:
        project_key: Project partition key. Falls back to VALOR_PROJECT_KEY
            env var, then to DEFAULT_PROJECT_KEY.
        category: Filter to memories whose metadata.category equals this.
            None = all categories.
        decay_only: If True, restrict to records with
            `dismissal_count >= DISMISSAL_DECAY_THRESHOLD - 1`.
        include_superseded: If False (default), drop records where
            `superseded_by` is set. If True, keep them.
        limit: Maximum number of records to return after filtering and
            sorting. Default 200.

    Returns:
        Dict with:
            project_key (str): the resolved project key
            records (list[dict]): decorated, filtered, sorted, capped records
            total_matched (int): number of records that matched filters
                BEFORE truncation
            truncated_count (int): how many records were dropped by the
                limit (0 if total_matched <= limit)
            categories (list[str]): the distinct categories present in the
                full filtered set (pre-truncation), useful for filter UI
    """
    pk = _resolve_project_key(project_key)

    try:
        from models.memory import Memory

        all_records = list(Memory.query.filter(project_key=pk))
    except Exception as e:
        logger.warning(f"Failed to query Memory records for project_key={pk!r}: {e}")
        return {
            "project_key": pk,
            "records": [],
            "total_matched": 0,
            "truncated_count": 0,
            "categories": [],
        }

    decorated = [_decorate_record(r) for r in all_records]

    # Apply filters first.
    if not include_superseded:
        decorated = [r for r in decorated if not r["superseded"]]

    if category:
        decorated = [r for r in decorated if r["category"] == category]

    if decay_only:
        decorated = [r for r in decorated if r["decay_imminent"]]

    # Track distinct categories in the filtered set (pre-truncation) for filter UI.
    categories = sorted({r["category"] for r in decorated})

    # Sort the filtered subset by relevance desc (the DecayingSortedField
    # score). Stable secondary sort on memory_id keeps results deterministic.
    decorated.sort(
        key=lambda r: (-(r["relevance"] or 0.0), r["memory_id"]),
    )

    total_matched = len(decorated)
    if total_matched > limit:
        truncated_count = total_matched - limit
        decorated = decorated[:limit]
    else:
        truncated_count = 0

    return {
        "project_key": pk,
        "records": decorated,
        "total_matched": total_matched,
        "truncated_count": truncated_count,
        "categories": categories,
    }


def get_memory_detail(memory_id: str) -> dict | None:
    """Return a single memory's full inspection dict, or None if missing.

    Thin wrapper over `tools.memory_search.inspect`. Used by an optional
    detail route (deferred to v2 in this PR; the helper is wired up so the
    detail page can land later without changes here).
    """
    from tools.memory_search import inspect

    result = inspect(memory_id=memory_id)
    if not result or "error" in result:
        return None
    return result
