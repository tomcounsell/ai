"""Tests for `run_per_project_audit` in `reflections/utils.py`.

Covers:
- empty projects list → status="ok", empty findings/projects
- skip predicate → project recorded with status="skipped", excluded from findings
- one project erroring does not abort the rest
- skip_if exception is isolated per project (network-mount / OSError race)
- `[slug]` prefix on findings
- aggregate status rules from the plan's table
- `name` kwarg appears in summary
- `disabled` aggregation
"""

from __future__ import annotations

from unittest.mock import patch

from reflections.utilities import run_per_project_audit


def _project(slug: str, wd: str = "/tmp") -> dict:
    return {"slug": slug, "working_directory": wd}


def test_empty_projects_returns_ok_no_findings():
    with patch("reflections.utilities.load_local_projects", return_value=[]):
        result = run_per_project_audit(
            lambda p: {"status": "ok", "findings": [], "summary": "x", "duration": 0.0},
            name="test-audit",
        )
    assert result["status"] == "ok"
    assert result["findings"] == []
    assert result["projects"] == []
    assert "test-audit" in result["summary"]


def test_skip_if_skips_silently_and_excludes_from_findings():
    projects = [_project("ai"), _project("popoto")]

    def audit(p):
        return {
            "status": "ok",
            "findings": [f"finding-{p['slug']}"],
            "summary": "",
            "duration": 0.1,
        }

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(
            audit,
            skip_if=lambda repo_root: True,
            name="hooks-audit",
        )
    assert result["status"] == "ok"
    assert result["findings"] == []
    assert len(result["projects"]) == 2
    for record in result["projects"]:
        assert record["status"] == "skipped"
        assert record["findings_count"] == 0
        assert record["error"] is None


def test_one_project_error_continues_others_and_aggregate_is_error():
    projects = [_project("ai"), _project("popoto")]

    def audit(p):
        if p["slug"] == "popoto":
            raise RuntimeError("boom")
        return {
            "status": "ok",
            "findings": ["something"],
            "summary": "",
            "duration": 0.1,
        }

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, name="tech-debt-scan")

    assert result["status"] == "error"
    assert any(f.startswith("[ai] ") for f in result["findings"])
    assert not any(f.startswith("[popoto] ") for f in result["findings"])
    by_slug = {p["slug"]: p for p in result["projects"]}
    assert by_slug["ai"]["status"] == "ok"
    assert by_slug["popoto"]["status"] == "error"
    assert "RuntimeError" in by_slug["popoto"]["error"]


def test_skip_if_exception_isolated_per_project():
    """A `skip_if` raising OSError must NOT abort the whole audit."""
    projects = [_project("ai"), _project("popoto")]

    def skip_if(repo_root):
        if "popoto" in str(repo_root) or repo_root.name == "popoto":
            raise OSError("network mount unreachable")
        return False

    def audit(p):
        return {
            "status": "ok",
            "findings": [f"finding-{p['slug']}"],
            "summary": "",
            "duration": 0.1,
        }

    projects = [_project("ai", wd="/tmp/ai"), _project("popoto", wd="/tmp/popoto")]
    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, skip_if=skip_if, name="docs-audit")

    assert result["status"] == "error"
    by_slug = {p["slug"]: p for p in result["projects"]}
    assert by_slug["ai"]["status"] == "ok"
    assert by_slug["popoto"]["status"] == "error"
    assert "OSError" in by_slug["popoto"]["error"]
    assert any(f.startswith("[ai]") for f in result["findings"])


def test_slug_prefix_on_findings():
    projects = [_project("ai"), _project("popoto")]

    def audit(p):
        return {
            "status": "ok",
            "findings": ["X", "Y"],
            "summary": "",
            "duration": 0.1,
        }

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, name="x")
    assert "[ai] X" in result["findings"]
    assert "[ai] Y" in result["findings"]
    assert "[popoto] X" in result["findings"]
    assert "[popoto] Y" in result["findings"]


def test_all_disabled_aggregates_to_disabled():
    projects = [_project("ai"), _project("popoto")]

    def audit(p):
        return {
            "status": "disabled",
            "findings": [],
            "summary": "global cap",
            "duration": 0.0,
            "error": "global API cap reached",
        }

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, name="documentation-audit")
    assert result["status"] == "disabled"
    for record in result["projects"]:
        assert record["status"] == "disabled"


def test_mix_ok_and_disabled_aggregates_to_ok():
    projects = [_project("ai"), _project("popoto")]

    def audit(p):
        if p["slug"] == "popoto":
            return {"status": "disabled", "findings": [], "summary": "", "duration": 0.0}
        return {"status": "ok", "findings": ["a"], "summary": "", "duration": 0.0}

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, name="x")
    assert result["status"] == "ok"


def test_mix_error_and_anything_aggregates_to_error():
    projects = [_project("ai"), _project("popoto"), _project("third")]

    def audit(p):
        if p["slug"] == "popoto":
            return {"status": "disabled", "findings": [], "summary": "", "duration": 0.0}
        if p["slug"] == "third":
            return {
                "status": "error",
                "findings": [],
                "summary": "",
                "duration": 0.0,
                "error": "kaboom",
            }
        return {"status": "ok", "findings": ["a"], "summary": "", "duration": 0.0}

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, name="x")
    assert result["status"] == "error"


def test_name_appears_in_summary():
    projects = [_project("ai")]

    def audit(p):
        return {"status": "ok", "findings": [], "summary": "", "duration": 0.0}

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, name="hooks-audit")
    assert "hooks-audit" in result["summary"]


def test_skipped_record_has_zero_findings_count():
    projects = [_project("ai")]

    def audit(p):
        return {
            "status": "ok",
            "findings": ["should not see this"],
            "summary": "",
            "duration": 0.0,
        }

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, skip_if=lambda r: True, name="x")
    assert result["projects"][0]["findings_count"] == 0
    assert result["findings"] == []


def test_audit_returning_non_dict_recorded_as_error():
    projects = [_project("ai")]

    def audit(p):
        return "oops, not a dict"

    with patch("reflections.utilities.load_local_projects", return_value=projects):
        result = run_per_project_audit(audit, name="x")
    assert result["status"] == "error"
    assert result["projects"][0]["status"] == "error"
    assert "TypeError" in result["projects"][0]["error"]
