"""Memory search tool: search, save, inspect, and forget memories.

Provides a direct interface to the Memory model for querying, creating,
inspecting, and deleting memory records. Usable from agent sessions
(via direct import) and Claude Code sessions (via CLI).

All public functions follow the fail-silent contract: they wrap their
bodies in try/except and return empty results or None on failure.
No function raises exceptions to callers.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _fetch_all_records(project_key: str) -> list:
    """Fetch all memory records for a project key.

    Shared helper used by inspect(stats=True) and status() to avoid
    duplicating the Memory.query.filter(...) call.

    Args:
        project_key: Project partition key.

    Returns:
        List of Memory records, or empty list on error.
    """
    from models.memory import Memory

    try:
        return list(Memory.query.filter(project_key=project_key))
    except Exception:
        return []


def _resolve_project_key(project_key: str | None = None) -> str:
    """Resolve project_key from argument or environment."""
    if project_key:
        return project_key
    from config.memory_defaults import DEFAULT_PROJECT_KEY

    return os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)


def search(
    query: str,
    project_key: str | None = None,
    limit: int = 10,
    category: str | None = None,
    tag: str | None = None,
    min_act_rate: float | None = None,
    assess_quality: bool = False,
    min_rrf_score: float | None = None,
) -> dict[str, Any]:
    """Search memories by query string using BM25 + RRF fusion.

    Args:
        query: The search query text.
        project_key: Project partition key. Resolved from env if not provided.
        limit: Maximum number of results to return.
        category: Filter by metadata category (correction, decision, pattern, surprise).
        tag: Filter by metadata tag.
        min_act_rate: Filter to memories with act_rate >= this threshold (0.0-1.0).
        assess_quality: If True, run a RetrievalQuality probe via ContextAssembler
            after retrieval and attach the result as a "quality" key in the return dict.
            This makes one additional Redis read (composite_score). Failure is non-fatal:
            on error the result dict is returned without the "quality" key.
        min_rrf_score: Optional post-fusion relevance threshold passed through to
            `retrieve_memories`. CLI defaults to None for back-compat (no filtering);
            the recall hooks pass `config.memory_defaults.RRF_MIN_SCORE` to enable
            the gate. See docs/features/subconscious-memory.md.

    Returns:
        Dict with "results" list and "error" key (None if no error).
        Each result has: content, score, confidence, source, access_count, memory_id, metadata.
        If assess_quality=True and the probe succeeds, also includes "quality" key with
        a RetrievalQuality dict: avg_confidence, score_spread, fok_score, staleness_ratio.
    """
    try:
        if not query or not query.strip():
            return {"results": [], "error": None}

        project_key = _resolve_project_key(project_key)

        # Bloom pre-check: ExistenceFilter probe per query token. Per
        # config.memory_defaults.BLOOM_MIN_HITS the gate requires at least
        # that many distinct token hits before BM25 + RRF runs. Single-token
        # queries with one bloom hit no longer pass -- they were a low-precision
        # anti-pattern. On bloom error, fall through and let BM25 run.
        from config.memory_defaults import BLOOM_MIN_HITS
        from models.memory import Memory

        bloom_field = Memory._meta.fields.get("bloom")
        if bloom_field:
            # Extract simple words from query for bloom check
            import re

            words = re.findall(r"[a-zA-Z]{3,}", query)
            bloom_hits = 0
            bloom_error = False
            for word in words:
                try:
                    if bloom_field.might_exist(Memory, word):
                        bloom_hits += 1
                except Exception:
                    # On bloom error, proceed with full query (treat as pass)
                    bloom_error = True
                    break
            if not words:
                # No words to probe -- proceed (e.g. single-character or numeric query)
                pass
            elif bloom_error:
                pass  # let BM25 handle it
            elif bloom_hits < BLOOM_MIN_HITS:
                return {"results": [], "error": None}

        # Retrieve via BM25 + RRF fusion
        from agent.memory_retrieval import retrieve_memories

        all_records = retrieve_memories(
            query_text=query,
            project_key=project_key,
            limit=limit,
            min_rrf_score=min_rrf_score,
        )

        if not all_records:
            return {"results": [], "error": None}

        # Post-retrieval metadata filtering
        filtered_records = all_records
        if category:
            filtered_records = [
                r
                for r in filtered_records
                if (getattr(r, "metadata", None) or {}).get("category") == category
            ]
        if tag:
            filtered_records = [
                r
                for r in filtered_records
                if tag in (getattr(r, "metadata", None) or {}).get("tags", [])
            ]
        if min_act_rate is not None:
            from agent.memory_extraction import compute_act_rate

            def _passes_act_rate(r: object) -> bool:
                meta = getattr(r, "metadata", None) or {}
                history = meta.get("outcome_history", [])
                rate = compute_act_rate(history)
                return rate is not None and rate >= min_act_rate

            filtered_records = [r for r in filtered_records if _passes_act_rate(r)]

        results = []
        for record in filtered_records[:limit]:
            meta = getattr(record, "metadata", None) or {}
            results.append(
                {
                    "content": getattr(record, "content", ""),
                    "score": getattr(record, "score", 0.0),
                    "confidence": getattr(record, "confidence", 0.0),
                    "source": getattr(record, "source", ""),
                    "access_count": getattr(record, "access_count", 0),
                    "memory_id": getattr(record, "memory_id", ""),
                    "metadata": meta,
                }
            )

        result: dict[str, Any] = {"results": results, "error": None}

        # Optional quality probe: RetrievalQuality metacognitive layer (popoto v1.5.0)
        # Runs one additional Redis read (composite_score); failure is non-fatal.
        if assess_quality:
            try:
                import dataclasses

                from popoto.recipes import ContextAssembler

                assembler = ContextAssembler(
                    model_class=Memory,
                    score_weights={"relevance": 0.6, "confidence": 0.3},
                )
                quality = assembler.assess({"query": query})
                result["quality"] = dataclasses.asdict(quality)
            except Exception as q_err:
                logger.debug(
                    f"[memory_search] quality probe failed (non-fatal): "
                    f"{type(q_err).__name__}: {q_err}"
                )

        return result

    except Exception as e:
        logger.warning(f"[memory_search] search failed (non-fatal): {type(e).__name__}: {e}")
        return {"results": [], "error": None}


def save(
    content: str,
    importance: float | None = None,
    project_key: str | None = None,
    source: str = "human",
) -> dict[str, Any] | None:
    """Save a new memory record.

    Args:
        content: The memory content text.
        importance: Numeric importance score. Defaults to 6.0 (human weight).
        project_key: Project partition key. Resolved from env if not provided.
        source: Origin type - "human", "agent", or "system".

    Returns:
        Dict with memory_id and content if saved, or None if filtered/failed.
    """
    try:
        if not content or not content.strip():
            return None

        if importance is None:
            importance = 6.0  # InteractionWeight.HUMAN

        project_key = _resolve_project_key(project_key)

        from models.memory import Memory

        record = Memory.safe_save(
            content=content,
            importance=importance,
            source=source,
            project_key=project_key,
            agent_id=project_key,
        )

        if record is None:
            return None

        return {
            "memory_id": getattr(record, "memory_id", ""),
            "content": content,
        }

    except Exception as e:
        logger.warning(f"[memory_search] save failed (non-fatal): {e}")
        return None


def inspect(
    memory_id: str | None = None,
    project_key: str | None = None,
    stats: bool = False,
) -> dict[str, Any]:
    """Inspect a specific memory or get aggregate stats.

    Args:
        memory_id: ID of a specific memory to inspect.
        project_key: Project partition key for stats aggregation.
        stats: If True and no memory_id, return aggregate statistics.

    Returns:
        Dict with memory details, stats, or error guidance.
    """
    try:
        from models.memory import Memory

        if memory_id:
            # Direct lookup by memory_id
            try:
                record = Memory.query.filter(memory_id=memory_id).first()
            except Exception:
                record = None

            if not record:
                return {"error": f"Memory not found: {memory_id}"}

            meta = getattr(record, "metadata", None) or {}
            return {
                "memory_id": getattr(record, "memory_id", ""),
                "content": getattr(record, "content", ""),
                "importance": getattr(record, "importance", 0.0),
                "source": getattr(record, "source", ""),
                "confidence": getattr(record, "confidence", 0.0),
                "access_count": getattr(record, "access_count", 0),
                "project_key": getattr(record, "project_key", ""),
                "agent_id": getattr(record, "agent_id", ""),
                "metadata": meta,
            }

        if stats:
            project_key = _resolve_project_key(project_key)
            # Aggregate stats across project
            all_records = _fetch_all_records(project_key)

            if not all_records:
                return {
                    "project_key": project_key,
                    "total": 0,
                    "by_source": {},
                    "avg_confidence": 0.0,
                }

            by_source: dict[str, int] = {}
            total_confidence = 0.0
            for record in all_records:
                src = getattr(record, "source", "unknown")
                by_source[src] = by_source.get(src, 0) + 1
                total_confidence += getattr(record, "confidence", 0.0)

            return {
                "project_key": project_key,
                "total": len(all_records),
                "by_source": by_source,
                "avg_confidence": total_confidence / len(all_records) if all_records else 0.0,
            }

        return {"error": "Provide --id for a specific memory or --stats for aggregate statistics."}

    except Exception as e:
        logger.warning(f"[memory_search] inspect failed (non-fatal): {e}")
        return {"error": str(e)}


def outcome_stats(
    project_key: str | None = None,
) -> dict[str, Any]:
    """Get aggregate outcome statistics for memories with outcome history.

    Args:
        project_key: Project partition key. Resolved from env if not provided.

    Returns:
        Dict with total_with_history, avg_act_rate, and top_acted list.
    """
    try:
        from agent.memory_extraction import compute_act_rate
        from models.memory import Memory

        project_key = _resolve_project_key(project_key)

        try:
            all_records = list(Memory.query.filter(project_key=project_key))
        except Exception:
            all_records = []

        memories_with_history = []
        for record in all_records:
            meta = getattr(record, "metadata", None) or {}
            history = meta.get("outcome_history", [])
            if history:
                rate = compute_act_rate(history)
                memories_with_history.append(
                    {
                        "memory_id": getattr(record, "memory_id", ""),
                        "content": getattr(record, "content", "")[:100],
                        "act_rate": rate if rate is not None else 0.0,
                        "total_outcomes": len(history),
                    }
                )

        if not memories_with_history:
            return {
                "project_key": project_key,
                "total_with_history": 0,
                "avg_act_rate": 0.0,
                "top_acted": [],
            }

        avg_rate = sum(m["act_rate"] for m in memories_with_history) / len(memories_with_history)
        top_acted = sorted(memories_with_history, key=lambda m: m["act_rate"], reverse=True)[:5]

        return {
            "project_key": project_key,
            "total_with_history": len(memories_with_history),
            "avg_act_rate": round(avg_rate, 3),
            "top_acted": top_acted,
        }

    except Exception as e:
        logger.warning(f"[memory_search] outcome_stats failed (non-fatal): {e}")
        return {"error": str(e)}


def status(
    project_key: str | None = None,
    deep: bool = False,
) -> dict[str, Any]:
    """Return a health summary of the memory system.

    Fast path (<1s): Redis ping, total count, category breakdown, superseded
    count, average confidence, last-write timestamp, EmbeddingField detection.

    Deep path (behind deep=True): orphan index count using _count_orphans()
    from scripts/popoto_index_cleanup, per-category confidence averages.

    Args:
        project_key: Project partition key. Resolved from env if not provided.
        deep: If True, run slow checks (orphan scan, per-category confidence).

    Returns:
        Dict with keys: healthy, redis, total, by_category, superseded,
        avg_confidence, last_write, embedding_field. On Redis failure:
        {"healthy": False, "error": "Redis unreachable: ..."}.
    """
    try:
        from models.memory import Memory

        # Redis ping — fail-fast
        try:
            from popoto.redis_db import POPOTO_REDIS_DB

            POPOTO_REDIS_DB.ping()
            redis_ok = True
        except Exception as e:
            return {"healthy": False, "error": f"Redis unreachable: {e}"}

        project_key = _resolve_project_key(project_key)
        all_records = _fetch_all_records(project_key)

        total = len(all_records)

        # Category breakdown
        known_categories = {"correction", "decision", "pattern", "surprise"}
        by_category: dict[str, int] = {}
        superseded_count = 0
        total_confidence = 0.0
        last_relevance = 0.0

        for record in all_records:
            meta = getattr(record, "metadata", None) or {}
            cat = meta.get("category") or ""
            key = cat if cat in known_categories else "other"
            by_category[key] = by_category.get(key, 0) + 1

            if getattr(record, "superseded_by", "") != "":
                superseded_count += 1

            total_confidence += getattr(record, "confidence", 0.0)

            rel = getattr(record, "relevance", 0.0) or 0.0
            if rel > last_relevance:
                last_relevance = rel

        avg_confidence = total_confidence / total if total > 0 else 0.0

        # last_write: relevance stores Unix timestamp float (last relevance update)
        # NOTE: this reflects last relevance update, not creation time — decay/boost
        # operations on old records can make them appear recent here.
        if last_relevance > 0:
            from datetime import datetime

            last_write = datetime.fromtimestamp(last_relevance).isoformat()
        else:
            last_write = None

        # EmbeddingField detection
        embedding_field = Memory._meta.fields.get("embedding")
        embedding_status = "configured" if embedding_field is not None else "not_configured"

        result: dict[str, Any] = {
            "healthy": True,
            "redis": {"ok": redis_ok},
            "project_key": project_key,
            "total": total,
            "by_category": by_category,
            "superseded": superseded_count,
            "avg_confidence": round(avg_confidence, 4),
            "last_write": last_write,
            "embedding_field": embedding_status,
        }

        if deep:
            # Orphan detection — import from scripts/
            orphan_count: int | str = "unavailable"
            disk_orphan_count: int | str = "unavailable"
            try:
                import sys
                from pathlib import Path

                scripts_dir = str(Path(__file__).parents[2] / "scripts")
                if scripts_dir not in sys.path:
                    sys.path.insert(0, scripts_dir)
                from popoto_index_cleanup import _count_disk_orphans, _count_orphans

                orphan_count = _count_orphans(Memory)
                disk_orphan_count = _count_disk_orphans(Memory)
            except Exception as e:
                logger.warning(f"[memory_search] orphan count failed: {e}")
                orphan_count = f"error: {e}"
                disk_orphan_count = f"error: {e}"

            # Per-category confidence averages
            cat_confidence: dict[str, dict[str, Any]] = {}
            cat_totals: dict[str, list[float]] = {}
            for record in all_records:
                meta = getattr(record, "metadata", None) or {}
                cat = meta.get("category") or ""
                key = cat if cat in known_categories else "other"
                conf = getattr(record, "confidence", 0.0)
                cat_totals.setdefault(key, []).append(conf)
            for cat_key, confs in cat_totals.items():
                cat_confidence[cat_key] = {
                    "count": len(confs),
                    "avg_confidence": round(sum(confs) / len(confs), 4) if confs else 0.0,
                }

            result["orphan_index_count"] = orphan_count
            result["disk_orphan_count"] = disk_orphan_count
            result["by_category_confidence"] = cat_confidence

        return result

    except Exception as e:
        logger.warning(f"[memory_search] status failed (non-fatal): {e}")
        return {"healthy": False, "error": str(e)}


def timeline(
    project_key: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    category: str | None = None,
    group_by: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query memories within a time range, optionally grouped by day or category.

    Provides a time-sliced view of the memory system for answering questions
    like "what did we learn this week?" without materialized artifacts.

    Args:
        project_key: Project partition key. Resolved from env if not provided.
        since: Start of time range (inclusive). None = no lower bound.
        until: End of time range (inclusive). None = no upper bound.
        category: Filter by metadata category (correction, decision, pattern, surprise).
        group_by: Group results by "day" or "category". None = flat list.
        limit: Maximum number of results to return.

    Returns:
        Dict with "results" list, "summary" dict, "groups" dict (if group_by),
        and "error" key (None if no error).
    """
    try:
        project_key = _resolve_project_key(project_key)

        # Convert datetime bounds to Unix timestamps for the retrieval helper
        since_ts = since.timestamp() if since else None
        until_ts = until.timestamp() if until else None

        from agent.memory_retrieval import get_memories_in_time_range

        records = get_memories_in_time_range(
            project_key=project_key,
            since=since_ts,
            until=until_ts,
            limit=limit * 2,  # over-fetch to account for post-filtering
        )

        # Post-filter by category if requested
        if category:
            records = [
                r
                for r in records
                if (getattr(r, "metadata", None) or {}).get("category") == category
            ]

        # Limit results
        records = records[:limit]

        # Serialize results
        results = []
        for record in records:
            meta = getattr(record, "metadata", None) or {}

            # Use _timeline_score (relevance sorted set score) as the timestamp.
            # This is set by get_memories_in_time_range() and approximates
            # creation time. Falls back to last_accessed or relevance field.
            ts_float = getattr(record, "_timeline_score", None)
            if ts_float is None:
                # Fallback: try relevance field directly
                rel = getattr(record, "relevance", None)
                if rel is not None:
                    try:
                        ts_float = float(rel)
                    except (TypeError, ValueError):
                        ts_float = None

            timestamp_str = None
            if ts_float is not None and ts_float > 0:
                try:
                    timestamp_str = datetime.fromtimestamp(ts_float, tz=UTC).isoformat()
                except (OSError, OverflowError, ValueError):
                    timestamp_str = None

            results.append(
                {
                    "memory_id": getattr(record, "memory_id", ""),
                    "content": getattr(record, "content", ""),
                    "importance": getattr(record, "importance", 0.0),
                    "source": getattr(record, "source", ""),
                    "confidence": getattr(record, "confidence", 0.0),
                    "timestamp": timestamp_str,
                    "metadata": meta,
                }
            )

        # Build summary
        by_source: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for r in results:
            src = r.get("source", "unknown")
            by_source[src] = by_source.get(src, 0) + 1
            cat = r.get("metadata", {}).get("category", "other")
            by_category[cat] = by_category.get(cat, 0) + 1

        summary = {
            "total": len(results),
            "by_source": by_source,
            "by_category": by_category,
        }

        response: dict[str, Any] = {
            "results": results,
            "summary": summary,
            "error": None,
        }

        # Grouping
        if group_by == "day":
            groups: dict[str, list[dict]] = {}
            for r in results:
                ts = r.get("timestamp")
                if ts:
                    # Extract date portion from ISO string
                    day_key = ts[:10] if len(ts) >= 10 else "unknown"
                else:
                    day_key = "unknown"
                groups.setdefault(day_key, []).append(r)
            response["groups"] = groups
        elif group_by == "category":
            groups = {}
            for r in results:
                cat = r.get("metadata", {}).get("category", "other")
                groups.setdefault(cat, []).append(r)
            response["groups"] = groups

        return response

    except Exception as e:
        logger.warning(f"[memory_search] timeline failed (non-fatal): {type(e).__name__}: {e}")
        return {"results": [], "summary": {"total": 0}, "error": None}


def forget(memory_id: str) -> dict[str, Any]:
    """Delete a memory record by ID.

    Args:
        memory_id: The ID of the memory to delete.

    Returns:
        Dict with deleted=True and memory_id, or error message.
    """
    try:
        if not memory_id or not memory_id.strip():
            return {"error": "memory_id is required", "deleted": False}

        from models.memory import Memory

        try:
            record = Memory.query.filter(memory_id=memory_id).first()
        except Exception:
            record = None

        if not record:
            return {"error": f"Memory not found: {memory_id}", "deleted": False}

        try:
            record.delete()
        except Exception as e:
            return {"error": f"Delete failed: {e}", "deleted": False}

        return {"deleted": True, "memory_id": memory_id}

    except Exception as e:
        logger.warning(f"[memory_search] forget failed (non-fatal): {e}")
        return {"error": str(e), "deleted": False}
