"""Threading tests for BridgeAdapter mid-loop delivery (`_deliver_sync`).

The production shape: `BridgeAdapter.run()` executes on the worker's
asyncio thread, captures the running loop into `self._loop`, then runs
`Container.run` in an `asyncio.to_thread` worker thread. The container's
`on_user_payload` / `on_complete_payload` callbacks fire on THAT thread,
where `asyncio.get_running_loop()` raises. Before the loop-capture fix,
every async send_cb delivery from the pexpect thread was skipped with
reason `no_event_loop` — i.e. 100% of mid-loop Telegram deliveries were
dropped on the bridge path. These tests pin the fixed behavior.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.bridge_adapter import BridgeAdapter
from agent.granite_container.pty_pool import PTYPool


def _fake_session() -> MagicMock:
    session = MagicMock()
    session.chat_id = "42"
    session.telegram_message_id = 7
    session.session_events = []
    return session


def _adapter_with_async_cb(delivered, timeout_s: float = 5.0) -> BridgeAdapter:
    async def send_cb(chat_id, payload, reply_to, agent_session):
        delivered.append((chat_id, payload, reply_to))

    pool = MagicMock(spec=PTYPool)
    return BridgeAdapter(
        agent_session=_fake_session(),
        project_key="valor",
        transport="telegram",
        pool=pool,
        resolve_callbacks=lambda pk, tr: (send_cb, None),
        delivery_timeout_s=timeout_s,
    )


class TestDeliverFromWorkerThread(unittest.TestCase):
    """The production path: callback fires on a no-loop thread."""

    def test_async_send_cb_delivered_from_to_thread(self) -> None:
        delivered: list = []
        adapter = _adapter_with_async_cb(delivered)

        async def _runner():
            adapter._loop = asyncio.get_running_loop()
            # Fire the callback from a worker thread, exactly as
            # Container.run does under asyncio.to_thread.
            await asyncio.to_thread(adapter._on_user_payload, "hello user")

        asyncio.run(_runner())
        self.assertEqual(delivered, [("42", "hello user", 7)])
        # No delivery_failure events were appended.
        events = adapter._agent_session.session_events
        self.assertEqual([e for e in events if e["type"] == "delivery_failure"], [])

    def test_complete_payload_also_delivered(self) -> None:
        delivered: list = []
        adapter = _adapter_with_async_cb(delivered)

        async def _runner():
            adapter._loop = asyncio.get_running_loop()
            await asyncio.to_thread(adapter._on_complete_payload, "all done")

        asyncio.run(_runner())
        self.assertEqual(delivered, [("42", "all done", 7)])

    def test_run_captures_loop_before_container(self) -> None:
        """`run()` must set self._loop on the asyncio thread — the
        callbacks have no other way to reach the loop."""
        pool = MagicMock(spec=PTYPool)
        pm, dev = MagicMock(), MagicMock()

        class _Ctx:
            async def __aenter__(self):
                # acquire_pair yields a 3-tuple (pm, dev, pty_slot) since #1663.
                return (pm, dev, 0)

            async def __aexit__(self, *a):
                return False

        pool.acquire_pair.return_value = _Ctx()
        adapter = BridgeAdapter(
            agent_session=_fake_session(),
            project_key="valor",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda pk, tr: (None, None),
        )
        seen: dict = {}

        fake_result = MagicMock()
        fake_result.exit_reason = "pm_complete"
        fake_result.turns = []
        fake_result.classification_compliance_misses = 0

        def _fake_container(**kwargs):
            container = MagicMock()

            def _run():
                seen["loop_at_container_run"] = adapter._loop
                return fake_result

            container.run = _run
            return container

        with patch(
            "agent.granite_container.bridge_adapter.Container",
            side_effect=_fake_container,
        ):
            asyncio.run(adapter.run("hi", "/tmp"))

        self.assertIsNotNone(seen["loop_at_container_run"])


class TestDeliverFailureModes(unittest.TestCase):
    def test_no_captured_loop_reenqueues_to_outbox(self) -> None:
        """Async send_cb with no captured loop: primary delivery skipped,
        payload re-enqueued to outbox via _enqueue_to_outbox (sync Redis).
        Session event records outcome recovered_via_outbox."""
        delivered: list = []
        adapter = _adapter_with_async_cb(delivered)
        self.assertIsNone(adapter._loop)

        mock_redis = MagicMock()
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_redis):
            adapter._on_user_payload("payload without loop")

        # Primary send_cb was not invoked.
        self.assertEqual(delivered, [])
        # _enqueue_to_outbox was called — Redis rpush received the payload.
        mock_redis.rpush.assert_called_once()
        self.assertIn("telegram:outbox:", mock_redis.rpush.call_args[0][0])
        events = adapter._agent_session.session_events
        failures = [e for e in events if e["type"] == "delivery_failure"]
        self.assertEqual(len(failures), 1)
        # Outbox enqueue succeeded → outcome is recovered, not dropped.
        self.assertEqual(failures[0]["reason"], "recovered_via_outbox")
        self.assertEqual(failures[0]["failure_reason"], "no_event_loop")

    def test_timeout_records_failure_with_exception_type(self) -> None:
        """On timeout, the payload is re-enqueued to the outbox (recovered
        path), not silently dropped. The session event records outcome
        `recovered_via_outbox`."""

        async def slow_cb(chat_id, payload, reply_to, agent_session):
            await asyncio.sleep(5)

        pool = MagicMock(spec=PTYPool)
        adapter = BridgeAdapter(
            agent_session=_fake_session(),
            project_key="valor",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda pk, tr: (slow_cb, None),
            delivery_timeout_s=0.05,
        )

        mock_redis = MagicMock()
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_redis):

            async def _runner():
                adapter._loop = asyncio.get_running_loop()
                await asyncio.to_thread(adapter._on_user_payload, "slow payload")

            asyncio.run(_runner())

        events = adapter._agent_session.session_events
        failures = [e for e in events if e["type"] == "delivery_failure"]
        self.assertEqual(len(failures), 1)
        # Outcome is recovered (re-enqueued), not dropped.
        self.assertEqual(failures[0]["reason"], "recovered_via_outbox")

    def test_sync_send_cb_called_directly(self) -> None:
        delivered: list = []

        def sync_cb(chat_id, payload, reply_to, agent_session):
            delivered.append(payload)

        pool = MagicMock(spec=PTYPool)
        adapter = BridgeAdapter(
            agent_session=_fake_session(),
            project_key="valor",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda pk, tr: (sync_cb, None),
        )
        # No loop needed for a sync cb — callable from any thread.
        adapter._on_user_payload("sync payload")
        self.assertEqual(delivered, ["sync payload"])

    def test_same_thread_call_does_not_deadlock(self) -> None:
        """If the callback fires ON the asyncio thread (sync container
        run in tests), delivery is scheduled fire-and-forget instead of
        blocking the loop on its own future."""
        delivered: list = []
        adapter = _adapter_with_async_cb(delivered)

        async def _runner():
            adapter._loop = asyncio.get_running_loop()
            adapter._on_user_payload("same-thread payload")
            # Let the scheduled task run.
            await asyncio.sleep(0)

        asyncio.run(_runner())
        self.assertEqual(delivered, [("42", "same-thread payload", 7)])

    def test_same_thread_failure_reenqueues_to_outbox(self) -> None:
        """Same-thread fire-and-forget path: if the scheduled send task
        raises, the done-callback re-enqueues to the outbox so the reply
        is not silently lost (issue #1805 concern 1)."""

        async def failing_cb(chat_id, payload, reply_to, agent_session):
            raise RuntimeError("send boom")

        pool = MagicMock(spec=PTYPool)
        adapter = BridgeAdapter(
            agent_session=_fake_session(),
            project_key="valor",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda pk, tr: (failing_cb, None),
        )
        mock_redis = MagicMock()

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_redis):

            async def _runner():
                adapter._loop = asyncio.get_running_loop()
                adapter._on_user_payload("same-thread fail payload")
                # Let the scheduled task run and its done-callback fire.
                await asyncio.sleep(0)
                await asyncio.sleep(0)

            asyncio.run(_runner())

        mock_redis.rpush.assert_called_once()
        self.assertIn("telegram:outbox:", mock_redis.rpush.call_args[0][0])
        events = adapter._agent_session.session_events
        failures = [e for e in events if e["type"] == "delivery_failure"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["reason"], "recovered_via_outbox")


