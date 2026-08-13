"""Guards over dashboard Jinja2 filter registration (#2719).

Two symmetric guards keep `ui.app.register_template_filters` the single
source of truth for dashboard template filters:

1. **Filter-demand guard**: every `| filter` and `{% filter %}` token used
   across `ui/templates/**/*.html` must resolve against the registrar's
   output. Catches a template using a filter nobody registered — the class
   of bug that shipped `TemplateRuntimeError: No filter named 'usd' found`.
2. **No-hand-copy guard**: no file under `tests/` may hand-roll a filter
   dict (`env.filters["x"] = ...` / `env.filters["x"] = lambda: ...`)
   instead of calling the registrar. Catches a future fixture re-opening the
   hand-copy hole that caused this bug in the first place.

Known blind spot (documented, not solved): dynamic filter application via
`|attr('name')` or `map('name')` passes the filter name as a runtime string
and is invisible to AST scanning. No dashboard template currently uses
either idiom, so the guard is complete for the current template set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, nodes

from ui.app import TEMPLATES_DIR, register_template_filters

pytestmark = [pytest.mark.unit, pytest.mark.webui]

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent.parent
HAND_COPY_PATTERN = re.compile(r"\.filters\s*\[")


def _registrar_env() -> Environment:
    """A bare Environment with only the registrar applied (the guard env)."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    register_template_filters(env)
    return env


def _production_shaped_env() -> Jinja2Templates:
    """A Jinja2Templates instance with the registrar applied — the shape
    production actually renders through. Deliberately NOT create_app(), which
    mounts StaticFiles and builds every router for no benefit here."""
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    register_template_filters(templates.env)
    return templates


def test_registrar_covers_all_six_dashboard_filters():
    """`register_template_filters` adds exactly the six dashboard filters
    beyond Jinja's built-ins — proof that "add a filter, tests inherit it"
    is mechanical, not aspirational."""
    baseline = set(Environment().filters)
    env = Environment()
    register_template_filters(env)
    added = set(env.filters) - baseline
    assert added == {
        "format_timestamp",
        "format_duration",
        "format_interval_filter",
        "format_relative",
        "freshness_age",
        "usd",
    }


def test_guard_env_matches_production_shaped_filter_keys():
    """The guard env is a bare Environment; production renders through
    Jinja2Templates (ui/app.py, imported from fastapi.templating). Assert
    filter-KEY equality between the two so a future production-only filter
    added by any mechanism other than the registrar (filters.update(...),
    Jinja2Templates(env=...), a Jinja extension in create_app) trips this
    guard instead of silently re-diverging one level up. The two envs
    legitimately differ in autoescape and a url_for global — keys only."""
    guard_env = _registrar_env()
    prod = _production_shaped_env()
    assert set(guard_env.filters) == set(prod.env.filters)


def test_every_template_filter_reference_resolves():
    """Parse every ui/templates/**/*.html and assert every `nodes.Filter`
    and `nodes.FilterBlock` name resolves against the registrar's output.
    `nodes.FilterBlock` (the `{% filter %}` tag) is a distinct AST node from
    `nodes.Filter` (the `| name` token) and must be scanned alongside it."""
    env = _registrar_env()
    templates = sorted(TEMPLATES_DIR.rglob("*.html"))

    missing: list[tuple[str, str, int]] = []
    demanded: set[str] = set()

    for template_path in templates:
        source = template_path.read_text()
        ast = env.parse(source, filename=str(template_path))
        for node in ast.find_all((nodes.Filter, nodes.FilterBlock)):
            demanded.add(node.name)
            if node.name not in env.filters:
                missing.append((str(template_path), node.name, node.lineno))

    # An empty sweep would pass vacuously and prove nothing (#2719 Risk 2) —
    # assert the scan actually visited templates and collected filter demand.
    assert len(templates) > 0, "No templates found under ui/templates/ — guard scanned nothing"
    assert len(demanded) > 0, "No filter usage found across templates — guard scanned nothing"

    if missing:
        lines = "\n".join(f"  {path}:{lineno} uses unregistered filter '{name}'" for path, name, lineno in missing)
        pytest.fail(f"Template(s) reference unregistered Jinja filters:\n{lines}")


def test_no_hand_copied_filter_registration_in_tests():
    """No file under tests/ may assign into a Jinja `.filters[...]` dict —
    that is exactly the hand-copy hole that caused #2719: three independent
    filter lists with nothing keeping them in sync. Anchored on the
    attribute-access form (`\\.filters\\s*\\[`), not a bare `filters[`, so a
    local variable named `filters` cannot false-positive. This file itself
    is exempted because it legitimately reads `env.filters`."""
    self_path = Path(__file__).resolve()
    offenders: list[str] = []

    for py_file in sorted(TESTS_DIR.rglob("*.py")):
        if py_file.resolve() == self_path:
            continue
        text = py_file.read_text()
        if HAND_COPY_PATTERN.search(text):
            offenders.append(str(py_file.relative_to(REPO_ROOT)))

    assert not offenders, (
        "Test file(s) hand-copy Jinja filter registration instead of calling "
        f"ui.app.register_template_filters: {offenders}"
    )
