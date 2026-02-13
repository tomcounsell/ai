from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import cache_page

from apps.podcast.models import Podcast


@method_decorator(cache_page(300), name="dispatch")
class PodcastFeedView(View):
    """Generate a valid podcast RSS XML feed for a given Podcast."""

    def get(self, request, slug: str) -> HttpResponse:
        podcast = get_object_or_404(Podcast, slug=slug, is_public=True)
        episodes = (
            podcast.episodes.filter(
                published_at__isnull=False,
            )
            .filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            )
            .order_by("-episode_number")
        )
        xml = render_to_string(
            "podcast/feed.xml",
            {
                "podcast": podcast,
                "episodes": episodes,
                "request": request,
            },
        )
        return HttpResponse(xml, content_type="application/rss+xml; charset=utf-8")
