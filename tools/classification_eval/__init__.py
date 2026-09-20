"""The classifier comparison runner (#3410, lane A).

A classification site lands on a local backend only with a paired comparison
record behind it. This package produces that record: it runs the site's
inputs through a *reference* arm (the site's backend on ``main`` before the
taxonomy, with the site's prompt verbatim) and one or more *candidate* arms
(``--candidate ollama``, ``--candidate anthropic``), scores each candidate's
agreement with the reference, measures latency at concurrency 1 and 4, and
writes one ``ImprovementEvidence(kind="classifier_comparison")`` row whose
``detail`` is the whole :class:`ComparisonRecord`. The PR body carries the
per-site summary; ``--audit`` re-applies the acceptance bar mechanically.

Shape of the work:

* :func:`compare` is pure orchestration over injectable :class:`Arm`
  callables, so the math is unit-testable with no network.
  ``tools/classification_eval/arms.py`` holds the live arms (Haiku through
  the Anthropic leg, granite through the Ollama leg, gemma via OpenRouter
  metered under ``promise_detector``).
* ``tools/classification_eval/sites.py`` is the data-driven site table: one
  :class:`Site` row per declared classification site, added by the builder
  who lands that site. A row names the reference prompt builder, the output
  type, the label to compare, the input minimum, the latency budget, the
  fixture loader, and (optionally) a candidate prompt / ``system`` / schema
  the builder varies while iterating on the local arm.
* The acceptance bar (:func:`evaluate_bar`) is the reviewer's six criteria
  from the plan's Key Elements: agreement at or above the tier bar (``high``
  95 / ``medium`` 90 / ``low`` 85, tier from the declared
  ``LLMTask.error_cost``), p95 at concurrency 4 inside the budget (the
  site's own budget when it has one, else the reference p95 plus one
  second), ``contended: false`` (Race 3), error rate at or under 2%, ``n``
  at or above the site minimum, and ``n_real`` at or above half of it
  (Risk 7). A latency-only record (a site that stays on granite) skips the
  agreement criterion and the relative latency budget.
* Records belong to improvement case :data:`CASE_ID`; the CLI also records
  the run's claims on a ``probe`` investigation of that case through
  ``tools/improvement_investigations.py::record_claims``.

The runner is offline tooling: it never runs on the message hot path and it
reads eligibility through the blocking ``is_open_source`` (Risk 3).
"""

from __future__ import annotations

from tools.classification_eval.core import (
    CASE_ID,
    CONTENDED_LABELS,
    EVIDENCE_KIND,
    ISSUE_URL,
    MAX_ERROR_RATE,
    PROJECT_KEY,
    TIER_BAR,
    Arm,
    ArmCall,
    ArmResult,
    ComparisonRecord,
    Input,
    Price,
    ShortfallError,
    Site,
    compare,
    evaluate_bar,
    is_contended,
    latency_budget_s,
    percentile,
    render_report,
    require_minimum,
)
from tools.classification_eval.records import (
    attach_claims,
    audit,
    claims_for,
    declared_classification_tasks,
    latest_record,
    write_record,
)

__all__ = [
    "CASE_ID",
    "CONTENDED_LABELS",
    "EVIDENCE_KIND",
    "ISSUE_URL",
    "MAX_ERROR_RATE",
    "PROJECT_KEY",
    "TIER_BAR",
    "Arm",
    "ArmCall",
    "ArmResult",
    "ComparisonRecord",
    "Input",
    "Price",
    "ShortfallError",
    "Site",
    "attach_claims",
    "audit",
    "claims_for",
    "compare",
    "declared_classification_tasks",
    "evaluate_bar",
    "is_contended",
    "latency_budget_s",
    "latest_record",
    "percentile",
    "render_report",
    "require_minimum",
    "write_record",
]
