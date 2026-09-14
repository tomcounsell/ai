"""UTC timestamp utilities for consistent timezone handling.

All timestamps in the system should be tz-aware UTC. Use these utilities
instead of datetime.now() to ensure consistency across logs, storage,
and cross-component correlation.

Display conversion to local time happens only at the presentation boundary.

This module is dependency-free by contract: it imports nothing outside the
standard library. Standalone ``tools/`` packages import it, and any first-party
import added here would chain them back to the harness.
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


def to_unix_ts(val) -> float | None:
    """Convert a datetime/float/ISO-string to a Unix timestamp.

    Naive datetimes are treated as UTC, avoiding the default Python behavior
    where ``.timestamp()`` on a naive datetime interprets it as machine-local
    time — which silently offsets every age calculation by the machine's UTC
    offset (e.g., 7h on UTC+7 hosts). popoto 1.9.0 decodes every stored
    datetime as aware UTC; the naive-tzinfo branches below are for callers
    passing a `created_at` (no `__setattr__` coercion), an ISO string, or a
    plain float from a non-popoto source — this is the repo's canonical
    coercer and every caller outside popoto is a candidate producer.

    Returns None when the input cannot be coerced.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        # Keep: general-purpose coercer accepting datetime | int | float |
        # str from mixed callers, not exclusively popoto reads.
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        # Keep: an ISO string from a non-popoto source may carry no offset.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    return None
