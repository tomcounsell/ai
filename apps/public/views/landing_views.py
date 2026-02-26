"""
Homepage view for Yudame platform.

The homepage leads with recent podcast episodes and provides secondary
navigation to AI tools (MCP servers). Context includes the 3 most recently
published episodes for the featured section.
"""

from django.shortcuts import redirect
from django.views.generic import TemplateView

from apps.podcast.models import Episode


class HomeView(TemplateView):
    """
    Main homepage for the Yudame platform.

    Displays:
    - Hero section with platform overview
    - Latest published podcast episodes (up to 3)
    - AI Tools section (Creative Juices, CTO Tools)

    Authenticated users are redirected to the dashboard.

    Template: templates/home.html
    URL: / (root)
    """

    template_name = "home.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("public:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["recent_episodes"] = (
            Episode.objects.filter(published_at__isnull=False)
            .select_related("podcast")
            .order_by("-published_at")[:3]
        )
        return context
