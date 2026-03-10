"""Context builder functions for SDLC pipeline stage handoffs.

Provides 4 fidelity modes to right-size the context passed to sub-skills
(/do-plan, /do-build, /do-test, etc.) instead of always sending the full
50k+ token conversation context:

- full:     Pass-through for backward compat (~unlimited tokens)
- compact:  Structured handoff context (~800 tokens)
- minimal:  Ultra-compact for individual builder agents (~200 tokens)
- steering: Observer coaching messages (~300 tokens)

Each builder function accepts an AgentSession and returns a formatted
string ready to inject into the sub-skill prompt.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# Ordered SDLC stages — mirrors models.agent_session.SDLC_STAGES
_SDLC_STAGES = ["ISSUE", "PLAN", "BUILD", "TEST", "REVIEW", "DOCS"]


def build_full_context(
    session: AgentSession,
    enriched_message: str = "",
) -> str:
    """Full-fidelity context — pass-through for backward compatibility.

    If enriched_message is provided, returns it with a context_mode header.
    If empty, builds a basic context from session fields.

    Args:
        session: The current agent session.
        enriched_message: Pre-built enriched message to pass through.

    Returns:
        Context string with context_mode: full header.
    """
    header = "context_mode: full"

    if enriched_message and enriched_message.strip():
        return f"{header}\n{enriched_message}"

    # Build basic context from session fields
    lines = [header]
    lines.append(f"session_id: {session.session_id}")

    if session.correlation_id:
        lines.append(f"correlation_id: {session.correlation_id}")

    lines.append(f"project: {session.project_key}")
    lines.append(f"status: {session.status}")

    links = session.get_links()
    if links:
        lines.append("links:")
        for kind, url in links.items():
            lines.append(f"  {kind}: {url}")

    if session.branch_name:
        lines.append(f"branch: {session.branch_name}")

    if session.context_summary:
        lines.append(f"summary: {session.context_summary}")

    if session.expectations:
        lines.append(f"expectations: {session.expectations}")

    return "\n".join(lines)


def build_compact_context(
    session: AgentSession,
    plan_path: str | None = None,
    previous_artifacts: dict[str, Any] | None = None,
) -> str:
    """Structured compact context for stage handoffs (~800 tokens target).

    Includes session identity, links, stage progress, branch, artifacts,
    plan summary (first 30 lines), last 5 history entries, and semantic
    routing fields.

    Args:
        session: The current agent session.
        plan_path: Optional path to plan document; first 30 lines are included.
        previous_artifacts: Optional dict of artifacts (URLs, commits, etc.)
            from previous stages.

    Returns:
        Structured context string with context_mode: compact header.
    """
    lines = ["context_mode: compact"]

    # Identity
    lines.append(f"session_id: {session.session_id}")
    if session.correlation_id:
        lines.append(f"correlation_id: {session.correlation_id}")
    lines.append(f"project: {session.project_key}")

    # Links
    links = session.get_links()
    if links:
        lines.append("links:")
        for kind, url in links.items():
            lines.append(f"  {kind}: {url}")

    # Stage progress
    progress = session.get_stage_progress()
    lines.append("stage_progress:")
    for stage, status in progress.items():
        lines.append(f"  {stage}: {status}")

    # Branch
    if session.branch_name:
        lines.append(f"branch: {session.branch_name}")

    # Previous artifacts
    if previous_artifacts:
        lines.append("artifacts:")
        for key, value in previous_artifacts.items():
            lines.append(f"  {key}: {value}")

    # Plan summary (first 30 lines)
    if plan_path:
        plan_lines = _read_plan_summary(plan_path, max_lines=30)
        if plan_lines:
            lines.append("plan_summary:")
            lines.extend(f"  {line}" for line in plan_lines)

    # Last 5 history entries
    history = session.get_history_list()
    recent = history[-5:] if len(history) > 5 else history
    if recent:
        lines.append("recent_history:")
        for entry in recent:
            lines.append(f"  - {entry}")

    # Semantic routing fields
    if session.context_summary:
        lines.append(f"context_summary: {session.context_summary}")
    if session.expectations:
        lines.append(f"expectations: {session.expectations}")

    return "\n".join(lines)


def build_minimal_context(
    session: AgentSession,
    task_description: str,
    relevant_files: list[str] | None = None,
) -> str:
    """Ultra-compact context for individual builder sub-agents (~200 tokens).

    Contains only the task description, relevant files, branch, working
    directory, and essential links. No history, no plan, no stage progress.

    Args:
        session: The current agent session.
        task_description: What the builder should do. Required; raises
            ValueError if empty or whitespace-only.
        relevant_files: Optional list of file paths relevant to the task.

    Returns:
        Minimal context string with context_mode: minimal header.

    Raises:
        ValueError: If task_description is empty or whitespace-only.
    """
    if not task_description or not task_description.strip():
        raise ValueError("task_description must not be empty")

    lines = ["context_mode: minimal"]
    lines.append(f"task: {task_description.strip()}")

    if relevant_files:
        lines.append("files:")
        for f in relevant_files:
            lines.append(f"  - {f}")

    if session.branch_name:
        lines.append(f"branch: {session.branch_name}")

    if session.working_dir:
        lines.append(f"working_dir: {session.working_dir}")

    # Only include issue and PR links (not plan — minimal means minimal)
    links = session.get_links()
    link_lines = []
    if "issue" in links:
        link_lines.append(f"  issue: {links['issue']}")
    if "pr" in links:
        link_lines.append(f"  pr: {links['pr']}")
    if link_lines:
        lines.append("links:")
        lines.extend(link_lines)

    return "\n".join(lines)


def build_steering_context(session: AgentSession) -> str:
    """Context for Observer coaching messages (~300 tokens).

    Focuses on pipeline position: current stage, completed stages, next
    expected stage, plus any queued human messages and semantic fields.

    Args:
        session: The current agent session.

    Returns:
        Steering context string with context_mode: steering header.
    """
    lines = ["context_mode: steering"]

    progress = session.get_stage_progress()

    # Determine current stage (highest non-completed stage)
    current_stage = None
    completed_stages = []
    next_stage = None

    for stage in _SDLC_STAGES:
        status = progress.get(stage, "pending")
        if status == "completed":
            completed_stages.append(stage)
        elif status == "in_progress":
            current_stage = stage
        elif status in ("pending", "failed") and current_stage is not None and next_stage is None:
            next_stage = stage

    # If no stage is in_progress, find the first pending one as current
    if current_stage is None:
        for stage in _SDLC_STAGES:
            if progress.get(stage, "pending") == "pending":
                current_stage = stage
                break

    # If current_stage was just set (from pending), find next after it
    if next_stage is None and current_stage is not None:
        found_current = False
        for stage in _SDLC_STAGES:
            if stage == current_stage:
                found_current = True
                continue
            if found_current and progress.get(stage, "pending") != "completed":
                next_stage = stage
                break

    if current_stage:
        lines.append(f"current_stage: {current_stage}")
    if completed_stages:
        lines.append(f"completed_stages: {', '.join(completed_stages)}")
    if next_stage:
        lines.append(f"next_stage: {next_stage}")

    # Queued human messages
    queued = session.queued_steering_messages
    if isinstance(queued, list) and queued:
        lines.append("queued_messages:")
        for msg in queued:
            lines.append(f"  - {msg}")

    # Links
    links = session.get_links()
    if links:
        lines.append("links:")
        for kind, url in links.items():
            lines.append(f"  {kind}: {url}")

    # Semantic routing fields
    if session.context_summary:
        lines.append(f"context_summary: {session.context_summary}")
    if session.expectations:
        lines.append(f"expectations: {session.expectations}")

    return "\n".join(lines)


def get_context_mode(skill_path: str) -> str:
    """Extract context_fidelity from a SKILL.md file's YAML frontmatter.

    Reads the file, looks for YAML frontmatter between ``---`` delimiters,
    and extracts the ``context_fidelity`` field value.

    Args:
        skill_path: Path to a SKILL.md file.

    Returns:
        The context_fidelity value (e.g., "compact", "minimal", "full",
        "steering"), or "compact" as default if the field is missing
        or the file cannot be read.
    """
    try:
        path = Path(skill_path)
        if not path.exists():
            return "compact"

        text = path.read_text()
        if not text.strip():
            return "compact"

        # Parse YAML frontmatter between --- delimiters
        if not text.startswith("---"):
            return "compact"

        # Find the closing ---
        end_idx = text.find("---", 3)
        if end_idx == -1:
            return "compact"

        frontmatter = text[3:end_idx]

        # Simple key: value parsing (avoids PyYAML dependency)
        for line in frontmatter.splitlines():
            line = line.strip()
            if line.startswith("context_fidelity:"):
                value = line.split(":", 1)[1].strip()
                if value:
                    return value

        return "compact"

    except Exception:
        logger.debug(f"Could not read context_fidelity from {skill_path}", exc_info=True)
        return "compact"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_plan_summary(plan_path: str, max_lines: int = 30) -> list[str]:
    """Read the first N lines of a plan document.

    Returns an empty list if the file cannot be read.

    Args:
        plan_path: Path to the plan markdown file.
        max_lines: Maximum number of lines to read.

    Returns:
        List of line strings (without trailing newlines), or empty list.
    """
    try:
        path = Path(plan_path)
        if not path.exists():
            return []
        text = path.read_text()
        lines = text.splitlines()[:max_lines]
        return lines
    except Exception:
        logger.debug(f"Could not read plan summary from {plan_path}", exc_info=True)
        return []
