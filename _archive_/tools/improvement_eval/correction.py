"""Holm step-down correction and the fixed-batch stopping rule.

Holm's adjusted p-values are three named operations on the ascending-sorted
p-values: sort ascending, multiply each by its rank weight ``(m - j + 1)``,
then take the cumulative maximum clamped at 1.0, and map the results back to
the original input order. The cumulative maximum is the known defect site: it
is what enforces monotonicity, and implementations that skip it have shipped
as real bugs, so the test suite pins monotonicity as a property rather than
only spot-checking a worked example.

The stopping rule is fixed-batch only. The batch size and endpoint count are
declared up front, and ``describe`` renders the exact string written to
``ImprovementEvaluation.correction``. A run short of its declared batch
yields ``inconclusive`` and never computes a verdict from a partial batch.

Pure Python; only the standard library.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _coerce_p_value(value: float | None, index: int) -> float:
    """Validate one p-value, returning it as a float."""
    if value is None:
        raise ValueError(f"p-value at index {index} is None")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"p-value at index {index} is not numeric: {value!r}") from None
    if math.isnan(number):
        raise ValueError(f"p-value at index {index} is NaN")
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"p-value at index {index} is outside [0, 1]: {value!r}")
    return number


def sort_ascending(p_values: Sequence[float]) -> list[tuple[int, float]]:
    """Operation 1: sort ascending, keeping each value's original index."""
    return sorted(enumerate(p_values), key=lambda pair: pair[1])


def multiply_by_rank_weight(sorted_p_values: Sequence[float]) -> list[float]:
    """Operation 2: multiply the j-th sorted value (1-based) by ``(m - j + 1)``."""
    m = len(sorted_p_values)
    return [p * (m - j) for j, p in enumerate(sorted_p_values)]


def cumulative_maximum_clamped(weighted: Sequence[float]) -> list[float]:
    """Operation 3: running maximum over the weighted values, clamped at 1.0."""
    adjusted: list[float] = []
    running = 0.0
    for value in weighted:
        running = max(running, value)
        adjusted.append(min(1.0, running))
    return adjusted


def holm_adjust(p_values: Sequence[float | None]) -> list[float]:
    """Holm-adjusted p-values in the original input order.

    Raises ``ValueError`` on a value outside ``[0, 1]``, on ``None``, and on
    ``NaN``: a correction computed from a malformed input is worse than a
    refusal.
    """
    values = [_coerce_p_value(p, i) for i, p in enumerate(p_values)]
    if not values:
        return []
    ordered = sort_ascending(values)
    weighted = multiply_by_rank_weight([p for _, p in ordered])
    adjusted_sorted = cumulative_maximum_clamped(weighted)
    adjusted = [0.0] * len(values)
    for (index, _), adj in zip(ordered, adjusted_sorted):
        adjusted[index] = adj
    return adjusted


class FixedBatchStoppingRule:
    """A declared fixed batch: ``batch_size`` trials over ``n_endpoints`` endpoints."""

    def __init__(self, batch_size: int, n_endpoints: int) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError(f"batch_size must be a positive int, got {batch_size!r}")
        if isinstance(n_endpoints, bool) or not isinstance(n_endpoints, int) or n_endpoints <= 0:
            raise ValueError(f"n_endpoints must be a positive int, got {n_endpoints!r}")
        self.batch_size = batch_size
        self.n_endpoints = n_endpoints

    def describe(self) -> str:
        """The exact string written to ``ImprovementEvaluation.correction``."""
        return f"holm; fixed-batch(n={self.batch_size}, endpoints={self.n_endpoints})"

    def is_complete(self, n_completed: int) -> bool:
        """True once the declared batch has run in full."""
        return n_completed >= self.batch_size

    def shortfall(self, n_completed: int) -> int:
        """How many trials are still missing; zero once the batch is complete."""
        return max(0, self.batch_size - n_completed)
