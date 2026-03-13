from __future__ import annotations

import contextlib
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View

from apps.podcast.models import Episode, EpisodeWorkflow, Podcast
from apps.podcast.services.workflow import WORKFLOW_STEPS
from apps.podcast.services.workflow_progress import compute_workflow_progress
from apps.public.views.helpers.main_content_view import MainContentView

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step → task mapping
# ---------------------------------------------------------------------------
# Maps each workflow step name to the task function(s) that execute it.
# Lazy imports via strings to avoid circular imports — resolved in _enqueue().

STEP_TASK_MAP: dict[str, list[str]] = {
    "Setup": ["apps.podcast.tasks.produce_episode"],
    "Perplexity Research": ["apps.podcast.tasks.step_perplexity_research"],
    "Question Discovery": ["apps.podcast.tasks.step_question_discovery"],
    "Targeted Research": [
        "apps.podcast.tasks.step_gpt_research",
        "apps.podcast.tasks.step_gemini_research",
    ],
    "Cross-Validation": ["apps.podcast.tasks.step_cross_validation"],
    "Master Briefing": ["apps.podcast.tasks.step_master_briefing"],
    "Synthesis": ["apps.podcast.tasks.step_synthesis"],
    "Episode Planning": ["apps.podcast.tasks.step_episode_planning"],
    "Audio Generation": ["apps.podcast.tasks.step_audio_generation"],
    "Audio Processing": [
        "apps.podcast.tasks.step_transcribe_audio",
        "apps.podcast.tasks.step_generate_chapters",
    ],
    "Publishing Assets": [
        "apps.podcast.tasks.step_cover_art",
        "apps.podcast.tasks.step_metadata",
        "apps.podcast.tasks.step_companions",
    ],
    "Publish": ["apps.podcast.tasks.step_publish"],
}


def _resolve_task(dotted_path: str):
    """Import and return a task function from its dotted path."""
    module_path, func_name = dotted_path.rsplit(".", 1)
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def _compute_button_state(episode: Episode, step: int) -> dict:
    """Compute what pipeline action button to show for the given step.

    Returns a dict with keys:
        show (bool): Whether to show a button at all.
        label (str): Button text.
        color (str): Button color (green/amber/blue/red).
        icon (str): Icon name for the action_button component.
        disabled (bool): Whether the button should be disabled.
        blocked_reason (str): Why the pipeline is paused/failed (shown to user).
        error (str): Error message if the step failed.
    """
    # No workflow record exists yet
    try:
        wf = episode.workflow
    except EpisodeWorkflow.DoesNotExist:
        if step == 1:
            disabled = not episode.description.strip()
            return {
                "show": True,
                "label": "Start Pipeline",
                "color": "green" if not disabled else "gray",
                "icon": "check",
                "disabled": disabled,
                "blocked_reason": "Episode description is required" if disabled else "",
                "error": "",
            }
        return {"show": False}

    # Determine which step the workflow is on
    wf_step_idx = -1
    for i, s in enumerate(WORKFLOW_STEPS):
        if s == wf.current_step:
            wf_step_idx = i + 1
            break

    current_step_idx = step

    # Completed steps — no button
    if wf_step_idx > current_step_idx:
        return {"show": False}

    # Future steps — no button
    if wf_step_idx < current_step_idx and wf.status != "complete":
        return {"show": False}

    # Workflow complete — no buttons anywhere
    if wf.status == "complete":
        return {"show": False}

    # Current step — show button based on status
    if wf.status == "running":
        # Detect stuck Targeted Research: all automated artifacts have content
        # but workflow never transitioned to paused (e.g. old code, killed worker).
        if wf.current_step == "Targeted Research":
            from apps.podcast.signals import _check_targeted_research_complete

            if _check_targeted_research_complete(episode.pk):
                return {
                    "show": True,
                    "label": "Resume Pipeline",
                    "color": "blue",
                    "icon": "check",
                    "disabled": False,
                    "blocked_reason": (
                        "Automated research complete. Add Grok or manual "
                        "research, or resume to continue."
                    ),
                    "error": "",
                }
        return {
            "show": True,
            "label": "Running...",
            "color": "yellow",
            "icon": "",
            "disabled": True,
            "blocked_reason": "",
            "error": "",
        }

    if wf.status == "paused_for_human":
        return {
            "show": True,
            "label": "Resume Pipeline",
            "color": "blue",
            "icon": "check",
            "disabled": False,
            "blocked_reason": wf.blocked_on,
            "error": "",
        }

    if wf.status == "paused_at_gate":
        return {
            "show": True,
            "label": "Resume Pipeline",
            "color": "blue",
            "icon": "check",
            "disabled": False,
            "blocked_reason": wf.blocked_on or "Quality gate review required",
            "error": "",
        }

    if wf.status == "failed":
        # Extract error from history
        error_msg = ""
        for entry in reversed(wf.history):
            if entry.get("error"):
                error_msg = entry["error"]
                break
        return {
            "show": True,
            "label": "Retry Step",
            "color": "red",
            "icon": "warning",
            "disabled": False,
            "blocked_reason": "",
            "error": error_msg,
        }

    if wf.status == "pending" and step == 1:
        disabled = not episode.description.strip()
        return {
            "show": True,
            "label": "Start Pipeline",
            "color": "green" if not disabled else "gray",
            "icon": "check",
            "disabled": disabled,
            "blocked_reason": "Episode description is required" if disabled else "",
            "error": "",
        }

    return {"show": False}


