"""Tests for Teammate metrics tracking (Popoto-backed)."""

from unittest.mock import MagicMock, patch

from agent.teammate_metrics import get_stats, record_classification, record_response_time


class TestRecordClassification:
    def test_teammate_high_confidence(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_classified_count = 5
        mock_metrics.teammate_low_confidence_count = 2
        mock_metrics.work_classified_count = 3
        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            record_classification("teammate", 0.95)
            assert mock_metrics.teammate_classified_count == 6
            mock_metrics.save.assert_called_once()

    def test_teammate_low_confidence(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_classified_count = 5
        mock_metrics.teammate_low_confidence_count = 2
        mock_metrics.work_classified_count = 3
        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            record_classification("teammate", 0.80)
            assert mock_metrics.teammate_low_confidence_count == 3
            mock_metrics.save.assert_called_once()

    def test_work_classification(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_classified_count = 5
        mock_metrics.teammate_low_confidence_count = 2
        mock_metrics.work_classified_count = 3
        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            record_classification("work", 0.99)
            assert mock_metrics.work_classified_count == 4
            mock_metrics.save.assert_called_once()

    def test_no_metrics_does_not_crash(self):
        with patch("agent.teammate_metrics._get_metrics", return_value=None):
            record_classification("teammate", 0.95)  # Should not raise

    def test_metrics_error_does_not_crash(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_classified_count = 0
        mock_metrics.save.side_effect = RuntimeError("connection lost")
        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            record_classification("teammate", 0.95)  # Should not raise

    def test_empty_intent_does_not_crash(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_classified_count = 0
        mock_metrics.teammate_low_confidence_count = 0
        mock_metrics.work_classified_count = 0
        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            record_classification("", 0.5)  # Should not raise
            assert mock_metrics.work_classified_count == 1


class TestRecordResponseTime:
    def test_records_teammate_time(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_response_times = {}
        mock_metrics.work_response_times = {}
        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            record_response_time("teammate", 1.5)
            mock_metrics.save.assert_called_once()

    def test_records_work_time(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_response_times = {}
        mock_metrics.work_response_times = {}
        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            record_response_time("work", 2.3)
            mock_metrics.save.assert_called_once()

    def test_no_metrics_does_not_crash(self):
        with patch("agent.teammate_metrics._get_metrics", return_value=None):
            record_response_time("teammate", 1.5)  # Should not raise


class TestGetStats:
    def test_returns_counts(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_classified_count = 10
        mock_metrics.work_classified_count = 20
        mock_metrics.teammate_low_confidence_count = 5

        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            stats = get_stats()
            assert stats["teammate_classified"] == 10
            assert stats["work_classified"] == 20
            assert stats["teammate_low_confidence"] == 5
            assert stats["total"] == 35

    def test_no_metrics_returns_empty(self):
        with patch("agent.teammate_metrics._get_metrics", return_value=None):
            stats = get_stats()
            assert stats == {}

    def test_handles_none_counts(self):
        mock_metrics = MagicMock()
        mock_metrics.teammate_classified_count = None
        mock_metrics.work_classified_count = None
        mock_metrics.teammate_low_confidence_count = None

        with patch("agent.teammate_metrics._get_metrics", return_value=mock_metrics):
            stats = get_stats()
            assert stats["teammate_classified"] == 0
            assert stats["work_classified"] == 0
            assert stats["teammate_low_confidence"] == 0
            assert stats["total"] == 0
