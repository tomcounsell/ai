"""
Account menu context function.
"""

from django.http import HttpRequest


def get_account_menu_context(request: HttpRequest):
    """
    Return context for the account menu.

    Displays user-specific options such as settings and logout.
    Shows login link for unauthenticated users.

    Args:
        request: The HttpRequest object

    Returns:
        dict: Context variables for the account menu template
    """
    return {}
