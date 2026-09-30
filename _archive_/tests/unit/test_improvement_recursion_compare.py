"""Recursive comparison of research processes (lane 6, #3218).

``freeze`` writes a frozen comparison experiment through lane 4's protocol
store and contract digest; ``run`` runs both arms behind the ``ArmRunner``
seam, accounts their spend, scores paired validated gains with lane 4's
statistics, and writes one ``recursive-comparison/1`` evaluation. Rows land
in the claimed test DB (autouse ``redis_test_db``, tests/conftest.py) under a
test-scoped ``project_key``; the charter is seeded from the real file, cases
carry a ``priority_area``, and the replay runner admits its unit-3 spend
through lane 7's ledger so ``LedgerBudgetReader`` reads it on the production
path. Unit 2 (paid inference) has no meter, so the accept / reject / mismatch
fixtures inject a reader that meters it; the unit-2-unknown test uses the real
reader.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_experiment import ImprovementExperiment
from models.improvement_model_revision import ImprovementModelRevision
from tools.improvement_eval.runner import compute_contract_digest, load_protocol
from tools.improvement_recursion.arms import (
    ArmResult,
    ArmRunnerAbsent,
    BudgetCap,
    BudgetUse,
    ReplayArm,
    ReplayArmRunner,
    register_arm_runner,
)
from tools.improvement_recursion.budget import LedgerBudgetReader
from tools.improvement_recursion.compare import (
    EVALUATOR_VERSION,
    PRIMARY_ENDPOINT,
    REFUSAL_CODES,
    ComparisonRefused,
    current_revisions,
    freeze,
    run,
    supersede_revision,
)
from tools.improvement_release.evaluation_read import (
    budget_of,
    effect_of,
    interval_of,
    notes_of,
)

PK = "test-3218-compare"
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
CAP = BudgetCap(unit2_usd=10.0, unit3_usd=10.0, subscription_turns=100, wall_seconds=3600)
USE = BudgetUse(unit3_usd=1.0, subscription_turns=10, wall_seconds=100)
AREAS = ("memory", "skills", "memory", "inference")

ACCEPT_GAINS = (
    {"a": 0.1, "b": 0.5},
    {"a": 0.1, "b": 0.6},
    {"a": 0.2, "b": 0.5},
    {"a": 0.0, "b": 0.4},
)
REJECT_GAINS = (
    {"a": 0.5, "b": 0.1},
    {"a": 0.6, "b": 0.1},
    {"a": 0.5, "b": 0.2},
    {"a": 0.4, "b": 0.0},
)
MIXED_GAINS = (
    {"a": 0.1, "b": 0.4},
    {"a": 0.4, "b": 0.1},
    {"a": 0.2, "b": 0.4},
    {"a": 0.4, "b": 0.2},
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registry():
    register_arm_runner(None)
    yield
    register_arm_runner(None)


@pytest.fixture
def charter(tmp_path):
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None
    return row


def _settings(cap=50.0):
    return SimpleNamespace(
        improvement=SimpleNamespace(
            weekly_infrastructure_usd=cap, budget_week_start="monday", budget_day_boundary="UTC"
        )
    )


def _case(area: str = "memory", state: str = "observed") -> ImprovementCase:
    return ImprovementCase.create(
        project_key=PK,
        state=state,
        priority_area=area,
        title=f"case-{area}",
        created_at=datetime.now(UTC),
    )


def _cases(n: int = 4) -> list[str]:
    return [_case(AREAS[i % len(AREAS)]).id for i in range(n)]


def _replay(gains_per_case, case_ids, *, use_a=USE, use_b=USE) -> ReplayArmRunner:
    """A runner replaying ``gains_per_case[i] = {"a": gain_a, "b": gain_b}`` for ``case_ids[i]``."""
    arms = {
        DIGEST_A: ReplayArm(
            gains={cid: g["a"] for cid, g in zip(case_ids, gains_per_case, strict=True)},
            budget_use=use_a,
        ),
        DIGEST_B: ReplayArm(
            gains={cid: g["b"] for cid, g in zip(case_ids, gains_per_case, strict=True)},
            budget_use=use_b,
        ),
    }
    return ReplayArmRunner(arms, project_key=PK, settings=_settings(), now=NOW)


class _MeteredReader(LedgerBudgetReader):
    """The ledger reader with a unit-2 meter the tests control per arm run."""

    def __init__(self, unit2: dict[str, float] | float | None = 2.0) -> None:
        super().__init__(PK)
        self._unit2 = unit2

    def unit2_usd(self, arm_run_id: str) -> float | None:
        if isinstance(self._unit2, dict):
            return self._unit2.get(arm_run_id.rsplit(":", 1)[-1])
        return self._unit2


class _RaisingRunner:
    def run(self, process_digest, opportunity_ids, budget_cap, arm_run_id):
        raise RuntimeError("the arm fell over")


def _freeze(case_ids, *, arm_a=DIGEST_A, arm_b=DIGEST_B, **kwargs) -> ImprovementExperiment:
    return freeze(
        arm_a=arm_a, arm_b=arm_b, opportunity_ids=case_ids, budget_cap=CAP, project_key=PK, **kwargs
    )


def _revision(digest: str, state: str = "current") -> ImprovementModelRevision:
    return ImprovementModelRevision.create(
        project_key=PK,
        created_at=datetime.now(UTC),
        state=state,
        research_process_digest=digest,
        summary=f"revision {(digest or 'undigested')[:12]}",
    )


def _reload_experiment(experiment) -> ImprovementExperiment:
    row = ImprovementExperiment.query.filter(project_key=PK, id=experiment.id).first()
    assert row is not None
    return row


def _reload_evaluation(evaluation) -> ImprovementEvaluation:
    row = ImprovementEvaluation.query.filter(project_key=PK, id=evaluation.id).first()
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------


def test_refusal_vocabulary_is_closed():
    assert REFUSAL_CODES == (
        "NO_OPPORTUNITIES",
        "OPPORTUNITY_NOT_FRESH",
        "ARMS_IDENTICAL",
        "INCUMBENT_PROCESS_UNKNOWN",
        "REVISION_CONFLICT",
        "WRONG_STATE",
        "CONTRACT_DIGEST_MISMATCH",
        "NOT_FOUND",
    )
    refused = ComparisonRefused("NOT_FOUND", "detail")
    assert refused.code == "NOT_FOUND" and refused.detail == "detail"
    assert "NOT_FOUND" in str(refused)


def test_freeze_writes_a_frozen_experiment_lane4_can_verify(charter):
    case_ids = _cases()
    experiment = _freeze(case_ids, minimum_worthwhile_effect=0.05)
    row = _reload_experiment(experiment)
    assert row.state == "frozen"
    assert row.frozen_at is not None
    assert row.case_id is None
    assert json.loads(row.candidate_surfaces) == ["research_process"]
    assert row.contract_digest == compute_contract_digest(row)
    assert row.hypothesis and row.mechanism and row.falsifier
    protocol = load_protocol(row)
    assert protocol["arms"] == {"a": DIGEST_A, "b": DIGEST_B}
    assert protocol["opportunity_ids"] == sorted(case_ids)
    expected_set = hashlib.sha256(
        json.dumps(sorted(case_ids), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert protocol["opportunity_set_digest"] == "sha256:" + expected_set
    assert protocol["budget_cap"] == {
        "unit2_usd": 10.0,
        "unit3_usd": 10.0,
        "subscription_turns": 100,
        "wall_seconds": 3600,
    }
    assert protocol["primary_endpoint"] == PRIMARY_ENDPOINT == "validated_gain"
    assert protocol["endpoints"] == ["validated_gain"]
    assert protocol["minimum_worthwhile_effect"] == 0.05
    assert protocol["stopping_rule"] == "finite batch"
    assert protocol["evaluator_version"] == EVALUATOR_VERSION == "recursive-comparison/1"
    from tools.improvement_eval.runner import read_content

    manifest = json.loads(read_content(row, "manifest"))
    assert manifest["protocol_ref"] == protocol_ref_of(row)
    assert manifest["opportunity_ids"] == sorted(case_ids)


def protocol_ref_of(experiment) -> str:
    from tools.improvement_eval.runner import read_content

    return json.loads(read_content(experiment, "manifest"))["protocol_ref"]


def test_freeze_refuses_zero_opportunities(charter):
    with pytest.raises(ComparisonRefused) as info:
        _freeze([])
    assert info.value.code == "NO_OPPORTUNITIES"
    assert list(ImprovementExperiment.query.filter(project_key=PK)) == []


def test_freeze_refuses_worked_opportunity(charter):
    fresh, worked = _case(), _case()
    ImprovementExperiment(
        project_key=PK, created_at=datetime.now(UTC), case_id=worked.id, state="complete"
    ).save()
    with pytest.raises(ComparisonRefused) as info:
        _freeze([fresh.id, worked.id])
    assert info.value.code == "OPPORTUNITY_NOT_FRESH"
    assert f"{worked.id}:HAS_EXPERIMENT" in info.value.detail
    assert fresh.id not in info.value.detail
    experiments = list(ImprovementExperiment.query.filter(project_key=PK))
    assert [e.case_id for e in experiments] == [worked.id], "nothing frozen"


def test_freeze_refuses_unknown_opportunity(charter):
    with pytest.raises(ComparisonRefused) as info:
        _freeze(["nosuch"])
    assert info.value.code == "OPPORTUNITY_NOT_FRESH"
    assert "nosuch:NOT_FOUND" in info.value.detail


def test_freeze_marks_its_opportunities_worked_for_the_next_freeze(charter):
    case_ids = _cases(2)
    _freeze(case_ids)
    with pytest.raises(ComparisonRefused) as info:
        _freeze(case_ids)
    assert info.value.code == "OPPORTUNITY_NOT_FRESH"
    assert "IN_PRIOR_COMPARISON" in info.value.detail


def test_freeze_with_no_incumbent_refuses(charter):
    with pytest.raises(ComparisonRefused) as info:
        _freeze(_cases(2), arm_a=None)
    assert info.value.code == "INCUMBENT_PROCESS_UNKNOWN"


def test_freeze_with_incumbent_lacking_digest_refuses(charter):
    _revision(None)
    with pytest.raises(ComparisonRefused) as info:
        _freeze(_cases(2), arm_a=None)
    assert info.value.code == "INCUMBENT_PROCESS_UNKNOWN"


def test_freeze_takes_the_incumbent_digest_from_the_current_revision(charter):
    _revision(DIGEST_A)
    experiment = _freeze(_cases(2), arm_a=None)
    assert load_protocol(_reload_experiment(experiment))["arms"]["a"] == DIGEST_A


def test_freeze_refuses_two_current_revisions_naming_both(charter):
    first, second = _revision(DIGEST_A), _revision(DIGEST_B)
    with pytest.raises(ComparisonRefused) as info:
        _freeze(_cases(2), arm_a=None)
    assert info.value.code == "REVISION_CONFLICT"
    assert first.id in info.value.detail and second.id in info.value.detail


# ---------------------------------------------------------------------------
# run: refusals
# ---------------------------------------------------------------------------


def test_run_refuses_identical_arms(charter):
    experiment = _freeze(_cases(2), arm_a=DIGEST_A, arm_b=DIGEST_A)
    with pytest.raises(ComparisonRefused) as info:
        run(experiment.id, runner=_RaisingRunner(), project_key=PK)
    assert info.value.code == "ARMS_IDENTICAL"
    assert _reload_experiment(experiment).state == "frozen"
    assert list(ImprovementEvaluation.query.filter(project_key=PK)) == []


def test_run_refuses_unknown_experiment(charter):
    with pytest.raises(ComparisonRefused) as info:
        run("nosuch", project_key=PK)
    assert info.value.code == "NOT_FOUND"


def test_run_refuses_wrong_state(charter):
    experiment = _freeze(_cases(2))
    experiment.state = "complete"
    experiment.save()
    with pytest.raises(ComparisonRefused) as info:
        run(experiment.id, runner=_RaisingRunner(), project_key=PK)
    assert info.value.code == "WRONG_STATE"


def test_run_refuses_a_moved_contract(charter):
    experiment = _freeze(_cases(2))
    experiment.hypothesis = "rewritten after freezing"
    experiment.save()
    with pytest.raises(ComparisonRefused) as info:
        run(experiment.id, runner=_RaisingRunner(), project_key=PK)
    assert info.value.code == "CONTRACT_DIGEST_MISMATCH"


def test_run_refuses_two_current_revisions_before_running(charter):
    case_ids = _cases(2)
    experiment = _freeze(case_ids)
    _revision(DIGEST_A)
    _revision(DIGEST_B)
    calls = []

    class _Counting:
        def run(self, *args):
            calls.append(args)
            raise AssertionError("never reached")

    with pytest.raises(ComparisonRefused) as info:
        run(experiment.id, runner=_Counting(), project_key=PK)
    assert info.value.code == "REVISION_CONFLICT"
    assert calls == []
    assert _reload_experiment(experiment).state == "frozen"


def test_run_without_a_runner_raises_arm_runner_absent(charter):
    experiment = _freeze(_cases(2))
    with pytest.raises(ArmRunnerAbsent):
        run(experiment.id, project_key=PK)
    assert _reload_experiment(experiment).state == "frozen"


@pytest.mark.parametrize(
    "spec, fragment",
    [
        ("nosuch.module:Runner", "nosuch"),
        ("tools.improvement_recursion.arms:NoSuchAttr", "NoSuchAttr"),
    ],
    ids=["missing-module", "missing-attr"],
)
def test_run_refuses_an_unresolvable_arm_runner_spec(charter, spec, fragment):
    experiment = _freeze(_cases(2))
    with pytest.raises(ArmRunnerAbsent) as info:
        run(experiment.id, arm_runner_spec=spec, project_key=PK)
    assert info.value.code == "ARM_RUNNER_ABSENT"
    assert fragment in info.value.detail


def test_run_resolves_the_replay_runner_by_spec(charter):
    """The spec resolves to an empty replay runner, which then has no fixture: infra_failure."""
    experiment = _freeze(_cases(2))
    evaluation = run(
        experiment.id,
        arm_runner_spec="tools.improvement_recursion.arms:ReplayArmRunner",
        project_key=PK,
    )
    assert evaluation.verdict == "infra_failure"
    assert "LookupError" in evaluation.notes


def test_run_uses_the_registered_runner_when_none_is_passed(charter):
    case_ids = _cases()
    experiment = _freeze(case_ids)
    register_arm_runner(_replay(ACCEPT_GAINS, case_ids))
    evaluation = run(experiment.id, budget_reader=_MeteredReader(), project_key=PK)
    assert evaluation.verdict == "accept"


# ---------------------------------------------------------------------------
# run: verdicts on equal-cap replay fixtures
# ---------------------------------------------------------------------------


def _run_fixture(gains, *, reader=None, rng_seed=7, **freeze_kwargs):
    case_ids = _cases()
    experiment = _freeze(case_ids, **freeze_kwargs)
    evaluation = run(
        experiment.id,
        runner=_replay(gains, case_ids),
        budget_reader=reader if reader is not None else _MeteredReader(),
        rng_seed=rng_seed,
        project_key=PK,
    )
    return experiment, evaluation, case_ids


@pytest.mark.parametrize(
    "gains, verdict",
    [(ACCEPT_GAINS, "accept"), (REJECT_GAINS, "reject"), (MIXED_GAINS, "inconclusive")],
    ids=["accept", "reject", "inconclusive"],
)
def test_equal_cap_replay_verdicts(charter, gains, verdict):
    experiment, evaluation, case_ids = _run_fixture(gains)
    row = _reload_evaluation(evaluation)
    assert row.verdict == verdict
    assert row.state == "complete"
    assert row.evaluator_version == "recursive-comparison/1"
    assert row.experiment_id == experiment.id
    assert row.contract_digest == experiment.contract_digest
    assert row.charter_digest == ImprovementCharter.pinned(PK).digest
    assert row.blinded is False
    assert row.trials == len(case_ids)
    assert row.correction == "holm"
    assert _reload_experiment(experiment).state == "complete"

    expected_mean = sum(g["b"] - g["a"] for g in gains) / len(gains)
    assert effect_of(row, "validated_gain") == pytest.approx(expected_mean)
    interval = interval_of(row, "validated_gain")
    assert set(interval) == {"lower", "upper", "n", "raw_p_value", "adjusted_p_value"}
    assert interval["n"] == len(case_ids)
    assert interval["lower"] <= expected_mean <= interval["upper"]
    if verdict == "accept":
        assert interval["lower"] > 0
    elif verdict == "reject":
        assert interval["upper"] < 0
    else:
        assert interval["lower"] <= 0 <= interval["upper"]

    budget = budget_of(row)
    assert budget["comparable"] is True and budget["reasons"] == []
    assert budget["cap"] == {
        "unit2_usd": 10.0,
        "unit3_usd": 10.0,
        "subscription_turns": 100,
        "wall_seconds": 3600,
    }
    for arm in ("a", "b"):
        assert budget[arm] == {
            "unit2_usd": 2.0,
            "unit3_usd": 1.0,
            "subscription_turns": 10,
            "wall_seconds": 100,
        }
    lines = notes_of(row)
    assert sum(line.startswith("budget=") for line in lines) == 1
    assert f"arms=a:{DIGEST_A},b:{DIGEST_B}" in lines
    assert any(line.startswith("arm_assignment_digest=sha256:") for line in lines)


def test_accept_needs_the_lower_bound_above_the_minimum_worthwhile_effect(charter):
    _, evaluation, _ = _run_fixture(ACCEPT_GAINS, minimum_worthwhile_effect=0.9)
    assert evaluation.verdict == "inconclusive"
    assert current_revisions(PK) == []


def test_compare_refuses_claim_on_unknown_unit2(charter):
    """Unit 2 is unmetered: the real reader answers ``None`` and no claim is made."""
    _, evaluation, _ = _run_fixture(ACCEPT_GAINS, reader=LedgerBudgetReader(PK))
    row = _reload_evaluation(evaluation)
    assert row.verdict == "inconclusive"
    assert "BUDGET_UNKNOWN:unit2" in notes_of(row)
    budget = budget_of(row)
    assert budget["comparable"] is False
    assert budget["reasons"] == ["BUDGET_UNKNOWN:unit2"]
    assert budget["a"]["unit2_usd"] is None and budget["b"]["unit2_usd"] is None
    assert budget["a"]["unit3_usd"] == 1.0, "unit 3 still read from the ledger"
    assert effect_of(row, "validated_gain") > 0, "the deltas alone would have accepted"
    assert current_revisions(PK) == []


def test_arm_that_admitted_nothing_is_unknown_in_unit3(charter):
    case_ids = _cases()
    experiment = _freeze(case_ids)
    unknown = BudgetUse(unit3_usd=None, subscription_turns=10, wall_seconds=100)
    evaluation = run(
        experiment.id,
        runner=_replay(ACCEPT_GAINS, case_ids, use_b=unknown),
        budget_reader=_MeteredReader(),
        project_key=PK,
    )
    row = _reload_evaluation(evaluation)
    assert row.verdict == "inconclusive"
    assert "BUDGET_UNKNOWN:unit3" in notes_of(row)
    assert budget_of(row)["b"]["unit3_usd"] is None
    assert budget_of(row)["a"]["unit3_usd"] == 1.0


def test_mismatched_budget_is_inconclusive(charter):
    _, evaluation, _ = _run_fixture(ACCEPT_GAINS, reader=_MeteredReader({"a": 1.0, "b": 6.0}))
    row = _reload_evaluation(evaluation)
    assert row.verdict == "inconclusive"
    assert "BUDGET_MISMATCH:unit2" in notes_of(row)
    assert budget_of(row)["a"]["unit2_usd"] == 1.0
    assert budget_of(row)["b"]["unit2_usd"] == 6.0
    assert current_revisions(PK) == []


def test_arm_exception_is_infra_failure_never_a_result(charter):
    experiment = _freeze(_cases(2))
    evaluation = run(experiment.id, runner=_RaisingRunner(), project_key=PK)
    row = _reload_evaluation(evaluation)
    assert row.verdict == "infra_failure"
    assert row.state == "complete"
    assert row.experiment_id == experiment.id
    assert "RuntimeError" in row.notes
    assert row.effect is None and row.confidence_interval is None
    assert _reload_experiment(experiment).state == "aborted"
    assert current_revisions(PK) == []


def test_post_arm_scoring_failure_is_infra_failure_not_a_wedged_running_state(charter, monkeypatch):
    """A raise after both arms ran (the priority-area lookup here) still aborts the
    experiment; the only alternative is a row stuck in ``running`` that a retry
    refuses ``WRONG_STATE`` and no subcommand recovers."""
    from tools.improvement_recursion import compare

    case_ids = _cases()
    experiment = _freeze(case_ids)

    def _boom(project_key, opportunity_ids):
        raise LookupError("priority areas unavailable")

    monkeypatch.setattr(compare, "_priority_areas", _boom)
    evaluation = run(
        experiment.id,
        runner=_replay(ACCEPT_GAINS, case_ids),
        budget_reader=_MeteredReader(),
        project_key=PK,
    )
    row = _reload_evaluation(evaluation)
    assert row.verdict == "infra_failure"
    assert "LookupError: priority areas unavailable" in notes_of(row)[0]
    assert _reload_experiment(experiment).state == "aborted"
    assert current_revisions(PK) == []


def test_no_pinned_charter_is_infra_failure_before_any_arm_runs():
    """Lane 4's shape: a missing charter pin is a harness failure, never a silent ``None``."""
    assert ImprovementCharter.pinned(PK) is None
    calls = []

    class _Counting:
        def run(self, *args):
            calls.append(args)
            return ArmResult(gains={}, budget_use=USE)

    experiment = _freeze(_cases(2))
    evaluation = run(experiment.id, runner=_Counting(), project_key=PK)
    row = _reload_evaluation(evaluation)
    assert row.verdict == "infra_failure"
    assert row.charter_digest is None
    assert "CHARTER_NOT_PINNED" in notes_of(row)[0]
    assert calls == [], "no arm ran"
    assert _reload_experiment(experiment).state == "aborted"


