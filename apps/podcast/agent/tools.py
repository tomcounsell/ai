"""Tool definitions that map podcast service functions to Agent SDK tools.

Each entry describes a callable service function with its name, description,
parameters schema, and a reference to the implementation.  The orchestrator
uses this list to register tools with the Anthropic agent.
"""

from __future__ import annotations

from typing import Any, Callable

from apps.podcast.services import (
    analysis,
    audio,
    publishing,
    research,
    setup,
    synthesis,
    workflow,
)

# ---------------------------------------------------------------------------
# Type alias for a tool definition dict
# ---------------------------------------------------------------------------

ToolDef = dict[str, Any]


def _tool(
    name: str,
    fn: Callable[..., Any],
    description: str,
    parameters: dict[str, dict[str, Any]],
) -> ToolDef:
    """Build a standardised tool definition dict."""
    return {
        "name": name,
        "fn": fn,
        "description": description,
        "parameters": parameters,
    }


# ---------------------------------------------------------------------------
# Tool definitions -- one per service function
# ---------------------------------------------------------------------------

tools: list[ToolDef] = [
    # -- Setup ---------------------------------------------------------------
    _tool(
        name="setup_episode",
        fn=setup.setup_episode,
        description=(
            "Initialize episode workflow. Creates a p1-brief artifact from "
            "the episode description and an EpisodeWorkflow record to track "
            "production state. Must be called before any other tools."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode to initialize.",
                "required": True,
            },
        },
    ),
    # -- Research ------------------------------------------------------------
    _tool(
        name="run_perplexity_research",
        fn=research.run_perplexity_research,
        description=(
            "Run Perplexity Deep Research (sonar-deep-research model) and "
            "save results as a p2-perplexity artifact. This is typically the "
            "first research step after setup."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Research query to send to Perplexity. Should include "
                    "methodology instructions for academic rigour."
                ),
                "required": True,
            },
        },
    ),
    _tool(
        name="run_gpt_researcher",
        fn=research.run_gpt_researcher,
        description=(
            "Run GPT-Researcher multi-agent research and save results as a "
            "p2-chatgpt artifact. Best for industry analysis, case studies, "
            "and technical documentation."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Research query focusing on industry, technical, or "
                    "implementation questions identified during question "
                    "discovery."
                ),
                "required": True,
            },
        },
    ),
    _tool(
        name="run_gemini_research",
        fn=research.run_gemini_research,
        description=(
            "Run Gemini Deep Research and save results as a p2-gemini "
            "artifact. Best for policy analysis, regulatory frameworks, "
            "and strategic context."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Research query focusing on policy, regulatory, or "
                    "strategic questions."
                ),
                "required": True,
            },
        },
    ),
    _tool(
        name="add_manual_research",
        fn=research.add_manual_research,
        description=(
            "Save human-provided research content as a p2-{title} artifact. "
            "Use for research pasted from Claude, Grok, expert interviews, "
            "or any manual source."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "title": {
                "type": "string",
                "description": (
                    "Short identifier for the research source, e.g. "
                    "'claude', 'grok', 'expert-interview'. Will be "
                    "prefixed with 'p2-'."
                ),
                "required": True,
            },
            "content": {
                "type": "string",
                "description": "The full research text to store.",
                "required": True,
            },
        },
    ),
    # -- Analysis ------------------------------------------------------------
    _tool(
        name="discover_questions",
        fn=analysis.discover_questions,
        description=(
            "Analyze initial research to discover follow-up questions and "
            "knowledge gaps. Creates a question-discovery artifact. Requires "
            "at least one p2-* research artifact to exist."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="create_research_digest",
        fn=analysis.create_research_digest,
        description=(
            "Digest a single research artifact into a structured summary. "
            "Creates a digest-{suffix} artifact. Call once per p2-* artifact "
            "before cross-validation."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "artifact_title": {
                "type": "string",
                "description": (
                    "Exact title of the research artifact to digest, "
                    "e.g. 'p2-perplexity', 'p2-gemini'."
                ),
                "required": True,
            },
        },
    ),
    _tool(
        name="cross_validate",
        fn=analysis.cross_validate,
        description=(
            "Cross-validate findings across all p2-* research artifacts. "
            "Creates a cross-validation artifact with verified claims, "
            "single-source claims, and conflicting claims. Requires at "
            "least one p2-* artifact."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="write_briefing",
        fn=analysis.write_briefing,
        description=(
            "Create the master research briefing (p3-briefing artifact). "
            "Reads cross-validation and digest artifacts to produce a "
            "comprehensive briefing organized by topic. Requires "
            "cross-validation to have been run first."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    # -- Synthesis -----------------------------------------------------------
    _tool(
        name="synthesize_report",
        fn=synthesis.synthesize_report,
        description=(
            "Synthesize all research into a narrative podcast report "
            "(5,000-8,000 words). Reads p3-briefing and p2-* artifacts, "
            "produces a report, and saves to Episode.report_text. Requires "
            "the p3-briefing artifact to exist."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="plan_episode_content",
        fn=synthesis.plan_episode_content,
        description=(
            "Create a structured episode content plan for NotebookLM audio "
            "generation. Reads report_text and p3-briefing to produce a "
            "content_plan artifact with episode structure, counterpoints, "
            "and dialogue guidance. Requires report_text to be populated."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    # -- Audio ---------------------------------------------------------------
    _tool(
        name="generate_audio",
        fn=audio.generate_audio,
        description=(
            "Generate episode audio via NotebookLM Enterprise API. This is "
            "a long-running operation (5-30 minutes). Creates a notebook, "
            "uploads source texts, generates audio, downloads, and uploads "
            "to storage. Updates Episode.audio_url. Requires report_text "
            "and content_plan."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="transcribe_audio",
        fn=audio.transcribe_audio,
        description=(
            "Transcribe episode audio using OpenAI Whisper API. Downloads "
            "audio from Episode.audio_url and saves transcript to "
            "Episode.transcript. Requires audio_url to be set."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="generate_episode_chapters",
        fn=audio.generate_episode_chapters,
        description=(
            "Generate chapter markers from the episode transcript using AI. "
            "Saves chapter data as JSON to Episode.chapters. Requires "
            "transcript to be populated."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    # -- Publishing ----------------------------------------------------------
    _tool(
        name="generate_cover_art",
        fn=publishing.generate_cover_art,
        description=(
            "Generate AI cover art for the episode and upload to storage. "
            "Updates Episode.cover_image_url. NOTE: Currently raises "
            "NotImplementedError -- the CLI pipeline needs to be refactored "
            "into importable functions."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="write_episode_metadata",
        fn=publishing.write_episode_metadata,
        description=(
            "Generate episode publishing metadata (description, keywords, "
            "timestamps, resources, CTA) using AI. Creates a metadata "
            "artifact and updates Episode.description and show_notes. "
            "Requires report_text and transcript."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="generate_companions",
        fn=publishing.generate_companions,
        description=(
            "Generate companion resources: one-page summary, action "
            "checklist, and frameworks reference. Creates three "
            "companion-* artifacts. Requires report_text."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="publish_episode",
        fn=publishing.publish_episode,
        description=(
            "Mark the episode as complete and published. Sets status to "
            "'complete' and published_at to now. This is the final step "
            "in the production workflow."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    # -- Workflow state management -------------------------------------------
    _tool(
        name="get_status",
        fn=workflow.get_status,
        description=(
            "Get the current workflow state for an episode. Returns "
            "current_step, status, blocked_on, completed_steps, next_step, "
            "and full history. Use this to determine what to do next."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="advance_step",
        fn=workflow.advance_step,
        description=(
            "Mark a workflow step as completed and move to the next step. "
            "Call this after each step's tool succeeds to advance the "
            "production pipeline."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "completed_step": {
                "type": "string",
                "description": (
                    "Name of the step that was just completed. Must match "
                    "one of the 12 workflow steps exactly."
                ),
                "required": True,
            },
        },
    ),
    _tool(
        name="pause_for_human",
        fn=workflow.pause_for_human,
        description=(
            "Pause the workflow to wait for human input. Use when manual "
            "research (Grok, Claude) is needed, when cover art needs "
            "approval, or at quality gates requiring human review."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "reason": {
                "type": "string",
                "description": (
                    "Description of what human input is needed, e.g. "
                    "'Waiting for manual Grok research to be pasted'."
                ),
                "required": True,
            },
        },
    ),
    _tool(
        name="resume_workflow",
        fn=workflow.resume_workflow,
        description=(
            "Resume a previously paused workflow. Clears the blocked_on "
            "reason and sets status back to running."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
        },
    ),
    _tool(
        name="check_quality_gate",
        fn=workflow.check_quality_gate,
        description=(
            "Check whether a quality gate passes. Supported gates: "
            "'wave_1' (after Master Briefing, checks p3-briefing exists "
            "with 200+ words) and 'wave_2' (after Episode Planning, "
            "checks content_plan exists). Returns {passed: bool, details}."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "gate_name": {
                "type": "string",
                "description": "Quality gate to check: 'wave_1' or 'wave_2'.",
                "required": True,
            },
        },
    ),
    _tool(
        name="fail_step",
        fn=workflow.fail_step,
        description=(
            "Mark the workflow as failed at a specific step. Records the "
            "error message in the workflow history. Use when a tool call "
            "raises an unrecoverable error."
        ),
        parameters={
            "episode_id": {
                "type": "integer",
                "description": "Primary key of the Episode.",
                "required": True,
            },
            "step": {
                "type": "string",
                "description": "Name of the step that failed.",
                "required": True,
            },
            "error": {
                "type": "string",
                "description": "Error message describing what went wrong.",
                "required": True,
            },
        },
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_tool_by_name(name: str) -> ToolDef | None:
    """Look up a tool definition by name."""
    for tool in tools:
        if tool["name"] == name:
            return tool
    return None


def get_tool_names() -> list[str]:
    """Return a sorted list of all tool names."""
    return sorted(t["name"] for t in tools)


def to_anthropic_tool_schemas() -> list[dict[str, Any]]:
    """Convert tool definitions to Anthropic API tool schemas.

    Each schema follows the Anthropic tool_use format with ``name``,
    ``description``, and ``input_schema`` (JSON Schema).
    """
    schemas: list[dict[str, Any]] = []
    for tool in tools:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param_def in tool["parameters"].items():
            prop: dict[str, Any] = {
                "type": param_def["type"],
            }
            if "description" in param_def:
                prop["description"] = param_def["description"]
            properties[param_name] = prop
            if param_def.get("required", False):
                required.append(param_name)

        schemas.append(
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )
    return schemas
