"""
Context processors for the public app.

These processors add variables to the context of all templates rendered
through a RequestContext.
"""

from typing import Any

from django.http import HttpRequest


def active_navigation(request: HttpRequest) -> dict[str, Any]:
    """
    Determines the active navigation section based on the current URL path.

    Args:
        request: The current request object

    Returns:
        Dict containing the active_section key with the name of the active
        navigation section as a string
    """
    path = request.path
    active_section = None

    # Determine active section based on URL path
    if path == "/" or path.startswith("/landing"):
        active_section = "home"
    elif path.startswith("/todos"):
        active_section = "todos"
    elif path.startswith("/team"):
        active_section = "teams"
    elif path.startswith("/account"):
        active_section = "account"
    elif path.startswith("/admin"):
        active_section = "admin"
    elif path.startswith("/pricing"):
        active_section = "pricing"
    elif path.startswith("/blog"):
        active_section = "blog"

    return {"active_section": active_section}


def debug_toolbar_toggle(request: HttpRequest) -> dict[str, Any]:
    from settings.env import LOCAL, STAGE

    if not (LOCAL or STAGE):
        return {}
    return {
        "show_debug_toolbar_toggle": True,
        "debug_toolbar_enabled": request.COOKIES.get("debug_toolbar", "on") == "on",
    }
