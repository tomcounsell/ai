from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views import View

from apps.common.services.storage import get_file_url
from apps.podcast.models import Podcast


class PodcastFeedView(View):
    """Generate a valid podcast RSS XML feed for a given Podcast.

    Public podcasts: served with 5-minute cache, permanent URLs.
    Private podcasts: requires ?token= parameter, generates fresh signed URLs.
    """

    def get(self, request, slug: str) -> HttpResponse:
        podcast = get_object_or_404(Podcast, slug=slug)

        if not podcast.is_public:
            return self._serve_private_feed(request, podcast)
        return self._serve_public_feed(request, podcast)

    def _serve_public_feed(self, request, podcast) -> HttpResponse:
        # Check Django cache first (invalidated on episode publish/unpublish)
        cache_key = f"podcast_feed_{podcast.slug}"
        cached_xml = cache.get(cache_key)

        if cached_xml is None:
            # Generate feed XML
            episodes = self._published_episodes(podcast)
            cached_xml = render_to_string(
                "podcast/feed.xml",
                {"podcast": podcast, "episodes": episodes, "request": request},
            )
            # Cache for 5 minutes (matches HTTP Cache-Control header)
            cache.set(cache_key, cached_xml, 300)

        response = HttpResponse(
            cached_xml, content_type="application/rss+xml; charset=utf-8"
        )
        response["Cache-Control"] = "public, max-age=300"
        return response

    def _serve_private_feed(self, request, podcast) -> HttpResponse:
        # Validate access token
        token = request.GET.get("token", "")
        expected_token = getattr(settings, "SUPABASE_USER_ACCESS_TOKEN", "")
        if not token or not expected_token or token != expected_token:
            return HttpResponseForbidden("Invalid or missing access token.")

        episodes = list(self._published_episodes(podcast))

        # Generate fresh signed URLs for private episodes.
        # Override in-memory attributes so the template renders signed URLs
        # without needing conditional logic.
        for episode in episodes:
            if episode.audio_url:
                episode.audio_url = get_file_url(episode.audio_url, public=False)
            if episode.cover_image_url:
                episode.cover_image_url = get_file_url(
                    episode.cover_image_url, public=False
                )

        xml = render_to_string(
            "podcast/feed.xml",
            {"podcast": podcast, "episodes": episodes, "request": request},
        )
        response = HttpResponse(xml, content_type="application/rss+xml; charset=utf-8")
        response["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def _published_episodes(podcast):
        """Get published, non-expired episodes ordered by episode number."""
        return (
            podcast.episodes.filter(published_at__isnull=False)
            .filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            )
            .order_by("-episode_number")
        )
