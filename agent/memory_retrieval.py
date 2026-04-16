"""BM25 + RRF fusion retrieval for the subconscious memory system.

Replaces ContextAssembler with a three-signal Reciprocal Rank Fusion:
  1. BM25 keyword match quality (via BM25Field.search)
  2. Temporal relevance (via DecayingSortedField sorted set)
  3. Historical confidence (via ConfidenceField companion hash)

Each signal produces a ranked list of (redis_key, score) tuples.
RRF fuses them into a single ranking: score = sum(1 / (k + rank_i)).

All functions are fail-silent -- retrieval failures never crash the agent.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _filter_by_project(
    results: list[tuple[str, float]],
    project_key: str,
) -> list[tuple[str, float]]:
    """Filter (redis_key, score) tuples to only those belonging to project_key.

    Memory's project_key is a KeyField, so it is embedded in each Redis key.
    We check that the project_key segment appears in the key string.

    Args:
        results: List of (redis_key, score) tuples.
        project_key: Project partition key to filter by.

    Returns:
        Filtered list containing only entries whose key includes project_key.
    """
    if not project_key:
        return results
    return [(k, s) for k, s in results if project_key in k]


def rrf_fuse(
    *ranked_lists: list[tuple[str, float]],
    k: int = 60,
    limit: int = 10,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked lists using Reciprocal Rank Fusion.

    Each ranked list is a sequence of (key, score) tuples sorted by score
    descending. The RRF score for a key is: sum(1 / (k + rank)) across
    all lists where it appears (rank is 1-based).

    Args:
        *ranked_lists: Variable number of ranked (key, score) lists.
        k: RRF constant. Higher = more uniform blending. Default 60.
        limit: Maximum results to return.

    Returns:
        List of (key, rrf_score) tuples sorted by RRF score descending.
    """
    scores: dict[str, float] = {}

    for ranked_list in ranked_lists:
        if not ranked_list:
            continue
        for rank_idx, (key, _score) in enumerate(ranked_list):
            # Normalize key to string (Redis returns bytes sometimes)
            str_key = key.decode() if isinstance(key, bytes) else str(key)
            rrf_score = 1.0 / (k + rank_idx + 1)  # 1-based rank
            scores[str_key] = scores.get(str_key, 0.0) + rrf_score

    # Sort by RRF score descending
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused[:limit]


def get_relevance_ranked(
    project_key: str,
    limit: int = 50,
) -> list[tuple[str, float]]:
    """Get relevance-ranked memory keys from the DecayingSortedField.

    Reads directly from the Redis sorted set backing Memory.relevance,
    partitioned by project_key. Returns (redis_key, decay_score) tuples
    sorted by score descending (most relevant first).

    Args:
        project_key: Project partition key.
        limit: Maximum entries to return.

    Returns:
        List of (redis_key, score) tuples. Empty list on any error.
    """
    try:
        from popoto import DecayingSortedField
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.memory import Memory

        sorted_set_key = DecayingSortedField.get_sortedset_db_key(Memory, "relevance", project_key)
        results = POPOTO_REDIS_DB.zrevrange(sorted_set_key.redis_key, 0, limit - 1, withscores=True)
        # Results are [(bytes_key, float_score), ...]
        return [(k.decode() if isinstance(k, bytes) else str(k), float(s)) for k, s in results]
    except Exception as e:
        logger.warning(f"[memory_retrieval] relevance ranked fetch failed: {e}")
        return []


def get_confidence_ranked(
    project_key: str,
    limit: int = 50,
) -> list[tuple[str, float]]:
    """Get confidence-ranked memory keys from the ConfidenceField hash.

    Reads all entries from the ConfidenceField companion hash, filters
    to the given project_key, and sorts by confidence score descending.
    Returns (redis_key, confidence) tuples.

    Args:
        project_key: Project partition key. Only entries whose Redis key
            contains this value are returned.
        limit: Maximum entries to return.

    Returns:
        List of (redis_key, confidence) tuples. Empty list on any error.
    """
    try:
        import msgpack
        from popoto import ConfidenceField
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.memory import Memory

        # Derive hash key from popoto API instead of hardcoding
        # TODO: Replace ":data" suffix with proper accessor once tomcounsell/popoto#323 ships
        base_key = ConfidenceField.get_special_use_field_db_key(Memory, "confidence")
        hash_key = base_key.redis_key + ":data"
        raw_data = POPOTO_REDIS_DB.hgetall(hash_key)

        if not raw_data:
            return []

        entries: list[tuple[str, float]] = []
        for member_key, packed_data in raw_data.items():
            try:
                str_key = member_key.decode() if isinstance(member_key, bytes) else str(member_key)
                data = msgpack.unpackb(packed_data, raw=False)
                confidence = float(data.get("confidence", 0.5))
                entries.append((str_key, confidence))
            except Exception:
                continue

        # Filter to project scope (project_key is embedded in Redis keys)
        entries = _filter_by_project(entries, project_key)

        # Sort by confidence descending
        entries.sort(key=lambda x: x[1], reverse=True)
        return entries[:limit]
    except Exception as e:
        logger.warning(f"[memory_retrieval] confidence ranked fetch failed: {e}")
        return []


