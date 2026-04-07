"""Fingerprint classifier - classifies SDLC episodes by structural topology.

Uses a single lightweight LLM call (Claude Haiku) to classify a completed
SDLC session's problem topology, affected layer, ambiguity level, and
whether acceptance criteria were defined.

This is called by the Reflections cycle-close step after an SDLC session
completes. The output fingerprint is stored on the CyclicEpisode for
pattern matching.

Falls back to "ambiguous" topology on any LLM failure (timeout, malformed
response, API error) so episodes are always created even if classification fails.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from config.models import HAIKU

logger = logging.getLogger(__name__)

CLASSIFIER_PROMPT = """\
You are classifying a completed SDLC (software development lifecycle) cycle.
Given the session summary and metadata below, classify it structurally.

Session summary: {summary}
Issue URL: {issue_url}
Branch: {branch_name}
Tool sequence (stage:tool pairs): {tool_sequence}
Friction events: {friction_events}
Stage durations: {stage_durations}
Tags: {tags}

Classify this session into the following structured output. Return ONLY valid JSON, no markdown.

{{
  "problem_topology": one of ["new_feature", "bug_fix", "refactor", "integration", "configuration", "ambiguous"],
  "affected_layer": one of ["model", "bridge", "agent", "tool", "config", "test", "docs", "infra", "unknown"],
  "ambiguity_at_intake": float 0.0-1.0 (0=crystal clear requirements, 1=fully ambiguous),
  "acceptance_criterion_defined": boolean (were there clear success criteria?)
}}

Rules:
- "new_feature": adding wholly new capability
- "bug_fix": fixing broken behavior
- "refactor": restructuring without behavior change
- "integration": connecting systems or components
- "configuration": environment, config, deployment changes
- "ambiguous": cannot determine from available context
- For affected_layer, choose the PRIMARY layer affected
- ambiguity_at_intake reflects the INITIAL state, not the final clarity
"""

# Default fingerprint returned on classifier failure
DEFAULT_FINGERPRINT = {
    "problem_topology": "ambiguous",
    "affected_layer": "unknown",
    "ambiguity_at_intake": 0.5,
    "acceptance_criterion_defined": False,
}


def classify_fingerprint(
    summary: str = "",
    issue_url: str = "",
    branch_name: str = "",
    tool_sequence: list[str] | None = None,
    friction_events: list[str] | None = None,
    stage_durations: dict[str, float] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Classify an SDLC session into a structural fingerprint.

    Args:
        summary: Session summary text
        issue_url: GitHub issue URL if available
        branch_name: Git branch name
        tool_sequence: List of "stage:tool_type" strings
        friction_events: List of friction event strings
        stage_durations: Dict of stage name to duration in seconds
        tags: Session tags

    Returns:
        Dict with keys: problem_topology, affected_layer,
        ambiguity_at_intake, acceptance_criterion_defined
    """
    if anthropic is None:
        logger.warning("anthropic package not available, returning default fingerprint")
        return dict(DEFAULT_FINGERPRINT)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, returning default fingerprint")
        return dict(DEFAULT_FINGERPRINT)

    prompt = CLASSIFIER_PROMPT.format(
        summary=summary or "(no summary)",
        issue_url=issue_url or "(none)",
        branch_name=branch_name or "(none)",
        tool_sequence=", ".join(tool_sequence or []) or "(none)",
        friction_events=", ".join(friction_events or []) or "(none)",
        stage_durations=json.dumps(stage_durations or {}),
        tags=", ".join(tags or []) or "(none)",
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=HAIKU,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from response
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        result = json.loads(text)

        # Validate and sanitize
        from models.cyclic_episode import PROBLEM_TOPOLOGIES, AFFECTED_LAYERS

        if result.get("problem_topology") not in PROBLEM_TOPOLOGIES:
            result["problem_topology"] = "ambiguous"
        if result.get("affected_layer") not in AFFECTED_LAYERS:
            result["affected_layer"] = "unknown"

        ambiguity = result.get("ambiguity_at_intake", 0.5)
        result["ambiguity_at_intake"] = max(0.0, min(1.0, float(ambiguity)))
        result["acceptance_criterion_defined"] = bool(
            result.get("acceptance_criterion_defined", False)
        )

        logger.info(
            f"Fingerprint classified: topology={result['problem_topology']}, "
            f"layer={result['affected_layer']}, "
            f"ambiguity={result['ambiguity_at_intake']:.2f}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Fingerprint classifier returned malformed JSON: {e}")
        return dict(DEFAULT_FINGERPRINT)
    except Exception as e:
        logger.warning(f"Fingerprint classifier failed: {e}")
        return dict(DEFAULT_FINGERPRINT)


def classify_session(session) -> dict[str, Any]:
    """Convenience wrapper: classify an AgentSession object.

    Args:
        session: An AgentSession instance

    Returns:
        Fingerprint dict
    """
    return classify_fingerprint(
        summary=session.summary or "",
        issue_url=session.issue_url or "",
        branch_name=session.branch_name or "",
        tool_sequence=session.tool_sequence if isinstance(session.tool_sequence, list) else [],
        friction_events=session.friction_events
        if isinstance(session.friction_events, list)
        else [],
        stage_durations={},  # Not yet tracked on AgentSession
        tags=session.tags if isinstance(session.tags, list) else [],
    )
