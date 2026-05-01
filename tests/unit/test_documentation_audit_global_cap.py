"""Tests for `documentation-audit` global Anthropic API cap (#1187 step 2b).

Per-project iteration multiplies API spend by N projects. The wrapper
enforces a hard ceiling of `DOCS_AUDIT_MAX_TOTAL_API_CALLS=500` calls
per scheduled run; once exhausted, remaining projects are recorded with
`status="disabled"` and the loop exits without invoking DocsAuditor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch


def _projects(tmp_path: Path, slugs: list[str]) -> list[dict]:
    out = []
    for slug in slugs:
        wd = tmp_path / slug
        wd.mkdir()
        (wd / "docs").mkdir()
        out.append({"slug": slug, "working_directory": str(wd)})
    return out


def test_global_cap_constant_is_500():
    from reflections.auditing import DOCS_AUDIT_MAX_TOTAL_API_CALLS

    assert DOCS_AUDIT_MAX_TOTAL_API_CALLS == 500


def test_remaining_projects_disabled_after_cap_exhausted(tmp_path):
    """After project 1 burns 500 API calls, projects 2 and 3 must be disabled."""
    from reflections.auditing import run_documentation_audit

    projects = _projects(tmp_path, ["ai", "popoto", "third"])

    mock_summary = MagicMock()
    mock_summary.skipped = False
    mock_summary.skip_type = ""
    mock_summary.skip_reason = ""
    mock_summary.kept = ["a.md"]
    mock_summary.updated = []
    mock_summary.deleted = []

    with (
        patch("reflections.utils.load_local_projects", return_value=projects),
        patch("scripts.docs_auditor.DocsAuditor") as mock_da,
    ):
        instance = MagicMock()
        instance.run.return_value = mock_summary
        # First call exhausts the budget all at once.
        instance._api_call_count = 500
        mock_da.return_value = instance
        result = asyncio.run(run_documentation_audit())

    # First project ran (status=ok). Remaining two are disabled and never
    # constructed a real DocsAuditor.
    by_slug = {p["slug"]: p for p in result["projects"]}
    assert by_slug["ai"]["status"] == "ok"
    assert by_slug["popoto"]["status"] == "disabled"
    assert by_slug["third"]["status"] == "disabled"
    assert "global API cap reached" in by_slug["popoto"]["error"]


def test_aggregate_status_when_all_remaining_disabled(tmp_path):
    """If every project is disabled (cap pre-exhausted), aggregate is 'disabled'."""
    from reflections import auditing

    projects = _projects(tmp_path, ["ai", "popoto"])

    # Pre-exhaust the budget by setting it to 0 via a low cap.
    with (
        patch("reflections.utils.load_local_projects", return_value=projects),
        patch("scripts.docs_auditor.DocsAuditor") as mock_da,
        patch.object(auditing, "DOCS_AUDIT_MAX_TOTAL_API_CALLS", 0),
    ):
        mock_da.return_value = MagicMock()  # never gets called
        result = asyncio.run(auditing.run_documentation_audit())

    for p in result["projects"]:
        assert p["status"] == "disabled"
    assert result["status"] == "disabled"
    # DocsAuditor should never be instantiated when budget is pre-exhausted.
    mock_da.assert_not_called()


def test_within_budget_runs_all_projects(tmp_path):
    """When per-project spend stays below the cap, every project runs."""
    from reflections.auditing import run_documentation_audit

    projects = _projects(tmp_path, ["ai", "popoto"])

    mock_summary = MagicMock()
    mock_summary.skipped = False
    mock_summary.skip_type = ""
    mock_summary.skip_reason = ""
    mock_summary.kept = []
    mock_summary.updated = []
    mock_summary.deleted = []

    with (
        patch("reflections.utils.load_local_projects", return_value=projects),
        patch("scripts.docs_auditor.DocsAuditor") as mock_da,
    ):
        instance = MagicMock()
        instance.run.return_value = mock_summary
        instance._api_call_count = 50  # well under 500
        mock_da.return_value = instance
        result = asyncio.run(run_documentation_audit())

    by_slug = {p["slug"]: p for p in result["projects"]}
    assert by_slug["ai"]["status"] == "ok"
    assert by_slug["popoto"]["status"] == "ok"
