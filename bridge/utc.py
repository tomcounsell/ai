"""UTC timestamp utilities for consistent timezone handling.

All timestamps in the system should be tz-aware UTC. Use these utilities
instead of datetime.now() to ensure consistency across logs, storage,
and cross-component correlation.

Display conversion to local time happens only at the presentation boundary
(e.g., Telegram messages to humans).
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return current time as tz-aware UTC datetime."""
    return datetime.now(UTC)


def to_local(ts: datetime) -> datetime:
    """Convert a tz-aware UTC datetime to machine-local time for display.

    Raises ValueError if given a naive (timezone-unaware) datetime,
    to catch missed conversions early.
    """
    if ts.tzinfo is None:
        raise ValueError(
            "to_local() requires a tz-aware datetime. "
            "Got naive datetime — use utc_now() instead of datetime.now()."
        )
    return ts.astimezone()


def utc_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return utc_now().isoformat().replace("+00:00", "Z")