def test_arm_assignment_digest_is_deterministic_under_rng_seed(charter):
    _, first, _ = _run_fixture(MIXED_GAINS, rng_seed=11)
    _, second, _ = _run_fixture(MIXED_GAINS, rng_seed=11)
    assert first.arm_assignment_digest.startswith("sha256:")
    assert first.arm_assignment_digest == second.arm_assignment_digest
    orders = {
        line
        for seed in range(6)
        for line in notes_of(_run_fixture(MIXED_GAINS, rng_seed=seed)[1])
        if line.startswith("run_order=")
    }
    assert orders == {"run_order=a,b", "run_order=b,a"}, "the seed decides the order"


def test_arm_run_ids_carry_the_experiment_and_arm(charter):
    seen = []

    class _Recording:
        def run(self, process_digest, opportunity_ids, budget_cap, arm_run_id):
            seen.append((process_digest, list(opportunity_ids), budget_cap, arm_run_id))
            return ArmResult(gains={}, budget_use=USE)

    case_ids = _cases(2)
    experiment = _freeze(case_ids)
    run(experiment.id, runner=_Recording(), budget_reader=_MeteredReader(), project_key=PK)
    assert {(d, r) for d, _, _, r in seen} == {
        (DIGEST_A, f"{experiment.id}:a"),
        (DIGEST_B, f"{experiment.id}:b"),
    }
    assert all(ids == sorted(case_ids) and cap == CAP for _, ids, cap, _ in seen)


