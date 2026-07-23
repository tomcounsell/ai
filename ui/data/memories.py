"""Data access layer for the Memories dashboard.

All functions are synchronous (def, not async def) because Popoto uses
synchronous Redis calls. FastAPI runs sync route handlers in a threadpool,
which avoids blocking the event loop.

The view is read-only — it never writes to Memory records. Mutation lives
in `python -m tools.memory_search`.
"""

import json
import logging
import os
from pathlib import Path
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


def _resolve_project_keys(project_key: str | None = None) -> list[str]:
    """Resolve project keys for READ operations in this view module.

    When an explicit project_key is given, returns [that_key].
    When None (the dashboard default), returns all project keys owned by
    this machine so memories from every owned project are visible.
    Falls back to [DEFAULT_PROJECT_KEY] if machine config is unavailable.
    """
    if project_key:
        return [project_key]

    env_key = os.environ.get("VALOR_PROJECT_KEY")
    if env_key:
        return [env_key]

    # No env var — resolve from machine config (same source as the dashboard).
    from config.machine import get_machine_name
    from ui.data.machine import get_machine_projects

    projects = get_machine_projects()
    if projects:
        # Deduplicate by project_name → project_key mapping.
        # get_machine_projects returns project_name, not project_key.
        # We need the keys from projects.json to match Memory.project_key.
        config_path = Path("~/Desktop/Valor/projects.json").expanduser()
        try:
            config = json.loads(config_path.read_text())
            keys = list(config.get("projects", {}).keys())
            machine = get_machine_name().lower()
            return [
                k
                for k, v in config.get("projects", {}).items()
                if v.get("machine", "").lower() == machine
            ] or keys
        except Exception:
            pass

    return [DEFAULT_PROJECT_KEY]


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
    pks = _resolve_project_keys(project_key)

    try:
        from models.memory import Memory

        all_records = []
        for pk in pks:
            all_records.extend(Memory.query.filter(project_key=pk))
    except Exception as e:
        logger.warning(f"Failed to query Memory records for project_keys={pks!r}: {e}")
        return {
            "project_key": ", ".join(pks),
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
        "project_key": ", ".join(pks),
        "records": decorated,
        "total_matched": total_matched,
        "truncated_count": truncated_count,
        "categories": categories,
    }


_GATE_COUNTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("ack", "gate_rejected_ack"),
    ("fragment", "gate_rejected_fragment"),
    ("short", "gate_rejected_short"),
    ("fallback_dropped", "gate_fallback_dropped"),
)

# Issue #2203: activated pruning/dedup counters, reusing the #2201 gate-counter
# pattern. Unlike the write-gate counters above (always attributed to a `pk`
# already in the resolved corpus scope), decay-prune scans the ENTIRE corpus
# (`Memory.query.all()`, not scoped to `pks`) and increments per-record using
# that record's own `project_key`, coalesced to `DEFAULT_PROJECT_KEY` when
# null/empty (see `reflections/memory/memory_decay_prune.py` and
# `scripts/memory_consolidation.py::_apply_merge`). So the summed pk list for
# these two fields must always include `DEFAULT_PROJECT_KEY`, even when the
# resolved corpus scope (`pks`) doesn't contain it -- otherwise increments for
# records with a null/empty project_key would silently vanish from the total.
_PRUNE_DEDUP_COUNTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("prune_count", "prune_count"),
    ("dedup_merge_count", "dedup_merge_count"),
    # outcome_resolve_count (issue #2203): the memory-outcome-resolve sweep
    # also scans corpus-wide (stale session sidecars, not scoped to `pks`)
    # and increments per-record via that record's own project_key, coalesced
    # to DEFAULT_PROJECT_KEY -- same rationale as prune_count/dedup_merge_count
    # above, so it shares the same coalesced-pk summation call below.
    ("outcome_resolve_count", "outcome_resolve_count"),
)


def _pks_with_default(pks: list[str]) -> list[str]:
    """Return `pks` plus `DEFAULT_PROJECT_KEY`, deduped, order-preserving."""
    if DEFAULT_PROJECT_KEY in pks:
        return pks
    return [*pks, DEFAULT_PROJECT_KEY]


# Distillation-outcome counters (memory-distilled-ingest, Phase 3, issue #2202).
# Live on a SEPARATE Redis namespace from the write-gate counters above
# (`{project_key}:memory-distill:{reason}`, not `:memory-gate:`) -- see
# models/memory_distill_gate.py's module docstring for why the two are kept
# apart. `distill_abandoned` here is the CUMULATIVE counter (how many
# transitions have ever happened); `abandoned_count` below is the separate
# LIVE gauge (how many records are currently in that terminal state).
_DISTILL_COUNTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("distilled", "distill_distilled"),
    ("distill_failed", "distill_failed"),
    ("distill_refused", "distill_refused"),
    ("distill_abandoned", "distill_abandoned_total"),
)

