"""Task-per-step pipeline for podcast episode production.

Replaces the monolithic Anthropic agentic loop with discrete Django tasks,
each corresponding to one step of the 12-phase production workflow.  Tasks
call the existing service layer functions and enqueue the next step on
success.  Fan-in for parallel steps (Targeted Research, Publishing Assets)
is handled by :mod:`apps.podcast.signals` via ``post_save`` on
:class:`EpisodeArtifact`.

Usage::

    from apps.podcast.tasks import produce_episode

    result = produce_episode.enqueue(episode_id=42)
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.tasks import task

from apps.podcast.models import EpisodeArtifact, EpisodeWorkflow
from apps.podcast.services import (
    analysis,
    audio,
    publishing,
    research,
    setup,
    synthesis,
    workflow,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _acquire_step_lock(episode_id: int, expected_step: str) -> None:
    """Verify the workflow is at the expected step and not already running.

    Uses ``select_for_update`` inside an atomic block to prevent race
    conditions.  The lock scope is intentionally narrow -- it covers only
    the status check, not the entire step execution.

    Raises:
        ValueError: If the workflow is already running the expected step
            or is at a different step entirely.
        EpisodeWorkflow.DoesNotExist: If no workflow record exists.
    """
    with transaction.atomic():
        wf = EpisodeWorkflow.objects.select_for_update().get(episode_id=episode_id)
        if wf.status == "running" and wf.current_step == expected_step:
            # Check history to see if step was already started by another task
            for entry in reversed(wf.history):
                if entry["step"] == expected_step and entry["status"] == "started":
                    raise ValueError(
                        f"Step '{expected_step}' already running "
                        f"for episode {episode_id}"
                    )
        if wf.current_step != expected_step:
            raise ValueError(
                f"Episode {episode_id} is at step '{wf.current_step}', "
                f"not '{expected_step}'"
            )


def _get_crafted_prompt(episode_id: int, artifact_title: str) -> str:
    """Read a pre-generated prompt from an artifact.

    Args:
        episode_id: Primary key of the Episode.
        artifact_title: Title of the prompt artifact, e.g. ``"prompt-gpt"``
            or ``"prompt-gemini"``.

    Returns:
        The crafted prompt string.

    Raises:
        EpisodeArtifact.DoesNotExist: If no matching artifact exists.
        ValueError: If the artifact has no content.
    """
    artifact = EpisodeArtifact.objects.get(episode_id=episode_id, title=artifact_title)
    if not artifact.content:
        raise ValueError(
            f"Prompt artifact '{artifact_title}' for episode {episode_id} "
            f"has no content."
        )
    return artifact.content


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@task
def produce_episode(episode_id: int) -> None:
    """Top-level entry point: set up the episode and start the pipeline.

    Creates the workflow record and p1-brief artifact via
    :func:`setup.setup_episode`, advances past Setup, and enqueues
    the first research step.
    """
    try:
        setup.setup_episode(episode_id)
        workflow.advance_step(episode_id, "Setup")
        step_perplexity_research.enqueue(episode_id=episode_id)
        logger.info("produce_episode: started pipeline for episode %d", episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Setup", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 2: Perplexity Research
# ---------------------------------------------------------------------------


@task
def step_perplexity_research(episode_id: int) -> None:
    """Run Perplexity Deep Research and enqueue question discovery."""
    _acquire_step_lock(episode_id, "Perplexity Research")
    try:
        artifact = analysis.craft_research_prompt(episode_id, "perplexity")
        research.run_perplexity_research(episode_id, prompt=artifact.content)
        workflow.advance_step(episode_id, "Perplexity Research")
        step_question_discovery.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Perplexity Research", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 3: Question Discovery
# ---------------------------------------------------------------------------


@task
def step_question_discovery(episode_id: int) -> None:
    """Analyze research to find followup questions, then fan out to parallel research.

    After question discovery, enqueues BOTH ``step_gpt_research`` and
    ``step_gemini_research`` to run in parallel.  Fan-in is handled by
    the ``post_save`` signal on :class:`EpisodeArtifact`.
    """
    _acquire_step_lock(episode_id, "Question Discovery")
    try:
        analysis.discover_questions(episode_id)
        # Generate targeted prompts and create placeholder artifacts
        # before advancing, so prompts are available as artifacts
        analysis.craft_targeted_research_prompts(episode_id)
        workflow.advance_step(episode_id, "Question Discovery")
        # Fan-out: enqueue all targeted research steps in parallel
        step_gpt_research.enqueue(episode_id=episode_id)
        step_gemini_research.enqueue(episode_id=episode_id)
        step_together_research.enqueue(episode_id=episode_id)
        step_claude_research.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Question Discovery", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 4: Targeted Research (parallel sub-steps)
# ---------------------------------------------------------------------------


@task
def step_gpt_research(episode_id: int) -> None:
    """Run GPT-Researcher for industry/technical questions.

    This is a parallel sub-step of "Targeted Research".  Does NOT enqueue
    the next step -- the ``post_save`` signal handles fan-in once all
    ``p2-*`` research artifacts have content.
    """
    # Skip strict step lock for parallel sub-steps; just verify we're at
    # the right workflow step.
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    if wf.current_step != "Targeted Research":
        raise ValueError(
            f"Episode {episode_id} is at step '{wf.current_step}', "
            f"not 'Targeted Research'"
        )
    try:
        prompt = _get_crafted_prompt(episode_id, "prompt-gpt")
        research.run_gpt_researcher(episode_id, prompt=prompt)
        logger.info("step_gpt_research: completed for episode %d", episode_id)
        # Do NOT enqueue next step -- signal handles fan-in
    except Exception as exc:
        workflow.fail_step(episode_id, "Targeted Research", str(exc))
        raise


@task
def step_gemini_research(episode_id: int) -> None:
    """Run Gemini Deep Research for policy/strategic questions.

    This is a parallel sub-step of "Targeted Research".  Does NOT enqueue
    the next step -- the ``post_save`` signal handles fan-in once all
    ``p2-*`` research artifacts have content.
    """
    # Skip strict step lock for parallel sub-steps
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    if wf.current_step != "Targeted Research":
        raise ValueError(
            f"Episode {episode_id} is at step '{wf.current_step}', "
            f"not 'Targeted Research'"
        )
    try:
        prompt = _get_crafted_prompt(episode_id, "prompt-gemini")
        research.run_gemini_research(episode_id, prompt=prompt)
        logger.info("step_gemini_research: completed for episode %d", episode_id)
        # Do NOT enqueue next step -- signal handles fan-in
    except Exception as exc:
        workflow.fail_step(episode_id, "Targeted Research", str(exc))
        raise


@task
def step_together_research(episode_id: int) -> None:
    """Run Together Open Deep Research for exploratory multi-hop questions.

    This is a parallel sub-step of "Targeted Research".  Does NOT enqueue
    the next step -- the ``post_save`` signal handles fan-in once all
    ``p2-*`` research artifacts have content.
    """
    # Skip strict step lock for parallel sub-steps
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    if wf.current_step != "Targeted Research":
        raise ValueError(
            f"Episode {episode_id} is at step '{wf.current_step}', "
            f"not 'Targeted Research'"
        )
    try:
        prompt = _get_crafted_prompt(episode_id, "prompt-together")
        research.run_together_research(episode_id, prompt=prompt)
        logger.info("step_together_research: completed for episode %d", episode_id)
        # Do NOT enqueue next step -- signal handles fan-in
    except Exception as exc:
        workflow.fail_step(episode_id, "Targeted Research", str(exc))
        raise


@task
def step_claude_research(episode_id: int) -> None:
    """Run Claude deep research orchestrator.

    This is a parallel sub-step of "Targeted Research".  Does NOT enqueue
    the next step -- the ``post_save`` signal handles fan-in once all
    ``p2-*`` research artifacts have content.
    """
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    if wf.current_step != "Targeted Research":
        raise ValueError(
            f"Episode {episode_id} is at step '{wf.current_step}', "
            f"not 'Targeted Research'"
        )
    try:
        prompt = _get_crafted_prompt(episode_id, "prompt-claude")
        research.run_claude_research(episode_id, prompt=prompt)
        logger.info("step_claude_research: completed for episode %d", episode_id)
        # Do NOT enqueue next step -- signal handles fan-in
    except Exception as exc:
        workflow.fail_step(episode_id, "Targeted Research", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 4b: Research Digests (bridge between targeted research and cross-val)
# ---------------------------------------------------------------------------


@task
def step_research_digests(episode_id: int) -> None:
    """Create a structured digest for each p2-* research artifact.

    Iterates over all ``p2-*`` artifacts and calls
    :func:`analysis.create_research_digest` for each.  Then enqueues
    cross-validation.
    """
    # This step runs after Targeted Research is complete but before
    # Cross-Validation.  The workflow is still at "Targeted Research"
    # when the signal fires, so we advance first.
    try:
        p2_artifacts = EpisodeArtifact.objects.filter(
            episode_id=episode_id,
            title__startswith="p2-",
        ).exclude(content="")

        for artifact in p2_artifacts:
            analysis.create_research_digest(episode_id, artifact.title)
            logger.info(
                "step_research_digests: digested '%s' for episode %d",
                artifact.title,
                episode_id,
            )

        # Advance past Targeted Research (the signal already confirmed
        # all p2-* artifacts are populated) and into Cross-Validation.
        workflow.advance_step(episode_id, "Targeted Research")
        step_cross_validation.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Targeted Research", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 5: Cross-Validation
# ---------------------------------------------------------------------------


@task
def step_cross_validation(episode_id: int) -> None:
    """Cross-validate findings across all research artifacts."""
    _acquire_step_lock(episode_id, "Cross-Validation")
    try:
        analysis.cross_validate(episode_id)
        workflow.advance_step(episode_id, "Cross-Validation")
        step_master_briefing.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Cross-Validation", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 6: Master Briefing + Quality Gate Wave 1
# ---------------------------------------------------------------------------


@task
def step_master_briefing(episode_id: int) -> None:
    """Write the master research briefing and check Quality Gate Wave 1.

    If the gate passes, enqueues synthesis.  If it fails, pauses the
    workflow for human review.
    """
    _acquire_step_lock(episode_id, "Master Briefing")
    try:
        analysis.write_briefing(episode_id)
        workflow.advance_step(episode_id, "Master Briefing")

        # Quality Gate: Wave 1
        gate = workflow.check_quality_gate(episode_id, "wave_1")
        if gate["passed"]:
            logger.info(
                "step_master_briefing: wave_1 gate passed for episode %d",
                episode_id,
            )
            step_synthesis.enqueue(episode_id=episode_id)
        else:
            logger.warning(
                "step_master_briefing: wave_1 gate failed for episode %d: %s",
                episode_id,
                gate["details"],
            )
            workflow.pause_for_human(
                episode_id,
                f"Quality Gate Wave 1 failed: {gate['details']}",
            )
    except Exception as exc:
        workflow.fail_step(episode_id, "Master Briefing", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 7: Synthesis
# ---------------------------------------------------------------------------


@task
def step_synthesis(episode_id: int) -> None:
    """Generate the narrative synthesis report."""
    _acquire_step_lock(episode_id, "Synthesis")
    try:
        synthesis.synthesize_report(episode_id)
        workflow.advance_step(episode_id, "Synthesis")
        step_episode_planning.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Synthesis", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 8: Episode Planning + Quality Gate Wave 2
# ---------------------------------------------------------------------------


@task
def step_episode_planning(episode_id: int) -> None:
    """Create the episode content plan and check Quality Gate Wave 2.

    If the gate passes, enqueues audio generation.  If it fails, pauses
    the workflow for human review.
    """
    _acquire_step_lock(episode_id, "Episode Planning")
    try:
        synthesis.plan_episode_content(episode_id)
        workflow.advance_step(episode_id, "Episode Planning")

        # Quality Gate: Wave 2
        gate = workflow.check_quality_gate(episode_id, "wave_2")
        if gate["passed"]:
            logger.info(
                "step_episode_planning: wave_2 gate passed for episode %d",
                episode_id,
            )
            step_audio_generation.enqueue(episode_id=episode_id)
        else:
            logger.warning(
                "step_episode_planning: wave_2 gate failed for episode %d: %s",
                episode_id,
                gate["details"],
            )
            workflow.pause_for_human(
                episode_id,
                f"Quality Gate Wave 2 failed: {gate['details']}",
            )
    except Exception as exc:
        workflow.fail_step(episode_id, "Episode Planning", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 9: Audio Generation
# ---------------------------------------------------------------------------


@task
def step_audio_generation(episode_id: int) -> None:
    """Pause workflow for local audio worker to pick up."""
    _acquire_step_lock(episode_id, "Audio Generation")
    try:
        workflow.pause_for_human(episode_id, "audio_generation")
        logger.info(
            "step_audio_generation: paused for local worker, episode %d",
            episode_id,
        )
    except Exception as exc:
        workflow.fail_step(episode_id, "Audio Generation", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 10: Audio Processing (transcription + chapters)
# ---------------------------------------------------------------------------


@task
def step_transcribe_audio(episode_id: int) -> None:
    """Transcribe audio via Whisper API."""
    _acquire_step_lock(episode_id, "Audio Processing")
    try:
        audio.transcribe_audio(episode_id)
        # Don't advance yet -- chapters are also part of Audio Processing
        step_generate_chapters.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Audio Processing", str(exc))
        raise


@task
def step_generate_chapters(episode_id: int) -> None:
    """Generate chapter markers from transcript, then fan out to publishing.

    After chapters are generated, enqueues ALL THREE publishing asset
    tasks (cover art, metadata, companions) to run in parallel.
    """
    # Still in "Audio Processing" step
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    if wf.current_step != "Audio Processing":
        raise ValueError(
            f"Episode {episode_id} is at step '{wf.current_step}', "
            f"not 'Audio Processing'"
        )
    try:
        audio.generate_episode_chapters(episode_id)
        workflow.advance_step(episode_id, "Audio Processing")
        # Fan-out: enqueue all three publishing sub-steps in parallel
        step_cover_art.enqueue(episode_id=episode_id)
        step_metadata.enqueue(episode_id=episode_id)
        step_companions.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Audio Processing", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 11: Publishing Assets (parallel sub-steps)
# ---------------------------------------------------------------------------


@task
def step_cover_art(episode_id: int) -> None:
    """Generate episode cover art.

    This is a parallel sub-step of "Publishing Assets".  Does NOT enqueue
    the next step -- the ``post_save`` signal handles fan-in once all
    publishing artifacts have content.
    """
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    if wf.current_step != "Publishing Assets":
        raise ValueError(
            f"Episode {episode_id} is at step '{wf.current_step}', "
            f"not 'Publishing Assets'"
        )
    try:
        publishing.generate_cover_art(episode_id)
        logger.info("step_cover_art: completed for episode %d", episode_id)
        # Do NOT enqueue next step -- signal handles fan-in
    except NotImplementedError:
        # Cover art generation is currently a stub. Log a warning and
        # create a placeholder artifact so the fan-in signal can proceed.
        logger.warning(
            "step_cover_art: cover art generation not yet implemented "
            "for episode %d, creating placeholder",
            episode_id,
        )
        from apps.podcast.models import Episode

        episode = Episode.objects.get(pk=episode_id)
        EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="cover-art",
            defaults={
                "content": "placeholder",
                "description": "Cover art placeholder (generation not yet implemented).",
                "workflow_context": "Publishing Assets",
            },
        )
    except Exception as exc:
        workflow.fail_step(episode_id, "Publishing Assets", str(exc))
        raise


@task
def step_metadata(episode_id: int) -> None:
    """Generate episode metadata (description, keywords, timestamps, CTAs).

    This is a parallel sub-step of "Publishing Assets".  Does NOT enqueue
    the next step -- the ``post_save`` signal handles fan-in.
    """
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    if wf.current_step != "Publishing Assets":
        raise ValueError(
            f"Episode {episode_id} is at step '{wf.current_step}', "
            f"not 'Publishing Assets'"
        )
    try:
        publishing.write_episode_metadata(episode_id)
        logger.info("step_metadata: completed for episode %d", episode_id)
        # Do NOT enqueue next step -- signal handles fan-in
    except Exception as exc:
        workflow.fail_step(episode_id, "Publishing Assets", str(exc))
        raise


@task
def step_companions(episode_id: int) -> None:
    """Generate companion resources (summary, checklist, frameworks).

    This is a parallel sub-step of "Publishing Assets".  Does NOT enqueue
    the next step -- the ``post_save`` signal handles fan-in.
    """
    wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    if wf.current_step != "Publishing Assets":
        raise ValueError(
            f"Episode {episode_id} is at step '{wf.current_step}', "
            f"not 'Publishing Assets'"
        )
    try:
        publishing.generate_companions(episode_id)
        logger.info("step_companions: completed for episode %d", episode_id)
        # Do NOT enqueue next step -- signal handles fan-in
    except Exception as exc:
        workflow.fail_step(episode_id, "Publishing Assets", str(exc))
        raise


# ---------------------------------------------------------------------------
# Phase 12: Publish
# ---------------------------------------------------------------------------


@task
def step_publish(episode_id: int) -> None:
    """Mark episode as complete and published."""
    _acquire_step_lock(episode_id, "Publish")
    try:
        publishing.publish_episode(episode_id)
        workflow.advance_step(episode_id, "Publish")
        logger.info("step_publish: episode %d published successfully", episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Publish", str(exc))
        raise
