"""Q&A mode metrics tracking.

Redis-backed counters for intent classification distribution and response
times. All operations are fire-and-forget -- metrics failures must never
affect message processing.
"""

from __future__ import annotations

import logging
import time

from agent.intent_classifier import QA_CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)

# Redis key prefix for Q&A metrics
_PREFIX = "qa_metrics"

# Module-level lazy singleton for Redis connection
_redis_client = None


def _get_redis():
    """Get Redis connection (lazy singleton), returns None on failure."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        from config.redis_config import get_redis_url

        _redis_client = redis.Redis.from_url(get_redis_url(), decode_responses=True)
        return _redis_client
    except Exception:
        return None


def record_classification(intent: str, confidence: float) -> None:
    """Record an intent classification result.

    Args:
        intent: "qa" or "work"
        confidence: Classification confidence (0.0-1.0)
    """
    try:
        r = _get_redis()
        if not r:
            return

        if intent == "qa" and confidence >= QA_CONFIDENCE_THRESHOLD:
            r.incr(f"{_PREFIX}:qa_classified_count")
        elif intent == "qa":
            r.incr(f"{_PREFIX}:qa_low_confidence_count")
        else:
            r.incr(f"{_PREFIX}:work_classified_count")

        logger.debug(f"[qa_metrics] Recorded classification: {intent} ({confidence:.2f})")
    except Exception as e:
        logger.debug(f"[qa_metrics] Failed to record classification: {e}")


def record_response_time(mode: str, elapsed_seconds: float) -> None:
    """Record response time for a Q&A or work session.

    Args:
        mode: "qa" or "work"
        elapsed_seconds: Time from message receipt to response delivery.
    """
    try:
        r = _get_redis()
        if not r:
            return

        key = f"{_PREFIX}:{mode}_response_times"
        # Store as a sorted set with timestamp as score for time-windowed analysis
        r.zadd(key, {f"{elapsed_seconds:.2f}:{time.time():.0f}": time.time()})
        # Keep only last 1000 entries
        r.zremrangebyrank(key, 0, -1001)

        logger.debug(f"[qa_metrics] Recorded {mode} response time: {elapsed_seconds:.2f}s")
    except Exception as e:
        logger.debug(f"[qa_metrics] Failed to record response time: {e}")


def get_stats() -> dict:
    """Get current Q&A metrics summary.

    Returns:
        Dict with classification counts and average response times.
    """
    try:
        r = _get_redis()
        if not r:
            return {}

        qa_count = int(r.get(f"{_PREFIX}:qa_classified_count") or 0)
        work_count = int(r.get(f"{_PREFIX}:work_classified_count") or 0)
        low_conf_count = int(r.get(f"{_PREFIX}:qa_low_confidence_count") or 0)

        return {
            "qa_classified": qa_count,
            "work_classified": work_count,
            "qa_low_confidence": low_conf_count,
            "total": qa_count + work_count + low_conf_count,
        }
    except Exception as e:
        logger.debug(f"[qa_metrics] Failed to get stats: {e}")
        return {}
