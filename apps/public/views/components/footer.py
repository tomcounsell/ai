"""
Footer context function.
"""

from django.http import HttpRequest


def get_footer_context(request: HttpRequest):
    """
    Return context for the footer.

    Site footer shown at the bottom of all pages.
    Contains copyright information and site attribution.

    Args:
        request: The HttpRequest object

    Returns:
        dict: Context variables for the footer template
    """
    return {}