def retrieve_memories(
    query_text: str,
    project_key: str,
    limit: int = 10,
    rrf_k: int | None = None,
) -> list[Any]:
    """Retrieve memories using BM25 + RRF fusion of three signals.

    Combines BM25 keyword match, temporal relevance, and confidence
    via Reciprocal Rank Fusion. Returns hydrated Memory instances
    with a `score` attribute set to the RRF fusion score.

    Args:
        query_text: Search query string.
        project_key: Project partition key.
        limit: Maximum memories to return.
        rrf_k: RRF constant override. Uses config default if None.

    Returns:
        List of Memory instances with `score` attribute, sorted by
        RRF score descending. Empty list on any error.
    """
    try:
        from popoto import BM25Field

        from config.memory_defaults import RRF_K
        from models.memory import Memory

        if rrf_k is None:
            rrf_k = RRF_K

        # Signal 1: BM25 keyword match (global index, post-filtered to project)
        try:
            bm25_results = BM25Field.search(Memory, "bm25", query_text, limit=50)
            bm25_results = _filter_by_project(bm25_results, project_key)
        except Exception as e:
            logger.warning(f"[memory_retrieval] BM25 search failed: {e}")
            bm25_results = []

        # Signal 2: Temporal relevance (decay-sorted, natively partitioned)
        relevance_results = get_relevance_ranked(project_key, limit=50)

        # Signal 3: Confidence (global hash, post-filtered to project)
        confidence_results = get_confidence_ranked(project_key, limit=50)

        # Fuse the three signals
        fused = rrf_fuse(
            bm25_results,
            relevance_results,
            confidence_results,
            k=rrf_k,
            limit=limit,
        )

        if not fused:
            # Analytics: record recall attempt with zero hits
            try:
                from analytics.collector import record_metric

                record_metric("memory.recall_attempt", 1, {"hits": 0, "project_key": project_key})
            except Exception:
                pass
            return []

        # Hydrate Memory instances from fused keys
        records = []
        for redis_key, rrf_score in fused:
            try:
                record = Memory.query.get(redis_key)
                if record is not None:
                    # Attach RRF score for downstream use (_apply_category_weights)
                    record.score = rrf_score
                    records.append(record)
            except Exception:
                continue

        # Filter out superseded records — archived memories remain in Redis for audit
        # but must not surface in recall. Handles both "" and None safely.
        records = [r for r in records if not r.superseded_by]

        # Analytics: record recall attempt with hit count
        try:
            from analytics.collector import record_metric

            dims = {"hits": len(records), "project_key": project_key}
            record_metric("memory.recall_attempt", 1, dims)
        except Exception:
            pass

        return records

    except Exception as e:
        logger.warning(f"[memory_retrieval] retrieve_memories failed: {e}")
        return []


def get_memories_in_time_range(
    project_key: str,
    since: float | None = None,
    until: float | None = None,
    limit: int = 100,
) -> list[Any]:
    """Retrieve Memory instances for a project within a time range.

    Uses the DecayingSortedField sorted set to build a score lookup (scores
    approximate creation timestamps), then hydrates records via
    Memory.query.filter. Records are filtered by score range and sorted
    by score descending (most recent first).

    Records marked as superseded are excluded.

    Args:
        project_key: Project partition key.
        since: Unix timestamp lower bound (inclusive). None = no lower bound.
        until: Unix timestamp upper bound (inclusive). None = no upper bound.
        limit: Maximum records to return.

    Returns:
        List of Memory instances with _timeline_score attribute, sorted by
        score descending (most recent first). Empty list on any error.
    """
    try:
        from models.memory import Memory

        # Build a score lookup from the relevance sorted set
        score_map: dict[str, float] = {}
        try:
            from popoto import DecayingSortedField
            from popoto.redis_db import POPOTO_REDIS_DB

            sorted_set_key = DecayingSortedField.get_sortedset_db_key(
                Memory, "relevance", project_key
            )
            min_score = since if since is not None else "-inf"
            max_score = until if until is not None else "+inf"

            raw_results = POPOTO_REDIS_DB.zrangebyscore(
                sorted_set_key.redis_key,
                min_score,
                max_score,
                withscores=True,
            )

            if raw_results:
                for redis_key, score in raw_results:
                    str_key = redis_key.decode() if isinstance(redis_key, bytes) else str(redis_key)
                    score_map[str_key] = float(score)
        except Exception as e:
            logger.debug(f"[memory_retrieval] sorted set lookup failed: {e}")

        # If we have no score map and time filters are set, return empty
        # (we cannot filter by time without scores)
        if not score_map and (since is not None or until is not None):
            return []

        # Hydrate all records for this project
        try:
            all_records = list(Memory.query.filter(project_key=project_key))
        except Exception as e:
            logger.warning(f"[memory_retrieval] time range query failed: {e}")
            return []

        # Filter out superseded records
        active = [r for r in all_records if not getattr(r, "superseded_by", "")]

        # If time filters are set, only include records whose Redis key
        # appeared in the score_map (already filtered by ZRANGEBYSCORE)
        if score_map:
            filtered = []
            for record in active:
                # Match record to score_map by checking if its memory_id is in a key
                mid = getattr(record, "memory_id", "")
                matched_score = None
                for skey, sval in score_map.items():
                    if mid and mid in skey:
                        matched_score = sval
                        break
                if matched_score is not None:
                    record._timeline_score = matched_score
                    filtered.append(record)
                elif since is None and until is None:
                    # No time filter — include all
                    record._timeline_score = 0.0
                    filtered.append(record)
            active = filtered
        else:
            # No score map — attach zero scores
            for record in active:
                record._timeline_score = 0.0

        # Sort by score descending (most recent first)
        active.sort(key=lambda r: getattr(r, "_timeline_score", 0.0), reverse=True)
        return active[:limit]

    except Exception as e:
        logger.warning(f"[memory_retrieval] get_memories_in_time_range failed: {e}")
        return []