# ---------------------------------------------------------------------------
# revisions
# ---------------------------------------------------------------------------


def test_accept_writes_the_revision_new_then_supersede(charter):
    prior = _revision(DIGEST_A)
    _, evaluation, _ = _run_fixture(ACCEPT_GAINS, arm_a=None)
    assert evaluation.verdict == "accept"
    current = current_revisions(PK)
    assert len(current) == 1
    new = current[0]
    assert new.research_process_digest == DIGEST_B
    assert new.supersedes_id == prior.id
    assert new.revision == prior.revision + 1
    assert evaluation.id in (new.rationale or "")
    assert "validated gain" in new.prediction and DIGEST_B in new.prediction
    interval = interval_of(evaluation, "validated_gain")
    assert f"{interval['lower']:.4f}" in new.prediction
    reloaded_prior = ImprovementModelRevision.query.filter(project_key=PK, id=prior.id).first()
    assert reloaded_prior.state == "superseded"
    assert new.created_at >= reloaded_prior.created_at


def test_accept_with_no_prior_revision_writes_the_first_current(charter):
    _, evaluation, _ = _run_fixture(ACCEPT_GAINS)
    assert evaluation.verdict == "accept"
    (new,) = current_revisions(PK)
    assert new.research_process_digest == DIGEST_B
    assert new.supersedes_id is None
    assert new.revision == 1


