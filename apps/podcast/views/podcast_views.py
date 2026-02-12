from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.podcast.models import Episode, Podcast
from apps.public.views.helpers.main_content_view import MainContentView


class PodcastListView(MainContentView):
    """List all public podcasts."""

    template_name = "podcast/podcast_list.html"

    def get(self, request, *args, **kwargs):
        self.context["podcasts"] = Podcast.objects.filter(is_public=True)
        return self.render(request)


class PodcastDetailView(MainContentView):
    """Detail view for a single podcast with its published episodes."""

    template_name = "podcast/podcast_detail.html"

    def get(self, request, slug: str, *args, **kwargs):
        podcast = get_object_or_404(Podcast, slug=slug, is_public=True)
        episodes = (
            podcast.episodes.filter(published_at__isnull=False)
            .filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            )
            .order_by("episode_number")
        )
        self.context["podcast"] = podcast
        self.context["episodes"] = episodes
        return self.render(request)


class EpisodeDetailView(MainContentView):
    """Detail view for a single episode."""

    template_name = "podcast/episode_detail.html"

    def get(self, request, slug: str, episode_slug: str, *args, **kwargs):
        podcast = get_object_or_404(Podcast, slug=slug, is_public=True)
        episode = get_object_or_404(
            Episode.objects.filter(
                published_at__isnull=False,
            ).filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            ),
            podcast=podcast,
            slug=episode_slug,
        )
        self.context["podcast"] = podcast
        self.context["episode"] = episode
        return self.render(request)


class EpisodeReportView(View):
    """Return episode report_text as plain text."""

    def get(
        self, request, slug: str, episode_slug: str, *args, **kwargs
    ) -> HttpResponse:
        podcast = get_object_or_404(Podcast, slug=slug, is_public=True)
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
            from django.http import Http404

            raise Http404("No report available for this episode.")
        return HttpResponse(episode.report_text, content_type="text/plain")


class EpisodeSourcesView(View):
    """Return episode sources_text as plain text."""

    def get(
        self, request, slug: str, episode_slug: str, *args, **kwargs
    ) -> HttpResponse:
        podcast = get_object_or_404(Podcast, slug=slug, is_public=True)
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
            from django.http import Http404

            raise Http404("No sources available for this episode.")
        return HttpResponse(episode.sources_text, content_type="text/plain")
