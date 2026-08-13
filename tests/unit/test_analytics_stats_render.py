"""Render coverage for `_partials/analytics_stats.html` (#2719).

This is the last of the three `| usd` call sites (alongside jobs_table.html
and session_modal_content.html) and previously had zero render coverage
anywhere under tests/ — a gap called out explicitly by the plan. It renders
through a registrar-configured env exactly like the other render tests, so
the ceil-to-cent `usd` filter behavior shipped by `96b0f65dd` is protected
here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from ui.app import register_template_filters

pytestmark = [pytest.mark.unit, pytest.mark.webui]


@pytest.fixture
def env() -> Environment:
    template_dir = Path(__file__).resolve().parent.parent.parent / "ui" / "templates"
    e = Environment(loader=FileSystemLoader(str(template_dir)))
    register_template_filters(e)
    return e


def _minimal_analytics(**overrides) -> dict:
    base = dict(
        sessions_started_today=3,
        sessions_started_7d=12,
        sessions_completed_today=2,
        sessions_completed_7d=10,
        cost_today_usd=1.2345,
        cost_7d_usd=9.001,
        turns_today=42,
        turns_7d=280,
        turns_avg_today=6.0,
        turns_avg_7d=5.5,
        memory_recalls_today=4,
        memory_recalls_7d=20,
        memory_extractions_today=1,
        memory_extractions_7d=6,
    )
    base.update(overrides)
    return base


def test_both_cost_cards_render_ceil_to_cent(env):
    tmpl = env.get_template("_partials/analytics_stats.html")
    html = tmpl.render(analytics=_minimal_analytics(cost_today_usd=1.2345, cost_7d_usd=9.001))
    assert "stats-grid" in html
    # Ceil-to-cent: 1.2345 -> $1.24, 9.001 -> $9.01 (never truncated down).
    assert "$1.24" in html
    assert "$9.01" in html


def test_sub_cent_cost_renders_as_one_cent_not_zero(env):
    """The behavior 96b0f65dd exists to protect: a sub-cent cost must never
    display as $0.00."""
    tmpl = env.get_template("_partials/analytics_stats.html")
    html = tmpl.render(analytics=_minimal_analytics(cost_today_usd=0.001, cost_7d_usd=0.0001))
    assert "$0.01" in html
    assert "$0.00" not in html


def test_no_analytics_renders_empty_state():
    """Falsy `analytics` renders the empty-state message, not a KeyError."""
    template_dir = Path(__file__).resolve().parent.parent.parent / "ui" / "templates"
    e = Environment(loader=FileSystemLoader(str(template_dir)))
    register_template_filters(e)
    tmpl = e.get_template("_partials/analytics_stats.html")
    html = tmpl.render(analytics=None)
    assert "No analytics data yet." in html
    assert "stats-grid" not in html
