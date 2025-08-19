"""
Navigation bar context function.
"""

from django.http import HttpRequest


def get_navbar_context(request: HttpRequest, active_thing_id=None):
    """
    Return context for the navbar.

    Args:
        request: The HttpRequest object
        active_thing_id: Optional ID of the currently active item

    Returns:
        dict: Context variables for the navbar template
    """
    return {}
