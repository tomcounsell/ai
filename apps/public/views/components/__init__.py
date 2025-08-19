"""
Context functions for reusable UI components.

This module provides functions to generate context data for UI components
used across the application, replacing the previous component framework.
"""

from .account_menu import get_account_menu_context
from .footer import get_footer_context
from .navbar import get_navbar_context
from .toast import get_toast_context

__all__ = [
    "get_account_menu_context",
    "get_footer_context",
    "get_navbar_context",
    "get_toast_context",
]
