"""Trace JSON schema for the happy path testing pipeline.

Defines the structured trace format that serves as the contract between
the discovery stage (agent-browser exploration) and the generation stage
(Rodney script generation). Each trace file represents one happy path
as an ordered list of steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Valid action types for trace steps
VALID_ACTIONS = frozenset({"navigate", "input", "click", "wait", "assert", "screenshot", "exists"})

# Actions that require a CSS selector
SELECTOR_REQUIRED_ACTIONS = frozenset({"input", "click", "wait", "exists"})

# Valid assertion types
VALID_ASSERT_TYPES = frozenset({"url_contains", "text_visible", "element_exists", "title_equals"})


@dataclass
class TraceStep:
    """A single step in a happy path trace."""

    action: str
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    type: str | None = None  # For assert steps
    path: str | None = None  # For screenshot steps

    def validate(self) -> list[str]:
        """Validate this step and return a list of error messages."""
        errors: list[str] = []

        if not self.action:
            errors.append("Step missing required 'action' field")
            return errors

        if self.action not in VALID_ACTIONS:
            errors.append(
                f"Invalid action '{self.action}'. Must be one of: {sorted(VALID_ACTIONS)}"
            )
            return errors

        if self.action == "navigate" and not self.url:
            errors.append("'navigate' action requires 'url' field")

        if self.action in SELECTOR_REQUIRED_ACTIONS and not self.selector:
            errors.append(f"'{self.action}' action requires 'selector' field")

        if self.action == "input" and self.value is None:
            errors.append("'input' action requires 'value' field")

        if self.action == "assert":
            if not self.type:
                errors.append("'assert' action requires 'type' field")
            elif self.type not in VALID_ASSERT_TYPES:
                errors.append(
                    f"Invalid assert type '{self.type}'. "
                    f"Must be one of: {sorted(VALID_ASSERT_TYPES)}"
                )
            if self.value is None:
                errors.append("'assert' action requires 'value' field")

        return errors


@dataclass
class Trace:
    """A complete happy path trace representing a user journey."""

    name: str
    url: str
    steps: list[TraceStep] = field(default_factory=list)
    expected_final_url: str | None = None
    expected_text: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Validate the entire trace and return a list of error messages."""
        errors: list[str] = []

        if not self.name:
            errors.append("Trace missing required 'name' field")
        if not self.url:
            errors.append("Trace missing required 'url' field")

        if not self.steps:
            errors.append("Trace has no steps (empty steps array)")
            return errors

        for i, step in enumerate(self.steps):
            step_errors = step.validate()
            for err in step_errors:
                errors.append(f"Step {i}: {err}")

        return errors


def parse_trace(data: dict[str, Any]) -> Trace:
    """Parse a trace dictionary into a Trace dataclass.

    Args:
        data: Dictionary parsed from trace JSON file.

    Returns:
        Trace instance.

    Raises:
        ValueError: If data is not a dict or missing required structure.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Trace data must be a dict, got {type(data).__name__}")

    steps = []
    raw_steps = data.get("steps", [])
    if not isinstance(raw_steps, list):
        raise ValueError(f"'steps' must be a list, got {type(raw_steps).__name__}")

    for step_data in raw_steps:
        if not isinstance(step_data, dict):
            raise ValueError(f"Each step must be a dict, got {type(step_data).__name__}")
        steps.append(
            TraceStep(
                action=step_data.get("action", ""),
                url=step_data.get("url"),
                selector=step_data.get("selector"),
                value=step_data.get("value"),
                type=step_data.get("type"),
                path=step_data.get("path"),
            )
        )

    expected_text = data.get("expected_text", [])
    if not isinstance(expected_text, list):
        expected_text = [expected_text] if expected_text else []

    return Trace(
        name=data.get("name", ""),
        url=data.get("url", ""),
        steps=steps,
        expected_final_url=data.get("expected_final_url"),
        expected_text=expected_text,
    )


def validate_trace_file(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a trace data dictionary.

    Args:
        data: Dictionary parsed from trace JSON file.

    Returns:
        Tuple of (is_valid, list of error messages).
    """
    try:
        trace = parse_trace(data)
    except ValueError as e:
        return False, [str(e)]

    errors = trace.validate()
    return len(errors) == 0, errors
