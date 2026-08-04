"""Tests for the dashboard analytics summary (`ui/data/analytics.py`).

Cost and turn totals derive from `AgentSession.total_cost_usd`/`turn_count`
rather than the metrics ledger (issue #1245): the ledger's `session.cost_usd`
emit sites lived in the in-process SDK path, which production never runs.

Totals come from the enumeration seam's class-set scan rather than the `status`
secondary index, which under-reports and made those totals silently low
(issue #2519). The scan is the expensive part of the summary, so
`get_analytics_summary` pays for it once: the 1d window is a strict subset of
the 7d window, and both are cut from the one result. #2122 is the precedent for
watching dashboard fan-out cost.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]


def _completed(hours_ago: float, cost: float, turns: int):
    """A completed AgentSession stand-in carrying just the summed fields."""
    return SimpleNamespace(
        status="completed",
        completed_at=datetime.now(tz=UTC) - timedelta(hours=hours_ago),
        total_cost_usd=cost,
        turn_count=turns,
    )


@pytest.fixture
def scan():
    """Patch the enumeration seam and record every call it receives."""

    def _install(sessions):
        calls = []

        def _enumerate(statuses=None, **kwargs):
            calls.append(statuses)
            return list(sessions)

        patcher = patch("models.session_enumeration.enumerate_sessions", _enumerate)
        patcher.start()
        return calls, patcher

    installed = []

    def _factory(sessions):
        calls, patcher = _install(sessions)
        installed.append(patcher)
        return calls

    yield _factory
    for patcher in installed:
        patcher.stop()


@pytest.fixture(autouse=True)
def no_metrics_ledger():
    """Keep the SQLite ledger out of it; these tests are about the scan."""
    with patch("analytics.query.query_metric_count", return_value=0):
        yield


class TestScanCount:
    def test_summary_scans_the_class_set_once(self, scan):
        from ui.data.analytics import get_analytics_summary

        calls = scan([])
        get_analytics_summary()
        assert len(calls) == 1

    def test_the_one_scan_is_narrowed_to_completed_sessions(self, scan):
        from ui.data.analytics import get_analytics_summary

        calls = scan([])
        get_analytics_summary()
        assert list(calls[0]) == ["completed"]


class TestWindowPartition:
    def test_today_is_a_subset_of_the_week(self, scan):
        from ui.data.analytics import get_analytics_summary

        scan([_completed(1, 1.50, 2), _completed(72, 4.00, 8)])
        summary = get_analytics_summary()

        assert summary["cost_today_usd"] == pytest.approx(1.50)
        assert summary["turns_today"] == pytest.approx(2.0)
        assert summary["cost_7d_usd"] == pytest.approx(5.50)
        assert summary["turns_7d"] == pytest.approx(10.0)

    def test_sessions_older_than_the_week_are_excluded(self, scan):
        from ui.data.analytics import get_analytics_summary

        scan([_completed(24 * 30, 9.99, 40)])
        summary = get_analytics_summary()

        assert summary["cost_7d_usd"] == pytest.approx(0.0)
        assert summary["turns_7d"] == pytest.approx(0.0)

    def test_a_record_with_an_unreadable_timestamp_is_skipped(self, scan):
        """One malformed record leaves the rest of the window intact."""
        from ui.data.analytics import get_analytics_summary

        broken = SimpleNamespace(
            status="completed", completed_at="yesterday", total_cost_usd=1, turn_count=1
        )
        scan([broken, _completed(1, 2.00, 3)])
        summary = get_analytics_summary()

        assert summary["cost_today_usd"] == pytest.approx(2.00)

    def test_scan_failure_yields_zeros(self):
        from ui.data.analytics import get_analytics_summary

        with patch(
            "models.session_enumeration.enumerate_sessions",
            side_effect=RuntimeError("redis down"),
        ):
            summary = get_analytics_summary()

        assert summary["cost_today_usd"] == 0.0
        assert summary["cost_7d_usd"] == 0.0

    @pytest.mark.parametrize("days", [0, -1])
    def test_a_non_positive_window_holds_nothing(self, days):
        from ui.data.analytics import _completed_within

        assert _completed_within([_completed(1, 1.0, 1)], days=days) == []


class TestSums:
    def test_empty_input_sums_to_zero(self):
        from ui.data.analytics import _sum_cost_and_turns

        assert _sum_cost_and_turns([]) == (0.0, 0)

    def test_a_record_contributes_every_field_it_can_parse(self):
        """A garbled `turn_count` costs the row its turns, not its dollars."""
        from ui.data.analytics import _sum_cost_and_turns

        rows = [
            SimpleNamespace(total_cost_usd=1.0, turn_count=2),
            SimpleNamespace(total_cost_usd="not-a-number", turn_count=3),
            SimpleNamespace(total_cost_usd=2.5, turn_count="not-an-int"),
            SimpleNamespace(total_cost_usd=None, turn_count=None),
            SimpleNamespace(total_cost_usd=0.5, turn_count=1),
        ]
        # cost: 1.0 + 2.5 + 0.5. turns: 2 + 1, with None coerced through `or 0`.
        assert _sum_cost_and_turns(rows) == (4.0, 3)
