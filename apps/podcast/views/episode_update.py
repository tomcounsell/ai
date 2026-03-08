from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.podcast.models import Episode, Podcast


class EpisodeUpdateFieldView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Handle inline field updates for episode metadata via HTMX PATCH requests."""

    def test_func(self) -> bool:
        slug = self.kwargs.get("slug")
        if not slug:
            return False
        podcast = get_object_or_404(Podcast, slug=slug)
        return self.request.user.is_staff or podcast.owner == self.request.user

    def patch(self, request, slug: str, episode_slug: str):
        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        field = request.POST.get("field")
        value = request.POST.get(field, "")

        # Whitelist editable fields
        allowed_fields = ["title", "description", "show_notes", "tags"]
        if field not in allowed_fields:
            return HttpResponse("Invalid field", status=400)

        # Validate required fields are not empty
        if field in ["title", "description"] and not value.strip():
            return HttpResponse(
                f'<span class="text-red-600">{field.title()} cannot be empty</span>',
                status=400,
            )

        setattr(
            episode,
            field,
            value.strip() if field in ["title", "description"] else value,
        )
        episode.save(update_fields=[field])

        return HttpResponse(
            '<span class="text-green-600"><i class="fas fa-check-circle"></i> Saved</span>',
            status=200,
        )
