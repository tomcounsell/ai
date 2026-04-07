"""
Escape hatch for explicit human input requests.

This module provides a mechanism for the agent to explicitly request human input
when genuinely blocked. The request_human_input() function generates a specially
marked message that bypasses auto-continue logic to ensure these requests are
not bypassed.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Marker prefix that signals the classifier to treat this as requiring human input
HUMAN_INPUT_MARKER = "[HUMAN_INPUT_REQUIRED]"

# Flag file to track pending human input requests (for audit and detection)
_pending_request: dict | None = None


def request_human_input(
    reason: str,
    options: list[str] | None = None,
) -> str:
    """
    Force a pause and request human input.

    Use ONLY when genuinely blocked on something you cannot resolve:
    - Missing credentials you cannot obtain
    - Ambiguous requirements after checking all context
    - Scope decision with significant business impact

    Args:
        reason: Clear explanation of why human input is needed
        options: Optional list of choices for the human to pick from

    Returns:
        Formatted message with the HUMAN_INPUT_REQUIRED marker

    Raises:
        ValueError: If reason is empty or whitespace-only
    """
    global _pending_request

    # Validate reason
    if not reason or not reason.strip():
        raise ValueError("reason must be a non-empty string")

    reason = reason.strip()

    # Build the formatted message
    message_parts = [
        HUMAN_INPUT_MARKER,
        "",
        f"**Human Input Needed:** {reason}",
    ]

    if options:
        # Filter out empty options
        valid_options = [opt.strip() for opt in options if opt and opt.strip()]
        if valid_options:
            message_parts.append("")
            message_parts.append("**Options:**")
            for i, option in enumerate(valid_options, 1):
                message_parts.append(f"  {i}. {option}")

    formatted_message = "\n".join(message_parts)

    # Log the request for audit purposes
    _pending_request = {
        "timestamp": datetime.now().isoformat(),
        "reason": reason,
        "options": options,
        "formatted_message": formatted_message,
    }

    logger.info(
        "Human input requested: reason=%r, options=%r",
        reason,
        options,
    )

    return formatted_message


def has_pending_request() -> bool:
    """Check if there is a pending human input request."""
    return _pending_request is not None


def get_pending_request() -> dict | None:
    """Get the pending human input request details."""
    return _pending_request


def clear_pending_request() -> None:
    """Clear the pending human input request after it's been handled."""
    global _pending_request
    _pending_request = None
    logger.debug("Pending human input request cleared")


def is_human_input_required(message: str) -> bool:
    """
    Check if a message contains the human input required marker.

    This is used by the classifier/auto-continue logic to detect
    explicit human input requests and bypass auto-continue.

    Args:
        message: The message to check

    Returns:
        True if the message starts with the HUMAN_INPUT_MARKER
    """
    if not message:
        return False
    return message.strip().startswith(HUMAN_INPUT_MARKER)
