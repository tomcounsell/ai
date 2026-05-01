"""Unit tests for issue #1245 — Popoto-derived analytics aggregation.

Validates the small helpers `_query_completed_sessions_in_window` and
`_sum_cost_and_turns` in `ui/data/analytics.py`. These replace the
metrics-ledger `query_metric_total("session.cost_usd"|"session.turns")`
emit sites that were unreachable in production (worker uses harness
path; the ledger emits lived inside the in-process SDK path).
"""

from __future__ import annotations

from unittest.mock import patch


def test_query_session_sums_empty():
    """Empty AgentSession query → (0.0, 0)."""
    from ui.data.analytics import _sum_cost_and_turns

    assert _sum_cost_and_turns([]) == (0.0, 0)


def test_query_session_sums_skips_invalid_records():
    """Records with non-numeric fields are skipped, not raised on."""
    from ui.data.analytics import _sum_cost_and_turns

    class _Row:
        def __init__(self, cost, turns):
            self.total_cost_usd = cost
            self.turn_count = turns

    rows = [
        _Row(1.0, 2),
        _Row("not-a-number", 3),  # raises in float()
        _Row(2.5, "not-an-int"),  # raises in int()
        _Row(None, None),  # `or 0` defaults
        _Row(0.5, 1),
    ]
    cost, turns = _sum_cost_and_turns(rows)
    # Only the valid rows survive: 1.0 + 0.5 + 0.5 = 4.0; turns 2 + 1 + 0 = 3 (None coerced)
    assert cost == 4.0
    assert turns == 3


def test_query_session_sums_popoto_failure_returns_empty(monkeypatch):
    """A Popoto exception → returns [], no raise."""
    from ui.data.analytics import _query_completed_sessions_in_window

    def boom(*a, **kw):
        raise RuntimeError("redis down")

    # Patch the AgentSession.query.filter at its source so the helper hits boom.
    with patch("models.agent_session.AgentSession.query.filter", side_effect=boom):
        result = _query_completed_sessions_in_window(days=7)
    assert result == []


def test_query_session_sums_zero_days_returns_empty():
    """days=0 short-circuits to [] without touching Popoto."""
    from ui.data.analytics import _query_completed_sessions_in_window

    assert _query_completed_sessions_in_window(days=0) == []


def test_query_session_sums_negative_days_returns_empty():
    """Negative days short-circuits to [] without touching Popoto."""
    from ui.data.analytics import _query_completed_sessions_in_window

    assert _query_completed_sessions_in_window(days=-1) == []
