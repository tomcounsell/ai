from django.utils import timezone
from django.views.generic import ListView, TemplateView

from apps.book.models import Announcement


class LandingView(TemplateView):
    """Book landing page with hero section and buying links."""

    template_name = "book/landing.html"


class AnnouncementListView(ListView):
    """Chronological list of published announcements."""

    template_name = "book/announcements.html"
    context_object_name = "announcements"

    def get_queryset(self):
        return Announcement.objects.filter(
            published_at__isnull=False,
            published_at__lte=timezone.now(),
        ).order_by("-published_at")
