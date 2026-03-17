"""Tests for monitoring.telemetry module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from monitoring import telemetry


@pytest.fixture(autouse=True)
def _reset_redis_cache():
    """Reset the cached Redis connection between tests."""
    telemetry._redis_client = None
    yield
    telemetry._redis_client = None


@pytest.fixture
def mock_redis():
    """Provide a mock Redis client."""
    client = MagicMock()
    with patch.object(telemetry, "_get_redis", return_value=client):
        yield client


class TestRecordDecision:
    def test_increments_steer_count(self, mock_redis):
        telemetry.record_decision("sess1", "corr1", "steer", reason="test")
        calls = mock_redis.hincrby.call_args_list
        # Should increment steer_count on main hash and daily hash
        assert any(c[0] == ("telemetry:observer:decisions", "steer_count", 1) for c in calls)

    def test_increments_deliver_count(self, mock_redis):
        telemetry.record_decision("sess1", "corr1", "deliver")
        calls = mock_redis.hincrby.call_args_list
        assert any(c[0] == ("telemetry:observer:decisions", "deliver_count", 1) for c in calls)

    def test_increments_error_count(self, mock_redis):
        telemetry.record_decision("sess1", "corr1", "error")
        calls = mock_redis.hincrby.call_args_list
        assert any(c[0] == ("telemetry:observer:decisions", "error_count", 1) for c in calls)

    def test_daily_key_with_ttl(self, mock_redis):
        telemetry.record_decision("sess1", "corr1", "steer")
        # Should set expire on daily key
        expire_calls = mock_redis.expire.call_args_list
        assert len(expire_calls) >= 1
        daily_key = expire_calls[0][0][0]
        assert daily_key.startswith("telemetry:daily:")
        assert expire_calls[0][0][1] == 604800

    def test_redis_error_does_not_raise(self, mock_redis):
        mock_redis.hincrby.side_effect = Exception("connection lost")
        # Should not raise
        telemetry.record_decision("sess1", "corr1", "steer")


class TestRecordStageTransition:
    def test_increments_completed(self, mock_redis):
        telemetry.record_stage_transition("sess1", "corr1", "build", "in_progress", "completed")
        calls = mock_redis.hincrby.call_args_list
        assert any(c[0] == ("telemetry:pipeline:completions", "build_completed", 1) for c in calls)

    def test_increments_started(self, mock_redis):
        telemetry.record_stage_transition("sess1", "corr1", "test", "pending", "in_progress")
        calls = mock_redis.hincrby.call_args_list
        assert any(c[0] == ("telemetry:pipeline:completions", "test_started", 1) for c in calls)

    def test_daily_rollup(self, mock_redis):
        telemetry.record_stage_transition("sess1", "corr1", "build", "in_progress", "completed")
        expire_calls = mock_redis.expire.call_args_list
        assert len(expire_calls) >= 1

    def test_redis_error_does_not_raise(self, mock_redis):
        mock_redis.hincrby.side_effect = Exception("timeout")
        telemetry.record_stage_transition("sess1", "corr1", "build", "pending", "completed")


class TestRecordToolUse:
    def test_increments_tool_count(self, mock_redis):
        telemetry.record_tool_use("sess1", "corr1", "web_search", duration_ms=150)
        calls = mock_redis.hincrby.call_args_list
        assert any(c[0] == ("telemetry:observer:tool_usage", "web_search", 1) for c in calls)

    def test_redis_error_does_not_raise(self, mock_redis):
        mock_redis.hincrby.side_effect = Exception("broken")
        telemetry.record_tool_use("sess1", "corr1", "web_search")


class TestRecordInterjection:
    def test_lpush_and_ltrim(self, mock_redis):
        telemetry.record_interjection("sess1", "corr1", 5, "steer")
        assert mock_redis.lpush.called
        key = mock_redis.lpush.call_args[0][0]
        assert key == "telemetry:interjections"
        # Verify JSON payload
        payload = json.loads(mock_redis.lpush.call_args[0][1])
        assert payload["session_id"] == "sess1"
        assert payload["correlation_id"] == "corr1"
        assert payload["message_count"] == 5
        assert payload["action"] == "steer"
        assert "timestamp" in payload
        # Should trim to 100
        mock_redis.ltrim.assert_called_once_with("telemetry:interjections", 0, 99)

    def test_redis_error_does_not_raise(self, mock_redis):
        mock_redis.lpush.side_effect = Exception("nope")
        telemetry.record_interjection("sess1", "corr1", 3, "deliver")


class TestGetSummary:
    def test_returns_all_counters(self, mock_redis):
        mock_redis.hgetall.side_effect = [
            {b"steer_count": b"10", b"deliver_count": b"5", b"error_count": b"1"},
            {b"build_completed": b"3"},
            {b"web_search": b"7"},
        ]
        mock_redis.lrange.return_value = [
            json.dumps({"session_id": "s1", "action": "steer"}).encode()
        ]
        result = telemetry.get_summary()
        assert result["decisions"]["steer_count"] == 10
        assert result["decisions"]["deliver_count"] == 5
        assert result["decisions"]["error_count"] == 1
        assert result["pipeline"]["build_completed"] == 3
        assert result["tool_usage"]["web_search"] == 7
        assert len(result["recent_interjections"]) == 1

    def test_returns_zeros_when_no_data(self, mock_redis):
        mock_redis.hgetall.return_value = {}
        mock_redis.lrange.return_value = []
        result = telemetry.get_summary()
        assert result["decisions"] == {}
        assert result["pipeline"] == {}
        assert result["tool_usage"] == {}
        assert result["recent_interjections"] == []

    def test_redis_error_returns_empty(self, mock_redis):
        mock_redis.hgetall.side_effect = Exception("down")
        result = telemetry.get_summary()
        assert result["decisions"] == {}
        assert result["recent_interjections"] == []


class TestCheckObserverHealth:
    def test_ok_status(self, mock_redis):
        mock_redis.hgetall.return_value = {
            b"steer_count": b"90",
            b"deliver_count": b"5",
            b"error_count": b"5",
        }
        result = telemetry.check_observer_health()
        assert result["status"] == "ok"
        assert result["total_decisions"] == 100
        assert abs(result["error_rate"] - 0.05) < 0.001

    def test_degraded_status(self, mock_redis):
        mock_redis.hgetall.return_value = {
            b"steer_count": b"80",
            b"deliver_count": b"5",
            b"error_count": b"15",
        }
        result = telemetry.check_observer_health()
        assert result["status"] == "degraded"

    def test_unhealthy_status(self, mock_redis):
        mock_redis.hgetall.return_value = {
            b"steer_count": b"50",
            b"deliver_count": b"10",
            b"error_count": b"40",
        }
        result = telemetry.check_observer_health()
        assert result["status"] == "unhealthy"

    def test_no_decisions(self, mock_redis):
        mock_redis.hgetall.return_value = {}
        result = telemetry.check_observer_health()
        assert result["status"] == "ok"
        assert result["total_decisions"] == 0
        assert result["error_rate"] == 0.0

    def test_redis_error_returns_unknown(self, mock_redis):
        mock_redis.hgetall.side_effect = Exception("gone")
        result = telemetry.check_observer_health()
        assert result["status"] == "unknown"


class TestGetRedis:
    def test_caches_connection(self):
        with patch("redis.Redis") as mock_cls:
            mock_cls.return_value = MagicMock()
            c1 = telemetry._get_redis()
            c2 = telemetry._get_redis()
            assert c1 is c2
            assert mock_cls.call_count == 1

    def test_connection_params(self):
        with patch("redis.Redis") as mock_cls:
            mock_cls.return_value = MagicMock()
            telemetry._get_redis()
            mock_cls.assert_called_once_with(host="localhost", port=6379, socket_timeout=2)


class TestSetLinkStructuredLogging:
    """Test that set_link() emits structured INFO log lines."""

    def test_set_link_logs_structured_info(self):
        """set_link should emit a structured LINK log at INFO level."""
        from models.agent_session import AgentSession

        session = MagicMock(spec=AgentSession)
        session.session_id = "test-session-123"
        session.correlation_id = "abc123def456"
        session.set_link = AgentSession.set_link.__get__(session, AgentSession)

        with patch("models.agent_session.logger") as mock_logger:
            session.set_link("issue", "https://github.com/foo/bar/issues/1")
            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any("LINK" in c for c in info_calls), (
                f"Expected structured LINK log at INFO level, got: {info_calls}"
            )
            link_call = [c for c in info_calls if "LINK" in c][0]
            assert "session=" in link_call
            assert "correlation=" in link_call
            assert "type=" in link_call or "kind=" in link_call
            assert "url=" in link_call

    def test_set_link_logs_with_correlation_id(self):
        """set_link should include correlation_id in the structured log."""
        from models.agent_session import AgentSession

        session = MagicMock(spec=AgentSession)
        session.session_id = "sess-42"
        session.correlation_id = "corr-99"
        session.set_link = AgentSession.set_link.__get__(session, AgentSession)

        with patch("models.agent_session.logger") as mock_logger:
            session.set_link("pr", "https://github.com/foo/bar/pull/5")
            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            link_calls = [c for c in info_calls if "LINK" in c]
            assert len(link_calls) > 0
            assert "corr-99" in link_calls[0]


class TestHealthCheckerTelemetry:
    """Test that health checker includes observer telemetry."""

    def test_overall_health_includes_observer_check(self):
        """get_overall_health should include an observer telemetry check."""
        from monitoring.health import HealthChecker, HealthCheckResult, HealthStatus

        checker = HealthChecker()
        with (
            patch("monitoring.health.HealthChecker.check_database") as mock_db,
            patch("monitoring.health.HealthChecker.check_telegram_connection") as mock_tg,
            patch("monitoring.health.HealthChecker.check_disk_space") as mock_disk,
            patch("monitoring.health.HealthChecker.check_api_keys") as mock_keys,
            patch("monitoring.telemetry.check_observer_health") as mock_obs,
        ):
            mock_db.return_value = HealthCheckResult(
                component="database", status=HealthStatus.HEALTHY, message="ok"
            )
            mock_tg.return_value = HealthCheckResult(
                component="telegram", status=HealthStatus.HEALTHY, message="ok"
            )
            mock_disk.return_value = HealthCheckResult(
                component="disk_space", status=HealthStatus.HEALTHY, message="ok"
            )
            mock_keys.return_value = {}
            mock_obs.return_value = {
                "status": "ok",
                "error_rate": 0.0,
                "total_decisions": 0,
                "violations": [],
            }

            result = checker.get_overall_health()
            components = [c.component for c in result.checks]
            assert "observer_telemetry" in components, (
                f"Expected observer_telemetry in health checks, got: {components}"
            )
