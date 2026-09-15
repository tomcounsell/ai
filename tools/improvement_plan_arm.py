"""The planner tick as one of lane 6's comparison arms.

Lane 6 (#3218) compares research processes by running each as an "arm" over
the same opportunities and reading the gains from the records. This module
is lane 5's arm: :class:`PlannerArmRunner` runs exactly one
:func:`reflections.improvement_plan.plan_tick` restricted to the named
opportunities and reports what the records already say about them.

**The single-tick limit.** One arm run is one planner tick. It opens
nothing new, proposes at most one action, and never dispatches a research
session or waits for an evaluation inside ``run``; the gains it returns are
taken from ``accept`` evaluations **already on record** for those cases. A
multi-tick arm that dispatches and evaluates inside ``run`` is #3311's.

**Money is read from records, never from this object.** ``BudgetUse`` here
carries ``unit2_usd=None`` and ``unit3_usd=None``: lane 6 reads the
paid-inference and infrastructure dollars from lane 3's meter and lane 7's
reservation rows keyed by ``arm_run_id``. Any infrastructure an arm admits
must carry the ``arm:<arm_run_id>:`` resource-name prefix so that read finds
it; this arm admits none.

**No module-level import of lane 6.** ``tools.improvement_recursion.arms``
is imported inside :meth:`PlannerArmRunner.run`; when lane 6 is not merged
the import fails and :class:`ArmRunnerUnavailable` is raised with
``ARM_RUNNER_UNAVAILABLE: lane 6 not merged``. ``tools/improvement.py``'s CLI
entry registers the runner only when that module is importable, so
``compare run --arm-runner tools.improvement_plan_arm:PlannerArmRunner``
finds it once lane 6 lands. The class is constructed with no arguments.
"""

from __future__ import annotations

import json
import time


class ArmRunnerUnavailable(RuntimeError):  # noqa: N818 -- the name lane 6's contract cites
    """Lane 6's arm types are not importable in this checkout."""


def _recorded_gains(project_key: str, opportunity_ids: list[str]) -> dict[str, float | None]:
    """Per-case gain from ``accept`` evaluations already on record, else None."""
    from models.improvement_case import ImprovementCase
    from models.improvement_evaluation import ImprovementEvaluation

    gains: dict[str, float | None] = {}
    for case_id in opportunity_ids:
        gains[case_id] = None
        case = ImprovementCase.query.get(project_key=project_key, id=case_id)
        if case is None:
            continue
        try:
            evaluation_ids = json.loads(case.evaluation_ids or "[]")
        except (TypeError, ValueError):
            evaluation_ids = []
        for evaluation_id in evaluation_ids:
            evaluation = ImprovementEvaluation.query.get(project_key=project_key, id=evaluation_id)
            if evaluation is None or evaluation.verdict != "accept":
                continue
            try:
                effect = json.loads(evaluation.effect or "{}")
            except (TypeError, ValueError):
                effect = {}
            numeric = [v for v in effect.values() if isinstance(v, (int, float))]
            gains[case_id] = float(numeric[0]) if numeric else 0.0
    return gains


class PlannerArmRunner:
    """Lane 5's planner tick behind lane 6's ``ArmRunner`` protocol.

    Constructed with no arguments (lane 6's ``compare run --arm-runner``
    contract). ``project_key`` defaults to the CLI's project.
    """

    def __init__(self):
        from tools.improvement import PROJECT_KEY

        self.project_key = PROJECT_KEY

    def run(self, process_digest: str, opportunity_ids: list[str], budget_cap, arm_run_id: str):
        try:
            from tools.improvement_recursion.arms import ArmResult, BudgetUse
        except ImportError as exc:
            raise ArmRunnerUnavailable("ARM_RUNNER_UNAVAILABLE: lane 6 not merged") from exc

        from reflections.improvement_plan import plan_tick

        started = time.monotonic()
        plan_tick(
            self.project_key,
            process_spec=process_digest,
            case_ids=list(opportunity_ids),
            budget_cap=budget_cap,
            arm_run_id=arm_run_id,
        )
        elapsed = time.monotonic() - started
        return ArmResult(
            gains=_recorded_gains(self.project_key, list(opportunity_ids)),
            budget_use=BudgetUse(
                unit2_usd=None,
                unit3_usd=None,
                subscription_turns=0,
                wall_seconds=elapsed,
            ),
        )
