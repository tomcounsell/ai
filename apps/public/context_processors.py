"""
Context processors for the public app.

These processors add variables to the context of all templates rendered
through a RequestContext.
"""

from typing import Any, Dict

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
    if path == "/":
        active_section = "home"
    elif path.startswith("/landing"):
        active_section = "home"  # Treat landing as home for nav
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