class EpisodeWorkflowView(LoginRequiredMixin, UserPassesTestMixin, MainContentView):
    """Staff-only episode workflow view showing 12-phase production progress.

    Supports two rendering modes:
    - Full page: Renders the complete layout with sidebar navigation and step
      content when accessed via a normal browser request.
    - HTMX partial: When the request comes from HTMX (detected via
      ``request.htmx``), only the step content partial is returned so the
      sidebar can swap in new content without a full page reload.

    POST requests trigger pipeline actions (start, resume, retry) based on
    the current workflow state.
    """

    template_name = "podcast/episode_workflow.html"

    def test_func(self) -> bool:
        slug = self.kwargs.get("slug")
        if not slug:
            return False
        podcast = get_object_or_404(Podcast, slug=slug)
        return self.request.user.is_staff or podcast.owner == self.request.user

    def _load_context(
        self, request, slug: str, episode_slug: str, step: int
    ) -> tuple[Podcast, Episode]:
        """Load podcast, episode, phases, and button state into self.context."""
        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        if step < 1 or step > 12:
            raise Http404("Workflow step must be between 1 and 12.")

        artifact_titles: list[str] = list(
            episode.artifacts.values_list("title", flat=True)
        )
        phases = compute_workflow_progress(episode, artifact_titles)
        current_phase = phases[step - 1]

        # Get artifact for current phase
        phase_artifact = self._get_phase_artifact(episode, step)
        auto_expand = step in [6, 8]  # Quality gates

        # Check if workflow is actively running (for polling)
        workflow_is_running = False
        with contextlib.suppress(EpisodeWorkflow.DoesNotExist):
            workflow_is_running = episode.workflow.status == "running"

        self.context["podcast"] = podcast
        self.context["episode"] = episode
        self.context["phases"] = phases
        self.context["current_phase"] = current_phase
        self.context["current_step"] = step
        self.context["total_steps"] = 12
        self.context["button_state"] = _compute_button_state(episode, step)
        self.context["phase_artifact"] = phase_artifact
        self.context["auto_expand_artifact"] = auto_expand
        self.context["workflow_is_running"] = workflow_is_running

        return podcast, episode

    def _get_phase_artifact(self, episode: Episode, step: int):
        """Get the artifact for the given workflow phase, if it exists."""
        artifact_map = {
            1: "p1-brief",
            2: "p2-research",
            3: "p3-questions",
            4: "p4-digest",
            5: "p5-validation",
            6: "p6-briefing",
            7: "p7-report",
            8: "p8-plan",
        }
        title = artifact_map.get(step)
        if not title:
            return None
        return episode.artifacts.filter(title=title).first()

    def get(self, request, slug: str, episode_slug: str, step: int, *args, **kwargs):
        self._load_context(request, slug, episode_slug, step)

        if getattr(request, "htmx", False):
            # Return step content + sidebar OOB swap so both update together.
            from django.template.loader import render_to_string

            content_html = render_to_string(
                "podcast/_workflow_step_content.html",
                self.context,
                request=request,
            )
            sidebar_html = render_to_string(
                "podcast/_workflow_sidebar.html",
                self.context,
                request=request,
            )
            combined = (
                content_html
                + '<div id="workflow-sidebar" hx-swap-oob="innerHTML">'
                + sidebar_html
                + "</div>"
            )
            return HttpResponse(combined)

        return self.render(request)

    def post(self, request, slug: str, episode_slug: str, step: int, *args, **kwargs):
        """Handle pipeline actions: start, resume, retry, or audio upload.

        Also handles audio file uploads for step 9 when action=upload_audio.

        Determines the correct action from the workflow state and enqueues
        the appropriate task(s). Returns HX-Redirect for HTMX clients or
        a standard redirect for regular form submissions.
        """
        podcast, episode = self._load_context(request, slug, episode_slug, step)

        # Handle audio upload for step 9
        if request.POST.get("action") == "upload_audio" and step == 9:
            return self._handle_audio_upload(request, episode, slug, episode_slug, step)

        button_state = self.context["button_state"]

        if not button_state.get("show") or button_state.get("disabled"):
            # No action possible — redirect back
            return self._redirect(slug, episode_slug, step)

        step_name = WORKFLOW_STEPS[step - 1]
        label = button_state.get("label", "")

        try:
            if label == "Start Pipeline":
                # For step 1 with no workflow — use produce_episode
                task_fn = _resolve_task("apps.podcast.tasks.produce_episode")
                task_fn.enqueue(episode_id=episode.pk)
                logger.info(
                    "Workflow action: Start Pipeline for episode %d", episode.pk
                )

            elif label == "Resume Pipeline":
                # Resume from paused state
                from apps.podcast.services import workflow as wf_service

                wf_service.resume_workflow(episode.pk)

                # Targeted Research pause means automated research is done;
                # resuming should advance to digests, not re-run research.
                if step_name == "Targeted Research":
                    task_fn = _resolve_task("apps.podcast.tasks.step_research_digests")
                    task_fn.enqueue(episode_id=episode.pk)
                else:
                    # Enqueue the current step's tasks
                    task_paths = STEP_TASK_MAP.get(step_name, [])
                    for path in task_paths:
                        task_fn = _resolve_task(path)
                        task_fn.enqueue(episode_id=episode.pk)
                logger.info(
                    "Workflow action: Resume Pipeline at '%s' for episode %d",
                    step_name,
                    episode.pk,
                )

            elif label == "Retry Step":
                # Reset failed status and re-enqueue
                from apps.podcast.services import workflow as wf_service

                wf_service.resume_workflow(episode.pk)
                task_paths = STEP_TASK_MAP.get(step_name, [])
                for path in task_paths:
                    task_fn = _resolve_task(path)
                    task_fn.enqueue(episode_id=episode.pk)
                logger.info(
                    "Workflow action: Retry Step '%s' for episode %d",
                    step_name,
                    episode.pk,
                )

        except Exception:
            logger.exception(
                "Failed to enqueue action '%s' for episode %d step '%s'",
                label,
                episode.pk,
                step_name,
            )

        return self._redirect(slug, episode_slug, step)

    def patch(
        self, request, slug: str, episode_slug: str, step: int, *args, **kwargs
    ) -> HttpResponse:
        """Handle HTMX field updates for episode title/description on step 1."""
        from django.http import QueryDict

        podcast, episode = self._load_context(request, slug, episode_slug, step)

        if step != 1:
            return HttpResponse("Field editing only available on step 1", status=400)

        # Parse PATCH data from request body (Django doesn't parse PATCH into request.POST)
        data = QueryDict(request.body)

        field = data.get("field")
        if field not in ["title", "description"]:
            return HttpResponse("Invalid field", status=400)

        value = data.get(field, "").strip()

        # Validation
        if field == "title" and not value:
            return HttpResponse(
                '<span class="text-red-600">Title cannot be empty</span>', status=400
            )

        if field == "description" and not value:
            return HttpResponse(
                '<span class="text-red-600">Description cannot be empty</span>',
                status=400,
            )

        # Save
        setattr(episode, field, value)
        episode.save(update_fields=[field])

        # Return success message
        return HttpResponse(
            '<span class="text-green-600"><i class="fas fa-check-circle"></i> Saved</span>'
        )

    # File upload limits
    MAX_AUDIO_SIZE = 500 * 1024 * 1024  # 500MB
    ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
    AUDIO_CONTENT_TYPES = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }

    def _handle_audio_upload(
        self, request, episode: Episode, slug: str, episode_slug: str, step: int
    ) -> HttpResponse:
        """Handle audio file upload for step 9.

        Saves the uploaded file to Supabase storage and sets Episode.audio_url.
        """
        import os

        from apps.common.services.storage import store_file

        audio_file = request.FILES.get("audio_file")
        if not audio_file:
            logger.warning(
                "Audio upload requested but no file provided for episode %d", episode.pk
            )
            return self._redirect(slug, episode_slug, step)

        # Validate file extension
        _, ext = os.path.splitext(audio_file.name.lower())
        if ext not in self.ALLOWED_AUDIO_EXTENSIONS:
            logger.warning(
                "Rejected audio upload with extension '%s' for episode %d",
                ext,
                episode.pk,
            )
            return self._redirect(slug, episode_slug, step)

        # Validate file size
        if audio_file.size > self.MAX_AUDIO_SIZE:
            logger.warning(
                "Rejected audio upload (%.1f MB) exceeding limit for episode %d",
                audio_file.size / (1024 * 1024),
                episode.pk,
            )
            return self._redirect(slug, episode_slug, step)

        try:
            content_type = self.AUDIO_CONTENT_TYPES.get(ext, "audio/mpeg")

            # Read file content
            file_content = audio_file.read()

            # Store in Supabase: podcast/{slug}/{episode_slug}/audio.mp3
            storage_key = f"podcast/{slug}/{episode_slug}/audio.mp3"
            audio_url = store_file(storage_key, file_content, content_type, public=True)

            # Update episode
            episode.audio_url = audio_url
            episode.save(update_fields=["audio_url"])

            logger.info(
                "Audio uploaded for episode %d: %s (%.2f MB)",
                episode.pk,
                audio_url,
                len(file_content) / (1024 * 1024),
            )

        except Exception:
            logger.exception("Failed to upload audio for episode %d", episode.pk)

        return self._redirect(slug, episode_slug, step)

    def _redirect(self, slug: str, episode_slug: str, step: int) -> HttpResponse:
        """Return HX-Redirect for HTMX or standard redirect."""
        url = reverse(
            "podcast:episode_workflow",
            kwargs={
                "slug": slug,
                "episode_slug": episode_slug,
                "step": step,
            },
        )
        if getattr(self.request, "htmx", False):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = url
            return response
        return HttpResponseRedirect(url)


