from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.podcast.models import Episode, Podcast
from apps.podcast.services.workflow_progress import compute_workflow_progress
from apps.public.views.helpers.main_content_view import MainContentView


class EpisodeWorkflowView(LoginRequiredMixin, UserPassesTestMixin, MainContentView):
    """Staff-only episode workflow view showing 12-phase production progress.

    Supports two rendering modes:
    - Full page: Renders the complete layout with sidebar navigation and step
      content when accessed via a normal browser request.
    - HTMX partial: When the request comes from HTMX (detected via
      ``request.htmx``), only the step content partial is returned so the
      sidebar can swap in new content without a full page reload.
    """

    template_name = "podcast/episode_workflow.html"

    def test_func(self) -> bool:
        return self.request.user.is_staff

    def get(self, request, slug: str, episode_slug: str, step: int, *args, **kwargs):
        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        if step < 1 or step > 12:
            raise Http404("Workflow step must be between 1 and 12.")

        artifact_titles: list[str] = list(
            episode.artifacts.values_list("title", flat=True)
        )
        phases = compute_workflow_progress(episode, artifact_titles)
        current_phase = phases[step - 1]

        self.context["podcast"] = podcast
        self.context["episode"] = episode
        self.context["phases"] = phases
        self.context["current_phase"] = current_phase
        self.context["current_step"] = step
        self.context["total_steps"] = 12

        if getattr(request, "htmx", False):
            return self.render(
                request, template_name="podcast/_workflow_step_content.html"
            )

        return self.render(request)
