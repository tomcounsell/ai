"""Tests for `Reflection.mark_completed(projects=...)` (#1187 step 3).

Post-#1273: the embedded ``run_history`` field was removed from Reflection.
Per-run rows now live in ``ReflectionRun`` (``models/reflection_run.py``);
the ``projects`` argument to ``mark_completed`` is preserved in the
``last_run_summary`` dict (which the dashboard and per-project audits read).
These tests exercise that path.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh_reflection():
    from models.reflection import Reflection

    ref = Reflection.get_or_create("_test_mark_completed_projects")
    yield ref
    try:
        ref.delete()
    except Exception:
        pass


def test_mark_completed_stores_projects_on_record(fresh_reflection):
    """Passing projects=[...] stores the list on the latest run record."""
    from models.reflection import Reflection

    projects = [
        {
            "slug": "ai",
            "status": "ok",
            "duration": 0.5,
            "findings_count": 2,
            "error": None,
        },
        {
            "slug": "popoto",
            "status": "skipped",
            "duration": 0.0,
            "findings_count": 0,
            "error": None,
        },
    ]
    fresh_reflection.mark_completed(duration=1.0, projects=projects)

    ref = Reflection.query.filter(name="_test_mark_completed_projects")[0]
    summary = ref.last_run_summary if isinstance(ref.last_run_summary, dict) else {}
    assert summary.get("projects") == projects


def test_mark_completed_default_projects_is_empty_list(fresh_reflection):
    """Omitting projects stores [] (NOT None) — keeps run records uniform."""
    from models.reflection import Reflection

    fresh_reflection.mark_completed(duration=0.5)

    ref = Reflection.query.filter(name="_test_mark_completed_projects")[0]
    summary = ref.last_run_summary if isinstance(ref.last_run_summary, dict) else {}
    assert summary.get("projects") == []


def test_mark_completed_with_none_projects_stores_empty_list(fresh_reflection):
    """Explicit projects=None coerces to [] in the run record."""
    from models.reflection import Reflection

    fresh_reflection.mark_completed(duration=0.5, projects=None)

    ref = Reflection.query.filter(name="_test_mark_completed_projects")[0]
    summary = ref.last_run_summary if isinstance(ref.last_run_summary, dict) else {}
    assert summary.get("projects") == []


def test_mark_completed_signature_accepts_projects_kwarg():
    """Inspectable signature includes the new projects kwarg."""
    import inspect

    from models.reflection import Reflection

    sig = inspect.signature(Reflection.mark_completed)
    assert "projects" in sig.parameters
    assert sig.parameters["projects"].default is None


def test_mark_completed_with_error_and_projects(fresh_reflection):
    """Both error and projects can be passed together."""
    from models.reflection import Reflection

    projects = [
        {
            "slug": "ai",
            "status": "error",
            "duration": 0.0,
            "findings_count": 0,
            "error": "boom",
        }
    ]
    fresh_reflection.mark_completed(duration=0.3, error="aggregate error", projects=projects)

    ref = Reflection.query.filter(name="_test_mark_completed_projects")[0]
    summary = ref.last_run_summary if isinstance(ref.last_run_summary, dict) else {}
    assert summary.get("status") == "error"
    assert summary.get("error") == "aggregate error"
    assert summary.get("projects") == projects
