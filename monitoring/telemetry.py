"""Observer agent telemetry tracking.

Provides functions for recording observer decisions, interjections,
and skips. Uses the ObserverTelemetry Popoto model for storage with
daily rollup keys and 7-day TTL for automatic cleanup.
"""

import logging
from datetime import UTC, datetime

from models.telemetry import ObserverTelemetry

logger = logging.getLogger(__name__)


def _today_key() -> str:
    """Get today's date key in YYYY-MM-DD format."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def record_decision(context: str | None = None) -> None:
    """Record an observer decision.

    Args:
        context: Optional context string describing the decision
    """
    try:
        record = ObserverTelemetry.get_or_create(_today_key())
        record.record_decision(context)
    except Exception as e:
        logger.debug(f"Telemetry record_decision failed (non-fatal): {e}")


def record_interjection(description: str) -> None:
    """Record an observer interjection with details.

    Args:
        description: Description of the interjection event
    """
    try:
        record = ObserverTelemetry.get_or_create(_today_key())
        record.record_interjection(description)
    except Exception as e:
        logger.debug(f"Telemetry record_interjection failed (non-fatal): {e}")


def record_skip() -> None:
    """Record a decision to skip/not interject."""
    try:
        record = ObserverTelemetry.get_or_create(_today_key())
        record.record_skip()
    except Exception as e:
        logger.debug(f"Telemetry record_skip failed (non-fatal): {e}")


def get_health() -> dict:
    """Get telemetry health summary for dashboard display.

    Returns a dict with today's counters and recent event list.
    """
    try:
        record = ObserverTelemetry.get_or_create(_today_key())
        return {
            "date": record.date_key,
            "decisions": record.decisions or 0,
            "interjections": record.interjections or 0,
            "skips": record.skips or 0,
            "recent_events": list(record.events or [])[-10:],
        }
    except Exception as e:
        logger.debug(f"Telemetry get_health failed (non-fatal): {e}")
        return {
            "date": _today_key(),
            "decisions": 0,
            "interjections": 0,
            "skips": 0,
            "recent_events": [],
            "error": str(e),
        }
