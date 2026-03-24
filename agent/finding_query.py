"""Finding query for cross-agent knowledge relay.

Retrieves prior findings for a work item (slug) using manual composite
scoring. Uses ExistenceFilter for O(1) pre-check before running the
full query.

Score weights:
- Recency (DecayingSortedField score): 0.4
- Confidence (ConfidenceField): 0.3
- Access frequency (AccessTrackerMixin): 0.2
- Topic relevance (keyword match): 0.1

All operations fail silently -- query failures must never crash the agent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.finding import Finding

logger = logging.getLogger(__name__)

# Composite score weights
WEIGHT_RELEVANCE = 0.4
WEIGHT_CONFIDENCE = 0.3
WEIGHT_ACCESS = 0.2
WEIGHT_TOPIC = 0.1

# Default limits
DEFAULT_LIMIT = 10
MAX_QUERY_RESULTS = 50


def query_findings(
    slug: str,
    topics: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Finding]:
    """Retrieve top findings for a work item, ranked by composite score.

    Uses ExistenceFilter pre-check to short-circuit when no relevant
    findings exist. Falls back to slug-based query if bloom check is
    inconclusive.

    Args:
        slug: Work item scope.
        topics: Optional topic keywords to filter by relevance.
        limit: Maximum findings to return.

    Returns:
        List of Finding records, sorted by composite score (highest first).
        Empty list on any failure.
    """
    if not slug:
        return []

    try:
        from models.finding import Finding

        # Bloom pre-check: if topics provided, check if any are in bloom
        if topics and not _bloom_has_relevant(Finding, topics):
            logger.debug(
                f"[finding_query] Bloom says no relevant findings for "
                f"slug={slug}, topics={topics[:3]}"
            )
            return []

        # Query all findings for this slug
        candidates = Finding.query_by_slug(slug, limit=MAX_QUERY_RESULTS)
        if not candidates:
            return []

        # Score and rank
        scored = []
        for finding in candidates:
            score = _composite_score(finding, topics)
            scored.append((score, finding))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Mark accessed findings
        results = []
        for _score, finding in scored[:limit]:
            try:
                finding.confirm_access()
            except Exception:
                pass
            results.append(finding)

        logger.debug(f"[finding_query] Returning {len(results)} findings for slug={slug}")
        return results

    except Exception as e:
        logger.warning(f"[finding_query] Query failed (non-fatal): {e}")
        return []


def format_findings_for_injection(
    findings: list[Finding],
    max_tokens: int = 2000,
) -> str | None:
    """Format findings as a text block for prompt injection.

    Returns a formatted string suitable for prepending to dev-session
    prompts, or None if no findings to inject.

    Args:
        findings: List of Finding records to format.
        max_tokens: Approximate token budget (chars / 4).

    Returns:
        Formatted findings text, or None.
    """
    if not findings:
        return None

    try:
        max_chars = max_tokens * 4  # rough approximation
        parts = ["## Prior Findings from Earlier Stages\n"]
        current_chars = len(parts[0])

        for finding in findings:
            content = getattr(finding, "content", "") or ""
            stage = getattr(finding, "stage", "") or ""
            category = getattr(finding, "category", "") or ""
            file_paths = getattr(finding, "file_paths", "") or ""

            entry = f"- [{stage}] ({category})"
            if file_paths:
                entry += f" [{file_paths}]"
            entry += f": {content}\n"

            if current_chars + len(entry) > max_chars:
                break

            parts.append(entry)
            current_chars += len(entry)

        if len(parts) <= 1:
            return None

        return "".join(parts)

    except Exception as e:
        logger.warning(f"[finding_query] Format failed (non-fatal): {e}")
        return None


def _bloom_has_relevant(finding_cls: type, topics: list[str]) -> bool:
    """Check ExistenceFilter for any topic hit.

    Returns True if bloom says at least one topic might have findings,
    or if bloom check is unavailable (fail-open).
    """
    try:
        bloom_field = finding_cls._meta.fields.get("bloom")
        if not bloom_field:
            return True  # Fail-open: no bloom, proceed with full query

        for topic in topics:
            try:
                if bloom_field.might_exist(finding_cls, topic):
                    return True
            except Exception:
                continue

        return False

    except Exception:
        return True  # Fail-open on any error


def _composite_score(finding: Finding, topics: list[str] | None = None) -> float:
    """Compute composite score for a finding.

    Combines:
    - Relevance (DecayingSortedField): 0.4
    - Confidence: 0.3
    - Access frequency: 0.2
    - Topic keyword match: 0.1
    """
    score = 0.0

    try:
        # Relevance: importance weighted by time decay
        # DecayingSortedField uses: base_score * elapsed_days ^ (-decay_rate)
        # We replicate that formula client-side since the Redis Lua score
        # isn't exposed on the instance (only the timestamp is stored).
        import math
        import time

        importance = getattr(finding, "importance", 3.0) or 3.0
        relevance_ts = getattr(finding, "relevance", None)
        if relevance_ts and isinstance(relevance_ts, int | float) and relevance_ts > 0:
            elapsed_days = max((time.time() - float(relevance_ts)) / 86400.0, 0.01)
            decay_rate = 0.5  # matches DecayingSortedField default
            decay_factor = elapsed_days ** (-decay_rate)
            decayed = importance * decay_factor
        else:
            decayed = importance
        # Normalize to 0-1 range (importance max 10.0, decay_factor <= 1.0 for age >= 1 day)
        relevance_score = min(decayed / 10.0, 1.0)
        score += WEIGHT_RELEVANCE * relevance_score
    except Exception:
        pass

    try:
        # Confidence from ConfidenceField
        confidence = getattr(finding, "confidence", None)
        if confidence is not None:
            # ConfidenceField stores a float value
            conf_val = float(confidence) if not callable(confidence) else 0.5
            score += WEIGHT_CONFIDENCE * conf_val
        else:
            score += WEIGHT_CONFIDENCE * 0.5
    except Exception:
        score += WEIGHT_CONFIDENCE * 0.5

    try:
        # Access frequency from AccessTrackerMixin
        access_count = getattr(finding, "_at_access_count", 0) or 0
        # Normalize: log scale, cap at 10 accesses
        import math

        access_score = min(math.log1p(access_count) / math.log1p(10), 1.0)
        score += WEIGHT_ACCESS * access_score
    except Exception:
        pass

    try:
        # Topic relevance via keyword matching
        if topics:
            content = (getattr(finding, "content", "") or "").lower()
            file_paths = (getattr(finding, "file_paths", "") or "").lower()
            combined = content + " " + file_paths
            matches = sum(1 for t in topics if t.lower() in combined)
            topic_score = min(matches / max(len(topics), 1), 1.0)
            score += WEIGHT_TOPIC * topic_score
    except Exception:
        pass

    return score
