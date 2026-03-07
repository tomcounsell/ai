from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.podcast.models import Episode, Podcast


class EpisodeUpdateFieldView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Handle inline field updates for episode metadata via HTMX PATCH requests."""

    def test_func(self) -> bool:
        return self.request.user.is_staff

    def patch(self, request, slug: str, episode_slug: str):
        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        field = request.POST.get("field")
        value = request.POST.get(field, "")

        # Whitelist editable fields
        allowed_fields = ["title", "description", "show_notes", "tags"]
        if field not in allowed_fields:
            return HttpResponse("Invalid field", status=400)

        setattr(episode, field, value)
        episode.save(update_fields=[field])

        return HttpResponse(
            '<span class="text-green-600"><i class="fas fa-check-circle"></i> Saved</span>',
            status=200,
        )
