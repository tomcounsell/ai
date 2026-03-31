"""Field observability utilities.

Lightweight helpers for monitoring unusually large field values at storage
boundaries. These are observation-only -- they never truncate or reject data.
"""

import logging

logger = logging.getLogger(__name__)


def log_large_field(
    field_name: str,
    value: str | None,
    threshold: int = 50_000,
) -> None:
    """Log a warning when a field value exceeds a soft size threshold.

    This is an observability-only helper. It never truncates, rejects, or
    modifies the value. Use it at high-traffic storage entry points
    (e.g., job enqueue, TelegramMessage creation) to surface unexpectedly
    large values for investigation.

    Args:
        field_name: Name of the field being checked (for log context).
        value: The string value to check. None and empty strings are no-ops.
        threshold: Character count above which a warning is emitted.
            Defaults to 50,000 (~12k tokens).
    """
    if not value:
        return
    length = len(value)
    if length > threshold:
        logger.warning(
            f"Large field value: {field_name}={length} chars "
            f"(threshold={threshold})"
        )