class WorkflowPollView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Lightweight polling endpoint that returns OOB swaps for sidebar + step content.

    Returns 286 (stop polling) when workflow is no longer running, which tells
    HTMX to stop the polling trigger. When running, returns the sidebar and
    step content as out-of-band swaps so both update simultaneously.
    """

    def test_func(self) -> bool:
        slug = self.kwargs.get("slug")
        if not slug:
            return False
        podcast = get_object_or_404(Podcast, slug=slug)
        return self.request.user.is_staff or podcast.owner == self.request.user

    def get(self, request, slug: str, episode_slug: str, step: int):
        from django.template.loader import render_to_string

        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        # Check workflow status
        try:
            wf = episode.workflow
            is_running = wf.status == "running"
        except EpisodeWorkflow.DoesNotExist:
            is_running = False

        # Build context
        artifact_titles = list(episode.artifacts.values_list("title", flat=True))
        phases = compute_workflow_progress(episode, artifact_titles)
        current_phase = phases[step - 1]

        # Get artifact for current phase (same logic as main view)
        artifact_map = {
            1: "p1-brief",
            2: "p2-research",
            3: "p3-questions",
            4: "p4-digest",
            5: "p5-validation",
            6: "p6-briefing",
            7: "p7-report",
            8: "p8-plan",
        }
        title = artifact_map.get(step)
        phase_artifact = (
            episode.artifacts.filter(title=title).first() if title else None
        )

        ctx = {
            "podcast": podcast,
            "episode": episode,
            "phases": phases,
            "current_phase": current_phase,
            "current_step": step,
            "total_steps": 12,
            "button_state": _compute_button_state(episode, step),
            "phase_artifact": phase_artifact,
            "auto_expand_artifact": step in [6, 8],
            "workflow_is_running": is_running,
        }

        # Render sidebar + step content as OOB swaps.
        # The poll URL now derives from window.location (set by hx-push-url),
        # so it always matches the step the user is viewing.
        sidebar_html = render_to_string(
            "podcast/_workflow_sidebar.html", ctx, request=request
        )
        content_html = render_to_string(
            "podcast/_workflow_step_content.html", ctx, request=request
        )

        response_html = (
            f'<div id="workflow-sidebar" hx-swap-oob="innerHTML">'
            f"{sidebar_html}</div>"
            f'<div id="workflow-step-content" hx-swap-oob="innerHTML">'
            f"{content_html}</div>"
        )

        response = HttpResponse(response_html)

        # Tell HTMX to stop polling when workflow is no longer running
        if not is_running:
            response.status_code = 286

        return response


class PasteResearchView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Accept pasted research content for Grok or manual research."""

    def test_func(self) -> bool:
        slug = self.kwargs.get("slug")
        if not slug:
            return False
        podcast = get_object_or_404(Podcast, slug=slug)
        return self.request.user.is_staff or podcast.owner == self.request.user

    def post(self, request, slug: str, episode_slug: str):
        from apps.podcast.services.research import add_manual_research

        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        research_key = request.POST.get("research_key", "").strip()
        content = request.POST.get("content", "").strip()

        allowed_keys = {"grok", "manual"}
        if research_key not in allowed_keys or not content:
            logger.warning(
                "Invalid paste research: key=%r, content_len=%d, episode=%d",
                research_key,
                len(content),
                episode.pk,
            )
            return self._redirect(slug, episode_slug)

        add_manual_research(episode.pk, research_key, content)
        logger.info(
            "Pasted %s research (%d chars) for episode %d",
            research_key,
            len(content),
            episode.pk,
        )

        return self._redirect(slug, episode_slug)

    def _redirect(self, slug: str, episode_slug: str) -> HttpResponse:
        url = reverse(
            "podcast:episode_workflow",
            kwargs={"slug": slug, "episode_slug": episode_slug, "step": 4},
        )
        if getattr(self.request, "htmx", False):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = url
            return response
        return HttpResponseRedirect(url)


