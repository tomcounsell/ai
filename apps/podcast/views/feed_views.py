from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View

from apps.common.services.storage import get_file_url
from apps.podcast.models import Podcast, PodcastAccessToken


class PodcastFeedView(View):
    """Generate a valid podcast RSS XML feed for a given Podcast.

    Public podcasts: served with 5-minute cache, permanent URLs.
    Unlisted podcasts: same as public (cached, permanent URLs), just not indexed.
    Restricted podcasts: requires ?token= parameter, generates fresh signed URLs.
    """

    def get(self, request, slug: str) -> HttpResponse:
        podcast = get_object_or_404(Podcast, slug=slug)

        if podcast.privacy == Podcast.Privacy.PUBLIC:
            return self._serve_public_feed(request, podcast)

        if podcast.privacy == Podcast.Privacy.UNLISTED:
            return self._serve_unlisted_feed(request, podcast)

        # Restricted
        return self._serve_restricted_feed(request, podcast)

    def _build_feed_context(self, request, podcast, episodes):
        """Build the template context for the RSS feed.

        Computes absolute URLs for the podcast page and each episode page
        so the <link> elements are never empty.
        """
        podcast_page_url = request.build_absolute_uri(
            reverse("podcast:detail", kwargs={"slug": podcast.slug})
        )

        # Annotate each episode with its absolute detail page URL
        for episode in episodes:
            episode.page_url = request.build_absolute_uri(
                reverse(
                    "podcast:episode_detail",
                    kwargs={
                        "slug": podcast.slug,
                        "episode_slug": episode.slug,
                    },
                )
            )

        return {
            "podcast": podcast,
            "episodes": episodes,
            "podcast_page_url": podcast_page_url,
            "request": request,
        }

    def _serve_public_feed(self, request, podcast) -> HttpResponse:
        """Public: cached 5 min, permanent audio URLs from public bucket."""
        cache_key = f"podcast_feed_{podcast.slug}"
        cached_xml = cache.get(cache_key)

        if cached_xml is None:
            episodes = list(self._published_episodes(podcast))
            context = self._build_feed_context(request, podcast, episodes)
            cached_xml = render_to_string("podcast/feed.xml", context)
            cache.set(cache_key, cached_xml, 300)

        response = HttpResponse(
            cached_xml, content_type="application/rss+xml; charset=utf-8"
        )
        response["Cache-Control"] = "public, max-age=300"
        return response

    def _serve_unlisted_feed(self, request, podcast) -> HttpResponse:
        """Unlisted: same as public (cached, permanent URLs) but not indexed."""
        cache_key = f"podcast_feed_{podcast.slug}"
        cached_xml = cache.get(cache_key)

        if cached_xml is None:
            episodes = list(self._published_episodes(podcast))
            context = self._build_feed_context(request, podcast, episodes)
            cached_xml = render_to_string("podcast/feed.xml", context)
            cache.set(cache_key, cached_xml, 300)

        response = HttpResponse(
            cached_xml, content_type="application/rss+xml; charset=utf-8"
        )
        response["Cache-Control"] = "public, max-age=300"
        return response

    def _serve_restricted_feed(self, request, podcast) -> HttpResponse:
        """Restricted: validate per-podcast token, generate signed URLs."""
        is_owner = (
            request.user.is_authenticated
            and podcast.owner
            and request.user == podcast.owner
        )

        if not is_owner:
            token_str = request.GET.get("token", "")
            if not token_str:
                return HttpResponseForbidden("Missing access token.")

            # Check per-podcast tokens first
            access_token = PodcastAccessToken.objects.filter(
                podcast=podcast, token=token_str, is_active=True
            ).first()

            if access_token:
                access_token.record_access()
            else:
                # Fallback: shared env token (backward compat, remove in v2)
                expected = getattr(settings, "SUPABASE_USER_ACCESS_TOKEN", "")
                if not expected or token_str != expected:
                    return HttpResponseForbidden("Invalid access token.")

        episodes = list(self._published_episodes(podcast))

        # Generate fresh signed URLs for restricted episodes.
        # Override in-memory attributes so the template renders signed URLs
        # without needing conditional logic.
        for episode in episodes:
            if episode.audio_url:
                episode.audio_url = get_file_url(episode.audio_url, public=False)
            if episode.cover_image_url:
                episode.cover_image_url = get_file_url(
                    episode.cover_image_url, public=False
                )

        context = self._build_feed_context(request, podcast, episodes)
        xml = render_to_string("podcast/feed.xml", context)
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
