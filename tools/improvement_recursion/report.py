"""The claim report: three ladder levels, each supported or not (lane 6, #3218).

Level 1 is a complete research cycle: an experiment with a complete
evaluation whose case has left the open states. Level 2 is a released change
that held its effect through its observation window: an ``accepted`` release
whose outcome says ``claim_level_2_supported``. Level 3 is the recursion
claim: a ``recursive-comparison/`` evaluation with verdict ``accept`` and
comparable budgets. Each level answers ``{name, supported, evidence,
confidence_interval, correction, falsifier, why_not}``; evidence names ids,
never totals, and no count of experiments, patches, or releases appears
anywhere in the report or its rendering. A count would invite the reading
that more work is more improvement, which is the claim this report exists to
test rather than assume.

Each level is computed inside its own guard, the shape lane 7's
``generate_report`` established: a read failure logs a warning and yields
``supported=False`` with ``why_not="could not be determined: <reason>"``
while the other two levels still answer. Every read of an evaluation's
``effect``, ``confidence_interval``, or ``notes`` goes through
``tools/improvement_release/evaluation_read.py``, so lane 4's rows and the
comparison's rows parse on one path.

Model imports are lazy so this module binds Redis at call time.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from tools.improvement_recursion.compare import EVALUATOR_VERSION, PRIMARY_ENDPOINT
from tools.improvement_release.evaluation_read import (
    budget_of,
    effect_of,
    interval_of,
    json_field,
    notes_of,
)
from tools.improvement_release.lineage import load_primary_endpoint

logger = logging.getLogger(__name__)

LEVEL_NAMES: dict[int, str] = {
    1: "a complete research cycle",
    2: "a released change held its measured effect through its observation window",
    3: (
        "a changed research process produced greater validated gain per comparable "
        "budget on fresh opportunities"
    ),
}

#: ``evaluator_version`` prefix that marks a comparison evaluation.
COMPARISON_PREFIX = EVALUATOR_VERSION.split("/", 1)[0] + "/"


def _level(level: int, **fields: Any) -> dict:
    entry = {
        "name": LEVEL_NAMES[level],
        "supported": False,
        "evidence": [],
        "confidence_interval": None,
        "correction": None,
        "falsifier": None,
        "why_not": None,
    }
    entry.update(fields)
    return entry


def _aware(stamp: Any) -> datetime | None:
    if not isinstance(stamp, datetime):
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _recency(row: Any) -> datetime:
    return _aware(getattr(row, "created_at", None)) or datetime.min.replace(tzinfo=UTC)


def _lookup(model: Any, project_key: str, row_id: Any) -> Any:
    if not row_id:
        return None
    return model.query.filter(project_key=project_key, id=str(row_id)).first()


# ---------------------------------------------------------------------------
# Level 1: a complete research cycle
# ---------------------------------------------------------------------------


def _complete_evaluations(project_key: str) -> list[Any]:
    from models.improvement_evaluation import ImprovementEvaluation

    rows = [
        r
        for r in ImprovementEvaluation.query.filter(project_key=project_key)
        if r.state == "complete"
    ]
    rows.sort(key=_recency, reverse=True)
    return rows


def _level_1(project_key: str) -> dict:
    from models.improvement_case import OPEN_CASE_STATES, ImprovementCase
    from models.improvement_experiment import ImprovementExperiment

    for evaluation in _complete_evaluations(project_key):
        experiment = _lookup(ImprovementExperiment, project_key, evaluation.experiment_id)
        case = _lookup(ImprovementCase, project_key, getattr(experiment, "case_id", None))
        if case is None or case.state in OPEN_CASE_STATES:
            continue
        endpoint = load_primary_endpoint(experiment)
        effect = effect_of(evaluation, endpoint) if endpoint else None
        return _level(
            1,
            supported=True,
            evidence=[
                f"experiment {experiment.id}",
                f"evaluation {evaluation.id} verdict {evaluation.verdict}",
                f"endpoint {endpoint} effect {effect}",
                f"case {case.id} state {case.state}",
            ],
            confidence_interval=interval_of(evaluation, endpoint) if endpoint else None,
            correction=getattr(evaluation, "correction", None),
            falsifier=getattr(experiment, "falsifier", None),
            why_not=None,
        )
    return _level(1, why_not="no complete cycle recorded")


# ---------------------------------------------------------------------------
# Level 2: a released change held its effect
# ---------------------------------------------------------------------------


def _accepted_releases(project_key: str) -> list[Any]:
    from models.improvement_release import ImprovementRelease

    rows = [
        r for r in ImprovementRelease.query.filter(project_key=project_key) if r.state == "accepted"
    ]
    rows.sort(key=_recency, reverse=True)
    return rows


def _level_2(project_key: str) -> dict:
    from models.improvement_evaluation import ImprovementEvaluation
    from models.improvement_experiment import ImprovementExperiment

    releases = _accepted_releases(project_key)
    if not releases:
        return _level(2, why_not="no accepted release recorded")
    unsupported: list[str] = []
    for release in releases:
        outcome = json_field(getattr(release, "outcome", None)) or {}
        evaluation = _lookup(ImprovementEvaluation, project_key, release.evaluation_id)
        experiment = _lookup(
            ImprovementExperiment, project_key, getattr(evaluation, "experiment_id", None)
        )
        endpoint = load_primary_endpoint(experiment)
        if outcome.get("claim_level_2_supported") is not True:
            unsupported.append(f"release {release.id} ({outcome.get('reason') or 'no reason'})")
            continue
        effect = effect_of(evaluation, endpoint) if endpoint else None
        return _level(
            2,
            supported=True,
            evidence=[
                f"release {release.id} state {release.state}",
                f"evaluation {getattr(evaluation, 'id', None)} verdict "
                f"{getattr(evaluation, 'verdict', None)}",
                f"endpoint {endpoint} effect {effect}",
                f"outcome {outcome.get('verdict')}",
            ],
            confidence_interval=interval_of(evaluation, endpoint) if endpoint else None,
            correction=getattr(evaluation, "correction", None),
            falsifier=outcome.get("falsifier"),
            why_not=None,
        )
    newest = json_field(getattr(releases[0], "outcome", None)) or {}
    return _level(
        2,
        falsifier=newest.get("falsifier"),
        why_not="accepted releases exist but none supports the claim: " + "; ".join(unsupported),
    )


# ---------------------------------------------------------------------------
# Level 3: the recursion claim
# ---------------------------------------------------------------------------


def _comparisons(project_key: str) -> list[Any]:
    return [
        r
        for r in _complete_evaluations(project_key)
        if str(getattr(r, "evaluator_version", "") or "").startswith(COMPARISON_PREFIX)
    ]


def _arms_line(evaluation: Any) -> str | None:
    for line in notes_of(evaluation):
        if line.startswith("arms="):
            return line[len("arms=") :]
    return None


def _level_3(project_key: str) -> dict:
    from models.improvement_experiment import ImprovementExperiment

    comparisons = _comparisons(project_key)
    if not comparisons:
        return _level(3, why_not="no comparison recorded")
    for evaluation in comparisons:
        budget = budget_of(evaluation) or {}
        comparable = budget.get("comparable") is True
        if evaluation.verdict != "accept" or not comparable:
            continue
        experiment = _lookup(ImprovementExperiment, project_key, evaluation.experiment_id)
        endpoint = load_primary_endpoint(experiment) or PRIMARY_ENDPOINT
        return _level(
            3,
            supported=True,
            evidence=[
                f"evaluation {evaluation.id} verdict {evaluation.verdict}",
                f"experiment {getattr(experiment, 'id', None)}",
                f"arms {_arms_line(evaluation)}",
                f"endpoint {endpoint} effect {effect_of(evaluation, endpoint)}",
                "budgets comparable in every unit",
            ],
            confidence_interval=interval_of(evaluation, endpoint),
            correction=getattr(evaluation, "correction", None),
            falsifier=getattr(experiment, "falsifier", None),
            why_not=None,
        )
    newest = comparisons[0]
    experiment = _lookup(ImprovementExperiment, project_key, newest.experiment_id)
    endpoint = load_primary_endpoint(experiment) or PRIMARY_ENDPOINT
    budget = budget_of(newest) or {}
    reasons = [str(r) for r in (budget.get("reasons") or [])]
    if reasons:
        why_not = (
            f"comparison {newest.id} was refused a claim: {', '.join(reasons)} "
            f"(verdict {newest.verdict})"
        )
    else:
        why_not = f"comparison {newest.id} verdict {newest.verdict}"
    return _level(
        3,
        confidence_interval=interval_of(newest, endpoint),
        correction=getattr(newest, "correction", None),
        falsifier=getattr(experiment, "falsifier", None),
        why_not=why_not,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


_LEVELS = {1: _level_1, 2: _level_2, 3: _level_3}


def claim_report(project_key: str = "valor", *, now: datetime | None = None) -> dict:
    """The three ladder levels for ``project_key``; each degrades on its own."""
    at = _aware(now) or datetime.now(UTC)
    levels: dict[int, dict] = {}
    for level, compute in _LEVELS.items():
        try:
            levels[level] = compute(project_key)
        except Exception as exc:  # noqa: BLE001 -- degrade one level, keep the other two
            logger.warning("claim report: level %d could not be determined: %s", level, exc)
            levels[level] = _level(level, why_not=f"could not be determined: {exc}")
    return {"project_key": project_key, "generated_at": at.isoformat(), "levels": levels}


def _interval_text(interval: dict | None) -> str:
    if not isinstance(interval, dict):
        return "none recorded"
    lower, upper = interval.get("lower"), interval.get("upper")
    if isinstance(lower, int | float) and isinstance(upper, int | float):
        return f"{lower:.4f} to {upper:.4f}"
    return "none recorded"


def render(report: dict) -> str:
    """The report as plain text, one block per level."""
    lines = [f"Claim report for {report.get('project_key')} at {report.get('generated_at')}"]
    for level in sorted(report.get("levels", {})):
        entry = report["levels"][level]
        lines.append("")
        lines.append(f"Level {level}: {entry.get('name')}")
        lines.append(f"  supported: {'yes' if entry.get('supported') else 'no'}")
        for item in entry.get("evidence") or []:
            lines.append(f"  evidence: {item}")
        lines.append(f"  interval: {_interval_text(entry.get('confidence_interval'))}")
        lines.append(f"  correction: {entry.get('correction') or 'none recorded'}")
        lines.append(f"  falsifier: {entry.get('falsifier') or 'none recorded'}")
        if entry.get("why_not"):
            lines.append(f"  why not: {entry['why_not']}")
    return "\n".join(lines)


__all__ = ["COMPARISON_PREFIX", "LEVEL_NAMES", "claim_report", "render"]