def _sum_gate_counter(reason: str, pks: list[str]) -> int:
    """Best-effort sum of the `{project_key}:memory-gate:{reason}` counter across `pks`.

    Issue #2201's write-gate counters live on plain `INCR`/`GET` Redis keys
    (not Popoto-managed), written by `models/memory.py`'s `Memory.save()`
    override and `agent/memory_extraction.py`'s fallback-drop path. This
    reuses `_sum_project_counter`'s `{project_key}:{suffix}` key layout
    (`ui/app.py:434`) but is DELIBERATELY driven by the `pks` this call's
    `get_corpus_metrics` already resolved (`_resolve_project_keys`), not
    `get_machine_project_keys()` -- the counters must match the exact
    corpus scope this metrics call reports on, not every project this
    machine owns.

    Never raises: any Redis error (including the whole handle being
    unreachable) yields 0 so the metrics payload stays well-formed and the
    dashboard never crashes.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R
    except Exception:
        return 0

    total = 0
    for pk in pks:
        try:
            val = _R.get(f"{pk}:memory-gate:{reason}")
            if val:
                total += int(val)
        except Exception:
            continue
    return total


def _sum_distill_counter(reason: str, pks: list[str]) -> int:
    """Best-effort sum of the `{project_key}:memory-distill:{reason}` counter across `pks`.

    Mirrors `_sum_gate_counter` exactly, but reads the SEPARATE
    `memory-distill` namespace written by
    `models/memory_distill_gate.py::_increment_distill_counter` (the
    `reflections/memory/memory_distill_backfill.py` reflection). Never
    raises: any Redis error yields 0 so the metrics payload stays
    well-formed and the dashboard never crashes.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R
    except Exception:
        return 0

    total = 0
    for pk in pks:
        try:
            val = _R.get(f"{pk}:memory-distill:{reason}")
            if val:
                total += int(val)
        except Exception:
            continue
    return total


def _count_distill_status(records: list, status: str) -> int:
    """Count records whose `metadata.distill_status == status`.

    A LIVE gauge over the corpus already loaded by `get_corpus_metrics`
    (`provisional_count` / `abandoned_count`) -- distinct from the
    cumulative `_sum_distill_counter` counters above, which never decrease.
    Never raises: a malformed `metadata` on any single record is skipped,
    not fatal to the whole count.
    """
    count = 0
    for r in records:
        meta = getattr(r, "metadata", None)
        if isinstance(meta, dict) and meta.get("distill_status") == status:
            count += 1
    return count


