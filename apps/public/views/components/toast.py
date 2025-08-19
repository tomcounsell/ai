"""
Toast notifications context function.
"""

from django.http import HttpRequest


def get_toast_context(request: HttpRequest):
    """
    Return context for toast notifications.

    Toast notifications for displaying messages to the user.
    Renders different styled notifications based on message type
    (success, info, warning, error).

    Args:
        request: The HttpRequest object

    Returns:
        dict: Context variables for the toast template
    """
    return {}