class TestDeliveryTimeoutOutboxReenqueue(unittest.TestCase):
    """Tests for the outbox re-enqueue recovery path (issue #1805)."""

    def _slow_adapter(self, timeout_s: float = 0.05) -> BridgeAdapter:
        async def slow_cb(chat_id, payload, reply_to, agent_session):
            await asyncio.sleep(5)

        pool = MagicMock(spec=PTYPool)
        return BridgeAdapter(
            agent_session=_fake_session(),
            project_key="valor",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda pk, tr: (slow_cb, None),
            delivery_timeout_s=timeout_s,
        )

    def test_timeout_reenqueues_to_outbox(self) -> None:
        """TimeoutError path: rpush is called and the session event records
        `recovered_via_outbox`."""
        adapter = self._slow_adapter()
        mock_redis = MagicMock()

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_redis):

            async def _runner():
                adapter._loop = asyncio.get_running_loop()
                await asyncio.to_thread(adapter._on_user_payload, "timeout payload")

            asyncio.run(_runner())

        mock_redis.rpush.assert_called_once()
        call_args = mock_redis.rpush.call_args
        self.assertIn("telegram:outbox:", call_args[0][0])

        events = adapter._agent_session.session_events
        failures = [e for e in events if e["type"] == "delivery_failure"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["reason"], "recovered_via_outbox")

    def test_loop_closed_reenqueues(self) -> None:
        """Loop-closed RuntimeError path: re-enqueue is attempted via the outbox."""
        adapter = self._slow_adapter()
        mock_redis = MagicMock()

        # Simulate a loop that is marked closed after capture.
        closed_loop = MagicMock()
        closed_loop.is_closed.return_value = False  # passes the initial check
        closed_loop.is_running.return_value = False
        # run_coroutine_threadsafe raises RuntimeError (loop closed race).
        closed_loop.run_coroutine_threadsafe = MagicMock(
            side_effect=RuntimeError("Event loop is closed")
        )
        adapter._loop = closed_loop

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_redis):
            with patch("asyncio.run_coroutine_threadsafe", side_effect=RuntimeError("loop closed")):
                adapter._on_user_payload("loop closed payload")

        mock_redis.rpush.assert_called_once()

    def test_double_failure_records_dropped(self) -> None:
        """When timeout fires AND re-enqueue also fails (Redis down), the
        session event records `dropped` and `_user_facing_routed` stays False."""
        adapter = self._slow_adapter()

        # Redis unavailable — rpush raises.
        mock_redis = MagicMock()
        mock_redis.rpush.side_effect = ConnectionError("Redis unavailable")

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_redis):

            async def _runner():
                adapter._loop = asyncio.get_running_loop()
                await asyncio.to_thread(adapter._on_user_payload, "lost payload")

            asyncio.run(_runner())

        events = adapter._agent_session.session_events
        failures = [e for e in events if e["type"] == "delivery_failure"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["reason"], "dropped")
        self.assertFalse(failures[0]["recovered"])
        # user_facing_routed must remain False on double failure.
        self.assertFalse(adapter._user_facing_routed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
