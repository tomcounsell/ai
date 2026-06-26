"""Unit tests for the subscribe-time NUMSUB self-check in _listen_in_thread.

Verifies three cases (all with mocked Redis — no real Redis required):
  1. Happy path  — NUMSUB >= 1 → listener proceeds to pubsub.listen()
  2. NUMSUB == 0 — listener returns early, WARNING logged, teardown runs
  3. pubsub_numsub raises — listener returns early without crashing, WARNING logged,
     teardown runs

All tests exercise _listen_in_thread indirectly by running one cycle of the
outer _session_notify_listener coroutine with a controlled mock environment.
This mirrors the integration-test pattern in tests/integration/test_session_notify.py
so the full call path (asyncio.to_thread → _listen_in_thread) is exercised.
"""

import asyncio
import logging
from unittest.mock import MagicMock, patch

from agent.agent_session_queue import _session_notify_listener

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_one_listener_cycle(mock_conn: MagicMock, mock_pubsub: MagicMock) -> None:
    """Run _session_notify_listener for one cycle, then cancel."""

    mock_popoto_redis = MagicMock()
    mock_popoto_redis.connection_pool.connection_kwargs = {
        "host": "localhost",
        "port": 6379,
        "db": 0,
    }

    import redis as _redis_module

    async def _run():
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", mock_popoto_redis),
            patch.object(_redis_module, "Redis", return_value=mock_conn),
        ):
            task = asyncio.create_task(_session_notify_listener())
            # Give the thread time to run and the coroutine to react
            await asyncio.sleep(0.4)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNUMSUBSelfCheck:
    """NUMSUB self-check cases for _listen_in_thread."""

    def test_numsub_ok_proceeds_to_listen(self):
        """Happy path: NUMSUB >= 1 → pubsub.listen() is called."""
        mock_pubsub = MagicMock()
        # listen() returns an empty iterator so the thread exits cleanly
        mock_pubsub.listen.return_value = iter([])

        mock_conn = MagicMock()
        mock_conn.pubsub.return_value = mock_pubsub
        # NUMSUB reports 1 subscriber
        mock_conn.pubsub_numsub.return_value = {"valor:sessions:new": 1}

        _run_one_listener_cycle(mock_conn, mock_pubsub)

        # listen() must have been called — listener did NOT bail out early
        mock_pubsub.listen.assert_called_once()

    def test_numsub_zero_returns_early_with_warning(self, caplog):
        """NUMSUB == 0 for all 3 attempts: returns early, WARNING emitted, teardown runs."""
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])

        mock_conn = MagicMock()
        mock_conn.pubsub.return_value = mock_pubsub
        # NUMSUB always reports 0
        mock_conn.pubsub_numsub.return_value = {"valor:sessions:new": 0}

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            _run_one_listener_cycle(mock_conn, mock_pubsub)

        # pubsub.listen() must NOT have been called — we returned early
        mock_pubsub.listen.assert_not_called()

        # A WARNING mentioning NUMSUB must have been logged
        numsub_warnings = [r for r in caplog.records if "NUMSUB" in r.message]
        assert numsub_warnings, (
            "Expected at least one WARNING log containing 'NUMSUB', got none. "
            f"All records: {[r.message for r in caplog.records]}"
        )

        # Teardown must have run — unsubscribe() called
        mock_pubsub.unsubscribe.assert_called()

    def test_numsub_raises_returns_early_with_warning(self, caplog):
        """pubsub_numsub() raises: returns early without crashing, WARNING logged, teardown runs."""
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])

        mock_conn = MagicMock()
        mock_conn.pubsub.return_value = mock_pubsub
        # NUMSUB raises a Redis error
        mock_conn.pubsub_numsub.side_effect = Exception("simulated Redis error")

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            # Must not raise
            _run_one_listener_cycle(mock_conn, mock_pubsub)

        # pubsub.listen() must NOT have been called
        mock_pubsub.listen.assert_not_called()

        # A WARNING must have been logged (either NUMSUB or NUMSUB-raised path)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, (
            "Expected at least one WARNING log when pubsub_numsub raises, got none. "
            f"All records: {[r.message for r in caplog.records]}"
        )

        # Teardown must have run
        mock_pubsub.unsubscribe.assert_called()
