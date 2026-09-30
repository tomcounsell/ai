"""Deterministic content assertions for the Resume Re-Verification rule (#2138).

The behavioral fix is entirely prompt/rails text loaded into every headless
turn. This CI gate asserts the anchor phrases survive edits to two surfaces:

- ``.claude/commands/roles/_prime-rails.md`` — the ``## Re-Verification on
  Resume`` section (the rule itself).
- ``config/personas/segments/work-patterns.md`` — the reconciling caveat that
  "not announcing a resume" does not license asserting prior work from memory.

It also enforces the ≤8-line rails-bloat cap (Risk 1 mitigation / critique
NIT #1): the rails load on every turn, so the section body must stay dense.

Failure-path strategy: every read asserts the file exists and is non-empty
BEFORE inspecting content, so a missing/truncated file fails loudly rather than
passing vacuously.
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.sdlc]

REPO_ROOT = Path(__file__).resolve().parents[2]
RAILS_PATH = REPO_ROOT / ".claude" / "commands" / "roles" / "_prime-rails.md"
WORK_PATTERNS_PATH = REPO_ROOT / "config" / "personas" / "segments" / "work-patterns.md"

RAILS_SECTION_HEADER = "## Re-Verification on Resume"
RAILS_BODY_LINE_CAP = 8


def _read_nonempty(path: Path) -> str:
    """Read a required prompt file, failing loudly if absent or empty."""
    assert path.exists(), f"Required prompt file is missing: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"Required prompt file is empty: {path}"
    return text


def _extract_section_body(text: str, header: str) -> list[str]:
    """Return the non-empty body lines of a Markdown section.

    Body runs from the line after ``header`` up to (but excluding) the next
    ``##`` heading or ``---`` horizontal rule.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    assert start is not None, f"Section header {header!r} not found"

    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## ") or stripped == "---":
            break
        if stripped:
            body.append(line)
    return body


class TestRailsReVerificationRule:
    """The ``## Re-Verification on Resume`` section must exist with its anchors."""

    def test_section_header_present(self):
        text = _read_nonempty(RAILS_PATH)
        assert RAILS_SECTION_HEADER in text, (
            f"{RAILS_SECTION_HEADER!r} section is missing from {RAILS_PATH}. "
            "The resume re-verification rule is the core of #2138 — without it "
            "resumed sessions can re-assert prior completion from memory."
        )

    def test_anchor_substrings_present(self):
        text = _read_nonempty(RAILS_PATH).lower()
        # Live-evidence requirement.
        assert "live evidence" in text, (
            "Rails rule must require re-derivation from 'live evidence'."
        )
        # Memory / recollection is NOT evidence.
        assert ("not evidence" in text) or ("remember" in text), (
            "Rails rule must state that recollection ('I remember doing it') is NOT evidence."
        )
        # Citation template marker so the conclusion names the checked artifact.
        assert "confirmed via" in text or "verified via" in text, (
            "Rails rule must include a concrete citation template (e.g. "
            "'confirmed via gh pr view: PR #123 open')."
        )
        # Named evidence sources.
        assert "gh pr view" in text, "Rails rule must name PR state as an evidence source."
        assert "valor-email read" in text, (
            "Rails rule must name the sent-mail log as an evidence source."
        )

    def test_section_body_within_line_cap(self):
        """Critique NIT #1 / Risk 1: rails load every turn — keep it dense."""
        text = _read_nonempty(RAILS_PATH)
        body = _extract_section_body(text, RAILS_SECTION_HEADER)
        assert body, (
            f"{RAILS_SECTION_HEADER!r} section has no body lines — the rule text is missing."
        )
        assert len(body) <= RAILS_BODY_LINE_CAP, (
            f"{RAILS_SECTION_HEADER!r} body is {len(body)} lines, exceeding the "
            f"{RAILS_BODY_LINE_CAP}-line cap. Rails load on every headless turn; "
            "keep the rule dense.\nBody was:\n" + "\n".join(body)
        )


class TestWorkPatternsCaveat:
    """The resume sentence must carry the 'not announcing != from memory' caveat."""

    def test_caveat_present(self):
        text = _read_nonempty(WORK_PATTERNS_PATH).lower()
        assert "re-derive" in text or "live evidence" in text, (
            "work-patterns.md must clarify that not announcing a resume does NOT "
            "mean asserting prior work from memory — it must reference silent "
            "re-derivation from live evidence."
        )
        assert "from memory" in text, (
            "work-patterns.md caveat must explicitly reject asserting prior work 'from memory'."
        )

    def test_cross_references_rails_rule(self):
        text = _read_nonempty(WORK_PATTERNS_PATH)
        assert "Re-Verification on Resume" in text, (
            "work-patterns.md caveat must point the reader at the "
            "'Re-Verification on Resume' rails rule so either surface lands on "
            "the full rule."
        )
