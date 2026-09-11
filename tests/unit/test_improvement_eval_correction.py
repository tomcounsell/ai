"""Tests for tools.improvement_eval.correction (lane 4, task build-stats).

Pins the three Holm operations, the worked example, monotonicity as a
property, input validation, and the fixed-batch stopping rule string.
"""

from __future__ import annotations

import math
import random

import pytest

from tools.improvement_eval.correction import (
    FixedBatchStoppingRule,
    cumulative_maximum_clamped,
    holm_adjust,
    multiply_by_rank_weight,
    sort_ascending,
)


def test_holm_adjust_empty_returns_empty():
    assert holm_adjust([]) == []


@pytest.mark.parametrize(
    ("p", "expected"),
    [(0.04, 0.04), (0.0, 0.0), (1.0, 1.0), (1, 1.0), (0, 0.0)],
)
def test_holm_adjust_single_returns_min_of_one_and_p(p, expected):
    assert holm_adjust([p]) == [pytest.approx(expected)]


def test_holm_adjust_worked_example():
    # Sorted: 0.01, 0.03, 0.04. Weighted: 0.03, 0.06, 0.04.
    # Cumulative max lifts the trailing 0.04 to 0.06; mapped back.
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_holm_adjust_maps_back_to_input_order():
    assert holm_adjust([0.5, 0.01]) == pytest.approx([0.5, 0.02])


def test_holm_adjust_clamps_at_one():
    assert holm_adjust([0.9, 0.95]) == pytest.approx([1.0, 1.0])


def test_holm_adjusted_values_are_non_decreasing():
    # Monotonicity as a property, not a spot check: adjusted values must be
    # non-decreasing in sorted order on every seeded family. Half the families
    # cluster near zero, where the rank weights most easily flip the order, so
    # dropping the cumulative maximum turns this red.
    rng = random.Random(3216)
    for _ in range(200):
        m = rng.randint(0, 10)
        if rng.random() < 0.5:
            family = [rng.random() * 0.05 for _ in range(m)]
        else:
            family = [rng.choice([rng.random(), 0.0, 1.0]) for _ in range(m)]
        adjusted = holm_adjust(family)
        in_sorted_order = [adjusted[i] for i, _ in sort_ascending(family)]
        assert all(earlier <= later for earlier, later in zip(in_sorted_order, in_sorted_order[1:]))


def test_holm_three_named_operations_compose_to_the_worked_example():
    ordered = sort_ascending([0.01, 0.04, 0.03])
    assert [p for _, p in ordered] == pytest.approx([0.01, 0.03, 0.04])
    assert [i for i, _ in ordered] == [0, 2, 1]
    assert multiply_by_rank_weight([p for _, p in ordered]) == pytest.approx([0.03, 0.06, 0.04])
    assert cumulative_maximum_clamped([0.03, 0.06, 0.04]) == pytest.approx([0.03, 0.06, 0.06])


@pytest.mark.parametrize(
    "bad",
    [-0.1, 1.5, 2, -1, None, math.nan, float("nan"), float("inf"), float("-inf")],
)
def test_holm_adjust_rejects_malformed_p_values(bad):
    with pytest.raises(ValueError):
        holm_adjust([0.01, bad, 0.03])


def test_fixed_batch_stopping_rule_describe_names_correction_and_rule():
    rule = FixedBatchStoppingRule(batch_size=40, n_endpoints=3)
    assert rule.describe() == "holm; fixed-batch(n=40, endpoints=3)"


@pytest.mark.parametrize("bad_size", [0, -1, -40])
def test_fixed_batch_stopping_rule_rejects_non_positive_batch(bad_size):
    with pytest.raises(ValueError):
        FixedBatchStoppingRule(batch_size=bad_size, n_endpoints=3)


@pytest.mark.parametrize("bad_count", [0, -2])
def test_fixed_batch_stopping_rule_rejects_non_positive_endpoints(bad_count):
    with pytest.raises(ValueError):
        FixedBatchStoppingRule(batch_size=40, n_endpoints=bad_count)


def test_fixed_batch_stopping_rule_completeness_and_shortfall():
    rule = FixedBatchStoppingRule(batch_size=40, n_endpoints=3)
    assert not rule.is_complete(39)
    assert rule.is_complete(40)
    assert rule.is_complete(41)
    assert rule.shortfall(39) == 1
    assert rule.shortfall(40) == 0
    assert rule.shortfall(41) == 0
