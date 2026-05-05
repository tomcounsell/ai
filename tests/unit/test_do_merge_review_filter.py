"""Tests for the commit-SHA-aware review filter (item 2 of sdlc-1155).

The filter itself lives in a shell snippet inside
``.claude/commands/do-merge.md``. We can't execute the full gate here
without a real PR, so these tests exercise the filter at the jq-expression
level via a small Python shim: we feed a synthetic comment list (and a
synthetic ``LATEST_COMMIT_DATE``) through ``jq`` directly and assert the
expected comment is selected.

This validates the semantic contract:

- Comments with ``created_at`` strictly older than the latest commit date
  are dropped (``select(.created_at >= $latest_date)``).
- Ties (``created_at == latest_date``) are kept (inclusive via ``>=``).
- Empty/filtered result yields the empty string.

An API failure path (``LATEST_COMMIT_DATE`` empty) is tested separately by
asserting the markdown contains the explicit ``GATES_FAILED`` wording.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DO_MERGE_MD = REPO_ROOT / ".claude" / "commands" / "do-merge.md"


def _run_filter(comments: list[dict], latest_date: str) -> str:
    """Run the jq expression from do-merge.md against a synthetic comment list.

    Returns the selected comment body string (or empty string on no match).
    """
    if shutil.which("jq") is None:
        pytest.skip("jq not installed")
    jq_expr = (
        '[.[] | select(.body | startswith("## Review:")) '
        '| select(.created_at >= $latest_date)] | last | .body // ""'
    )
    r = subprocess.run(
        ["jq", "--arg", "latest_date", latest_date, jq_expr],
        input=json.dumps(comments),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().strip('"').replace("\\n", "\n")


def test_stale_review_before_latest_commit_is_filtered():
    """A ``## Review: Approved`` comment older than the latest commit is dropped."""
    comments = [
        {"body": "## Review: Approved\nLGTM (stale)", "created_at": "2026-04-20T10:00:00Z"},
    ]
    result = _run_filter(comments, latest_date="2026-04-24T10:00:00Z")
    assert result == ""


def test_current_review_after_latest_commit_is_kept():
    """A ``## Review: Approved`` comment newer than the latest commit is kept."""
    comments = [
        {"body": "## Review: Approved\nLGTM", "created_at": "2026-04-24T11:00:00Z"},
    ]
    result = _run_filter(comments, latest_date="2026-04-24T10:00:00Z")
    assert "## Review: Approved" in result


def test_exact_time_tie_is_inclusive():
    """A ``## Review:`` comment with ``created_at == latest_date`` is kept."""
    comments = [
        {"body": "## Review: Approved\nTie", "created_at": "2026-04-24T10:00:00Z"},
    ]
    result = _run_filter(comments, latest_date="2026-04-24T10:00:00Z")
    assert "## Review: Approved" in result


def test_newest_is_selected_when_multiple_current():
    """When multiple ``## Review:`` comments post-date the commit, the newest wins."""
    comments = [
        {"body": "## Review: Changes Requested\nOlder", "created_at": "2026-04-24T10:30:00Z"},
        {"body": "## Review: Approved\nNewer", "created_at": "2026-04-24T11:00:00Z"},
    ]
    result = _run_filter(comments, latest_date="2026-04-24T10:00:00Z")
    assert "Approved" in result


def test_api_failure_fallback_is_explicit_in_markdown():
    """When ``LATEST_COMMIT_DATE`` is empty (API failure), the gate must fail
    with a diagnostic — NOT silently regress to unfiltered behavior."""
    md = DO_MERGE_MD.read_text()
    # Verify the specific GATES_FAILED-on-API-failure wording.
    assert "could not fetch latest commit date for review filter" in md
    # Verify the explicit diagnostic command suggestion is present.
    assert "gh api repos/$REPO/pulls/$ARGUMENTS/commits" in md


def test_committer_date_reference_present_in_markdown():
    """The filter must reference the committer date, not the author date."""
    md = DO_MERGE_MD.read_text()
    assert "committer.date" in md


# ---------------------------------------------------------------------------
# Safe-shape exemption (PR-shape-aware merge gates)
# ---------------------------------------------------------------------------


def test_safe_shape_exemption_extracts_head_sha_from_trailer():
    """The safe-shape exemption uses a regex to pull the approval-commit SHA
    out of the ``<!-- REVIEW_CONTEXT head_sha=... -->`` trailer.
    """
    md = DO_MERGE_MD.read_text()
    assert "REVIEW_CONTEXT head_sha=[a-f0-9]{40}" in md


def test_safe_shape_exemption_skips_when_trailer_missing():
    """A prior approval body without REVIEW_CONTEXT trailer must SKIP the
    exemption (fail closed): the markdown must include a SKIP message
    explaining the missing trailer.
    """
    md = DO_MERGE_MD.read_text()
    assert "no REVIEW_CONTEXT trailer" in md
    assert "fresh review" in md.lower()


def test_safe_shape_exemption_fetches_unfetchable_sha():
    """When the approval SHA isn't in local objects, the exemption must
    attempt to ``fetch origin <sha>`` before giving up.
    """
    md = DO_MERGE_MD.read_text()
    assert "fetch origin" in md
    assert "fetchable" in md.lower()


def test_safe_shape_exemption_classifies_post_approval_diff():
    """The exemption must invoke the classifier in --diff-from / --diff-to
    mode to determine if the post-approval diff is a safe shape.
    """
    md = DO_MERGE_MD.read_text()
    assert "pr_shape_classify --diff-from" in md
    assert "--diff-to" in md


def test_safe_shape_exemption_only_admits_safe_shapes():
    """Only ``docs-only``, ``lockfile-only``, ``small-patch`` post-approval
    diffs re-admit the prior approval. Feature/mixed shapes still invalidate.
    """
    md = DO_MERGE_MD.read_text()
    assert "docs-only lockfile-only small-patch" in md


def test_shape_classifier_called_before_review_check():
    """The Shape Classification block must precede the Structured Review
    Comment Check so $SHAPE / $CACHED_VERDICT are available downstream.
    """
    md = DO_MERGE_MD.read_text()
    shape_idx = md.find("### Shape Classification")
    review_idx = md.find("### Structured Review Comment Check")
    assert shape_idx >= 0
    assert review_idx >= 0
    assert shape_idx < review_idx


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
