"""
Homepage and landing page views for Yudame platform.

The homepage leads with recent podcast episodes and provides secondary
navigation to AI tools (MCP servers). Context includes the 3 most recently
published episodes for the featured section.

BriefingLandingView serves /briefing/ — a dedicated page explaining the
"AI Briefing" product promise. Accessible to both anonymous and authenticated
users; CTA varies by auth state.
"""

from django.shortcuts import redirect
from django.views.generic import TemplateView

from apps.podcast.models import Episode
from apps.public.views.helpers.main_content_view import MainContentView


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


class BriefingLandingView(MainContentView):
    """
    Landing page for the AI Briefing product at /briefing/.

    Accessible to both anonymous and authenticated users. The hero headline,
    three-step workflow, and what-you-get section are always rendered. The CTA
    varies by auth state:
    - Anonymous: "Get Your First Briefing" → /accounts/signup/
    - Authenticated: "Start a Briefing" → podcast:list

    Template: templates/briefing.html
    URL: /briefing/ (named public:briefing)
    """

    template_name = "briefing.html"

    def get(self, request, *args, **kwargs):
        """Render the briefing landing page with auth-aware context."""
        self.context["user_is_authenticated"] = request.user.is_authenticated
        return self.render(request)
