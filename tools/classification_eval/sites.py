"""The site table: one :class:`~tools.classification_eval.Site` row per
declared classification site (#3410).

Adding a site is one ``_site(...)`` entry here, made by the builder landing
that site: import the site's ``LLMTask``, output type, and prompt builder,
give the row a fixture loader (the site's unit-test examples) and a label
reducer, and pick the minimum and budget from the constants below.

Fields the landing builder varies while iterating on the local arm:
``candidate_prompt`` (a prompt shaped for a 3B model), ``candidate_system``,
and ``candidate_output_type`` (a tighter schema). The reference side is the
site's prompt verbatim and is never tuned here (Rabbit Holes: re-scoring the
reference is a different experiment).

Minimums: 50 inputs per site, 200 for the C1 to C4 routing sites (Risk 7),
with the real-message share at least half. Budgets: the 3 s sites (C8, C9,
C10) carry ``budget_s=3.0``; every other site is measured against the
reference arm's p95 plus one second.

This module imports the site modules, so the CLI loads it only on the
``--site`` path; ``--audit`` discovers sites from the AST instead.
"""

from __future__ import annotations

from types import SimpleNamespace

from bridge.job_router import JOB_ROUTE, JobRouteDecision, _build_prompt
from tools.classification_eval import Input, Site
from tools.classifier import INTAKE_INTENT, INTENT_CLASSIFICATION_PROMPT, IntentDecision

DEFAULT_MINIMUM_N = 50
ROUTING_MINIMUM_N = 200
HOT_PATH_BUDGET_S = 3.0

SITES: dict[str, Site] = {}


def _site(site: Site) -> Site:
    if site.id in SITES:
        raise ValueError(f"duplicate site row {site.id}")
    SITES[site.id] = site
    return site


# --- C13: classifier.intake_intent (stays on granite; latency-only record) -------------

_INTAKE_SESSION_CONTEXT = "Working on UI redesign"


def _intake_prompt(inp: Input) -> str:
    return INTENT_CLASSIFICATION_PROMPT.format(
        message=inp.text,
        session_context=inp.context.get("session_context", _INTAKE_SESSION_CONTEXT),
        session_status=inp.context.get("session_status", "active"),
    )


def _intake_fixtures() -> list[Input]:
    """The examples from ``tests/unit/test_intake_classifier.py`` and the
    prompt's own category examples."""
    examples = [
        "Actually make it blue instead",
        "Here's the file I mentioned",
        "Yes, that approach works",
        "Also add error handling",
        "Fix the login bug",
        "How does the auth system work?",
        "Add dark mode",
        "What time is my next meeting?",
    ]
    return [
        Input(text, "fixture", {"session_context": _INTAKE_SESSION_CONTEXT}) for text in examples
    ]


_site(
    Site(
        id=INTAKE_INTENT.site,
        task=INTAKE_INTENT,
        prompt=_intake_prompt,
        output_type=IntentDecision,
        label=lambda out: out.intent,
        reference="anthropic",
        minimum_n=DEFAULT_MINIMUM_N,
        budget_s=None,
        fixtures=_intake_fixtures,
    )
)


# --- C12: job_router.route (stays on granite; latency-only record) --------------------

_JOB_CANDIDATES = [
    SimpleNamespace(job_id="job-ui-redesign", current_goal=lambda: "Redesign the settings UI"),
    SimpleNamespace(job_id="job-login-bug", current_goal=lambda: "Fix the login page bug"),
]


def _job_route_prompt(inp: Input) -> str:
    return _build_prompt(inp.text, list(inp.context.get("candidates", _JOB_CANDIDATES)))


def _job_route_fixtures() -> list[Input]:
    examples = [
        "make the settings page buttons blue",
        "the login form still throws on submit",
        "start a new feature: dark mode toggle",
        "what's the weather",
    ]
    return [Input(text, "fixture", {"candidates": _JOB_CANDIDATES}) for text in examples]


_site(
    Site(
        id=JOB_ROUTE.site,
        task=JOB_ROUTE,
        prompt=_job_route_prompt,
        output_type=JobRouteDecision,
        label=lambda out: out.decision,
        reference="anthropic",
        minimum_n=DEFAULT_MINIMUM_N,
        budget_s=None,
        fixtures=_job_route_fixtures,
    )
)


def site_for(site_id: str) -> Site:
    """The row for ``site_id``; a missing row names the rows that exist."""
    try:
        return SITES[site_id]
    except KeyError:
        known = ", ".join(sorted(SITES)) or "(none)"
        raise KeyError(f"no site row for {site_id!r}; known rows: {known}") from None
