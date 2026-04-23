"""
Structural assertions for the long-task-checkpointing feature.

These tests verify that the required prompt guidance landed in the expected
files after the feature was shipped. They are pure file-read string assertions
with no external dependencies — no Redis, no API, no subprocess.

BASELINE_CHARS = 11029  # pre-edit size of builder.md at commit ceedbe68
"""

import re
from pathlib import Path

import pytest

# Pinned pre-edit baseline: builder.md size at commit ceedbe68.
# Do NOT change this to the current file size — it is the budget reference.
BASELINE_CHARS = 11029

REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.mark.unit
@pytest.mark.sdlc
def test_builder_prompt_has_externalization_section():
    """builder.md must contain the Working-state externalization section
    with multiple PROGRESS.md references (create / update / re-read)."""
    builder_md = REPO_ROOT / ".claude" / "agents" / "builder.md"
    text = builder_md.read_text()
    assert "## Working-state externalization" in text, (
        "builder.md is missing '## Working-state externalization' section"
    )
    progress_md_count = text.count("PROGRESS.md")
    assert progress_md_count >= 3, (
        f"Expected at least 3 mentions of 'PROGRESS.md' in builder.md "
        f"(create / update / re-read), found {progress_md_count}"
    )


@pytest.mark.unit
@pytest.mark.sdlc
def test_builder_prompt_soft_limit():
    """builder.md should not exceed BASELINE_CHARS + 5000 chars (soft limit).

    Exceeding this limit indicates the externalization section has grown
    beyond the terse imperative style intended by the plan. Treat as a
    warning: investigate whether the new content is necessary.
    """
    builder_md = REPO_ROOT / ".claude" / "agents" / "builder.md"
    current_size = len(builder_md.read_text())
    assert current_size < BASELINE_CHARS + 5000, (
        f"builder.md soft limit exceeded: {current_size} chars "
        f"(baseline {BASELINE_CHARS} + 5000 = {BASELINE_CHARS + 5000}). "
        "The externalization section may be too verbose — trim it."
    )


@pytest.mark.unit
@pytest.mark.sdlc
def test_builder_prompt_hard_limit():
    """builder.md must not exceed BASELINE_CHARS + 10000 chars (hard limit).

    Exceeding this limit risks material token-count inflation in every
    dev-session turn. This is a hard failure.
    """
    builder_md = REPO_ROOT / ".claude" / "agents" / "builder.md"
    current_size = len(builder_md.read_text())
    assert current_size < BASELINE_CHARS + 10000, (
        f"builder.md hard limit exceeded: {current_size} chars "
        f"(baseline {BASELINE_CHARS} + 10000 = {BASELINE_CHARS + 10000}). "
        "This will materially inflate token counts — reduce the section size."
    )


@pytest.mark.unit
@pytest.mark.sdlc
def test_progress_md_in_build_soft_check():
    """do-build SKILL.md must contain the PROGRESS.md soft-check shell line.

    The line must match the pattern:
        [ -f <something with PROGRESS.md> ] || echo
    This pattern ensures the check never returns nonzero (warn-only).
    """
    skill_md = REPO_ROOT / ".claude" / "skills" / "do-build" / "SKILL.md"
    text = skill_md.read_text()
    pattern = re.compile(r"\[ -f .*PROGRESS\.md.* \] \|\| echo")
    assert pattern.search(text) is not None, (
        "do-build SKILL.md is missing the PROGRESS.md soft-check line. "
        "Expected a line matching: [ -f <path/PROGRESS.md> ] || echo '...'"
    )
