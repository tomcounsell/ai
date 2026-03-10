"""Typed outcome contract for /do-* skills.

Every /do-* skill can emit a SkillOutcome as a parseable JSON block embedded
in its output text. The Observer and stage detector read this block for
deterministic routing decisions, falling back to LLM classification when
no outcome block is present.

The block format is an HTML comment so it's invisible in rendered markdown:

    <!-- OUTCOME {"status": "success", "stage": "BUILD", ...} -->

Usage:
    from agent.skill_outcome import SkillOutcome, parse_outcome_from_text, format_outcome

    # Emit an outcome
    outcome = SkillOutcome(
        status="success",
        stage="BUILD",
        artifacts={"pr_url": "https://github.com/org/repo/pull/42"},
        notes="PR created with 3 commits",
    )
    print(format_outcome(outcome))

    # Parse from mixed prose
    outcome = parse_outcome_from_text(worker_output)
    if outcome:
        print(f"Stage {outcome.stage} finished with status {outcome.status}")
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Valid status values for SkillOutcome
VALID_STATUSES = frozenset({"success", "fail", "partial", "retry", "skipped"})

# Valid stage values
VALID_STAGES = frozenset({"PLAN", "BUILD", "TEST", "REVIEW", "DOCS"})

# Regex to extract the OUTCOME block from mixed text
_OUTCOME_PATTERN = re.compile(
    r"<!--\s*OUTCOME\s+(\{.*?\})\s*-->",
    re.DOTALL,
)


@dataclass
class SkillOutcome:
    """Typed outcome from a /do-* skill execution.

    Attributes:
        status: Execution result - one of "success", "fail", "partial", "retry", "skipped".
        stage: Pipeline stage this outcome is for (e.g., "BUILD", "TEST").
        artifacts: Structured data produced by the skill (PR URLs, plan paths, etc.).
        notes: Human-readable summary of what happened.
        failure_reason: Why the skill failed (only set when status is "fail" or "partial").
        next_skill: Suggested next skill to invoke (e.g., "/do-test" after build).
    """

    status: str
    stage: str
    artifacts: dict = field(default_factory=dict)
    notes: str = ""
    failure_reason: str | None = None
    next_skill: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary.

        Returns:
            Dict with all fields. None values are omitted for cleaner output.
        """
        d = {
            "status": self.status,
            "stage": self.stage,
            "artifacts": self.artifacts,
            "notes": self.notes,
        }
        if self.failure_reason is not None:
            d["failure_reason"] = self.failure_reason
        if self.next_skill is not None:
            d["next_skill"] = self.next_skill
        return d

    @classmethod
    def from_dict(cls, data: dict) -> SkillOutcome:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with SkillOutcome fields.

        Returns:
            SkillOutcome instance.

        Raises:
            KeyError: If required fields (status, stage) are missing.
            TypeError: If data is not a dict.
        """
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")
        return cls(
            status=data["status"],
            stage=data["stage"],
            artifacts=data.get("artifacts", {}),
            notes=data.get("notes", ""),
            failure_reason=data.get("failure_reason"),
            next_skill=data.get("next_skill"),
        )

    def to_json(self) -> str:
        """Serialize to a JSON string.

        Returns:
            Compact JSON string representation.
        """
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def is_terminal(self) -> bool:
        """Whether this outcome represents a terminal state (success or fail).

        Returns:
            True if the status is "success", "fail", or "skipped".
        """
        return self.status in {"success", "fail", "skipped"}


def format_outcome(outcome: SkillOutcome) -> str:
    """Format a SkillOutcome as an HTML comment block for embedding in output.

    The block is invisible in rendered markdown but parseable by the pipeline.

    Args:
        outcome: The SkillOutcome to format.

    Returns:
        String in the form: <!-- OUTCOME {"status": "...", ...} -->
    """
    return f"<!-- OUTCOME {outcome.to_json()} -->"


def parse_outcome_from_text(text: str) -> SkillOutcome | None:
    """Extract a SkillOutcome from mixed prose/markdown text.

    Searches for the first <!-- OUTCOME {...} --> block in the text and
    parses the JSON payload. Returns None if no valid outcome block is found.

    This function is intentionally lenient: malformed JSON, missing fields,
    or unexpected values result in None (not an exception). The caller should
    fall back to LLM-based classification when this returns None.

    Args:
        text: Raw text output from a skill execution.

    Returns:
        SkillOutcome if a valid outcome block was found, None otherwise.
    """
    if not text:
        return None

    match = _OUTCOME_PATTERN.search(text)
    if not match:
        return None

    json_str = match.group(1)
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Malformed JSON in OUTCOME block: {e}")
        return None

    if not isinstance(data, dict):
        logger.warning(f"OUTCOME block JSON is not a dict: {type(data).__name__}")
        return None

    # Require status and stage
    if "status" not in data or "stage" not in data:
        logger.warning(f"OUTCOME block missing required fields: {list(data.keys())}")
        return None

    try:
        return SkillOutcome.from_dict(data)
    except (KeyError, TypeError) as e:
        logger.warning(f"Failed to construct SkillOutcome from OUTCOME block: {e}")
        return None