def get_corpus_metrics(project_key: str | None = None, min_evidence: int = 2) -> dict:
    """Corpus-wide ingest-quality metrics for the memory-telemetry surface.

    Unlike `get_memories`, this loads the FULL matching corpus -- no
    `limit` truncation -- via `.no_track()`. `.no_track()` is mandatory:
    it suppresses `AccessTrackerMixin.on_read()` staging on every record
    read here. Without it, a corpus scan would stage a read timestamp on
    every record that later gets promoted into `access_count`, silently
    contaminating the `access_count == 0` ("never-injected") metric this
    function reports.

    Each decorated record is classified via `agent.memory_quality`'s shared
    junk-definition heuristics (transitively, inside
    `compute_corpus_metrics`) and superseded records are separated from the
    durable-corpus denominator (`superseded_count` vs. `durable_denominator`
    in the returned dict) rather than being dropped or counted as durable.

    Wrapped in try/except like the other loaders in this module -- this
    must never crash the dashboard. On any query failure, returns the same
    well-formed, zero-filled metrics shape `compute_corpus_metrics` returns
    for an empty corpus, and logs a warning.

    Args:
        project_key: Project partition key. None resolves to every project
            key owned by this machine (see `_resolve_project_keys`).
        min_evidence: Minimum `acted + dismissed` outcome count for a
            record to contribute to the aggregate act-rate calculation.
            Passed straight through to `compute_corpus_metrics`.

    Returns:
        The `compute_corpus_metrics` result dict, plus `project_key` (the
        resolved, comma-joined project key string, matching `get_memories`'
        return contract).
    """
    from tools.memory_eval.ingest_quality import compute_corpus_metrics

    pks = _resolve_project_keys(project_key)

    try:
        from models.memory import Memory

        all_records = []
        for pk in pks:
            all_records.extend(Memory.query.filter(project_key=pk).no_track().all())
    except Exception as e:
        logger.warning(
            f"Failed to query Memory records for corpus metrics project_keys={pks!r}: {e}"
        )
        metrics = compute_corpus_metrics([], min_evidence=min_evidence)
        metrics["project_key"] = ", ".join(pks)
        for reason, field in _GATE_COUNTER_FIELDS:
            metrics[field] = _sum_gate_counter(reason, pks)
        for reason, field in _PRUNE_DEDUP_COUNTER_FIELDS:
            metrics[field] = _sum_gate_counter(reason, _pks_with_default(pks))
        for reason, field in _DISTILL_COUNTER_FIELDS:
            metrics[field] = _sum_distill_counter(reason, pks)
        metrics["provisional_count"] = 0
        metrics["abandoned_count"] = 0
        metrics["distilled_count"] = 0
        return metrics

    decorated = [_decorate_record(r) for r in all_records]

    metrics = compute_corpus_metrics(decorated, min_evidence=min_evidence)
    metrics["project_key"] = ", ".join(pks)

    # Best-effort counter-metric attachment (memory.extraction,
    # memory.extraction.error, memory.extraction.session_cap_hit) was
    # considered here, but analytics/collector.py only exposes
    # record_metric() (write-only, best-effort dual-write to SQLite +
    # Redis) -- there is no matching "read current value" accessor to
    # attach without inventing new analytics surface, so it is
    # intentionally skipped (per plan: skip rather than invent).
    #
    # The issue #2201 write-gate counters ARE attached below: unlike the
    # analytics metrics above, they live on plain readable `INCR`/`GET`
    # Redis keys (`_sum_gate_counter`), not analytics.collector's
    # write-only store, so there is a real "read current value" accessor.
    for reason, field in _GATE_COUNTER_FIELDS:
        metrics[field] = _sum_gate_counter(reason, pks)

    # Issue #2203: prune/dedup counters, keyed per-record and coalesced to
    # DEFAULT_PROJECT_KEY (see _PRUNE_DEDUP_COUNTER_FIELDS docstring above).
    for reason, field in _PRUNE_DEDUP_COUNTER_FIELDS:
        metrics[field] = _sum_gate_counter(reason, _pks_with_default(pks))

    # Distillation telemetry (memory-distilled-ingest, Phase 3, issue #2202):
    # cumulative outcome counters (Redis INCR, never decrease) plus three LIVE
    # gauges computed directly from the corpus this call already loaded --
    # `provisional_count` (records still awaiting distillation, the Risk 1
    # "stuck backlog" signal), `distilled_count` (records currently settled at
    # `distill_status=distilled`, the Task 3 lift-report coverage number), and
    # `abandoned_count` (records that hit the terminal distill_abandoned
    # state, the Risk 1 "rising abandon rate" signal). All three read from
    # `all_records` (raw Memory instances), not `decorated`, since
    # distill_status is not part of the dashboard's decorated-record shape.
    for reason, field in _DISTILL_COUNTER_FIELDS:
        metrics[field] = _sum_distill_counter(reason, pks)
    metrics["provisional_count"] = _count_distill_status(all_records, "provisional")
    metrics["distilled_count"] = _count_distill_status(all_records, "distilled")
    metrics["abandoned_count"] = _count_distill_status(all_records, "distill_abandoned")

    return metrics


def get_corpus_records(project_key: str | None = None) -> tuple[list[dict], list[str]]:
    """Fetch and decorate the full matching Memory corpus, without aggregating.

    A thin sibling of `get_corpus_metrics`: same `.no_track()` full-corpus
    fetch + `_decorate_record` shape, but returns the raw decorated-record
    list instead of the `compute_corpus_metrics` aggregate. Exists so a
    caller that needs to SEGMENT the corpus before aggregating (e.g. by
    `source`, for the distilled-ingest lift report -- see
    `tools/memory_eval/distilled_ingest_report.py`) can filter this list and
    call `compute_corpus_metrics` once per subset, without duplicating the
    query/decoration logic or touching `compute_corpus_metrics` itself.

    `get_corpus_metrics` does NOT call this helper -- it keeps its own
    independent fetch so its existing error-handling/counter-zero-fill
    behavior on query failure is untouched by this addition.

    Args:
        project_key: Project partition key. None resolves to every project
            key owned by this machine (see `_resolve_project_keys`).

    Returns:
        (decorated_records, resolved_project_keys). On query failure,
        returns ([], resolved_project_keys) and logs a warning -- never
        raises.
    """
    pks = _resolve_project_keys(project_key)

    try:
        from models.memory import Memory

        all_records = []
        for pk in pks:
            all_records.extend(Memory.query.filter(project_key=pk).no_track().all())
    except Exception as e:
        logger.warning(
            f"Failed to query Memory records for corpus records project_keys={pks!r}: {e}"
        )
        return [], pks

    return [_decorate_record(r) for r in all_records], pks


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
