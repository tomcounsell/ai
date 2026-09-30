"""Opportunity freshness (lane 6, #3218).

An opportunity is fresh when no record shows it was worked: the case is open,
no experiment or investigation cites it, and no prior comparison's manifest
lists it. Rows land in a claimed test DB (autouse ``redis_test_db``,
tests/conftest.py) under a test-scoped ``project_key``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from models.improvement_case import OPEN_CASE_STATES, TERMINAL_CASE_STATES, ImprovementCase
from models.improvement_experiment import ImprovementExperiment
from models.improvement_investigation import ImprovementInvestigation
from tools.improvement_recursion.freshness import fresh_opportunities

PK = "test-3218-fresh"


def _case(state: str = "observed") -> ImprovementCase:
    return ImprovementCase.create(
        project_key=PK, state=state, title=f"case-{state}", created_at=datetime.now(UTC)
    )


def _experiment(**fields) -> ImprovementExperiment:
    base = {"project_key": PK, "created_at": datetime.now(UTC)}
    base.update(fields)
    row = ImprovementExperiment(**base)
    assert row.save() is not False
    return row


def _comparison(opportunity_ids: list[str]) -> ImprovementExperiment:
    return _experiment(
        candidate_surfaces=json.dumps(["research_process"]),
        manifest=json.dumps({"opportunity_ids": opportunity_ids, "protocol_ref": "$CF:x"}),
    )


def test_empty_candidates_return_two_empty_lists():
    assert fresh_opportunities([], project_key=PK) == ([], [])


def test_unknown_case_excluded_not_found():
    fresh, excluded = fresh_opportunities(["nosuch"], project_key=PK)
    assert fresh == []
    assert excluded == [("nosuch", "NOT_FOUND")]


@pytest.mark.parametrize("state", OPEN_CASE_STATES)
def test_open_case_with_no_records_is_fresh(state):
    case = _case(state)
    fresh, excluded = fresh_opportunities([case.id], project_key=PK)
    assert fresh == [case.id]
    assert excluded == []


@pytest.mark.parametrize("state", TERMINAL_CASE_STATES)
def test_terminal_case_excluded_not_open(state):
    case = _case(state)
    fresh, excluded = fresh_opportunities([case.id], project_key=PK)
    assert fresh == []
    assert excluded == [(case.id, "NOT_OPEN")]


def test_case_with_experiment_excluded():
    case = _case()
    _experiment(case_id=case.id)
    fresh, excluded = fresh_opportunities([case.id], project_key=PK)
    assert fresh == []
    assert excluded == [(case.id, "HAS_EXPERIMENT")]


def test_case_with_investigation_excluded():
    case = _case()
    ImprovementInvestigation.create(
        project_key=PK, created_at=datetime.now(UTC), kind="probe", case_id=case.id
    )
    fresh, excluded = fresh_opportunities([case.id], project_key=PK)
    assert fresh == []
    assert excluded == [(case.id, "HAS_INVESTIGATION")]


def test_case_listed_in_prior_comparison_excluded():
    used, untouched = _case(), _case()
    _comparison([used.id])
    fresh, excluded = fresh_opportunities([used.id, untouched.id], project_key=PK)
    assert fresh == [untouched.id]
    assert excluded == [(used.id, "IN_PRIOR_COMPARISON")]


def test_non_comparison_manifest_listing_ids_does_not_exclude():
    """Only a comparison experiment's manifest names opportunities."""
    case = _case()
    _experiment(
        candidate_surfaces=json.dumps(["tools/x.py"]),
        manifest=json.dumps({"opportunity_ids": [case.id]}),
    )
    fresh, excluded = fresh_opportunities([case.id], project_key=PK)
    assert fresh == [case.id]
    assert excluded == []


def test_other_project_records_do_not_exclude():
    case = _case()
    other = ImprovementCase.create(
        project_key="test-3218-fresh-other",
        state="observed",
        title="elsewhere",
        created_at=datetime.now(UTC),
    )
    _experiment(project_key="test-3218-fresh-other", case_id=case.id)
    fresh, excluded = fresh_opportunities([case.id, other.id], project_key=PK)
    assert fresh == [case.id]
    assert excluded == [(other.id, "NOT_FOUND")]


def test_order_preserved_and_duplicates_collapsed():
    a, b = _case(), _case()
    fresh, excluded = fresh_opportunities([b.id, a.id, b.id, "ghost"], project_key=PK)
    assert fresh == [b.id, a.id]
    assert excluded == [("ghost", "NOT_FOUND")]
