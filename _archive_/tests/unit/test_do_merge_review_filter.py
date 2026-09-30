"""Markdown-contract tests for this repo's merge-gate addendum.

The addendum ``docs/sdlc/do-merge.md`` (the portable ``/do-merge`` skill
defers repo-specific gates to it) once carried an inline bash/jq
stale-review filter and a safe-shape re-admission exemption. Issue #2003
replaced all of that with a single invocation of the shared deterministic
predicate (``python -m tools.merge_predicate --pr-number {PR} --json``),
with strict verdict freshness (head_sha trailer match, else recorded-at vs
latest-commit committer date) and NO shape-based re-admission of stale
approvals.

The extracted behaviors — fail-closed on missing latest-commit data,
head_sha-trailer freshness, stale-timestamp rejection — are now unit-tested
directly against the helper in ``tests/unit/test_do_merge_docs_gate.py``,
which also carries the parity guard that the addendum invokes
``tools.merge_predicate`` (so this file does not duplicate it).

Issue #2376 then removed all test execution from the merge gate: the shape
classifier, per-SHA verdict cache, and full-suite/baseline machinery are
gone. The gate stack is now the shared predicate plus Ruff and Lockfile —
deterministic commands that complete in seconds and cannot wedge.

What remains here are the addendum's surviving markdown contracts:

- The freshness description references the ``committer.date`` fallback
  (committer date, not author date).
- The gate stack runs no pytest and says so explicitly.
- The Lockfile Sync Check is unconditional (no shape-based skip).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Repo-specific merge-gate logic moved from the retired
# .claude/commands/do-merge.md into the SDLC addendum the portable
# /do-merge skill (.claude/skills-global/do-merge/SKILL.md) defers to.
DO_MERGE_MD = REPO_ROOT / "docs" / "sdlc" / "do-merge.md"


def test_committer_date_reference_present_in_markdown():
    """The freshness fallback must reference the committer date, not the
    author date."""
    md = DO_MERGE_MD.read_text()
    assert "committer.date" in md


def test_gate_stack_declares_no_tests_at_merge():
    """The merge gate must state it runs no tests and point at the TEST
    stage + nightly regression as the owners (#2376)."""
    md = DO_MERGE_MD.read_text()
    assert "The merge gate runs no tests" in md
    assert "do-test.md" in md
    assert "nightly regression" in md.lower()


def test_gate_stack_contains_no_pytest_invocation():
    """No pytest / pytest-clean invocation may reappear in the gate stack —
    merge-time test execution is exactly the wedge class #2376 removed."""
    md = DO_MERGE_MD.read_text()
    gate_stack = md[md.find("## Gate Stack") :]
    assert gate_stack, "Gate Stack section missing"
    assert "pytest" not in gate_stack.replace(
        "Do not add a pytest invocation to this gate stack", ""
    )
    assert "### Full Suite Gate" not in md
    assert "### Shape Classification" not in md


def test_lockfile_check_is_unconditional():
    """The Lockfile Sync Check runs on every PR — no shape-based skip."""
    md = DO_MERGE_MD.read_text()
    assert "### Lockfile Sync Check" in md
    assert "LOCKFILE: SKIP" not in md
    assert "uv lock --locked" in md
