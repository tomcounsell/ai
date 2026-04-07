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
        "apps.podcast.tasks.step_together_research",
        "apps.podcast.tasks.step_claude_research",
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
        loading_text (str): Text shown with spinner during HTMX request.
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
                "loading_text": "Starting...",
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
                    "loading_text": "Resuming...",
                    "blocked_reason": (
                        "Automated research complete. Add Grok or manual "
                        "research, or resume to continue."
                    ),
                    "error": "",
                }

        # Detect stale workflow: running for >10 min with no progress
        import datetime

        from django.utils import timezone

        stale_threshold = timezone.now() - datetime.timedelta(minutes=10)
        if wf.modified_at < stale_threshold:
            return {
                "show": True,
                "label": "Retry Step",
                "color": "red",
                "icon": "warning",
                "disabled": False,
                "loading_text": "Retrying...",
                "blocked_reason": (
                    f"Workflow stalled — no progress since "
                    f"{wf.modified_at:%Y-%m-%d %H:%M} UTC. "
                    f"Use per-source Restart buttons or retry the whole step."
                ),
                "error": "",
            }

        return {
            "show": True,
            "label": "Running...",
            "color": "yellow",
            "icon": "",
            "disabled": True,
            "loading_text": "",
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
            "loading_text": "Resuming...",
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
            "loading_text": "Resuming...",
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
            "loading_text": "Retrying...",
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
            "loading_text": "Starting...",
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

        artifacts = episode.artifacts.all()
        artifact_titles: list[str] = [a.title for a in artifacts]
        # Build content map for Phase 4 per-source status rendering
        artifact_contents: dict[str, str] = {
            a.title: a.content or "" for a in artifacts if a.title.startswith("p2-")
        }

        # Check if workflow is actively running (for polling)
        workflow_is_running = False
        workflow_is_stale = False
        with contextlib.suppress(EpisodeWorkflow.DoesNotExist):
            wf = episode.workflow
            workflow_is_running = wf.status == "running"
            if workflow_is_running:
                import datetime

                from django.utils import timezone

                stale_threshold = timezone.now() - datetime.timedelta(minutes=10)
                workflow_is_stale = wf.modified_at < stale_threshold

        phases = compute_workflow_progress(
            episode,
            artifact_titles,
            artifact_contents=artifact_contents,
            workflow_is_running=workflow_is_running,
            workflow_is_stale=workflow_is_stale,
        )
        current_phase = phases[step - 1]

        # Get artifact for current phase
        phase_artifact = self._get_phase_artifact(episode, step)
        auto_expand = step in [6, 8]  # Quality gates

        self.context["podcast"] = podcast
        self.context["episode"] = episode
        self.context["phases"] = phases
        self.context["current_phase"] = current_phase
        self.context["current_step"] = step
        self.context["total_steps"] = 12
        self.context["button_state"] = _compute_button_state(episode, step)
        self.context["phase_artifact"] = phase_artifact
        self.context["auto_expand_artifact"] = auto_expand
        self.context["workflow_is_running"] = (
            workflow_is_running and not workflow_is_stale
        )

        # Build research prompt map for Phase 4 paste modals
        if step == 4:
            self.context["research_prompts"] = {
                a.title.replace("prompt-", ""): a.content
                for a in artifacts
                if a.title.startswith("prompt-") and a.content
            }

        # Build NotebookLM source artifacts list for step 9
        if step == 9:
            notebooklm_sources = []
            source_titles = [
                ("Episode Brief", "p1-brief"),
                ("Master Briefing", "p3-briefing"),
                ("Research Report", None),  # Stored in episode.report_text
                ("Content Plan", "content_plan"),
            ]
            for label, title in source_titles:
                if title is None:
                    # report_text lives on Episode, not an artifact
                    has_content = bool(episode.report_text)
                    notebooklm_sources.append(
                        {"label": label, "artifact": None, "has_content": has_content}
                    )
                else:
                    artifact = episode.artifacts.filter(title=title).first()
                    notebooklm_sources.append(
                        {
                            "label": label,
                            "artifact": artifact,
                            "has_content": bool(artifact and artifact.content),
                        }
                    )
            self.context["notebooklm_sources"] = notebooklm_sources

        return podcast, episode

    def _get_phase_artifact(self, episode: Episode, step: int):
        """Get the primary artifact for the given workflow phase, if it exists."""
        artifact_map = {
            1: "p1-brief",
            3: "question-discovery",
            5: "cross-validation",
            6: "p3-briefing",
            8: "content_plan",
            9: "content_plan",  # Show episode plan at Audio Generation step
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
                # Reset failed/stalled status and re-enqueue
                from apps.podcast.services import workflow as wf_service

                wf = episode.workflow
                if wf.status != "running":
                    wf_service.resume_workflow(episode.pk)
                else:
                    # Stalled workflow — touch modified_at to reset staleness
                    wf.save(update_fields=["modified_at"])
                task_paths = STEP_TASK_MAP.get(step_name, [])
                for path in task_paths:
                    task_fn = _resolve_task(path)
                    task_fn.enqueue(episode_id=episode.pk)
                logger.info(
                    "Workflow action: Retry Step '%s' for episode %d (status was: %s)",
                    step_name,
                    episode.pk,
                    wf.status,
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
        is_running = False
        is_stale = False
        try:
            wf = episode.workflow
            is_running = wf.status == "running"
            if is_running:
                import datetime

                from django.utils import timezone

                stale_threshold = timezone.now() - datetime.timedelta(minutes=10)
                is_stale = wf.modified_at < stale_threshold
        except EpisodeWorkflow.DoesNotExist:
            pass

        # Build context with per-source content for Phase 4 status
        artifacts = episode.artifacts.all()
        artifact_titles = [a.title for a in artifacts]
        artifact_contents = {
            a.title: a.content or "" for a in artifacts if a.title.startswith("p2-")
        }
        phases = compute_workflow_progress(
            episode,
            artifact_titles,
            artifact_contents=artifact_contents,
            workflow_is_running=is_running,
            workflow_is_stale=is_stale,
        )
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
            "workflow_is_running": is_running and not is_stale,
        }

        # Research prompts for Phase 4 paste modals
        if step == 4:
            ctx["research_prompts"] = {
                a.title.replace("prompt-", ""): a.content
                for a in artifacts
                if a.title.startswith("prompt-") and a.content
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

        # Tell HTMX to stop polling when workflow is no longer running or stale
        if not is_running or is_stale:
            response.status_code = 286

        return response


# Maps per-source retry key to (artifact_title, task_dotted_path)
_RETRY_SOURCE_MAP: dict[str, tuple[str, str]] = {
    "chatgpt": ("p2-chatgpt", "apps.podcast.tasks.step_gpt_research"),
    "gemini": ("p2-gemini", "apps.podcast.tasks.step_gemini_research"),
    "together": ("p2-together", "apps.podcast.tasks.step_together_research"),
    "claude": ("p2-claude", "apps.podcast.tasks.step_claude_research"),
}


class RetryResearchSourceView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Retry a single research source in Phase 4 (Targeted Research).

    Handles failed, stalled, or empty sources. Clears the artifact content
    (resetting it to empty so it appears as "pending"), sets the workflow
    back to "running" if paused, and re-enqueues just the one research task.
    """

    def test_func(self) -> bool:
        slug = self.kwargs.get("slug")
        if not slug:
            return False
        podcast = get_object_or_404(Podcast, slug=slug)
        return self.request.user.is_staff or podcast.owner == self.request.user

    def post(self, request, slug: str, episode_slug: str, source: str):
        from apps.podcast.models import EpisodeArtifact

        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        # Validate source key
        if source not in _RETRY_SOURCE_MAP:
            logger.warning(
                "Invalid retry source '%s' for episode %d", source, episode.pk
            )
            return self._redirect(slug, episode_slug)

        # Guard: only allow retry when workflow is at Targeted Research
        try:
            wf = episode.workflow
        except EpisodeWorkflow.DoesNotExist:
            logger.warning("No workflow for episode %d", episode.pk)
            return self._redirect(slug, episode_slug)

        if wf.current_step != "Targeted Research":
            logger.warning(
                "Cannot retry source '%s': workflow is at '%s', not 'Targeted Research' "
                "(episode %d)",
                source,
                wf.current_step,
                episode.pk,
            )
            return self._redirect(slug, episode_slug)

        artifact_title, task_path = _RETRY_SOURCE_MAP[source]

        # Clear the artifact to reset it to "pending"
        try:
            artifact = EpisodeArtifact.objects.get(
                episode=episode, title=artifact_title
            )
            artifact.content = ""
            artifact.metadata = artifact.metadata or {}
            artifact.metadata.pop("error", None)
            artifact.metadata.pop("failed_at", None)
            # Use .save() to trigger post_save signal
            artifact.save()
        except EpisodeArtifact.DoesNotExist:
            logger.warning(
                "Artifact '%s' not found for episode %d", artifact_title, episode.pk
            )
            return self._redirect(slug, episode_slug)

        # Resume workflow if paused; ensure running state for stalled workflows
        if wf.status in ("paused_for_human", "failed"):
            from apps.podcast.services import workflow as wf_service

            wf_service.resume_workflow(episode.pk)
        elif wf.status == "running":
            # Already running (likely stalled) — touch modified_at so staleness
            # detection resets for this retry attempt
            wf.save(update_fields=["modified_at"])

        # Re-enqueue just this one task
        task_fn = _resolve_task(task_path)
        task_fn.enqueue(episode_id=episode.pk)
        logger.info(
            "Retrying source '%s' (task: %s) for episode %d (workflow status was: %s)",
            source,
            task_path,
            episode.pk,
            wf.status,
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


class ArtifactSignedDownloadView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Upload artifact content to Supabase and return a public URL.

    POST uploads the artifact (or episode report) to the public Supabase bucket
    as a text file and returns an HTML snippet with the public URL — suitable
    for pasting directly into NotebookLM's URL import field.

    Special case: artifact_id=0 serves the episode's ``report_text`` field.
    """

    def test_func(self) -> bool:
        slug = self.kwargs.get("slug")
        if not slug:
            return False
        podcast = get_object_or_404(Podcast, slug=slug)
        return self.request.user.is_staff or podcast.owner == self.request.user

    def post(self, request, slug: str, episode_slug: str, artifact_id: int):
        import json as json_mod

        from django.utils.html import escape

        from apps.common.services.storage import store_file

        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        if artifact_id == 0:
            content = episode.report_text or ""
            filename = "report.md"
        else:
            from apps.podcast.models import EpisodeArtifact

            artifact = get_object_or_404(
                EpisodeArtifact, id=artifact_id, episode=episode
            )
            content = artifact.content or ""
            filename = f"{artifact.title}.md"

        # Upload to Supabase public bucket so NotebookLM can fetch it
        storage_key = f"podcast/{slug}/{episode_slug}/notebooklm/{filename}"
        try:
            url = store_file(
                storage_key,
                content.encode("utf-8"),
                "text/plain; charset=utf-8",
                public=True,
            )
            # LocalFileStorage returns relative paths — make absolute
            if url.startswith("/"):
                url = request.build_absolute_uri(url)
        except Exception:
            logger.exception(
                "Failed to upload artifact to storage for episode %d artifact_id %d",
                episode.pk,
                artifact_id,
            )
            return HttpResponse(
                '<span class="text-xs text-red-500 font-mono">Upload failed — check storage config</span>'
            )

        # Return JSON when requested (used by JS clipboard copy)
        if request.GET.get("json"):
            return HttpResponse(
                json_mod.dumps({"url": url}), content_type="application/json"
            )

        safe_url = escape(url)
        html = (
            f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer"'
            f' class="text-xs font-mono text-blue-600 hover:underline"'
            f' title="{safe_url}">Open</a>'
            f' <button type="button"'
            f' onclick="navigator.clipboard.writeText(\'{url.replace(chr(39), "")}\');this.textContent=\'Copied!\';setTimeout(()=>this.textContent=\'Copy URL\',2000)"'
            f' class="text-xs font-mono px-2 py-0.5 text-gray-500 border border-gray-200 hover:bg-gray-50">Copy URL</button>'
        )
        return HttpResponse(html)


class ArtifactSignedFetchView(View):
    """Public (no login required) endpoint that serves artifact content as a text file.

    Validates the HMAC token from ArtifactSignedDownloadView and serves the
    content as ``text/plain`` so NotebookLM can import it via URL.
    """

    MAX_AGE = 3600

    def get(self, request, slug: str, episode_slug: str, artifact_id: int):
        from django.core import signing
        from django.http import HttpResponseForbidden

        token = request.GET.get("token", "")
        if not token:
            return HttpResponseForbidden("Missing token")

        try:
            data = signing.loads(token, salt="artifact-download", max_age=self.MAX_AGE)
        except signing.SignatureExpired:
            return HttpResponseForbidden("Link expired — generate a new one")
        except signing.BadSignature:
            return HttpResponseForbidden("Invalid token")

        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        if data.get("type") == "report":
            if data.get("episode_id") != episode.pk:
                return HttpResponseForbidden("Token mismatch")
            content = episode.report_text or ""
            filename = "report.md"
        else:
            from django.http import HttpResponseForbidden

            from apps.podcast.models import EpisodeArtifact

            if data.get("artifact_id") != artifact_id:
                return HttpResponseForbidden("Token mismatch")
            artifact = get_object_or_404(
                EpisodeArtifact, id=artifact_id, episode=episode
            )
            content = artifact.content or ""
            filename = f"{artifact.title}.md"

        response = HttpResponse(content, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
