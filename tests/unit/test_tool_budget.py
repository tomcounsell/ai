"""Unit matrix for the pure per-tool budget evaluator (Fix #6, issue #1821).

``evaluate_tool_budget`` is PURE and SYNCHRONOUS — it returns a verdict only,
reading ``tool_call_count`` / ``total_cost_usd`` off the session. These tests
pin the ALLOW/DENY matrix and the fail-safe-on-missing-data behavior. They are
the Acceptance #2 unit core.

The module constants are read as globals at call time, so we override them with
``monkeypatch.setattr`` for deterministic thresholds.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import tool_budget
from agent.tool_budget import BudgetVerdict, evaluate_tool_budget


@pytest.fixture(autouse=True)
def _deterministic_thresholds(monkeypatch):
    """Small, deterministic thresholds and the budget ENABLED by default."""
    monkeypatch.setattr(tool_budget, "MAX_TOOL_CALLS_PER_SESSION", 10)
    monkeypatch.setattr(tool_budget, "SESSION_COST_CAP_USD", 5.0)
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_ENABLED", True)


def _session(calls=0, cost=0.0):
    return SimpleNamespace(tool_call_count=calls, total_cost_usd=cost)


def test_under_budget_allows():
    assert evaluate_tool_budget(_session(calls=5, cost=1.0)).allow is True


@pytest.mark.parametrize("calls", [10, 11, 1000])
def test_tool_call_cap_denies(calls):
    v = evaluate_tool_budget(_session(calls=calls, cost=0.0))
    assert v.allow is False
    assert "tool-call budget" in v.reason


@pytest.mark.parametrize("cost", [5.0, 5.01, 100.0])
def test_cost_cap_denies(cost):
    v = evaluate_tool_budget(_session(calls=0, cost=cost))
    assert v.allow is False
    assert "cost cap" in v.reason


def test_none_session_allows():
    assert evaluate_tool_budget(None).allow is True


def test_none_fields_allow():
    """Missing/None counters must never produce a false deny."""
    assert evaluate_tool_budget(_session(calls=None, cost=None)).allow is True
    assert evaluate_tool_budget(SimpleNamespace()).allow is True


def test_disabled_always_allows(monkeypatch):
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_ENABLED", False)
    # Even a wildly over-budget session allows when the master switch is off.
    assert evaluate_tool_budget(_session(calls=10_000, cost=10_000.0)).allow is True


def test_verdict_dataclass_shape():
    v = BudgetVerdict(allow=False, reason="x")
    assert (v.allow, v.reason) == (False, "x")
    assert BudgetVerdict(allow=True).reason is None
