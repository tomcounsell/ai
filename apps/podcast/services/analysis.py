"""Analyze research artifacts using existing Named AI Tool services."""

from __future__ import annotations

import logging

from apps.podcast.models import Episode, EpisodeArtifact

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_question_discovery(qd: object) -> str:
    """Convert a :class:`QuestionDiscovery` Pydantic model to readable markdown."""
    lines: list[str] = ["# Question Discovery\n"]

    if qd.subtopics_found:
        lines.append("## Subtopics Found\n")
        for st in qd.subtopics_found:
            lines.append(f"- **{st.name}** (coverage: {st.coverage_depth})")
        lines.append("")

    if qd.gaps_in_literature:
        lines.append("## Gaps in Literature\n")
        for gap in qd.gaps_in_literature:
            lines.append(f"- {gap}")
        lines.append("")

    if qd.recent_developments_needed:
        lines.append("## Recent Developments Needed\n")
        for item in qd.recent_developments_needed:
            lines.append(f"- {item}")
        lines.append("")

    if qd.contradictions_to_resolve:
        lines.append("## Contradictions to Resolve\n")
        for item in qd.contradictions_to_resolve:
            lines.append(f"- {item}")
        lines.append("")

    for section_title, field_name in [
        ("Industry Questions", "industry_questions"),
        ("Policy Questions", "policy_questions"),
        ("Practitioner Questions", "practitioner_questions"),
    ]:
        items = getattr(qd, field_name, [])
        if items:
            lines.append(f"## {section_title}\n")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    if qd.recommended_tools:
        lines.append("## Recommended Tools\n")
        for rec in qd.recommended_tools:
            lines.append(f"- **{rec.tool}** ({rec.priority}): {rec.focus}")
        lines.append("")

    return "\n".join(lines)


def _format_research_digest(rd: object) -> str:
    """Convert a :class:`ResearchDigest` Pydantic model to readable markdown."""
    lines: list[str] = ["# Research Digest\n"]

    if rd.table_of_contents:
        lines.append("## Table of Contents\n")
        for i, item in enumerate(rd.table_of_contents, 1):
            lines.append(f"{i}. {item}")
        lines.append("")

    if rd.key_findings:
        lines.append("## Key Findings\n")
        for kf in rd.key_findings:
            lines.append(
                f"- **{kf.finding}** (confidence: {kf.confidence}, source: {kf.source})"
            )
        lines.append("")

    if rd.statistics:
        lines.append("## Statistics\n")
        for stat in rd.statistics:
            lines.append(f"- {stat}")
        lines.append("")

    if rd.sources:
        lines.append("## Sources\n")
        for src in rd.sources:
            url_part = f" — {src.url}" if src.url else ""
            lines.append(f"- [{src.tier}] {src.citation}{url_part}")
        lines.append("")

    if rd.questions_answered:
        lines.append("## Questions Answered\n")
        for q in rd.questions_answered:
            lines.append(f"- {q}")
        lines.append("")

    if rd.questions_unanswered:
        lines.append("## Questions Unanswered\n")
        for q in rd.questions_unanswered:
            lines.append(f"- {q}")
        lines.append("")

    if rd.contradictions:
        lines.append("## Contradictions\n")
        for c in rd.contradictions:
            lines.append(f"- {c}")
        lines.append("")

    return "\n".join(lines)


