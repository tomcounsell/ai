"""Guard the #2377 Mode 2 fix: a verified-clean no-op DOCS run still marks completed.

Mode 2 is a skill-instruction behavior (the ai-repo `/do-docs` completion marker
lives in `.claude/skill-context/do-docs.md`, executed by the agent running the
skill — there is no code function to exercise). Before the fix, the marker was
gated on "complete **and committed**", so a cascade that verified docs already
consistent with nothing to commit never wrote `sdlc-tool stage-marker --stage
DOCS --status completed`, and router row 9 re-dispatched DOCS forever
(DOCS ↔ REVIEW loop). These content assertions lock in the decoupled contract:
the marker is written on a clean no-op, and withheld ONLY on an errored run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SKILL_CONTEXT = Path(__file__).resolve().parents[2] / ".claude" / "skill-context" / "do-docs.md"


@pytest.fixture(scope="module")
def context_text() -> str:
    return _SKILL_CONTEXT.read_text()


def test_skill_context_file_exists():
    assert _SKILL_CONTEXT.is_file(), f"missing skill-context file: {_SKILL_CONTEXT}"


def test_completion_marker_not_gated_on_committed(context_text: str):
    """The old 'complete and committed' phrasing was the Mode 2 defect — it
    excluded the clean no-op. It must be gone."""
    assert "complete and committed" not in context_text


def test_clean_no_op_still_marks_completed(context_text: str):
    """The decoupled contract must explicitly cover the nothing-to-commit case."""
    # Collapse runs of whitespace so line-wrapped phrases still match.
    normalized = " ".join(context_text.lower().split())
    assert "no-op" in normalized
    assert "nothing to commit" in normalized
    # The marker command itself is still present.
    assert "sdlc-tool stage-marker --stage DOCS --status completed" in context_text


def test_error_path_still_excluded_from_completion_marker(context_text: str):
    """The one legitimate incomplete path — Step 2d substrate `status: error` —
    must still be called out as NOT writing the completion marker."""
    lowered = context_text.lower()
    assert "status: error" in lowered
    assert "exception" in lowered  # "The one exception — an errored run must NOT..."


def test_router_loop_rationale_is_documented(context_text: str):
    """The fix should name why withholding the marker is harmful, so a future
    editor does not re-introduce the commit gate."""
    assert "#2377" in context_text
    assert "re-dispatch" in context_text.lower()
