"""Teammate mode metrics tracking.

Popoto-backed counters for intent classification distribution and response
times. All operations are fire-and-forget -- metrics failures must never
affect message processing.

Uses the TeammateMetrics Popoto model (single-instance pattern) instead of
raw Redis commands for proper ORM lifecycle and index management.
"""

from __future__ import annotations

import logging
import time

from agent.intent_classifier import TEAMMATE_CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)

# Max response time entries to keep per mode
_MAX_RESPONSE_TIMES = 1000


def _get_metrics():
    """Get the singleton TeammateMetrics record, returns None on failure."""
    try:
        from models.teammate_metrics import TeammateMetrics

        return TeammateMetrics.get_or_create()
    except Exception:
        return None


def record_classification(intent: str, confidence: float) -> None:
    """Record an intent classification result.

    Args:
        intent: "teammate" or "work"
        confidence: Classification confidence (0.0-1.0)
    """
    try:
        metrics = _get_metrics()
        if not metrics:
            return

        if intent == "teammate" and confidence >= TEAMMATE_CONFIDENCE_THRESHOLD:
            metrics.teammate_classified_count = (metrics.teammate_classified_count or 0) + 1
        elif intent == "teammate":
            metrics.teammate_low_confidence_count = (metrics.teammate_low_confidence_count or 0) + 1
        else:
            metrics.work_classified_count = (metrics.work_classified_count or 0) + 1

        metrics.save()
        logger.debug(f"[teammate_metrics] Recorded classification: {intent} ({confidence:.2f})")
    except Exception as e:
        logger.debug(f"[teammate_metrics] Failed to record classification: {e}")


def record_response_time(mode: str, elapsed_seconds: float) -> None:
    """Record response time for a teammate or work session.

    Args:
        mode: "teammate" or "work"
        elapsed_seconds: Time from message receipt to response delivery.
    """
    try:
        metrics = _get_metrics()
        if not metrics:
            return

        # Store as member:score in the sorted field (score=timestamp for time-windowed analysis)
        member = f"{elapsed_seconds:.2f}:{time.time():.0f}"
        timestamp = time.time()

        if mode == "teammate":
            times = dict(metrics.teammate_response_times or {})
            times[member] = timestamp
            # Keep only last N entries by removing oldest
            if len(times) > _MAX_RESPONSE_TIMES:
                sorted_entries = sorted(times.items(), key=lambda x: x[1])
                times = dict(sorted_entries[-_MAX_RESPONSE_TIMES:])
            metrics.teammate_response_times = times
        else:
            times = dict(metrics.work_response_times or {})
            times[member] = timestamp
            if len(times) > _MAX_RESPONSE_TIMES:
                sorted_entries = sorted(times.items(), key=lambda x: x[1])
                times = dict(sorted_entries[-_MAX_RESPONSE_TIMES:])
            metrics.work_response_times = times

        metrics.save()
        logger.debug(f"[teammate_metrics] Recorded {mode} response time: {elapsed_seconds:.2f}s")
    except Exception as e:
        logger.debug(f"[teammate_metrics] Failed to record response time: {e}")


def get_stats() -> dict:
    """Get current teammate metrics summary.

    Returns:
        Dict with classification counts and average response times.
    """
    try:
        metrics = _get_metrics()
        if not metrics:
            return {}

        teammate_count = metrics.teammate_classified_count or 0
        work_count = metrics.work_classified_count or 0
        low_conf_count = metrics.teammate_low_confidence_count or 0

        return {
            "teammate_classified": teammate_count,
            "work_classified": work_count,
            "teammate_low_confidence": low_conf_count,
            "total": teammate_count + work_count + low_conf_count,
        }
    except Exception as e:
        logger.debug(f"[teammate_metrics] Failed to get stats: {e}")
        return {}
