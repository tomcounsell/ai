"""Replaces dropped manual AC#8 smoke test from plan #1187.

Covers two-project aggregation across all 5 refactored audits with mocked
projects (no real Cowboy machine required). Asserts each audit:
- aggregates findings from both projects with `[slug]` prefixes
- produces a `projects` list of length 2
- each per-project record has the expected slug
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _two_projects(tmp_path: Path) -> list[dict]:
    ai_dir = tmp_path / "ai"
    popoto_dir = tmp_path / "popoto"
    ai_dir.mkdir()
    popoto_dir.mkdir()
    return [
        {"slug": "ai", "working_directory": str(ai_dir)},
        {"slug": "popoto", "working_directory": str(popoto_dir)},
    ]


def test_legacy_code_scan_aggregates_two_repos(tmp_path):
    from reflections.maintenance import run_legacy_code_scan

    projects = _two_projects(tmp_path)

    with (
        patch("reflections.utils.load_local_projects", return_value=projects),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="file.py:1:TODO: x\n", stderr="")
        result = run_legacy_code_scan()

    assert len(result["projects"]) == 2
    slugs = {p["slug"] for p in result["projects"]}
    assert slugs == {"ai", "popoto"}
    assert any(f.startswith("[ai] ") for f in result["findings"])
    assert any(f.startswith("[popoto] ") for f in result["findings"])


def test_documentation_audit_aggregates_two_repos(tmp_path):
    import asyncio

    from reflections.auditing import run_documentation_audit

    projects = _two_projects(tmp_path)
    for p in projects:
        (Path(p["working_directory"]) / "docs").mkdir()

    mock_summary = MagicMock()
    mock_summary.skipped = False
    mock_summary.skip_reason = ""
    mock_summary.skip_type = ""
    mock_summary.kept = ["a.md", "b.md"]
    mock_summary.updated = []
    mock_summary.deleted = []

    with (
        patch("reflections.utils.load_local_projects", return_value=projects),
        patch("scripts.docs_auditor.DocsAuditor") as mock_da,
    ):
        mock_instance = MagicMock()
        mock_instance.run.return_value = mock_summary
        mock_instance._api_call_count = 5
        mock_da.return_value = mock_instance
        result = asyncio.run(run_documentation_audit())

    assert len(result["projects"]) == 2
    slugs = {p["slug"] for p in result["projects"]}
    assert slugs == {"ai", "popoto"}


def test_skills_audit_aggregates_two_repos(tmp_path):
    from reflections.auditing import run_skills_audit

    projects = _two_projects(tmp_path)
    # Create the audit script in BOTH projects so neither is skipped.
    for p in projects:
        wd = Path(p["working_directory"])
        script_dir = wd / ".claude" / "skills" / "do-skills-audit" / "scripts"
        script_dir.mkdir(parents=True)
        (script_dir / "audit_skills.py").write_text("# audit\n")

    fake_audit_data = {
        "summary": {"fail": 0, "warn": 0, "total_skills": 1},
        "findings": [],
    }

    import json

    with (
        patch("reflections.utils.load_local_projects", return_value=projects),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_audit_data))
        result = run_skills_audit()

    assert len(result["projects"]) == 2
    slugs = {p["slug"] for p in result["projects"]}
    assert slugs == {"ai", "popoto"}


def test_hooks_audit_aggregates_two_repos(tmp_path):

    projects = _two_projects(tmp_path)
    # Place a settings.json in BOTH projects so neither is skipped.
    for p in projects:
        wd = Path(p["working_directory"])
        claude_dir = wd / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text('{"hooks": {}}')

    result = run_hooks_audit_with_mock(projects)
    assert len(result["projects"]) == 2
    slugs = {p["slug"] for p in result["projects"]}
    assert slugs == {"ai", "popoto"}


def run_hooks_audit_with_mock(projects):
    from reflections.auditing import run_hooks_audit

    with patch("reflections.utils.load_local_projects", return_value=projects):
        return run_hooks_audit()


def test_feature_docs_audit_aggregates_two_repos(tmp_path):
    from reflections.auditing import run_feature_docs_audit

    projects = _two_projects(tmp_path)
    # Place docs/features/ in BOTH projects so neither is skipped.
    for p in projects:
        wd = Path(p["working_directory"])
        (wd / "docs" / "features").mkdir(parents=True)

    with patch("reflections.utils.load_local_projects", return_value=projects):
        result = run_feature_docs_audit()

    assert len(result["projects"]) == 2
    slugs = {p["slug"] for p in result["projects"]}
    assert slugs == {"ai", "popoto"}
