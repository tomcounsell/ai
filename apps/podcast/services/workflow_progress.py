from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubStep:
    """A single checkable item within a workflow phase."""

    label: str
    complete: bool
    detail: str = ""


@dataclass
class Phase:
    """One of the 12 phases in the podcast episode production workflow."""

    number: int
    name: str
    description: str
    sub_steps: list[SubStep] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Return 'complete', 'in_progress', or 'pending' based on sub-step completion."""
        if not self.sub_steps:
            return "pending"
        completed = sum(1 for s in self.sub_steps if s.complete)
        if completed == len(self.sub_steps):
            return "complete"
        if completed > 0:
            return "in_progress"
        return "pending"

    @property
    def progress_fraction(self) -> float:
        """Return fraction of sub-steps completed (0.0 to 1.0)."""
        if not self.sub_steps:
            return 0.0
        return sum(1 for s in self.sub_steps if s.complete) / len(self.sub_steps)


def _has_artifact(artifact_titles: list[str], substring: str) -> bool:
    """Check if any artifact title contains the given substring (case-insensitive)."""
    needle = substring.lower()
    return any(needle in t.lower() for t in artifact_titles)


def _word_count(text: str) -> int:
    """Return the number of whitespace-delimited words in a string."""
    return len(text.split()) if text else 0


def compute_workflow_progress(
    episode: object,
    artifact_titles: list[str],
) -> list[Phase]:
    """Compute the 12-phase workflow progress for a podcast episode.

    Maps episode database fields and artifact titles to a structured list of
    Phase objects that represent the full production pipeline.

    Phase-to-DB mapping:
        Phase 1  (Setup)            - Episode record exists; status != "draft"
        Phase 2  (Perplexity)       - Artifact title contains "p2-perplexity"
        Phase 3  (Question Disc.)   - Artifact contains "question-discovery" or
                                      "gap-analysis"
        Phase 4  (Targeted Research) - Artifacts containing p2-grok, p2-chatgpt,
                                      p2-gemini, p2-claude, p2-manual (each a
                                      separate sub-step)
        Phase 5  (Cross-Validation) - Artifact contains "cross-validation"
        Phase 6  (Master Briefing)  - Artifact contains "p3-briefing"
        Phase 7  (Synthesis)        - episode.report_text is non-empty
        Phase 8  (Episode Planning) - Artifact contains "content_plan" or
                                      "content-plan"
        Phase 9  (Audio Generation) - episode.audio_url is set;
                                      episode.audio_file_size_bytes > 0
        Phase 10 (Audio Processing) - episode.transcript is populated;
                                      episode.chapters is populated
        Phase 11 (Publishing)       - episode.cover_image_url is set;
                                      episode.description is populated
        Phase 12 (Commit & Push)    - episode.published_at is set

    Args:
        episode: An Episode model instance (or any object with the expected
            attributes: status, report_text, audio_url, audio_file_size_bytes,
            transcript, chapters, cover_image_url, description, published_at).
        artifact_titles: A flat list of artifact title strings associated with
            the episode (e.g. ["research/p2-perplexity.md", "plans/content-plan.md"]).

    Returns:
        A list of 12 Phase objects ordered by phase number.
    """

    # -- Phase 1: Setup --
    phase_1 = Phase(
        number=1,
        name="Setup",
        description="Create episode record and begin production",
        sub_steps=[
            SubStep(label="Episode exists", complete=True),
            SubStep(
                label="Status is not draft",
                complete=(getattr(episode, "status", "draft") != "draft"),
            ),
        ],
    )

    # -- Phase 2: Perplexity Research --
    phase_2 = Phase(
        number=2,
        name="Perplexity Research",
        description="Initial deep research via Perplexity",
        sub_steps=[
            SubStep(
                label="Perplexity research artifact",
                complete=_has_artifact(artifact_titles, "p2-perplexity"),
            ),
        ],
    )

    # -- Phase 3: Question Discovery --
    phase_3 = Phase(
        number=3,
        name="Question Discovery",
        description="Identify knowledge gaps and research questions",
        sub_steps=[
            SubStep(
                label="Question discovery or gap analysis artifact",
                complete=(
                    _has_artifact(artifact_titles, "question-discovery")
                    or _has_artifact(artifact_titles, "gap-analysis")
                ),
            ),
        ],
    )

    # -- Phase 4: Targeted Research --
    targeted_sources = [
        ("Grok research", "p2-grok"),
        ("ChatGPT research", "p2-chatgpt"),
        ("Gemini research", "p2-gemini"),
        ("Claude research", "p2-claude"),
        ("Manual research", "p2-manual"),
    ]
    phase_4 = Phase(
        number=4,
        name="Targeted Research",
        description="Multi-source research to fill identified gaps",
        sub_steps=[
            SubStep(
                label=label,
                complete=_has_artifact(artifact_titles, needle),
            )
            for label, needle in targeted_sources
        ],
    )

    # -- Phase 5: Cross-Validation --
    phase_5 = Phase(
        number=5,
        name="Cross-Validation",
        description="Validate findings across sources",
        sub_steps=[
            SubStep(
                label="Cross-validation artifact",
                complete=_has_artifact(artifact_titles, "cross-validation"),
            ),
        ],
    )

    # -- Phase 6: Master Briefing --
    phase_6 = Phase(
        number=6,
        name="Master Briefing",
        description="Compile consolidated research briefing",
        sub_steps=[
            SubStep(
                label="Master briefing artifact",
                complete=_has_artifact(artifact_titles, "p3-briefing"),
            ),
        ],
    )

    # -- Phase 7: Synthesis --
    report_text = getattr(episode, "report_text", "") or ""
    report_wc = _word_count(report_text)
    phase_7 = Phase(
        number=7,
        name="Synthesis",
        description="Synthesize research into episode report",
        sub_steps=[
            SubStep(
                label="Report text populated",
                complete=len(report_text) > 0,
                detail=f"{report_wc:,} words" if report_wc else "",
            ),
        ],
    )

    # -- Phase 8: Episode Planning --
    phase_8 = Phase(
        number=8,
        name="Episode Planning",
        description="Create content plan for the episode",
        sub_steps=[
            SubStep(
                label="Content plan artifact",
                complete=(
                    _has_artifact(artifact_titles, "content_plan")
                    or _has_artifact(artifact_titles, "content-plan")
                ),
            ),
        ],
    )

    # -- Phase 9: Audio Generation --
    audio_url = getattr(episode, "audio_url", "") or ""
    audio_size = getattr(episode, "audio_file_size_bytes", None) or 0
    size_mb = audio_size / (1024 * 1024) if audio_size else 0.0
    phase_9 = Phase(
        number=9,
        name="Audio Generation",
        description="Generate episode audio via NotebookLM",
        sub_steps=[
            SubStep(
                label="Audio URL set",
                complete=len(audio_url) > 0,
            ),
            SubStep(
                label="Audio file size recorded",
                complete=audio_size > 0,
                detail=f"{size_mb:.1f} MB" if audio_size > 0 else "",
            ),
        ],
    )

    # -- Phase 10: Audio Processing --
    transcript = getattr(episode, "transcript", "") or ""
    transcript_wc = _word_count(transcript)
    chapters = getattr(episode, "chapters", "") or ""
    phase_10 = Phase(
        number=10,
        name="Audio Processing",
        description="Transcribe audio and generate chapters",
        sub_steps=[
            SubStep(
                label="Transcript populated",
                complete=len(transcript) > 0,
                detail=f"{transcript_wc:,} words" if transcript_wc else "",
            ),
            SubStep(
                label="Chapters populated",
                complete=len(chapters) > 0,
            ),
        ],
    )

    # -- Phase 11: Publishing --
    cover_url = getattr(episode, "cover_image_url", "") or ""
    description = getattr(episode, "description", "") or ""
    phase_11 = Phase(
        number=11,
        name="Publishing",
        description="Prepare episode metadata and cover art",
        sub_steps=[
            SubStep(
                label="Cover image URL set",
                complete=len(cover_url) > 0,
            ),
            SubStep(
                label="Description populated",
                complete=len(description) > 0,
            ),
        ],
    )

    # -- Phase 12: Commit & Push --
    published_at = getattr(episode, "published_at", None)
    phase_12 = Phase(
        number=12,
        name="Commit & Push",
        description="Publish the episode to feeds",
        sub_steps=[
            SubStep(
                label="Episode published",
                complete=published_at is not None,
            ),
        ],
    )

    return [
        phase_1,
        phase_2,
        phase_3,
        phase_4,
        phase_5,
        phase_6,
        phase_7,
        phase_8,
        phase_9,
        phase_10,
        phase_11,
        phase_12,
    ]


def get_workflow_summary(episode_id: int) -> dict:
    """Get combined workflow progress from both computed phases and EpisodeWorkflow.

    Fetches the Episode and its artifacts, computes the 12-phase progress
    using ``compute_workflow_progress``, and — if an ``EpisodeWorkflow``
    record exists — merges in the persisted workflow state (current step,
    status, blocked_on, and history).

    Returns a dict with keys:
        ``phases`` -- list of phase dicts (number, name, status, progress)
        ``overall_progress`` -- float 0.0-1.0 across all sub-steps
        ``workflow`` -- EpisodeWorkflow state dict or ``None``
    """
    from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow

    episode = Episode.objects.get(pk=episode_id)
    artifact_titles = list(
        EpisodeArtifact.objects.filter(episode=episode).values_list("title", flat=True)
    )

    phases = compute_workflow_progress(episode, artifact_titles)

    phase_dicts = [
        {
            "number": p.number,
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "progress": p.progress_fraction,
        }
        for p in phases
    ]

    # Overall progress across all sub-steps
    total_sub = sum(len(p.sub_steps) for p in phases)
    completed_sub = sum(sum(1 for s in p.sub_steps if s.complete) for p in phases)
    overall = completed_sub / total_sub if total_sub > 0 else 0.0

    # Merge persisted workflow state if available
    workflow_state: dict | None = None
    try:
        wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
        workflow_state = {
            "current_step": wf.current_step,
            "status": wf.status,
            "blocked_on": wf.blocked_on,
            "agent_session_id": wf.agent_session_id,
            "history": wf.history,
        }
    except EpisodeWorkflow.DoesNotExist:
        pass

    return {
        "phases": phase_dicts,
        "overall_progress": round(overall, 4),
        "workflow": workflow_state,
    }
