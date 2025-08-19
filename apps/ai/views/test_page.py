"""Test page view for AI chat."""

import os

from django.shortcuts import render
from django.views import View


class TestChatPageView(View):
    """Serve the test chat HTML page."""

    def get(self, request):
        """Render the test chat page."""
        context = {"openai_key_configured": bool(os.getenv("OPENAI_API_KEY"))}
        return render(request, "ai/test_chat.html", context)
