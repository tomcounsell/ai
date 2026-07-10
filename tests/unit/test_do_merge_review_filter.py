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

What remains here are the addendum's surviving markdown contracts:

- The freshness description references the ``committer.date`` fallback
  (committer date, not author date).
- Shape Classification precedes the Lockfile and Full Suite gates so
  ``$SHAPE`` / ``$CACHED_VERDICT`` are available downstream.
- docs-only shape skips the lockfile check and the full suite.
- small-patch shape routes to targeted pytest via ``tests_to_run``.
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


def test_shape_classification_precedes_lockfile_and_full_suite_gates():
    """The Shape Classification block must precede the Lockfile Sync Check
    and Full Suite gates so $SHAPE / $CACHED_VERDICT are available
    downstream."""
    md = DO_MERGE_MD.read_text()
    shape_idx = md.find("### Shape Classification")
    lockfile_idx = md.find("### Lockfile Sync Check")
    full_suite_idx = md.find("### Full Suite Gate")
    assert shape_idx >= 0
    assert lockfile_idx >= 0
    assert full_suite_idx >= 0
    assert shape_idx < lockfile_idx
    assert shape_idx < full_suite_idx


def test_lockfile_check_skipped_for_docs_only():
    md = DO_MERGE_MD.read_text()
    assert "LOCKFILE: SKIP" in md


def test_full_suite_skipped_for_docs_only():
    md = DO_MERGE_MD.read_text()
    assert "FULL_SUITE: SKIP" in md
    assert "no Python files changed" in md


def test_small_patch_uses_targeted_pytest():
    md = DO_MERGE_MD.read_text()
    assert "targeted pytest for small-patch" in md
    assert "tests_to_run" in md
