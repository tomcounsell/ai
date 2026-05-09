"""Tests for unconditional `repair_indexes()` invocation in `agent-session-cleanup`.

Issue #1361: PR #1078 introduced a `cleaned > 0 or phantoms_filtered > 0` gate
on `repair_indexes()`. Stale `$IndexF:AgentSession:status:*` members whose
underlying hash exists but whose status field disagrees with the index segment
(e.g., `waiting_for_children` index member pointing at a hash whose status is
`killed`) never tripped the gate, so they persisted indefinitely.

This module verifies:
1. `repair_indexes()` runs on every tick — even on a clean DB.
2. Per-status drift counts are emitted as `agent_session.indexed_field.stale_members`
   metrics with `dimensions={"status": <status>}`.
3. Metric-emission failure (RuntimeError from `record_metric`) does NOT abort
   the cleanup function.
4. Pre-scan failure (KEYS raises) is logged as WARNING but does NOT prevent
   `repair_indexes()` from running.
"""

from __future__ import annotations

import logging
from unittest.mock import patch


class TestRepairIndexesRunsUnconditionally:
    def test_repair_indexes_called_when_no_corruption(self, caplog):
        """Empty Redis: cleanup is invoked; `repair_indexes()` is still called."""
        from agent.session_health import cleanup_corrupted_agent_sessions
        from models.agent_session import AgentSession

        invoked = {"count": 0}
        original = AgentSession.repair_indexes

        def _spy():
            invoked["count"] += 1
            return original()

        with patch.object(AgentSession, "repair_indexes", staticmethod(_spy)):
            with caplog.at_level(logging.INFO, logger="agent.session_health"):
                result = cleanup_corrupted_agent_sessions()

        assert isinstance(result, dict)
        assert result["corrupted"] == 0
        assert invoked["count"] == 1, (
            "After #1361 the gate is removed; repair_indexes must run unconditionally"
        )


class TestPerStatusMetricEmission:
    def test_drift_member_emits_status_dimensioned_metric(self, monkeypatch):
        """A status-drift member triggers `record_metric` with `dimensions={"status": ...}`."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from agent.session_health import cleanup_corrupted_agent_sessions
        from models.agent_session import AgentSession

        # Real session with status="killed". Saving establishes its hash and
        # adds it to the "killed" index naturally.
        sess = AgentSession(
            session_id="drift-1361-a",
            project_key="test-1361",
            status="killed",
        )
        sess.save()
        real_key = sess._redis_key

        # Inject the same hash key into the "waiting_for_children" index. The
        # hash exists, but its status field says "killed" — this is the exact
        # drift mode #1361 targets.
        POPOTO_REDIS_DB.sadd("$IndexF:AgentSession:status:waiting_for_children", real_key)

        captured: list[tuple] = []

        def _capture(name, value, dimensions=None):
            captured.append((name, value, dimensions))

        monkeypatch.setattr("agent.session_health.record_metric", _capture)

        result = cleanup_corrupted_agent_sessions()

        assert isinstance(result, dict)
        # The drift metric must have been emitted at least once for waiting_for_children.
        drift_calls = [
            c
            for c in captured
            if c[0] == "agent_session.indexed_field.stale_members"
            and c[2] == {"status": "waiting_for_children"}
        ]
        assert drift_calls, (
            f"Expected stale-members metric for waiting_for_children; got: {captured}"
        )
        # Value must be a positive count.
        assert drift_calls[0][1] >= 1


class TestFailureResilience:
    def test_metric_emission_failure_does_not_abort_cleanup(self, monkeypatch, caplog):
        """If `record_metric` raises, cleanup still returns its dict contract."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from agent.session_health import cleanup_corrupted_agent_sessions
        from models.agent_session import AgentSession

        sess = AgentSession(
            session_id="drift-1361-b",
            project_key="test-1361",
            status="killed",
        )
        sess.save()
        real_key = sess._redis_key
        POPOTO_REDIS_DB.sadd("$IndexF:AgentSession:status:waiting_for_children", real_key)

        def _raise(name, value, dimensions=None):
            raise RuntimeError("simulated analytics outage")

        monkeypatch.setattr("agent.session_health.record_metric", _raise)

        # Must not propagate the exception.
        with caplog.at_level(logging.WARNING, logger="agent.session_health"):
            result = cleanup_corrupted_agent_sessions()

        assert isinstance(result, dict)
        assert "corrupted" in result and "orphans" in result

    def test_pre_scan_failure_logged_but_not_fatal(self, monkeypatch, caplog):
        """If the pre-scan KEYS call raises, cleanup logs WARNING and proceeds."""
        from agent.session_health import cleanup_corrupted_agent_sessions
        from models.agent_session import AgentSession

        # Patch *only* the pre-scan helper to raise. We don't want to break
        # repair_indexes()'s own KEYS call.
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated KEYS outage during pre-scan")

        monkeypatch.setattr("agent.session_health._count_per_status_stale_index_members", _boom)

        # repair_indexes() must still be invoked.
        invoked = {"count": 0}
        original = AgentSession.repair_indexes

        def _spy():
            invoked["count"] += 1
            return original()

        with patch.object(AgentSession, "repair_indexes", staticmethod(_spy)):
            with caplog.at_level(logging.WARNING, logger="agent.session_health"):
                result = cleanup_corrupted_agent_sessions()

        assert isinstance(result, dict)
        assert invoked["count"] == 1, (
            "Pre-scan failure must NOT prevent repair_indexes() from running"
        )
        # WARNING must be logged.
        warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "pre-scan" in r.message.lower() or "stale index" in r.message.lower()
            for r in warning_logs
        ), f"Expected WARNING about pre-scan failure; got: {[r.message for r in warning_logs]}"


class TestUnknownStatusCardinalityGuard:
    def test_unknown_status_segment_maps_to_unknown_dimension(self, monkeypatch, caplog):
        """If an index key segment is not in `ALL_STATUSES`, dimension = 'unknown'."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from agent.session_health import cleanup_corrupted_agent_sessions
        from models.agent_session import AgentSession

        sess = AgentSession(
            session_id="drift-1361-c",
            project_key="test-1361",
            status="killed",
        )
        sess.save()
        real_key = sess._redis_key

        # Inject a member into a bogus status segment that isn't a known status.
        POPOTO_REDIS_DB.sadd("$IndexF:AgentSession:status:not-a-real-status-xyz", real_key)

        captured: list[tuple] = []

        def _capture(name, value, dimensions=None):
            captured.append((name, value, dimensions))

        monkeypatch.setattr("agent.session_health.record_metric", _capture)

        with caplog.at_level(logging.WARNING, logger="agent.session_health"):
            cleanup_corrupted_agent_sessions()

        # The bogus segment should have been mapped to "unknown".
        unknown_calls = [
            c
            for c in captured
            if c[0] == "agent_session.indexed_field.stale_members" and c[2] == {"status": "unknown"}
        ]
        assert unknown_calls, (
            f"Expected dimension status='unknown' for bogus segment; got: {captured}"
        )
        # WARNING with the actual segment value should be logged.
        warning_text = " ".join(r.message for r in caplog.records)
        assert "not-a-real-status-xyz" in warning_text, (
            f"Expected WARNING naming the bogus segment; got: {warning_text}"
        )
