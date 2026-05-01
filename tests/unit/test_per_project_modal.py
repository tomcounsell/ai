"""Tests for the per-project sub-table in the reflection modal template.

The modal at ``ui/templates/reflections/_partials/modal_content.html`` renders
a per-project breakdown sub-row under each run record when ``run.projects``
is non-empty. This test renders the template directly via Jinja2 and asserts:

- ``run.projects = []`` (or omitted) → no sub-rows in output
- ``run.projects = [...]`` → one sub-row per project, with ``[slug]`` cell
- A project with ``error`` populated renders the error text
- ``status="disabled"`` renders a distinct ``badge-disabled`` class (not
  ``badge-ok`` / ``badge-error`` / ``badge-skipped``) so cost-cap exhaustion
  is visually legible

These guards prevent the per-project breakdown from silently regressing if
the template loop or CSS classes are restructured.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

UI_TEMPLATES = Path(__file__).resolve().parents[2] / "ui" / "templates"


@pytest.fixture
def env() -> Environment:
    """Jinja2 environment configured exactly like ``ui/app.py`` does."""
    e = Environment(loader=FileSystemLoader(str(UI_TEMPLATES)), autoescape=True)
    # The modal template expects these filters to exist; provide minimal
    # implementations so rendering does not crash. Output formatting is not
    # what we are asserting on — presence/absence of rows and class names is.
    e.filters["format_timestamp"] = lambda ts: str(ts) if ts is not None else "-"
    e.filters["format_duration"] = lambda s: f"{s}s" if s is not None else "-"
    e.filters["format_interval_filter"] = lambda s: f"{s}s" if s else "-"
    e.filters["format_relative"] = lambda s: f"{s}s" if s is not None else "-"
    return e


def _base_reflection_ctx() -> dict:
    """Minimal `r` context object the modal template requires."""
    return {
        "name": "tech-debt-scan",
        "description": "Per-project test fixture",
        "last_status": "success",
        "enabled": True,
        "group": "audits",
        "interval": 86400,
        "priority": "low",
        "execution_type": "function",
        "next_due": None,
        "overdue": False,
        "run_count": 1,
        "last_error": None,
    }


def _render_modal(env: Environment, recent_runs: list[dict]) -> str:
    tmpl = env.get_template("reflections/_partials/modal_content.html")
    return tmpl.render(
        r=_base_reflection_ctx(),
        recent_runs=recent_runs,
        sparkline=[],
        manual_command=None,
    )


class TestModalPerProjectRendering:
    """Sub-table rendering rules for `run.projects`."""

    def test_empty_projects_omits_sub_rows(self, env: Environment) -> None:
        """A run with `projects = []` renders no `.project-sub-row` rows."""
        runs = [
            {
                "timestamp": 1730000000.0,
                "status": "success",
                "duration": 1.5,
                "error": None,
                "projects": [],
            }
        ]
        html = _render_modal(env, runs)
        # CSS class definition still appears in <style>, but no <tr> uses it.
        assert '<tr class="project-sub-row">' not in html

    def test_missing_projects_key_omits_sub_rows(self, env: Environment) -> None:
        """Legacy run records (no `projects` key at all) render cleanly."""
        runs = [
            {
                "timestamp": 1730000000.0,
                "status": "success",
                "duration": 1.5,
                "error": None,
            }
        ]
        html = _render_modal(env, runs)
        assert '<tr class="project-sub-row">' not in html

    def test_two_projects_render_two_sub_rows_with_slug(self, env: Environment) -> None:
        """Two qualifying projects → two indented sub-rows tagged `[ai]`/`[popoto]`."""
        runs = [
            {
                "timestamp": 1730000000.0,
                "status": "success",
                "duration": 3.0,
                "error": None,
                "projects": [
                    {
                        "slug": "ai",
                        "status": "ok",
                        "duration": 1.2,
                        "findings_count": 0,
                        "error": None,
                    },
                    {
                        "slug": "popoto",
                        "status": "ok",
                        "duration": 1.7,
                        "findings_count": 2,
                        "error": None,
                    },
                ],
            }
        ]
        html = _render_modal(env, runs)
        assert html.count('<tr class="project-sub-row">') == 2
        assert "[ai]" in html
        assert "[popoto]" in html

    def test_project_error_text_rendered(self, env: Environment) -> None:
        """A project with an `error` field surfaces that text in the sub-row."""
        runs = [
            {
                "timestamp": 1730000000.0,
                "status": "error",
                "duration": 0.5,
                "error": None,
                "projects": [
                    {
                        "slug": "popoto",
                        "status": "error",
                        "duration": 0.5,
                        "findings_count": 0,
                        "error": "skip_if raised: OSError on network mount",
                    }
                ],
            }
        ]
        html = _render_modal(env, runs)
        assert "skip_if raised: OSError on network mount" in html

    def test_disabled_status_uses_distinct_badge(self, env: Environment) -> None:
        """`status=disabled` renders `badge-disabled` (NOT badge-ok/error/skipped)."""
        runs = [
            {
                "timestamp": 1730000000.0,
                "status": "success",
                "duration": 0.0,
                "error": None,
                "projects": [
                    {
                        "slug": "ai",
                        "status": "disabled",
                        "duration": 0.0,
                        "findings_count": 0,
                        "error": "global API cap reached (500)",
                    }
                ],
            }
        ]
        html = _render_modal(env, runs)
        # The disabled project's status cell uses badge-disabled, distinct
        # from the green "ok" / red "error" / gray "skipped" classes.
        assert "badge-disabled" in html
        # Be specific: the disabled label appears in a badge wrapper, not just
        # the prose. The full snippet is what the template emits.
        assert ">disabled<" in html

    def test_skipped_status_uses_skipped_badge(self, env: Environment) -> None:
        """`status=skipped` renders `badge-skipped` (gray)."""
        runs = [
            {
                "timestamp": 1730000000.0,
                "status": "success",
                "duration": 0.0,
                "error": None,
                "projects": [
                    {
                        "slug": "no-docs",
                        "status": "skipped",
                        "duration": 0.0,
                        "findings_count": 0,
                        "error": None,
                    }
                ],
            }
        ]
        html = _render_modal(env, runs)
        assert "badge-skipped" in html

    def test_all_four_status_colors_distinct(self, env: Environment) -> None:
        """ok / error / disabled / skipped each have their own CSS class.

        Visual legibility is a Success Criteria item — they must NOT collapse
        to a single class. This guards the CSS contract from regression.
        """
        runs = [
            {
                "timestamp": 1730000000.0,
                "status": "error",
                "duration": 0.0,
                "error": None,
                "projects": [
                    {
                        "slug": "a",
                        "status": "ok",
                        "duration": 0.0,
                        "findings_count": 0,
                        "error": None,
                    },
                    {
                        "slug": "b",
                        "status": "error",
                        "duration": 0.0,
                        "findings_count": 0,
                        "error": "boom",
                    },
                    {
                        "slug": "c",
                        "status": "disabled",
                        "duration": 0.0,
                        "findings_count": 0,
                        "error": None,
                    },
                    {
                        "slug": "d",
                        "status": "skipped",
                        "duration": 0.0,
                        "findings_count": 0,
                        "error": None,
                    },
                ],
            }
        ]
        html = _render_modal(env, runs)
        for cls in ("badge-ok", "badge-error", "badge-disabled", "badge-skipped"):
            assert cls in html, f"missing {cls} in rendered modal"
