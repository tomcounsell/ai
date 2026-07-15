"""Unit tests for agent/index_drift.py (AgentSession index-drift reconciliation).

Real Redis, no mocks (per this repo's testing philosophy) except where a test
specifically needs to simulate a failure mode (query.all() raising, or a
forced SCAN truncation) that cannot be reproduced with real data.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession

_PROJECT_PREFIX = "test-index-drift-"


def _make_session() -> AgentSession:
    """Create and save a real AgentSession under a test-scoped project_key."""
    session = AgentSession(
        chat_id=f"idx-drift-{uuid.uuid4().hex[:8]}",
        project_key=f"{_PROJECT_PREFIX}{uuid.uuid4().hex[:8]}",
        working_dir="/tmp/test-index-drift",
    )
    session.save()
    return session


@pytest.fixture
def cleanup_sessions():
    """Track sessions created during a test and delete them afterward via the ORM."""
    created: list[AgentSession] = []
    yield created
    for s in created:
        try:
            s.delete()
        except Exception:
            pass


class TestReconcileNoDrift:
    def test_equal_counts_no_drift_no_error_no_sentry(self, cleanup_sessions):
        from agent.index_drift import reconcile_agent_session_index

        session = _make_session()
        cleanup_sessions.append(session)

        with (
            patch("agent.index_drift.logger") as mock_logger,
            patch("sentry_sdk.capture_message") as mock_sentry,
        ):
            hash_count, queryable_count, drifted, truncated = reconcile_agent_session_index()

        assert hash_count >= 1
        assert queryable_count >= 1
        assert truncated is False
        assert drifted is False
        mock_logger.error.assert_not_called()
        mock_sentry.assert_not_called()


class TestReconcileDrift:
    def test_hash_exceeds_queryable_is_drift(self, cleanup_sessions):
        """Simulate hash_count > queryable_count by patching the SCAN counter
        to report more hashes than query.all() sees for real -- this reproduces
        the 2026-07-14 incident shape (hashes present, index blind to them)
        without needing to actually corrupt Popoto's index."""
        from agent.index_drift import reconcile_agent_session_index

        session = _make_session()
        cleanup_sessions.append(session)

        real_count = len(AgentSession.query.all())
        inflated_count = real_count + 5

        with (
            patch(
                "agent.index_drift._count_agentsession_hashes",
                return_value=(inflated_count, True),
            ),
            patch("sentry_sdk.capture_message") as mock_sentry,
        ):
            hash_count, queryable_count, drifted, truncated = reconcile_agent_session_index()

        assert hash_count == inflated_count
        assert queryable_count == real_count
        assert drifted is True
        assert truncated is False
        mock_sentry.assert_called_once()
        _, kwargs = mock_sentry.call_args
        assert kwargs.get("level") == "error"
        call_message = mock_sentry.call_args[0][0]
        assert str(inflated_count) in call_message
        assert str(real_count) in call_message


class TestCappedListExclusion:
    def test_companion_key_does_not_inflate_hash_count(self, cleanup_sessions):
        """A companion key of shape AgentSession:<key>::somefield must be
        excluded from the raw hash count."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from agent.index_drift import _count_agentsession_hashes

        session = _make_session()
        cleanup_sessions.append(session)

        before_count, _ = _count_agentsession_hashes()

        companion_key = f"AgentSession:{session.id}::somefield"
        POPOTO_REDIS_DB.hset(companion_key, "0", "synthetic-companion-value")
        try:
            after_count, exhaustive = _count_agentsession_hashes()
            assert exhaustive is True
            assert after_count == before_count, (
                "companion `::field` key must not inflate the hash count"
            )
        finally:
            POPOTO_REDIS_DB.delete(companion_key)


class TestTruncatedScan:
    def test_truncated_scan_skips_drift_determination(self, cleanup_sessions):
        """Forcing the iteration cap to 0 means the SCAN loop body never runs,
        so cursor never reaches 0 within the (zero) allotted iterations and the
        scan is reported truncated -- drift must not be computed from the
        resulting (empty, partial) count."""
        from agent import index_drift

        session = _make_session()
        cleanup_sessions.append(session)

        with (
            patch.object(index_drift, "_SCAN_MAX_ITERATIONS", 0),
            patch("agent.index_drift.logger") as mock_logger,
            patch("sentry_sdk.capture_message") as mock_sentry,
        ):
            hash_count, queryable_count, drifted, truncated = (
                index_drift.reconcile_agent_session_index()
            )

        assert truncated is True
        assert drifted is False
        warning_messages = [str(call.args) for call in mock_logger.warning.call_args_list]
        assert any("scan incomplete" in msg for msg in warning_messages)
        # A truncated scan must never itself trigger the drift ERROR/Sentry path.
        mock_sentry.assert_not_called()


class TestQueryAllRaises:
    def test_query_all_raising_is_caught_and_surfaced_loudly(self, cleanup_sessions):
        """reconcile must catch query.all() raising internally and fire the
        loud ERROR + Sentry itself -- asserted with NO outer try/except around
        the call, proving surfacing does not depend on any caller."""
        from agent.index_drift import reconcile_agent_session_index

        session = _make_session()
        cleanup_sessions.append(session)

        with (
            patch.object(AgentSession.query, "all", side_effect=RuntimeError("corrupt hash")),
            patch("agent.index_drift.logger") as mock_logger,
            patch("sentry_sdk.capture_message") as mock_sentry,
        ):
            # No try/except here on purpose -- if reconcile let the exception
            # propagate, this test itself would fail with an error (not a
            # clean assertion failure), proving the exception never escapes.
            hash_count, queryable_count, drifted, truncated = reconcile_agent_session_index()

        assert drifted is True
        assert queryable_count == 0
        mock_logger.error.assert_called_once()
        mock_sentry.assert_called_once()
        _, kwargs = mock_sentry.call_args
        assert kwargs.get("level") == "error"


class TestSentryNoiseFilterNonSuppression:
    def test_drift_message_prefix_not_suppressed_by_drop_orphan_noise(self):
        """The [index-drift] AgentSession prefix must never be caught by
        drop_orphan_noise -- that filter only targets the benign Popoto
        orphan-index diagnostic (issue #1835), and must pass this message
        through unchanged."""
        from agent.index_drift import _LOG_PREFIX
        from monitoring.sentry_config import _ORPHAN_NOISE_SUBSTRING, drop_orphan_noise

        assert _ORPHAN_NOISE_SUBSTRING not in _LOG_PREFIX

        event = {
            "logentry": {
                "formatted": f"{_LOG_PREFIX} drift detected: hash_count=11 > "
                f"queryable_count=0 (tolerance=0)",
            },
            "message": "",
        }
        result = drop_orphan_noise(event, hint=None)
        assert result is event, "drift message must NOT be dropped by drop_orphan_noise"


class TestToleranceConstant:
    def test_default_tolerance_is_zero(self):
        import importlib

        from agent import index_drift

        importlib.reload(index_drift)
        assert index_drift.AGENTSESSION_INDEX_DRIFT_TOLERANCE == 0
