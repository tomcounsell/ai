from django.db.models import F, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.podcast.models import Episode, Podcast
from apps.public.views.helpers.main_content_view import MainContentView


def _get_accessible_podcast(request, slug: str) -> Podcast:
    """Get podcast if public or owned by request.user, else 404."""
    podcast = get_object_or_404(Podcast, slug=slug)
    if not podcast.is_public and (
        not request.user.is_authenticated or podcast.owner != request.user
    ):
        raise Http404
    return podcast


class PodcastListView(MainContentView):
    """List all public podcasts and the logged-in user's private podcasts."""

    template_name = "podcast/podcast_list.html"

    def get(self, request, *args, **kwargs):
        podcasts = Podcast.objects.filter(is_public=True)
        if request.user.is_authenticated:
            user_private = Podcast.objects.filter(owner=request.user, is_public=False)
            podcasts = (podcasts | user_private).distinct()
        self.context["podcasts"] = podcasts
        return self.render(request)


class PodcastDetailView(MainContentView):
    """Detail view for a single podcast with its published episodes."""

    template_name = "podcast/podcast_detail.html"

    def get(self, request, slug: str, *args, **kwargs):
        podcast = _get_accessible_podcast(request, slug)
        episodes = (
            podcast.episodes.filter(published_at__isnull=False)
            .filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            )
            .order_by("episode_number")
        )
        self.context["is_owner"] = (
            request.user.is_authenticated and podcast.owner == request.user
        )
        self.context["podcast"] = podcast
        self.context["episodes"] = episodes
        return self.render(request)


class EpisodeDetailView(MainContentView):
    """Detail view for a single episode."""

    template_name = "podcast/episode_detail.html"

    def get(self, request, slug: str, episode_slug: str, *args, **kwargs):
        podcast = _get_accessible_podcast(request, slug)
        episode = get_object_or_404(
            Episode.objects.filter(
                published_at__isnull=False,
            ).filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            ),
            podcast=podcast,
            slug=episode_slug,
        )
        self.context["is_owner"] = (
            request.user.is_authenticated and podcast.owner == request.user
        )
        self.context["podcast"] = podcast
        self.context["episode"] = episode
        return self.render(request)


class EpisodeReportView(View):
    """Return episode report_text as plain text."""

    def get(
        self, request, slug: str, episode_slug: str, *args, **kwargs
    ) -> HttpResponse:
        podcast = _get_accessible_podcast(request, slug)
        episode = get_object_or_404(
            Episode.objects.filter(
                published_at__isnull=False,
            ).filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            ),
            podcast=podcast,
            slug=episode_slug,
        )
        if not episode.report_text:
            raise Http404("No report available for this episode.")
        return HttpResponse(episode.report_text, content_type="text/plain")


class EpisodeSourcesView(View):
    """Return episode sources_text as plain text."""

    def get(
        self, request, slug: str, episode_slug: str, *args, **kwargs
    ) -> HttpResponse:
        podcast = _get_accessible_podcast(request, slug)
        episode = get_object_or_404(
            Episode.objects.filter(
                published_at__isnull=False,
            ).filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            ),
            podcast=podcast,
            slug=episode_slug,
        )
        if not episode.sources_text:
            raise Http404("No sources available for this episode.")
        return HttpResponse(episode.sources_text, content_type="text/plain")
