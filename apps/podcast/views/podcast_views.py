from uuid import uuid4

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import F, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
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


class EpisodeReportView(MainContentView):
    """Render episode report as formatted HTML page."""

    template_name = "podcast/episode_report.html"

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
        if not episode.report_text:
            raise Http404("No report available for this episode.")
        self.context["podcast"] = podcast
        self.context["episode"] = episode
        return self.render(request)


class EpisodeSourcesView(MainContentView):
    """Render episode sources as formatted HTML page."""

    template_name = "podcast/episode_sources.html"

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
        if not episode.sources_text:
            raise Http404("No sources available for this episode.")
        self.context["podcast"] = podcast
        self.context["episode"] = episode
        return self.render(request)


class EpisodeCreateView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Create a bare draft Episode with UUID slug and redirect to workflow step 1."""

    def test_func(self) -> bool:
        return self.request.user.is_staff

    def get(self, request, slug: str, *args, **kwargs) -> HttpResponseRedirect:
        return HttpResponseRedirect(reverse("podcast:detail", kwargs={"slug": slug}))

    def post(self, request, slug: str, *args, **kwargs) -> HttpResponseRedirect:
        podcast = get_object_or_404(Podcast, slug=slug)
        episode = Episode.objects.create(
            podcast=podcast,
            title="Untitled Episode",
            slug=uuid4().hex[:12],
            status="draft",
        )
        return HttpResponseRedirect(
            reverse(
                "podcast:episode_workflow",
                kwargs={"slug": slug, "episode_slug": episode.slug, "step": 1},
            )
        )
