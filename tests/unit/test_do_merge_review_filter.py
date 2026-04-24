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