def _format_cross_validation(cv: object) -> str:
    """Convert a :class:`CrossValidation` Pydantic model to readable markdown."""
    lines: list[str] = ["# Cross-Validation Report\n"]

    if cv.summary:
        lines.append(f"{cv.summary}\n")

    if cv.verified_claims:
        lines.append("## Verified Claims (2+ sources)\n")
        for claim in cv.verified_claims:
            sources = ", ".join(claim.sources)
            lines.append(
                f"- **{claim.claim}** (confidence: {claim.confidence}, sources: {sources})"
            )
        lines.append("")

    if cv.single_source_claims:
        lines.append("## Single-Source Claims\n")
        for claim in cv.single_source_claims:
            sources = ", ".join(claim.sources)
            lines.append(f"- {claim.claim} (source: {sources})")
        lines.append("")

    if cv.conflicting_claims:
        lines.append("## Conflicting Claims\n")
        for conflict in cv.conflicting_claims:
            lines.append(f"### {conflict.topic}\n")
            for pos in conflict.positions:
                lines.append(f"- **{pos.source}**: {pos.position}")
            lines.append(f"- *Suggested resolution*: {conflict.resolution_suggestion}")
            lines.append("")

    if cv.source_quality:
        lines.append("## Source Quality Assessment\n")
        for sa in cv.source_quality:
            lines.append(f"### {sa.source}\n")
            if sa.strengths:
                lines.append("**Strengths:** " + "; ".join(sa.strengths))
            if sa.weaknesses:
                lines.append("**Weaknesses:** " + "; ".join(sa.weaknesses))
            if sa.unique_contributions:
                lines.append(
                    "**Unique contributions:** " + "; ".join(sa.unique_contributions)
                )
            lines.append("")

    if cv.coverage_map:
        lines.append("## Coverage Map\n")
        for entry in cv.coverage_map:
            sources = ", ".join(entry.sources_covering)
            lines.append(f"- **{entry.topic}** (depth: {entry.depth}) — {sources}")
        lines.append("")

    return "\n".join(lines)


