"""Budget accounting for a comparison arm (lane 6, #3218).

Unknown never reads as zero: a ``None`` use on either arm refuses the
comparison, and ``LedgerBudgetReader.unit3_usd`` answers ``None`` when no
``InfrastructureReservation`` row carries the arm's resource prefix. Ledger
rows are admitted through lane 7's ``admit()`` with an arm-prefixed
``ResourceDecl`` name, the same path a production arm runner takes. Rows
land in a claimed test DB (autouse ``redis_test_db``, tests/conftest.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tools.improvement_recursion.budget import (
    BudgetCap,
    BudgetUse,
    LedgerBudgetReader,
    accounted_use,
    budgets_comparable,
)
from tools.infrastructure_budget import ResourceDecl, admit, release, settle

PK = "test-3218-budget"
MONDAY = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _settings(cap=50.0):
    return SimpleNamespace(
        improvement=SimpleNamespace(
            weekly_infrastructure_usd=cap, budget_week_start="monday", budget_day_boundary="UTC"
        )
    )


def _admit(arm_run_id: str, resource: str, weekly_rate: object = 1.0):
    decision = admit(
        ResourceDecl(name=f"arm:{arm_run_id}:{resource}", weekly_rate_usd=weekly_rate),
        project_key=PK,
        settings=_settings(),
        now=MONDAY,
    )
    return decision


# ---------------------------------------------------------------------------
# LedgerBudgetReader
# ---------------------------------------------------------------------------


def test_unit3_unknown_when_no_arm_rows():
    reader = LedgerBudgetReader(PK)
    assert reader.unit3_usd("arm-none") is None

    other = _admit("arm-other", "gpu")
    assert other.admitted
    assert reader.unit3_usd("arm-none") is None, "another arm's prefix must not count"


def test_unit3_sums_reserved_rows_under_arm_prefix():
    first = _admit("arm-a", "gpu", 2.0)
    second = _admit("arm-a", "sandbox", 3.0)
    _admit("arm-b", "gpu", 9.0)
    assert first.admitted and second.admitted

    total = LedgerBudgetReader(PK).unit3_usd("arm-a")
    assert total == pytest.approx(first.forecast_usd + second.forecast_usd)


def test_unit3_prefers_settled_over_forecast():
    first = _admit("arm-a", "gpu", 2.0)
    second = _admit("arm-a", "sandbox", 3.0)
    settle(first.reservation_id, lambda: 0.25, project_key=PK)

    total = LedgerBudgetReader(PK).unit3_usd("arm-a")
    assert total == pytest.approx(0.25 + second.forecast_usd)


def test_unit3_ignores_refused_and_released_rows():
    refused = _admit("arm-a", "unforecastable", None)
    assert refused.admitted is False
    assert LedgerBudgetReader(PK).unit3_usd("arm-a") is None, "a refusal is not spend"

    reserved = _admit("arm-a", "gpu", 2.0)
    assert release(reserved.reservation_id, project_key=PK)
    assert LedgerBudgetReader(PK).unit3_usd("arm-a") is None, "a release returned the headroom"


def test_unit3_zero_spend_is_zero_not_unknown():
    decision = _admit("arm-a", "free", 0.0)
    assert decision.admitted
    assert LedgerBudgetReader(PK).unit3_usd("arm-a") == 0.0


def test_unit3_prefix_match_is_exact_on_arm_id():
    _admit("arm-a1", "gpu", 2.0)
    assert LedgerBudgetReader(PK).unit3_usd("arm-a") is None


def test_unit3_scoped_by_project_key():
    _admit("arm-a", "gpu", 2.0)
    assert LedgerBudgetReader("test-3218-budget-other").unit3_usd("arm-a") is None


def test_unit1_unknown_until_lane3_meters_it():
    assert LedgerBudgetReader(PK).unit1_usd("arm-a") is None


# ---------------------------------------------------------------------------
# accounted_use
# ---------------------------------------------------------------------------


def test_accounted_use_takes_dollars_from_reader_and_the_rest_from_the_arm():
    decision = _admit("arm-a", "gpu", 2.0)
    reported = BudgetUse(unit1_usd=99.0, unit3_usd=99.0, subscription_turns=4, wall_seconds=12.5)
    use = accounted_use(LedgerBudgetReader(PK), "arm-a", reported)
    assert use == BudgetUse(
        unit1_usd=None,
        unit3_usd=pytest.approx(decision.forecast_usd),
        subscription_turns=4,
        wall_seconds=12.5,
    )


# ---------------------------------------------------------------------------
# budgets_comparable
# ---------------------------------------------------------------------------

CAP = BudgetCap(unit1_usd=10.0, unit3_usd=10.0, subscription_turns=100, wall_seconds=3600)


def _use(**overrides) -> BudgetUse:
    fields = dict(unit1_usd=5.0, unit3_usd=5.0, subscription_turns=50, wall_seconds=1800)
    fields.update(overrides)
    return BudgetUse(**fields)


def test_comparable_when_both_arms_within_cap_and_tolerance():
    ok, reasons = budgets_comparable(_use(), _use(unit3_usd=5.5), CAP)
    assert ok is True
    assert reasons == []


@pytest.mark.parametrize(
    "a, b, unit",
    [
        (_use(unit1_usd=None), _use(), "unit1"),
        (_use(), _use(unit3_usd=None), "unit3"),
        (_use(subscription_turns=None), _use(subscription_turns=None), "subscription_turns"),
        (_use(wall_seconds=None), _use(), "wall_seconds"),
    ],
    ids=["a-unit1", "b-unit3", "both-turns", "a-wall"],
)
def test_unknown_use_on_either_arm_refuses(a, b, unit):
    ok, reasons = budgets_comparable(a, b, CAP)
    assert ok is False
    assert reasons == [f"BUDGET_UNKNOWN:{unit}"]


def test_exceeded_names_arm_and_unit():
    ok, reasons = budgets_comparable(_use(unit3_usd=11.0), _use(unit1_usd=10.5), CAP)
    assert ok is False
    assert "BUDGET_EXCEEDED:a:unit3" in reasons
    assert "BUDGET_EXCEEDED:b:unit1" in reasons


def test_use_equal_to_cap_is_not_exceeded():
    ok, reasons = budgets_comparable(_use(unit3_usd=10.0), _use(unit3_usd=10.0), CAP)
    assert ok is True
    assert reasons == []


def test_mismatch_beyond_tolerance_of_cap():
    ok, reasons = budgets_comparable(_use(unit1_usd=4.0), _use(unit1_usd=5.5), CAP)
    assert ok is False
    assert reasons == ["BUDGET_MISMATCH:unit1"]


def test_mismatch_at_tolerance_boundary_is_ok():
    ok, reasons = budgets_comparable(_use(unit1_usd=4.0), _use(unit1_usd=5.0), CAP)
    assert ok is True
    assert reasons == []


def test_tolerance_is_a_fraction_of_the_cap():
    ok, _ = budgets_comparable(_use(unit1_usd=4.0), _use(unit1_usd=5.5), CAP, tolerance=0.2)
    assert ok is True


def test_zero_cap_with_zero_use_on_both_sides_is_ok():
    cap = BudgetCap(unit1_usd=0, unit3_usd=10.0, subscription_turns=100, wall_seconds=3600)
    ok, reasons = budgets_comparable(_use(unit1_usd=0), _use(unit1_usd=0.0), cap)
    assert ok is True
    assert reasons == []


def test_zero_cap_with_any_use_is_exceeded():
    cap = BudgetCap(unit1_usd=0, unit3_usd=10.0, subscription_turns=100, wall_seconds=3600)
    ok, reasons = budgets_comparable(_use(unit1_usd=0), _use(unit1_usd=0.01), cap)
    assert ok is False
    assert "BUDGET_EXCEEDED:b:unit1" in reasons


def test_none_cap_is_never_ok():
    cap = BudgetCap(unit1_usd=None, unit3_usd=10.0, subscription_turns=100, wall_seconds=3600)
    ok, reasons = budgets_comparable(_use(unit1_usd=0), _use(unit1_usd=0), cap)
    assert ok is False
    assert reasons == ["CAP_UNKNOWN:unit1"]


def test_reasons_accumulate_across_units():
    cap = BudgetCap(unit1_usd=None, unit3_usd=10.0, subscription_turns=100, wall_seconds=3600)
    ok, reasons = budgets_comparable(
        _use(unit3_usd=None, subscription_turns=200), _use(wall_seconds=1000), cap
    )
    assert ok is False
    assert reasons == [
        "CAP_UNKNOWN:unit1",
        "BUDGET_UNKNOWN:unit3",
        "BUDGET_EXCEEDED:a:subscription_turns",
        "BUDGET_MISMATCH:subscription_turns",
        "BUDGET_MISMATCH:wall_seconds",
    ]
