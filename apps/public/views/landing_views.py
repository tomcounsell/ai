"""
Landing page views for AI Platform homepage.
"""

from django.views.generic import TemplateView


class AIPlatformLandingView(TemplateView):
    """
    Main landing page for the AI Integration Platform.

    Showcases available MCP servers and provides quick start guide.
    """

    template_name = "landing/ai_platform.html"