def _format_master_briefing(mb: object) -> str:
    """Convert a :class:`MasterBriefing` Pydantic model to readable markdown."""
    lines: list[str] = ["# Master Research Briefing\n"]

    if mb.verified_findings:
        lines.append("## Verified Findings\n")
        for tf in mb.verified_findings:
            lines.append(f"### {tf.topic}\n")
            lines.append(f"{tf.main_finding}\n")
            if tf.evidence:
                for ev in tf.evidence:
                    sample = f" (n={ev.sample_size})" if ev.sample_size else ""
                    lines.append(
                        f"- {ev.finding} — *{ev.source}* [{ev.quality}]{sample}"
                    )
            if tf.contradictions:
                lines.append("\n**Contradictions:**")
                for c in tf.contradictions:
                    lines.append(f"- {c}")
            lines.append("")

    if mb.practical_audit:
        lines.append("## Practical Audit\n")
        for step in mb.practical_audit:
            lines.append(f"- **{step.finding}**: {step.implementation}")
            if step.parameters:
                lines.append(f"  Parameters: {step.parameters}")
        lines.append("")

    if mb.story_bank:
        lines.append("## Story Bank\n")
        for story in mb.story_bank:
            lines.append(f"### {story.title}\n")
            lines.append(f"{story.narrative}\n")
            lines.append(
                f"*Memorability: {story.memorability} | "
                f"Emotional resonance: {story.emotional_resonance}*\n"
            )
            if story.integration_opportunity:
                lines.append(
                    f"Integration opportunity: {story.integration_opportunity}\n"
                )

    if mb.counterpoints:
        lines.append("## Counterpoints\n")
        for cp in mb.counterpoints:
            lines.append(f"### {cp.topic}\n")
            lines.append(f"- **Position A:** {cp.position_a}")
            lines.append(f"- **Position B:** {cp.position_b}")
            lines.append(f"- *Dialogue opportunity:* {cp.dialogue_opportunity}")
            lines.append("")

    if mb.research_gaps:
        lines.append("## Research Gaps\n")
        for gap in mb.research_gaps:
            lines.append(f"- {gap}")
        lines.append("")

    if mb.source_inventory:
        lines.append("## Source Inventory\n")
        si = mb.source_inventory
        if si.tier1:
            lines.append("### Tier 1 (Meta-analyses, Systematic Reviews)\n")
            for s in si.tier1:
                lines.append(f"- {s}")
            lines.append("")
        if si.tier2:
            lines.append("### Tier 2 (RCTs, Large Studies)\n")
            for s in si.tier2:
                lines.append(f"- {s}")
            lines.append("")
        if si.tier3:
            lines.append("### Tier 3 (Case Studies, Reports)\n")
            for s in si.tier3:
                lines.append(f"- {s}")
            lines.append("")

    if mb.synthesis_notes:
        lines.append("## Synthesis Notes\n")
        lines.append(f"{mb.synthesis_notes}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def discover_questions(episode_id: int) -> EpisodeArtifact:
    """Analyze initial research to discover followup questions.

    Reads the ``p2-perplexity`` artifact (or first available ``p2-*``),
    calls the :func:`~apps.podcast.services.discover_questions.discover_questions`
    Named AI Tool, and saves the result as a ``question-discovery`` artifact.

    Args:
        episode_id: Primary key of the target :class:`Episode`.

    Returns:
        The ``question-discovery`` :class:`EpisodeArtifact`.
    """
    from apps.podcast.services.discover_questions import (
        discover_questions as _discover_questions,
    )

    episode = Episode.objects.get(pk=episode_id)

    # Prefer p2-perplexity; fall back to any p2-* artifact with real content
    # (exclude skipped/failed artifacts)
    research_artifact = None
    try:
        candidate = EpisodeArtifact.objects.get(episode=episode, title="p2-perplexity")
        if (
            candidate.content
            and not candidate.content.startswith("[SKIPPED:")
            and not candidate.content.startswith("[FAILED:")
        ):
            research_artifact = candidate
    except EpisodeArtifact.DoesNotExist:
        pass

    if research_artifact is None:
        research_artifact = (
            EpisodeArtifact.objects.filter(episode=episode, title__startswith="p2-")
            .exclude(content="")
            .exclude(content__startswith="[SKIPPED:")
            .exclude(content__startswith="[FAILED:")
            .first()
        )

    if research_artifact is None:
        raise ValueError(
            f"No p2-* research artifact with content found for episode {episode_id}."
        )

    result = _discover_questions(
        research_digest=research_artifact.content,
        episode_topic=episode.title,
    )

    content_text = _format_question_discovery(result)

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="question-discovery",
        defaults={
            "content": content_text,
            "description": "Gap analysis and followup questions from initial research.",
            "workflow_context": "Research Gathering",
            "metadata": result.model_dump(),
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s question-discovery artifact for episode %s", action, episode_id)
    return artifact


def create_research_digest(episode_id: int, artifact_title: str) -> EpisodeArtifact:
    """Digest a single research artifact into a structured summary.

    Reads the specified ``p2-*`` artifact, calls
    :func:`~apps.podcast.services.digest_research.digest_research`, and
    saves the result as ``digest-{suffix}`` where *suffix* is derived from
    the input artifact title (e.g. ``p2-perplexity`` becomes
    ``digest-perplexity``).

    If the AI call fails due to quota errors (HTTP 429) or usage limit exceeded,
    this function logs a warning and creates a "skipped" digest artifact. The
    pipeline continues, falling back to raw research artifacts for briefing.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        artifact_title: Exact title of the research artifact to digest
            (e.g. ``"p2-perplexity"``).

    Returns:
        The digest :class:`EpisodeArtifact`.
    """
    from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded

    from apps.podcast.services.digest_research import (
        digest_research as _digest_research,
    )

    episode = Episode.objects.get(pk=episode_id)
    research_artifact = EpisodeArtifact.objects.get(
        episode=episode, title=artifact_title
    )

    if not research_artifact.content:
        raise ValueError(
            f"Artifact '{artifact_title}' for episode {episode_id} has no content."
        )

    # Derive digest title: "p2-perplexity" -> "digest-perplexity"
    suffix = artifact_title
    if suffix.startswith("p2-"):
        suffix = suffix[3:]
    digest_title = f"digest-{suffix}"

    try:
        result = _digest_research(
            research_text=research_artifact.content,
            episode_topic=episode.title,
        )

        content_text = _format_research_digest(result)

        artifact, created = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title=digest_title,
            defaults={
                "content": content_text,
                "description": f"Structured digest of {artifact_title}.",
                "workflow_context": "Research Gathering",
                "metadata": result.model_dump(),
            },
        )

        action = "Created" if created else "Updated"
        logger.info("%s %s artifact for episode %s", action, digest_title, episode_id)
        return artifact

    except (UsageLimitExceeded, ModelHTTPError) as exc:
        # Check if it's a quota/rate limit error (HTTP 429)
        is_quota_error = False
        if isinstance(exc, ModelHTTPError):
            is_quota_error = exc.response.status_code == 429
        elif isinstance(exc, UsageLimitExceeded):
            is_quota_error = True

        if is_quota_error:
            logger.warning(
                "AI quota exceeded when creating digest for %s (episode %s). "
                "Skipping digest - briefing will use raw research instead. "
                "Error: %s",
                artifact_title,
                episode_id,
                str(exc),
            )

            # Create a skipped artifact so the pipeline knows this step completed
            artifact, created = EpisodeArtifact.objects.update_or_create(
                episode=episode,
                title=digest_title,
                defaults={
                    "content": f"[SKIPPED: AI quota exceeded]\n\n"
                    f"Raw research available in {artifact_title}.",
                    "description": f"Digest of {artifact_title} (skipped - quota exceeded).",
                    "workflow_context": "Research Gathering",
                    "metadata": {"skipped": True, "reason": "quota_exceeded"},
                },
            )

            action = "Created" if created else "Updated"
            logger.info(
                "%s skipped %s artifact for episode %s",
                action,
                digest_title,
                episode_id,
            )
            return artifact
        else:
            # Re-raise non-quota errors
            raise


def cross_validate(episode_id: int) -> EpisodeArtifact:
    """Cross-validate findings across all research artifacts.

    Reads every ``p2-*`` artifact for the episode, calls
    :func:`~apps.podcast.services.cross_validate.cross_validate`, and saves
    the result as a ``cross-validation`` artifact.

    Args:
        episode_id: Primary key of the target :class:`Episode`.

    Returns:
        The ``cross-validation`` :class:`EpisodeArtifact`.
    """
    from apps.podcast.services.cross_validate import cross_validate as _cross_validate

    episode = Episode.objects.get(pk=episode_id)

    research_artifacts = (
        EpisodeArtifact.objects.filter(episode=episode, title__startswith="p2-")
        .exclude(content="")
        .exclude(content__startswith="[SKIPPED:")
        .exclude(content__startswith="[FAILED:")
    )

    research_texts: dict[str, str] = {}
    for art in research_artifacts:
        if art.content:
            # Use the portion after "p2-" as the tool name key
            tool_name = art.title[3:] if art.title.startswith("p2-") else art.title
            research_texts[tool_name] = art.content

    if not research_texts:
        raise ValueError(
            f"No p2-* research artifacts with content found for episode {episode_id}."
        )

    result = _cross_validate(
        research_texts=research_texts,
        episode_topic=episode.title,
    )

    content_text = _format_cross_validation(result)

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="cross-validation",
        defaults={
            "content": content_text,
            "description": "Cross-validation of findings across all research sources.",
            "workflow_context": "Cross-Validation",
            "metadata": result.model_dump(),
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s cross-validation artifact for episode %s", action, episode_id)
    return artifact


def write_briefing(episode_id: int) -> EpisodeArtifact:
    """Create the master research briefing.

    Reads the ``cross-validation`` artifact and all ``digest-*`` artifacts,
    calls :func:`~apps.podcast.services.write_briefing.write_briefing`, and
    saves the result as a ``p3-briefing`` artifact.

    Args:
        episode_id: Primary key of the target :class:`Episode`.

    Returns:
        The ``p3-briefing`` :class:`EpisodeArtifact`.
    """
    from apps.podcast.services.write_briefing import write_briefing as _write_briefing

    episode = Episode.objects.get(pk=episode_id)

    # Read cross-validation artifact
    try:
        cv_artifact = EpisodeArtifact.objects.get(
            episode=episode, title="cross-validation"
        )
    except EpisodeArtifact.DoesNotExist:
        raise ValueError(
            f"No cross-validation artifact found for episode {episode_id}. "
            "Run cross_validate() first."
        )

    # Gather digest artifacts; fall back to raw p2-* if no digests exist
    digest_artifacts = EpisodeArtifact.objects.filter(
        episode=episode, title__startswith="digest-"
    )
    research_digests: dict[str, str] = {}
    skipped_digests: set[str] = set()

    for art in digest_artifacts:
        if art.content:
            tool_name = art.title.replace("digest-", "", 1)
            # Check if digest was skipped or failed
            if art.content.startswith("[SKIPPED:") or art.content.startswith(
                "[FAILED:"
            ):
                skipped_digests.add(tool_name)
            else:
                research_digests[tool_name] = art.content

    # For skipped digests, use raw research instead
    if skipped_digests:
        logger.info(
            "Using raw research for skipped digests: %s (episode %s)",
            ", ".join(sorted(skipped_digests)),
            episode_id,
        )
        for art in EpisodeArtifact.objects.filter(
            episode=episode, title__startswith="p2-"
        ):
            if art.content:
                tool_name = art.title[3:] if art.title.startswith("p2-") else art.title
                if tool_name in skipped_digests:
                    research_digests[tool_name] = art.content

    if not research_digests:
        # Fall back to raw p2-* artifacts if no digests were created at all
        for art in EpisodeArtifact.objects.filter(
            episode=episode, title__startswith="p2-"
        ):
            if art.content:
                tool_name = art.title[3:] if art.title.startswith("p2-") else art.title
                research_digests[tool_name] = art.content

    if not research_digests:
        raise ValueError(
            f"No digest or research artifacts found for episode {episode_id}."
        )

    result = _write_briefing(
        cross_validation=cv_artifact.content,
        research_digests=research_digests,
        episode_title=episode.title,
    )

    content_text = _format_master_briefing(result)

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p3-briefing",
        defaults={
            "content": content_text,
            "description": "Master research briefing for episode planning.",
            "workflow_context": "Cross-Validation",
            "metadata": result.model_dump(),
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s p3-briefing artifact for episode %s", action, episode_id)
    return artifact


def craft_research_prompt(episode_id: int, research_type: str) -> EpisodeArtifact:
    """Craft a single AI-generated research prompt for the given research type.

    Reads the ``p1-brief`` artifact, calls the
    :func:`~apps.podcast.services.craft_research_prompt.craft_research_prompt`
    Named AI Tool, and saves the result as a ``prompt-{research_type}`` artifact.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        research_type: One of ``"perplexity"``, ``"gpt"``, or ``"gemini"``.

    Returns:
        The ``prompt-{research_type}`` :class:`EpisodeArtifact`.
    """
    from apps.podcast.services.craft_research_prompt import (
        craft_research_prompt as _craft_research_prompt,
    )

    episode = Episode.objects.get(pk=episode_id)

    # Read p1-brief artifact
    try:
        brief = EpisodeArtifact.objects.get(episode=episode, title="p1-brief")
        episode_brief = brief.content
    except EpisodeArtifact.DoesNotExist:
        episode_brief = episode.description

    result = _craft_research_prompt(
        episode_brief=episode_brief,
        episode_title=episode.title,
        research_type=research_type,
    )

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title=f"prompt-{research_type}",
        defaults={
            "content": result.prompt,
            "description": f"AI-crafted {research_type} research prompt.",
            "workflow_context": "Research Gathering",
        },
    )

    action = "Created" if created else "Updated"
    logger.info(
        "%s prompt-%s artifact for episode %s", action, research_type, episode_id
    )
    return artifact


def craft_targeted_research_prompts(
    episode_id: int,
) -> dict[str, EpisodeArtifact]:
    """Craft GPT-Researcher, Gemini, Together, Claude, and MiroFish prompts in a single AI call.

    Reads the ``p1-brief`` and ``question-discovery`` artifacts, calls the
    :func:`~apps.podcast.services.craft_research_prompt.craft_targeted_prompts`
    Named AI Tool, saves the prompts as ``prompt-gpt``, ``prompt-gemini``,
    ``prompt-together``, ``prompt-claude``, and ``prompt-mirofish`` artifacts,
    and creates empty placeholder ``p2-chatgpt`` / ``p2-gemini`` /
    ``p2-together`` / ``p2-claude`` / ``p2-mirofish`` artifacts for the
    fan-in signal.

    Args:
        episode_id: Primary key of the target :class:`Episode`.

    Returns:
        A dict mapping ``"prompt-gpt"``, ``"prompt-gemini"``,
        ``"prompt-together"``, ``"prompt-claude"``, and ``"prompt-mirofish"``
        to their :class:`EpisodeArtifact` instances.
    """
    from apps.podcast.services.craft_research_prompt import (
        craft_targeted_prompts as _craft_targeted_prompts,
    )

    episode = Episode.objects.get(pk=episode_id)

    # Read p1-brief artifact
    try:
        brief = EpisodeArtifact.objects.get(episode=episode, title="p1-brief")
        episode_brief = brief.content
    except EpisodeArtifact.DoesNotExist:
        episode_brief = episode.description

    # Read question-discovery artifact
    qd_artifact = EpisodeArtifact.objects.get(
        episode=episode, title="question-discovery"
    )
    if not qd_artifact.content:
        raise ValueError(
            f"question-discovery artifact for episode {episode_id} has no content."
        )

    result = _craft_targeted_prompts(
        episode_brief=episode_brief,
        question_discovery=qd_artifact.content,
        episode_title=episode.title,
    )

    # Save prompts as artifacts (canonical data-passing mechanism)
    gpt_artifact, _ = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="prompt-gpt",
        defaults={
            "content": result.gpt_prompt,
            "description": "AI-crafted GPT-Researcher research prompt.",
            "workflow_context": "Research Gathering",
        },
    )
    gemini_artifact, _ = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="prompt-gemini",
        defaults={
            "content": result.gemini_prompt,
            "description": "AI-crafted Gemini research prompt.",
            "workflow_context": "Research Gathering",
        },
    )
    together_artifact, _ = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="prompt-together",
        defaults={
            "content": result.together_prompt,
            "description": "AI-crafted Together research prompt.",
            "workflow_context": "Research Gathering",
        },
    )
    claude_artifact, _ = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="prompt-claude",
        defaults={
            "content": result.claude_prompt,
            "description": "AI-crafted Claude deep research prompt.",
            "workflow_context": "Research Gathering",
        },
    )

    # Create empty placeholder artifacts for the targeted research steps.
    # These are required for fan-in correctness: without them, if one
    # research task finishes before the other starts, the signal would see
    # only one p2-* artifact (with content) and advance prematurely.
    # The empty content="" causes two no-op signal evaluations, which is
    # acceptable overhead for correctness.
    EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-chatgpt",
        defaults={
            "content": "",
            "description": "GPT-Researcher targeted research (placeholder).",
            "workflow_context": "Research Gathering",
        },
    )
    EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-gemini",
        defaults={
            "content": "",
            "description": "Gemini targeted research (placeholder).",
            "workflow_context": "Research Gathering",
        },
    )
    EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-together",
        defaults={
            "content": "",
            "description": "Together Open Deep Research (placeholder).",
            "workflow_context": "Research Gathering",
        },
    )
    EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-claude",
        defaults={
            "content": "",
            "description": "Claude deep research (placeholder).",
            "workflow_context": "Research Gathering",
        },
    )
    logger.info(
        "craft_targeted_research_prompts: saved prompt-gpt + prompt-gemini + "
        "prompt-together + prompt-claude artifacts for episode %s",
        episode_id,
    )
    return {
        "prompt-gpt": gpt_artifact,
        "prompt-gemini": gemini_artifact,
        "prompt-together": together_artifact,
        "prompt-claude": claude_artifact,
    }
