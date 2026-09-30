"""Tests for the corpus-size anomaly detector (issue #2438).

Covers `reflections/memory/memory_quality_audit.py::_check_corpus_size_baseline`
and `models/memory_corpus_baseline.py::CorpusSizeBaseline`.

Kept in its own file (rather than folded into the large
`tests/unit/test_reflections_memory.py`) to avoid concurrent-edit collisions
with sibling build tasks touching that shared file for the same issue.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def run_async(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def _make_baseline_mock(ring: list[list[float]]):
    """Build a MagicMock standing in for a CorpusSizeBaseline singleton row.

    `ring` is a list of [size, recorded_at] pairs. `max_size()` mirrors the
    real model's behavior; `append_sample()` records calls for assertions.
    """
    baseline = MagicMock()
    baseline.ring = list(ring)
    baseline.max_size.return_value = max(int(size) for size, _ts in ring) if ring else None
    return baseline


class TestCorpusSizeBaselineDetector:
    """Tests for `_check_corpus_size_baseline` (the cross-run anomaly detector)."""

    def test_first_run_no_prior_baseline_initializes_quietly(self):
        """No prior ring + observed >= floor -> initialize, no alert."""
        from reflections.memory.memory_quality_audit import (
            CORPUS_MIN_HEALTHY_FLOOR,
            _check_corpus_size_baseline,
        )

        baseline = _make_baseline_mock(ring=[])

        with (
            patch("models.memory_corpus_baseline.CorpusSizeBaseline") as mock_model,
            patch(
                "reflections.memory.memory_quality_audit._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_model.get_or_create.return_value = baseline
            observed = CORPUS_MIN_HEALTHY_FLOOR + 10
            finding = run_async(_check_corpus_size_baseline(observed))

        mock_file.assert_not_called()
        baseline.append_sample.assert_called_once()
        args, kwargs = baseline.append_sample.call_args
        assert args[0] == observed
        assert "initialized" in finding

    def test_deploy_into_collapse_first_run_below_floor_alerts(self):
        """No prior ring + observed < floor -> file the alert immediately.

        This is the specific guard against installing the detector after a
        collapse (e.g. corpus currently at 1): without it, the baseline
        would silently lock in the collapsed value as "normal".
        """
        from reflections.memory.memory_quality_audit import (
            CORPUS_MIN_HEALTHY_FLOOR,
            _check_corpus_size_baseline,
        )

        baseline = _make_baseline_mock(ring=[])

        with (
            patch("models.memory_corpus_baseline.CorpusSizeBaseline") as mock_model,
            patch(
                "reflections.memory.memory_quality_audit._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_model.get_or_create.return_value = baseline
            mock_file.return_value = True
            observed = 1  # corpus==1, the motivating incident's end state
            assert observed < CORPUS_MIN_HEALTHY_FLOOR
            finding = run_async(_check_corpus_size_baseline(observed))

        mock_file.assert_called_once()
        assert mock_file.call_args.kwargs["signal_name"] == "corpus-size-collapse"
        baseline.append_sample.assert_called_once()
        assert "ALERT FILED" in finding

    def test_drop_beyond_fraction_vs_ring_high_water_alerts(self):
        """Ring high-water mark 100, observed 50 (50% drop > 10% threshold) -> alert.

        Ring also contains an older already-collapsed low sample (5) to prove
        the comparison uses max(ring), not the most recent sample — a single
        scalar "last seen" baseline would compare against 5 and never alert.
        """
        from reflections.memory.memory_quality_audit import (
            CORPUS_DROP_ALERT_FRACTION,
            _check_corpus_size_baseline,
        )

        now = time.time()
        baseline = _make_baseline_mock(ring=[[5, now - 200], [100, now - 100]])

        with (
            patch("models.memory_corpus_baseline.CorpusSizeBaseline") as mock_model,
            patch(
                "reflections.memory.memory_quality_audit._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_model.get_or_create.return_value = baseline
            mock_file.return_value = True
            observed = 50
            drop_fraction = (100 - observed) / 100
            assert drop_fraction > CORPUS_DROP_ALERT_FRACTION
            run_async(_check_corpus_size_baseline(observed))

        mock_file.assert_called_once()
        call_kwargs = mock_file.call_args.kwargs
        assert call_kwargs["signal_name"] == "corpus-size-collapse"
        assert "50" in call_kwargs["observed"]
        assert "100" in call_kwargs["observed"]
        baseline.append_sample.assert_called_once()

    def test_no_significant_drop_updates_baseline_without_alert(self):
        """Ring high-water 100, observed 95 (5% drop, under threshold) -> no alert."""
        from reflections.memory.memory_quality_audit import _check_corpus_size_baseline

        baseline = _make_baseline_mock(ring=[[100, time.time() - 100]])

        with (
            patch("models.memory_corpus_baseline.CorpusSizeBaseline") as mock_model,
            patch(
                "reflections.memory.memory_quality_audit._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_model.get_or_create.return_value = baseline
            observed = 95
            finding = run_async(_check_corpus_size_baseline(observed))

        mock_file.assert_not_called()
        baseline.append_sample.assert_called_once()
        args, _kwargs = baseline.append_sample.call_args
        assert args[0] == observed
        assert "ALERT" not in finding

    def test_empty_observed_corpus_no_divide_by_zero(self):
        """observed=0 against a nonzero ring high-water mark must not raise ZeroDivisionError."""
        from reflections.memory.memory_quality_audit import _check_corpus_size_baseline

        baseline = _make_baseline_mock(ring=[[100, time.time() - 100]])

        with (
            patch("models.memory_corpus_baseline.CorpusSizeBaseline") as mock_model,
            patch(
                "reflections.memory.memory_quality_audit._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
        ):
            mock_model.get_or_create.return_value = baseline
            mock_file.return_value = True
            finding = run_async(_check_corpus_size_baseline(0))

        # Must not raise; must alert given a 100% drop.
        mock_file.assert_called_once()
        assert "ALERT FILED" in finding

    def test_alert_channel_fallback_logs_error_on_gh_failure(self, caplog):
        """When `_file_anomaly_issue` returns False, a logger.error is emitted.

        The signal must not be swallowed at warning level when the alert
        channel itself fails (auth/network) during a real collapse.
        """
        import logging

        from reflections.memory.memory_quality_audit import _check_corpus_size_baseline

        baseline = _make_baseline_mock(ring=[[100, time.time() - 100]])

        with (
            patch("models.memory_corpus_baseline.CorpusSizeBaseline") as mock_model,
            patch(
                "reflections.memory.memory_quality_audit._file_anomaly_issue",
                new_callable=AsyncMock,
            ) as mock_file,
            caplog.at_level(logging.ERROR, logger="reflections.memory_management"),
        ):
            mock_model.get_or_create.return_value = baseline
            mock_file.return_value = False  # simulated gh failure
            run_async(_check_corpus_size_baseline(50))

        assert any(
            record.levelno == logging.ERROR and "corpus-size-collapse" in record.message
            for record in caplog.records
        )

    def test_model_query_failure_returns_finding_without_raising(self):
        """A failure reading/writing the baseline model must not crash the audit run."""
        from reflections.memory.memory_quality_audit import _check_corpus_size_baseline

        with patch("models.memory_corpus_baseline.CorpusSizeBaseline") as mock_model:
            mock_model.get_or_create.side_effect = Exception("redis unavailable")
            finding = run_async(_check_corpus_size_baseline(50))

        assert "failed" in finding.lower()


class TestCorpusSizeBaselineModel:
    """Tests for the `CorpusSizeBaseline` Popoto model's ring semantics."""

    def test_max_size_empty_ring_returns_none(self):
        from models.memory_corpus_baseline import CorpusSizeBaseline

        instance = CorpusSizeBaseline(ring=[])
        assert instance.max_size() is None

    def test_max_size_returns_high_water_mark(self):
        from models.memory_corpus_baseline import CorpusSizeBaseline

        instance = CorpusSizeBaseline(ring=[[5, 1.0], [100, 2.0], [50, 3.0]])
        assert instance.max_size() == 100

    def test_append_sample_caps_ring_size_dropping_oldest(self):
        """Appending beyond the cap drops the oldest entry (ring bounding)."""
        from models.memory_corpus_baseline import CorpusSizeBaseline

        instance = CorpusSizeBaseline(ring=[[1, 1.0], [2, 2.0], [3, 3.0]])
        with patch.object(CorpusSizeBaseline, "save", return_value=None):
            instance.append_sample(4, 4.0, ring_size=3)

        assert instance.ring == [[2, 2.0], [3, 3.0], [4, 4.0]]

    def test_append_sample_json_round_trips_through_list_field(self):
        """Ring entries survive a save/reload round trip via the real Redis-backed field."""
        from models.memory_corpus_baseline import CorpusSizeBaseline

        try:
            instance = CorpusSizeBaseline.get_or_create()
        except Exception as e:
            pytest.skip(f"Redis unavailable for integration-style round trip: {e}")

        try:
            instance.append_sample(42, 12345.0, ring_size=14)
            reloaded = CorpusSizeBaseline.query.filter(key=instance.key)[0]
            assert reloaded.max_size() == 42
            assert any(int(size) == 42 for size, _ts in reloaded.ring)
        finally:
            try:
                instance.delete()
            except Exception:
                pass