class RegenerateCoverArtView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Trigger cover art regeneration for an episode."""

    def test_func(self) -> bool:
        slug = self.kwargs.get("slug")
        if not slug:
            return False
        podcast = get_object_or_404(Podcast, slug=slug)
        return self.request.user.is_staff or podcast.owner == self.request.user

    def post(self, request, slug: str, episode_slug: str):
        from apps.podcast.services.publishing import generate_cover_art

        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        try:
            generate_cover_art(episode.id)
            logger.info("Cover art regenerated for episode %d", episode.pk)
        except Exception:
            logger.exception(
                "Failed to regenerate cover art for episode %d", episode.pk
            )

        return HttpResponseRedirect(
            reverse(
                "podcast:episode_workflow",
                kwargs={"slug": slug, "episode_slug": episode_slug, "step": 11},
            )
        )


class UploadCoverArtView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Upload custom cover art for an episode."""

    MAX_COVER_SIZE = 5 * 1024 * 1024  # 5MB
    ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

    def test_func(self) -> bool:
        slug = self.kwargs.get("slug")
        if not slug:
            return False
        podcast = get_object_or_404(Podcast, slug=slug)
        return self.request.user.is_staff or podcast.owner == self.request.user

    def post(self, request, slug: str, episode_slug: str):
        import os

        from apps.common.services.storage import store_file

        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        redirect_url = reverse(
            "podcast:episode_workflow",
            kwargs={"slug": slug, "episode_slug": episode_slug, "step": 11},
        )

        cover_file = request.FILES.get("cover_art")
        if not cover_file:
            return HttpResponseRedirect(redirect_url)

        # Validate file extension
        _, ext = os.path.splitext(cover_file.name.lower())
        if ext not in self.ALLOWED_IMAGE_EXTENSIONS:
            logger.warning(
                "Rejected cover art with extension '%s' for episode %d", ext, episode.pk
            )
            return HttpResponseRedirect(redirect_url)

        # Validate file size
        if cover_file.size > self.MAX_COVER_SIZE:
            logger.warning(
                "Rejected cover art (%.1f MB) exceeding 5MB limit for episode %d",
                cover_file.size / (1024 * 1024),
                episode.pk,
            )
            return HttpResponseRedirect(redirect_url)

        try:
            file_content = cover_file.read()
            content_type = cover_file.content_type or "image/png"
            storage_key = f"podcast/{slug}/{episode_slug}/cover.png"
            cover_url = store_file(storage_key, file_content, content_type, public=True)

            episode.cover_image_url = cover_url
            episode.save(update_fields=["cover_image_url"])
            logger.info(
                "Custom cover art uploaded for episode %d: %s", episode.pk, cover_url
            )
        except Exception:
            logger.exception("Failed to upload cover art for episode %d", episode.pk)

        return HttpResponseRedirect(
            reverse(
                "podcast:episode_workflow",
                kwargs={"slug": slug, "episode_slug": episode_slug, "step": 11},
            )
        )
