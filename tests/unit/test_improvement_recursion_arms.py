"""The arm runner seam (lane 6, #3218).

``arms`` is the only module of this lane that lane 5 imports, and it takes
four names from it at runtime. The registry, the ``module:attr`` resolver,
and the replay runner are exercised here; the replay runner admits its
fixture spend through lane 7's ``admit()`` so ``LedgerBudgetReader`` reads
it back on the production path. Rows land in a claimed test DB (autouse
``redis_test_db``, tests/conftest.py).
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import tools.improvement_recursion.arms as arms_module
from tools.improvement_recursion.arms import (
    ArmResult,
    ArmRunnerAbsent,
    BudgetCap,
    BudgetUse,
    ReplayArm,
    ReplayArmRunner,
    get_arm_runner,
    register_arm_runner,
    resolve_arm_runner,
)
from tools.improvement_recursion.budget import LedgerBudgetReader, accounted_use

PK = "test-3218-arms"
MONDAY = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
CAP = BudgetCap(unit2_usd=10.0, unit3_usd=10.0, subscription_turns=100, wall_seconds=3600)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


@pytest.fixture(autouse=True)
def _clear_registry():
    register_arm_runner(None)
    yield
    register_arm_runner(None)


def _settings(cap=50.0):
    return SimpleNamespace(
        improvement=SimpleNamespace(
            weekly_infrastructure_usd=cap, budget_week_start="monday", budget_day_boundary="UTC"
        )
    )


class _StubRunner:
    def run(self, process_digest, opportunity_ids, budget_cap, arm_run_id):
        return ArmResult(gains={}, budget_use=BudgetUse())


# ---------------------------------------------------------------------------
# Lane 5 seam
# ---------------------------------------------------------------------------


def test_arms_exports_lane5_seam():
    import tools.improvement_recursion.budget as budget
    from tools.improvement_recursion.arms import (  # noqa: F401
        ArmResult,
        BudgetUse,
        get_arm_runner,
        register_arm_runner,
    )

    assert BudgetUse is budget.BudgetUse
    assert BudgetCap is budget.BudgetCap


def test_arms_all_lists_the_seam_names():
    assert arms_module.__all__ == [
        "ArmRunner",
        "ArmResult",
        "ArmRunnerAbsent",
        "BudgetCap",
        "BudgetUse",
        "ReplayArmRunner",
        "get_arm_runner",
        "register_arm_runner",
        "resolve_arm_runner",
    ]
    for name in arms_module.__all__:
        assert getattr(arms_module, name) is not None


def test_budget_reexport_is_a_runtime_import_not_type_checking():
    """A ``TYPE_CHECKING``-guarded import satisfies a checker and fails lane 5 at call time."""
    tree = ast.parse(inspect.getsource(arms_module))
    top_level = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "tools.improvement_recursion.budget"
    ]
    assert top_level, "budget import must sit at module level"
    assert {alias.name for alias in top_level[0].names} >= {"BudgetCap", "BudgetUse"}


def test_budget_imports_nothing_from_arms():
    import tools.improvement_recursion.budget as budget

    tree = ast.parse(inspect.getsource(budget))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "arms" not in (node.module or "")
        if isinstance(node, ast.Import):
            assert all("arms" not in alias.name for alias in node.names)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_get_arm_runner_absent_raises():
    with pytest.raises(ArmRunnerAbsent) as info:
        get_arm_runner()
    assert info.value.code == "ARM_RUNNER_ABSENT"
    assert "ARM_RUNNER_ABSENT" in str(info.value)


def test_register_then_get_returns_the_same_runner():
    runner = _StubRunner()
    register_arm_runner(runner)
    assert get_arm_runner() is runner


def test_register_none_clears_the_registry():
    register_arm_runner(_StubRunner())
    register_arm_runner(None)
    with pytest.raises(ArmRunnerAbsent):
        get_arm_runner()


# ---------------------------------------------------------------------------
# resolve_arm_runner
# ---------------------------------------------------------------------------


def test_resolve_by_module_attr():
    runner = resolve_arm_runner("tools.improvement_recursion.arms:ReplayArmRunner")
    assert isinstance(runner, ReplayArmRunner)


def test_resolve_does_not_touch_the_registry():
    resolve_arm_runner("tools.improvement_recursion.arms:ReplayArmRunner")
    with pytest.raises(ArmRunnerAbsent):
        get_arm_runner()


@pytest.mark.parametrize(
    "spec, fragment",
    [
        ("nosuch.module:Runner", "nosuch"),
        ("tools.improvement_recursion.arms:NoSuchAttr", "NoSuchAttr"),
        ("tools.improvement_recursion.arms", "module:attr"),
        (":ReplayArmRunner", "module:attr"),
        ("tools.improvement_recursion.arms:", "module:attr"),
    ],
    ids=["missing-module", "missing-attr", "no-colon", "empty-module", "empty-attr"],
)
def test_resolve_failures_become_arm_runner_absent(spec, fragment):
    with pytest.raises(ArmRunnerAbsent) as info:
        resolve_arm_runner(spec)
    assert info.value.code == "ARM_RUNNER_ABSENT"
    assert fragment in info.value.detail
    assert "ARM_RUNNER_ABSENT" in str(info.value)


# ---------------------------------------------------------------------------
# ArmResult
# ---------------------------------------------------------------------------


def test_arm_result_scores_missing_or_none_gains_as_zero():
    result = ArmResult(gains={"c1": 0.4, "c2": None}, budget_use=BudgetUse())
    assert result.scored_gain("c1") == 0.4
    assert result.scored_gain("c2") == 0.0
    assert result.scored_gain("absent") == 0.0


# ---------------------------------------------------------------------------
# ReplayArmRunner
# ---------------------------------------------------------------------------


def _replay_runner(**arms) -> ReplayArmRunner:
    return ReplayArmRunner(arms, project_key=PK, settings=_settings(), now=MONDAY)


def test_replay_returns_fixture_gains_for_the_requested_opportunities():
    runner = _replay_runner(
        **{
            DIGEST_A: ReplayArm(
                gains={"c1": 0.2, "c2": None, "c9": 0.9},
                budget_use=BudgetUse(unit3_usd=None, subscription_turns=3, wall_seconds=10),
            )
        }
    )
    result = runner.run(DIGEST_A, ["c1", "c2", "c3"], CAP, "run-a")
    assert result.gains == {"c1": 0.2, "c2": None, "c3": None}
    assert result.budget_use.subscription_turns == 3
    assert result.budget_use.wall_seconds == 10


def test_replay_admits_unit3_spend_under_the_arm_prefix():
    runner = _replay_runner(
        **{
            DIGEST_A: ReplayArm(gains={"c1": 0.2}, budget_use=BudgetUse(unit3_usd=1.5)),
            DIGEST_B: ReplayArm(gains={"c1": 0.1}, budget_use=BudgetUse(unit3_usd=2.5)),
        }
    )
    runner.run(DIGEST_A, ["c1"], CAP, "run-a")
    runner.run(DIGEST_B, ["c1"], CAP, "run-b")

    reader = LedgerBudgetReader(PK)
    assert reader.unit3_usd("run-a") == pytest.approx(1.5)
    assert reader.unit3_usd("run-b") == pytest.approx(2.5)
    assert reader.unit3_usd("run-c") is None

    from models.improvement_infrastructure_ledger import InfrastructureReservation

    rows = [
        r
        for r in InfrastructureReservation.query.filter(project_key=PK)
        if r.resource.startswith("arm:run-a:")
    ]
    assert len(rows) == 1
    assert rows[0].state == "settled"
    assert rows[0].reason == "admitted", "the arm id rides on the resource name, never on reason"


def test_replay_with_unknown_unit3_admits_nothing():
    runner = _replay_runner(
        **{DIGEST_A: ReplayArm(gains={"c1": 0.2}, budget_use=BudgetUse(unit3_usd=None))}
    )
    result = runner.run(DIGEST_A, ["c1"], CAP, "run-a")
    use = accounted_use(LedgerBudgetReader(PK), "run-a", result.budget_use)
    assert use.unit3_usd is None
    assert use.unit2_usd is None


def test_replay_refuses_an_unknown_process_digest():
    runner = _replay_runner()
    with pytest.raises(LookupError, match=DIGEST_A):
        runner.run(DIGEST_A, ["c1"], CAP, "run-a")


def test_replay_raises_when_admission_is_refused():
    runner = ReplayArmRunner(
        {DIGEST_A: ReplayArm(gains={}, budget_use=BudgetUse(unit3_usd=5.0))},
        project_key=PK,
        settings=_settings(cap=1.0),
        now=MONDAY,
    )
    with pytest.raises(RuntimeError, match="week exhausted"):
        runner.run(DIGEST_A, [], CAP, "run-a")
    assert LedgerBudgetReader(PK).unit3_usd("run-a") is None
