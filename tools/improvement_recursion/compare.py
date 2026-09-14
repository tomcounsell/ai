"""Recursive comparison of two research processes (lane 6, #3218).

Claim level 3 says a changed research process produces greater validated
gain per comparable budget on fresh opportunities. This module is the
measurement behind that sentence: :func:`freeze` writes the contract before
either arm runs, :func:`run` runs both arms and scores them, and one
``ImprovementEvaluation`` with ``evaluator_version="recursive-comparison/1"``
carries the answer in lane 4's string shapes.

**Budgets are matched by cap and verified by accounting.** Both arms run
under the same :class:`BudgetCap` in four fields, numbered as the parent
plan's Gap D numbers them (``docs/plans/recursive-self-improvement.md``):
unit 1 is the subscription lane slot, accounted as ``subscription_turns``;
unit 2 is paid inference in USD (``unit2_usd``); unit 3 is infrastructure
in USD (``unit3_usd``); ``wall_seconds`` is elapsed time. The cap is the
promise; what each arm actually spent is read back afterwards through a
``BudgetReader`` (``budget.accounted_use``): dollars come from records
(unit 3 from lane 7's ledger rows under the ``arm:<arm_run_id>:`` prefix),
never from the arm's own report, because an arm that reports its own spend
can flatter itself. ``budget.budgets_comparable`` then decides whether the
two arms spent about the same; an evaluation whose budgets are not
comparable is ``inconclusive`` whatever the deltas say, because a comparison
of two processes that spent differently measures the spend and calls it
the process.

**Unknown unit-2 spend refuses a claim.** Charter section 8: uncertain or
missing metering is never zero cost. Unit 2, daily paid inference in the
parent plan's numbering, has no meter until lane 3 (#3215) lands its
``tools/paid_inference_meter.py``, so ``LedgerBudgetReader.unit2_usd``
answers ``None``, the comparability check names ``BUDGET_UNKNOWN:unit2``,
and the verdict is ``inconclusive``. A ``None`` read as ``0`` would let two arms with wildly
different paid-inference spend look matched and a level-3 claim would
measure the budget. The evaluation's ``budget=`` notes line carries every
unit for both arms whether or not the verdict is a claim, so the report can
say exactly which unit is unknown.

**Fresh means by record lookup.** Both arms must run on opportunities
neither process has worked, or the arm that saw them first inherits a head
start. ``freshness.fresh_opportunities`` decides by looking records up: the
``ImprovementCase`` exists and is open, no experiment or investigation cites
it, and no prior comparison's manifest lists it. Nothing is inferred from
titles or timing. The sorted id set is hashed into the protocol
(``opportunity_set_digest``) and the manifest, so a later freeze that names
one of these ids is refused ``OPPORTUNITY_NOT_FRESH:IN_PRIOR_COMPARISON``.

Statistics are lane 4's: paired deltas ``gain_b - gain_a`` per opportunity
(a rejected or inconclusive arm result scores 0), clustered by the case's
``priority_area`` through ``evaluate_family`` (which wraps
``clustered_bootstrap_ci``), Holm-adjusted. ``accept`` needs the interval's
lower bound above the minimum worthwhile effect and comparable budgets;
``reject`` needs the upper bound below zero and comparable budgets;
anything else is ``inconclusive``. On ``accept``, :func:`_write_revision`
creates the new ``ImprovementModelRevision`` first and supersedes the old
one second (Race 2), so a crash between the two leaves two ``current`` rows,
which ``run`` and the report detect as ``REVISION_CONFLICT``, never zero.

Model imports are lazy so this module binds Redis at call time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from tools.improvement_recursion.arms import (
    ArmResult,
    ArmRunner,
    BudgetCap,
    BudgetUse,
    get_arm_runner,
    resolve_arm_runner,
)
from tools.improvement_recursion.budget import (
    BudgetReader,
    LedgerBudgetReader,
    accounted_use,
    budgets_comparable,
)
from tools.improvement_recursion.freshness import COMPARISON_SURFACES, fresh_opportunities
from tools.improvement_release.rows import aware

logger = logging.getLogger(__name__)

EVALUATOR_VERSION = "recursive-comparison/1"
PRIMARY_ENDPOINT = "validated_gain"
STOPPING_RULE = "finite batch"
CORRECTION = "holm"

#: Refusal codes, closed vocabulary. The CLI maps a refusal to exit 2.
REFUSAL_CODES: tuple[str, ...] = (
    "NO_OPPORTUNITIES",
    "OPPORTUNITY_NOT_FRESH",
    "ARMS_IDENTICAL",
    "INCUMBENT_PROCESS_UNKNOWN",
    "REVISION_CONFLICT",
    "WRONG_STATE",
    "CONTRACT_DIGEST_MISMATCH",
    "NOT_FOUND",
)

#: Verdicts and states, named so a typo cannot invent a fifth.
VERDICT_ACCEPT = "accept"
VERDICT_REJECT = "reject"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_INFRA_FAILURE = "infra_failure"
STATE_COMPLETE = "complete"

ARMS: tuple[str, str] = ("a", "b")


class ComparisonRefused(Exception):  # noqa: N818 -- plan-mandated name, mirrors ReleaseRefused (#3218)
    """A comparison step refused. ``code`` is from :data:`REFUSAL_CODES`."""

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown refusal code {code!r}")
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


def current_revisions(project_key: str = "valor") -> list[Any]:
    """Every ``ImprovementModelRevision`` in state ``current``, oldest first.

    One is the normal case; zero means no process is in force yet; more than
    one is the Race 2 signature and refuses both ``freeze(arm_a=None)`` and
    ``run`` until an operator supersedes one through ``supersede_revision``.
    """
    from models.improvement_model_revision import ImprovementModelRevision

    rows = [
        r
        for r in ImprovementModelRevision.query.filter(project_key=project_key)
        if r.state == "current"
    ]
    rows.sort(key=lambda r: (aware(r.created_at) or datetime.min.replace(tzinfo=UTC), str(r.id)))
    return rows


def _refuse_revision_conflict(rows: list[Any]) -> None:
    if len(rows) > 1:
        ids = ", ".join(str(r.id) for r in rows)
        raise ComparisonRefused(
            "REVISION_CONFLICT",
            f"{len(rows)} model revisions are current ({ids}); supersede all but one "
            "with `revision supersede` before comparing",
        )


def _incumbent_digest(project_key: str) -> str:
    rows = current_revisions(project_key)
    _refuse_revision_conflict(rows)
    if not rows:
        raise ComparisonRefused(
            "INCUMBENT_PROCESS_UNKNOWN",
            f"no current ImprovementModelRevision under {project_key!r}; pass arm_a explicitly",
        )
    digest = rows[0].research_process_digest
    if not isinstance(digest, str) or not digest:
        raise ComparisonRefused(
            "INCUMBENT_PROCESS_UNKNOWN",
            f"current revision {rows[0].id} carries no research_process_digest; "
            "backfill it or pass arm_a explicitly",
        )
    return digest


def supersede_revision(revision_id: str, *, project_key: str = "valor", reason: str) -> Any:
    """Move one ``current`` revision to ``superseded`` by hand, recording ``reason``.

    The operator's repair for Race 2. Refuses ``NOT_FOUND`` and
    ``WRONG_STATE``; an empty ``reason`` is a ``ValueError``.
    """
    from models.improvement_model_revision import ImprovementModelRevision

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a reason is required to supersede a revision by hand")
    row = ImprovementModelRevision.query.filter(project_key=project_key, id=revision_id).first()
    if row is None:
        raise ComparisonRefused(
            "NOT_FOUND", f"no model revision {revision_id!r} under {project_key!r}"
        )
    if row.state != "current":
        raise ComparisonRefused(
            "WRONG_STATE", f"revision {revision_id} is {row.state!r}, not 'current'"
        )
    row.state = "superseded"
    note = f"superseded by hand: {reason.strip()}"
    row.rationale = f"{row.rationale}\n{note}" if row.rationale else note
    if row.save() is False:
        raise RuntimeError("ImprovementModelRevision.save() returned False")
    return row


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------


def opportunity_set_digest(opportunity_ids: list[str]) -> str:
    """``sha256:<hex>`` of the sorted id list's compact JSON."""
    payload = json.dumps(sorted(opportunity_ids), separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _cap_payload(cap: BudgetCap) -> dict:
    return asdict(cap)


def build_protocol(
    *,
    arm_a: str,
    arm_b: str,
    opportunity_ids: list[str],
    budget_cap: BudgetCap,
    minimum_worthwhile_effect: float,
    evaluator_version: str,
) -> dict:
    """The comparison's preregistered protocol, as lane 4's ``freeze_protocol`` stores it."""
    ids = sorted(str(i) for i in opportunity_ids)
    return {
        "arms": {"a": arm_a, "b": arm_b},
        "opportunity_ids": ids,
        "opportunity_set_digest": opportunity_set_digest(ids),
        "budget_cap": _cap_payload(budget_cap),
        "primary_endpoint": PRIMARY_ENDPOINT,
        "endpoints": [PRIMARY_ENDPOINT],
        "minimum_worthwhile_effect": float(minimum_worthwhile_effect),
        "stopping_rule": STOPPING_RULE,
        "evaluator_version": evaluator_version,
    }


def _default_hypothesis(arm_a: str, arm_b: str) -> str:
    return (
        f"research process {arm_b} produces greater validated gain than {arm_a} "
        "on fresh opportunities under a matched budget"
    )


def _mechanism() -> str:
    return (
        "the two processes differ in how they select opportunities, split "
        "investigation effort, revise, or in the prompt and skill bodies they run"
    )


def _falsifier(minimum_worthwhile_effect: float) -> str:
    return (
        "the lower bound of the paired validated-gain interval fails to clear "
        f"{minimum_worthwhile_effect} or the two arms' accounted budgets are not comparable"
    )


def freeze(
    *,
    arm_a: str | None,
    arm_b: str,
    opportunity_ids: list[str],
    budget_cap: BudgetCap,
    project_key: str = "valor",
    minimum_worthwhile_effect: float = 0.0,
    evaluator_version: str = EVALUATOR_VERSION,
    hypothesis: str | None = None,
    now: datetime | None = None,
) -> Any:
    """Freeze a comparison contract; return the ``ImprovementExperiment`` (state ``frozen``).

    ``arm_a=None`` takes the incumbent's digest from the single ``current``
    model revision. Refuses ``NO_OPPORTUNITIES``, ``OPPORTUNITY_NOT_FRESH``
    (naming every excluded id and its reason), ``INCUMBENT_PROCESS_UNKNOWN``,
    and ``REVISION_CONFLICT``. Nothing is written before every check passes.
    The contract digest is lane 4's ``compute_contract_digest`` over the
    saved row, so lane 4's ``load_protocol`` and digest verification work on
    it unchanged.
    """
    from models.improvement_experiment import ImprovementExperiment
    from tools.improvement_eval.runner import compute_contract_digest, freeze_protocol

    ids = [str(i) for i in opportunity_ids]
    if not ids:
        raise ComparisonRefused("NO_OPPORTUNITIES", "a comparison needs at least one opportunity")
    if arm_a is None:
        arm_a = _incumbent_digest(project_key)
    fresh, excluded = fresh_opportunities(ids, project_key=project_key)
    if excluded:
        detail = ", ".join(f"{cid}:{reason}" for cid, reason in excluded)
        raise ComparisonRefused("OPPORTUNITY_NOT_FRESH", detail)

    protocol = build_protocol(
        arm_a=arm_a,
        arm_b=arm_b,
        opportunity_ids=fresh,
        budget_cap=budget_cap,
        minimum_worthwhile_effect=minimum_worthwhile_effect,
        evaluator_version=evaluator_version,
    )
    protocol_ref = freeze_protocol(protocol)
    manifest = {
        "protocol_ref": protocol_ref,
        "opportunity_ids": protocol["opportunity_ids"],
        "opportunity_set_digest": protocol["opportunity_set_digest"],
        "arms": protocol["arms"],
    }
    at = aware(now) or datetime.now(UTC)
    experiment = ImprovementExperiment(
        project_key=project_key,
        created_at=at,
        state="proposed",
        case_id=None,
        hypothesis=hypothesis or _default_hypothesis(arm_a, arm_b),
        mechanism=_mechanism(),
        falsifier=_falsifier(minimum_worthwhile_effect),
        candidate_surfaces=json.dumps(list(COMPARISON_SURFACES)),
        manifest=json.dumps(manifest, sort_keys=True),
    )
    if experiment.save() is False:
        raise RuntimeError("ImprovementExperiment.save() returned False")
    experiment = _load_experiment(project_key, experiment.id)
    experiment.contract_digest = compute_contract_digest(experiment)
    experiment.state = "frozen"
    experiment.frozen_at = at
    if experiment.save() is False:
        raise RuntimeError("ImprovementExperiment.save() returned False")
    return _load_experiment(project_key, experiment.id)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _load_experiment(project_key: str, experiment_id: str) -> Any:
    from models.improvement_experiment import ImprovementExperiment

    row = ImprovementExperiment.query.filter(project_key=project_key, id=experiment_id).first()
    if row is None:
        raise ComparisonRefused(
            "NOT_FOUND", f"no ImprovementExperiment {experiment_id!r} under {project_key!r}"
        )
    return row


def _resolve_runner(runner: ArmRunner | None, spec: str | None) -> ArmRunner:
    if runner is not None:
        return runner
    if spec:
        return resolve_arm_runner(spec)
    return get_arm_runner()


def _assign_arms(arms: dict[str, str], rng_seed: int | None) -> tuple[list[str], str]:
    order = list(ARMS)
    random.Random(rng_seed).shuffle(order)
    payload = json.dumps({"arms": arms, "run_order": order}, sort_keys=True)
    return order, "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _priority_areas(project_key: str, opportunity_ids: list[str]) -> dict[str, str]:
    from models.improvement_case import ImprovementCase

    areas: dict[str, str] = {}
    for cid in opportunity_ids:
        case = ImprovementCase.query.filter(project_key=project_key, id=cid).first()
        areas[cid] = str(getattr(case, "priority_area", None) or "unknown")
    return areas


def _use_payload(use: BudgetUse) -> dict:
    return asdict(use)


def _decide(
    lower: float, upper: float, minimum_worthwhile_effect: float, comparable: bool
) -> tuple[str, str]:
    if not comparable:
        return VERDICT_INCONCLUSIVE, "budgets not comparable; the deltas measure spend, not process"
    if lower > minimum_worthwhile_effect:
        return VERDICT_ACCEPT, (
            f"interval lower bound {lower:.4f} clears the minimum worthwhile effect "
            f"{minimum_worthwhile_effect}"
        )
    if upper < 0.0:
        return VERDICT_REJECT, f"interval upper bound {upper:.4f} is below zero"
    return VERDICT_INCONCLUSIVE, (
        f"interval [{lower:.4f}, {upper:.4f}] neither clears {minimum_worthwhile_effect} "
        "nor sits below zero"
    )


def _write_evaluation(
    *,
    experiment: Any,
    project_key: str,
    charter_digest: str | None,
    evaluator_version: str,
    verdict: str,
    trials: int,
    arm_assignment_digest: str | None,
    effect: dict | None,
    confidence_interval: dict | None,
    correction: str | None,
    notes: list[str],
    now: datetime,
) -> Any:
    from models.improvement_evaluation import ImprovementEvaluation

    evaluation = ImprovementEvaluation(
        project_key=project_key,
        created_at=now,
        state=STATE_COMPLETE,
        verdict=verdict,
        experiment_id=str(experiment.id),
        contract_digest=experiment.contract_digest,
        charter_digest=charter_digest,
        evaluator_version=evaluator_version,
        holdout_partition=None,
        blinded=False,
        arm_assignment_digest=arm_assignment_digest,
        trials=trials,
        effect=json.dumps(effect, sort_keys=True) if effect is not None else None,
        confidence_interval=(
            json.dumps(confidence_interval, sort_keys=True)
            if confidence_interval is not None
            else None
        ),
        correction=correction,
        judge_records="",
        notes="\n".join(notes) if notes else None,
    )
    if evaluation.save() is False:
        raise RuntimeError("ImprovementEvaluation.save() returned False")
    return evaluation


def _finish_experiment(experiment: Any, state: str) -> None:
    experiment.state = state
    if experiment.save() is False:
        raise RuntimeError("ImprovementExperiment.save() returned False")


def _write_revision(
    *,
    project_key: str,
    winning_digest: str,
    evaluation: Any,
    mean: float,
    lower: float,
    upper: float,
    n: int,
    now: datetime,
) -> Any:
    """Create the new ``current`` revision, then supersede the previous one.

    New-then-supersede (Race 2): a crash between the two writes leaves two
    ``current`` rows, which is detectable, rather than none.
    """
    from models.improvement_model_revision import ImprovementModelRevision

    prior = current_revisions(project_key)
    previous = prior[0] if prior else None
    revision = ImprovementModelRevision(
        project_key=project_key,
        created_at=now,
        state="current",
        revision=(int(previous.revision or 0) + 1) if previous is not None else 1,
        summary=f"research process {winning_digest} adopted after comparison",
        rationale=(
            f"comparison evaluation {evaluation.id} under contract "
            f"{evaluation.contract_digest}: mean validated gain delta {mean:.4f} "
            f"(95% CI {lower:.4f} to {upper:.4f}, n={n}) with comparable budgets"
        ),
        prediction=(
            f"research process {winning_digest} produces a validated gain of {mean:.4f} "
            f"per opportunity over the incumbent (95% CI {lower:.4f} to {upper:.4f}) "
            "on fresh opportunities under a matched budget"
        ),
        evidence_ids=json.dumps([]),
        supersedes_id=str(previous.id) if previous is not None else None,
        research_process_digest=winning_digest,
    )
    if revision.save() is False:
        raise RuntimeError("ImprovementModelRevision.save() returned False")
    if previous is not None:
        previous.state = "superseded"
        if previous.save() is False:
            raise RuntimeError("ImprovementModelRevision.save() returned False")
    return revision


def run(
    experiment_id: str,
    *,
    runner: ArmRunner | None = None,
    arm_runner_spec: str | None = None,
    budget_reader: BudgetReader | None = None,
    rng_seed: int | None = None,
    project_key: str = "valor",
    now: datetime | None = None,
) -> Any:
    """Run a frozen comparison; return its single ``ImprovementEvaluation``.

    Refuses ``NOT_FOUND``, ``WRONG_STATE`` (not ``frozen``),
    ``CONTRACT_DIGEST_MISMATCH``, ``ARMS_IDENTICAL``, and ``REVISION_CONFLICT``
    before anything runs; ``ArmRunnerAbsent`` propagates from the runner
    lookup (``runner`` > ``arm_runner_spec`` > the registry). No pinned
    charter (``CHARTER_NOT_PINNED``, before either arm runs) and any exception
    from an arm, the accounting read, the priority-area lookup, or the
    statistics write ``infra_failure`` and move the
    experiment to ``aborted``, lane 4's shape. On ``accept`` the winning arm
    becomes the ``current`` model revision.
    """
    from models.improvement_charter import ImprovementCharter
    from tools.improvement_eval.runner import compute_contract_digest, load_protocol
    from tools.improvement_eval.statistics import evaluate_family

    experiment = _load_experiment(project_key, experiment_id)
    if experiment.state != "frozen":
        raise ComparisonRefused(
            "WRONG_STATE", f"experiment {experiment_id} is {experiment.state!r}, not 'frozen'"
        )
    recomputed = compute_contract_digest(experiment)
    if recomputed != experiment.contract_digest:
        raise ComparisonRefused(
            "CONTRACT_DIGEST_MISMATCH",
            f"contract digest {experiment.contract_digest} does not match the recomputed "
            f"{recomputed}; the contract moved after freezing",
        )
    protocol = load_protocol(experiment)
    arms = {arm: str(protocol["arms"][arm]) for arm in ARMS}
    if arms["a"] == arms["b"]:
        raise ComparisonRefused("ARMS_IDENTICAL", f"both arms are {arms['a']}")
    _refuse_revision_conflict(current_revisions(project_key))
    active = _resolve_runner(runner, arm_runner_spec)
    reader = budget_reader if budget_reader is not None else LedgerBudgetReader(project_key)

    opportunity_ids = [str(i) for i in protocol["opportunity_ids"]]
    cap = BudgetCap(**protocol["budget_cap"])
    minimum = float(protocol.get("minimum_worthwhile_effect", 0.0))
    evaluator_version = str(protocol.get("evaluator_version") or EVALUATOR_VERSION)
    at = aware(now) or datetime.now(UTC)
    charter = ImprovementCharter.pinned(project_key)
    charter_digest = getattr(charter, "digest", None)
    order, assignment_digest = _assign_arms(arms, rng_seed)
    notes = [
        f"arms=a:{arms['a']},b:{arms['b']}",
        f"run_order={','.join(order)}",
        f"arm_assignment_digest={assignment_digest}",
    ]

    _finish_experiment(experiment, "running")

    def _infra_failure(reason: str) -> Any:
        logger.warning("comparison %s infra_failure: %s", experiment.id, reason)
        notes.insert(0, f"infra_failure: {reason}")
        _finish_experiment(experiment, "aborted")
        return _write_evaluation(
            experiment=experiment,
            project_key=project_key,
            charter_digest=charter_digest,
            evaluator_version=evaluator_version,
            verdict=VERDICT_INFRA_FAILURE,
            trials=len(opportunity_ids),
            arm_assignment_digest=assignment_digest,
            effect=None,
            confidence_interval=None,
            correction=None,
            notes=notes,
            now=at,
        )

    # Lane 4's charter pin: a missing charter is a harness failure written as
    # infra_failure before either arm runs, never a silent None on the row.
    if charter is None:
        return _infra_failure(
            f"CHARTER_NOT_PINNED: no ImprovementCharter is pinned for {project_key!r}"
        )

    results: dict[str, ArmResult] = {}
    use: dict[str, BudgetUse] = {}
    # Everything from the first arm through the scoring is one guarded span:
    # a failure anywhere in it (an arm, the accounting read, the priority-area
    # lookup, the statistics) lands as infra_failure/aborted, never as an
    # experiment left in ``running`` that a retry refuses WRONG_STATE.
    try:
        for arm in order:
            arm_run_id = f"{experiment.id}:{arm}"
            results[arm] = active.run(arms[arm], list(opportunity_ids), cap, arm_run_id)
            use[arm] = accounted_use(reader, arm_run_id, results[arm].budget_use)

        comparable, reasons = budgets_comparable(use["a"], use["b"], cap)
        budget = {
            "cap": _cap_payload(cap),
            "a": _use_payload(use["a"]),
            "b": _use_payload(use["b"]),
            "comparable": comparable,
            "reasons": reasons,
        }

        areas = _priority_areas(project_key, opportunity_ids)
        deltas_by_area: dict[str, list[float]] = {}
        for cid in opportunity_ids:
            delta = results["b"].scored_gain(cid) - results["a"].scored_gain(cid)
            deltas_by_area.setdefault(areas[cid], []).append(delta)
        (outcome,) = evaluate_family({PRIMARY_ENDPOINT: deltas_by_area})
    except Exception as exc:  # noqa: BLE001 -- an arm or scoring failure is infra_failure, never a result
        return _infra_failure(f"{type(exc).__name__}: {exc}")

    interval = {
        "lower": outcome.lower,
        "upper": outcome.upper,
        "n": outcome.n,
        "raw_p_value": outcome.raw_p_value,
        "adjusted_p_value": outcome.adjusted_p_value,
    }
    verdict, rationale = _decide(outcome.lower, outcome.upper, minimum, comparable)
    notes.insert(0, rationale)
    notes.append("budget=" + json.dumps(budget, sort_keys=True))
    notes.extend(reasons)

    _finish_experiment(experiment, STATE_COMPLETE)
    evaluation = _write_evaluation(
        experiment=experiment,
        project_key=project_key,
        charter_digest=charter_digest,
        evaluator_version=evaluator_version,
        verdict=verdict,
        trials=len(opportunity_ids),
        arm_assignment_digest=assignment_digest,
        effect={PRIMARY_ENDPOINT: outcome.mean},
        confidence_interval={PRIMARY_ENDPOINT: interval},
        correction=CORRECTION,
        notes=notes,
        now=at,
    )
    if verdict == VERDICT_ACCEPT:
        _write_revision(
            project_key=project_key,
            winning_digest=arms["b"],
            evaluation=evaluation,
            mean=outcome.mean,
            lower=outcome.lower,
            upper=outcome.upper,
            n=outcome.n,
            now=at,
        )
    return evaluation


__all__ = [
    "CORRECTION",
    "EVALUATOR_VERSION",
    "PRIMARY_ENDPOINT",
    "REFUSAL_CODES",
    "STOPPING_RULE",
    "ComparisonRefused",
    "build_protocol",
    "current_revisions",
    "freeze",
    "opportunity_set_digest",
    "run",
    "supersede_revision",
]
