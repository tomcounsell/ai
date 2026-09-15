"""Evaluation read shape (#3218, lane 6).

Lane 4's single evaluation writer stores ``effect`` and ``confidence_interval``
as JSON strings keyed by endpoint and ``notes`` as a newline-joined string
(``tools/improvement_eval/runner.py::_write_evaluation``). The row here is
seeded with exactly those expressions, never a hand-built dict, so a shape
change in lane 4 is a red test in this lane.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from models.improvement_evaluation import ImprovementEvaluation
from tools.improvement_release.evaluation_read import (
    budget_of,
    effect_of,
    interval_of,
    notes_of,
)

PK = "test-3218-evaluation-read"

INTERVAL = {"lower": 0.02, "upper": 0.22, "n": 12, "raw_p_value": 0.01, "adjusted_p_value": 0.02}
BUDGET = {"unit1_usd": None, "unit2_seconds": 120, "unit3_usd": 0.5}
NOTES = ["holdout epoch-test", "budget=" + json.dumps(BUDGET, sort_keys=True), "accept"]


def _seed(**overrides) -> ImprovementEvaluation:
    fields = {
        "project_key": PK,
        "created_at": datetime.now(UTC),
        "state": "complete",
        "verdict": "accept",
        # runner.py:499-508's exact expressions
        "effect": json.dumps({"primary": 0.12}, sort_keys=True),
        "confidence_interval": json.dumps({"primary": INTERVAL}, sort_keys=True),
        "notes": "\n".join(NOTES),
        "judge_records": "",
    }
    fields.update(overrides)
    row = ImprovementEvaluation(**fields)
    assert row.save() is not False
    reloaded = ImprovementEvaluation.query.filter(project_key=PK, id=row.id).first()
    assert reloaded is not None
    return reloaded


class TestLane4StringShape:
    def test_reads_float_dict_lines_and_budget(self):
        row = _seed()
        assert effect_of(row, "primary") == 0.12
        assert interval_of(row, "primary") == INTERVAL
        assert notes_of(row) == NOTES
        assert budget_of(row) == BUDGET

    def test_missing_endpoint_is_none(self):
        row = _seed()
        assert effect_of(row, "secondary") is None
        assert interval_of(row, "secondary") is None


class TestNeverRaises:
    @pytest.mark.parametrize("raw", [None, ""])
    def test_empty_effect_and_interval(self, raw):
        row = _seed(effect=raw, confidence_interval=raw, notes=raw)
        assert effect_of(row, "primary") is None
        assert interval_of(row, "primary") is None
        assert notes_of(row) == []
        assert budget_of(row) is None

    def test_dict_value_answers_like_the_string_form(self):
        class Parsed:
            effect = {"primary": 0.12}
            confidence_interval = {"primary": INTERVAL}
            notes = "\n".join(NOTES)

        assert effect_of(Parsed(), "primary") == 0.12
        assert interval_of(Parsed(), "primary") == INTERVAL

    def test_malformed_json_is_none(self):
        row = _seed(effect="{not json", confidence_interval="[1, 2")
        assert effect_of(row, "primary") is None
        assert interval_of(row, "primary") is None

    def test_non_object_json_is_none(self):
        row = _seed(effect="0.12", confidence_interval="[]")
        assert effect_of(row, "primary") is None
        assert interval_of(row, "primary") is None

    def test_budget_without_line_is_none(self):
        row = _seed(notes="\n".join(["holdout epoch-test", "accept"]))
        assert budget_of(row) is None

    def test_budget_with_malformed_json_is_none(self):
        row = _seed(notes="budget={oops")
        assert budget_of(row) is None
