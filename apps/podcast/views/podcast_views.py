from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, F, Max, Q
from django.db.models.functions import Now
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic.edit import UpdateView

from apps.common.services.storage import store_file
from apps.podcast.models import Episode, Podcast
from apps.public.views.helpers.main_content_view import MainContentView


def _podcast_published_filter() -> Q:
    """Return a Q filter for published podcasts (published_at in the past, not unpublished)."""
    return Q(published_at__isnull=False, published_at__lte=Now()) & (
        Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
    )


def _get_accessible_podcast(request, slug: str) -> Podcast:
    """Get podcast if accessible to this user, else 404.

    Access rules:
    - Owner always has access (published or not)
    - Staff always has access
    - Public + published -> accessible to everyone
    - Unlisted + published -> accessible to everyone (via direct link)
    - Restricted -> owner/staff only on web UI (feed uses tokens)
    - Unpublished -> owner/staff only
    """
    podcast = get_object_or_404(Podcast, slug=slug)
    is_owner = request.user.is_authenticated and podcast.owner == request.user
    is_staff = request.user.is_authenticated and request.user.is_staff
    if is_owner or is_staff:
        return podcast
    # Must be published for anonymous/regular users
    if not podcast.is_published:
        raise Http404
    if podcast.privacy in (Podcast.Privacy.PUBLIC, Podcast.Privacy.UNLISTED):
        return podcast
    # Restricted: owner/staff only (already checked above)
    raise Http404


class PodcastListView(MainContentView):
    """List all published public podcasts and the logged-in user's non-public podcasts."""

    template_name = "podcast/podcast_list.html"

    def get(self, request, *args, **kwargs):
        # Public published podcasts visible to everyone
        podcasts = Podcast.objects.filter(
            _podcast_published_filter(),
            privacy=Podcast.Privacy.PUBLIC,
        )
        if request.user.is_authenticated:
            # Owner sees their own podcasts regardless of privacy/published state
            user_owned = Podcast.objects.filter(owner=request.user).exclude(
                privacy=Podcast.Privacy.PUBLIC
            )
            podcasts = (podcasts | user_owned).distinct()

        # Preserve existing annotations
        published_episode_filter = Q(episodes__published_at__isnull=False) & (
            Q(episodes__unpublished_at__isnull=True)
            | Q(episodes__unpublished_at__lt=F("episodes__published_at"))
        )
        podcasts = podcasts.annotate(
            episode_count=Count("episodes", filter=published_episode_filter),
            latest_episode_at=Max(
                "episodes__published_at", filter=published_episode_filter
            ),
        )
        self.context["podcasts"] = podcasts
        return self.render(request)


class PodcastDetailView(MainContentView):
    """Detail view for a single podcast with its published episodes."""

    template_name = "podcast/podcast_detail.html"

    def get(self, request, slug: str, *args, **kwargs):
        podcast = _get_accessible_podcast(request, slug)
        episodes = (
            podcast.episodes.select_related("podcast")
            .filter(published_at__isnull=False)
            .filter(
                Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at"))
            )
            .order_by("-episode_number")
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
        if not episode.has_meaningful_sources:
            raise Http404("No sources available for this episode.")
        self.context["podcast"] = podcast
        self.context["episode"] = episode
        return self.render(request)


class PodcastEditView(LoginRequiredMixin, UpdateView, MainContentView):
    """Edit podcast metadata and upload cover art. Owner-only access."""

    model = Podcast
    template_name = "podcast/podcast_edit.html"
    fields = [
        "title",
        "description",
        "author_name",
        "author_email",
        "language",
        "website_url",
        "spotify_url",
        "apple_podcasts_url",
    ]

    MAX_COVER_SIZE = 5 * 1024 * 1024  # 5MB

    def get_queryset(self):
        """Scope to podcasts owned by the current user — returns 404 for non-owners."""
        return Podcast.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Edit: {self.object.title}"
        context["podcast"] = self.object
        # MainContentView.get_context_data already provides base_template,
        # but dispatch() stores it in self.context (dict), not the template
        # context. Ensure it's available for {% extends base_template %}.
        context.setdefault("base_template", self.base_template)
        return context

    def form_valid(self, form):
        """Handle form submission including optional cover image upload."""
        cover_file = self.request.FILES.get("cover_image")
        if cover_file:
            if cover_file.size > self.MAX_COVER_SIZE:
                form.add_error(None, "Cover image must be 5MB or smaller.")
                return self.form_invalid(form)
            self._upload_cover(form.instance, cover_file)

        response = super().form_valid(form)
        messages.success(self.request, "Podcast updated.")
        return response

    def _upload_cover(self, podcast, cover_file):
        """Upload cover image to Supabase and set cover_image_url."""
        storage_key = f"podcast/{podcast.slug}/cover.png"
        image_bytes = cover_file.read()
        content_type = cover_file.content_type or "image/png"
        is_private = podcast.uses_private_bucket
        url = store_file(storage_key, image_bytes, content_type, public=not is_private)
        if is_private:
            podcast.cover_image_url = storage_key
        else:
            podcast.cover_image_url = url

    def get_success_url(self):
        return reverse("podcast:detail", kwargs={"slug": self.object.slug})


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
