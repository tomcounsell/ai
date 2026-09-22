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
  metered under ``promise_detector``, and the ``decisions`` candidate:
  TypeSafe's Jev through the decisions leg, called directly with a per-run
  ``SpendEnvelope`` under ``structured_decision`` so a leg failure is the
  arm's own error and never a fallback answer, #3421).
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
  (Risk 7), plus a seventh, ``cost``, against an Anthropic reference that
  recorded a per-call cost: the candidate's ``cost_per_call_usd`` at or
  under one tenth of the reference's (a reference cost of 0.0 or absent
  skips it). A latency-only record (a site that stays on granite) skips the
  agreement criterion and the relative latency budget.
* A site's reference arm is Haiku (C1 to C11), gemma (C15), or granite
  (``reference="ollama"``, C12 to C14, whose landed backend is granite). A
  granite reference is judged by :func:`evaluate_reference` (the latency-only
  criteria: ``p95_c4`` against the site budget when one exists,
  ``contended``, ``error_rate``, ``n``), which is how it serves as the
  fallback proof of a decisions comparison on the record it was measured in;
  ``_candidate_arms`` drops the ``ollama`` candidate at such a site so a
  record carries granite exactly once.
* ``--audit`` reads each site's landing record, :func:`landing_record`: the
  newest record in which the declared backend is a candidate arm (a
  comparison or the latency-only measurement), else the site's newest
  record. A ``DECISIONS`` landing needs the ``decisions`` candidate to clear
  the bar AND the same record's ollama arm to clear its criteria, selected
  by slot (the reference when its backend is ``ollama``, else the
  ``ollama`` candidate; never by name from a merged dict), with no
  substitution from any other record.
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
    evaluate_reference,
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
    landing_record,
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
    "evaluate_reference",
    "is_contended",
    "landing_record",
    "latency_budget_s",
    "latest_record",
    "percentile",
    "render_report",
    "require_minimum",
    "write_record",
]