def test_write_revision_order_is_new_then_supersede():
    """Race 2: a crash between the two writes leaves two ``current`` rows, never zero."""
    import ast
    import inspect

    from tools.improvement_recursion import compare

    tree = ast.parse(inspect.getsource(compare._write_revision))
    saves = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "save"
    ]
    supersede = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "attr", None) == "state" for t in n.targets)
        and getattr(n.value, "value", None) == "superseded"
    ]
    assert saves and supersede
    assert min(saves) < min(supersede), "the new row is saved before the old one is superseded"


@pytest.mark.parametrize("verdict_gains", [REJECT_GAINS, MIXED_GAINS], ids=["reject", "mixed"])
def test_non_accept_writes_no_revision(charter, verdict_gains):
    prior = _revision(DIGEST_A)
    _run_fixture(verdict_gains, arm_a=None)
    (still,) = current_revisions(PK)
    assert still.id == prior.id


def test_supersede_revision_by_hand(charter):
    first, second = _revision(DIGEST_A), _revision(DIGEST_B)
    superseded = supersede_revision(first.id, project_key=PK, reason="crash between the two writes")
    assert superseded.state == "superseded"
    assert "crash between the two writes" in superseded.rationale
    assert [r.id for r in current_revisions(PK)] == [second.id]
    with pytest.raises(ComparisonRefused) as info:
        supersede_revision(first.id, project_key=PK, reason="again")
    assert info.value.code == "WRONG_STATE"
    with pytest.raises(ComparisonRefused) as info:
        supersede_revision("nosuch", project_key=PK, reason="x")
    assert info.value.code == "NOT_FOUND"
    with pytest.raises(ValueError, match="reason"):
        supersede_revision(second.id, project_key=PK, reason="  ")
