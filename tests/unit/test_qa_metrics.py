"""Tests for Teammate metrics tracking."""

from unittest.mock import MagicMock, patch

from agent.teammate_metrics import get_stats, record_classification, record_response_time


class TestRecordClassification:
    def test_teammate_high_confidence(self):
        mock_redis = MagicMock()
        with patch("agent.teammate_metrics._get_redis", return_value=mock_redis):
            record_classification("teammate", 0.95)
            mock_redis.incr.assert_called_once_with("teammate_metrics:teammate_classified_count")

    def test_teammate_low_confidence(self):
        mock_redis = MagicMock()
        with patch("agent.teammate_metrics._get_redis", return_value=mock_redis):
            record_classification("teammate", 0.80)
            mock_redis.incr.assert_called_once_with(
                "teammate_metrics:teammate_low_confidence_count"
            )

    def test_work_classification(self):
        mock_redis = MagicMock()
        with patch("agent.teammate_metrics._get_redis", return_value=mock_redis):
            record_classification("work", 0.99)
            mock_redis.incr.assert_called_once_with("teammate_metrics:work_classified_count")

    def test_no_redis_does_not_crash(self):
        with patch("agent.teammate_metrics._get_redis", return_value=None):
            record_classification("teammate", 0.95)  # Should not raise

    def test_redis_error_does_not_crash(self):
        mock_redis = MagicMock()
        mock_redis.incr.side_effect = RuntimeError("connection lost")
        with patch("agent.teammate_metrics._get_redis", return_value=mock_redis):
            record_classification("teammate", 0.95)  # Should not raise


class TestRecordResponseTime:
    def test_records_time(self):
        mock_redis = MagicMock()
        with patch("agent.teammate_metrics._get_redis", return_value=mock_redis):
            record_response_time("teammate", 1.5)
            mock_redis.zadd.assert_called_once()
            mock_redis.zremrangebyrank.assert_called_once()

    def test_no_redis_does_not_crash(self):
        with patch("agent.teammate_metrics._get_redis", return_value=None):
            record_response_time("teammate", 1.5)  # Should not raise


class TestGetStats:
    def test_returns_counts(self):
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda key: {
            "teammate_metrics:teammate_classified_count": "10",
            "teammate_metrics:work_classified_count": "20",
            "teammate_metrics:teammate_low_confidence_count": "5",
        }.get(key, "0")

        with patch("agent.teammate_metrics._get_redis", return_value=mock_redis):
            stats = get_stats()
            assert stats["teammate_classified"] == 10
            assert stats["work_classified"] == 20
            assert stats["teammate_low_confidence"] == 5
            assert stats["total"] == 35

    def test_no_redis_returns_empty(self):
        with patch("agent.teammate_metrics._get_redis", return_value=None):
            stats = get_stats()
            assert stats == {}
