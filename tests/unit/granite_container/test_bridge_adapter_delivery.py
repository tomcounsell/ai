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
                return (pm, dev)

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
    def test_no_captured_loop_records_failure(self) -> None:
        """Async send_cb with no captured loop: skip delivery, record
        a delivery_failure with reason no_event_loop."""
        delivered: list = []
        adapter = _adapter_with_async_cb(delivered)
        self.assertIsNone(adapter._loop)

        adapter._on_user_payload("payload without loop")

        self.assertEqual(delivered, [])
        events = adapter._agent_session.session_events
        failures = [e for e in events if e["type"] == "delivery_failure"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["reason"], "no_event_loop")

    def test_timeout_records_failure_with_exception_type(self) -> None:
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

        async def _runner():
            adapter._loop = asyncio.get_running_loop()
            await asyncio.to_thread(adapter._on_user_payload, "slow payload")

        asyncio.run(_runner())
        events = adapter._agent_session.session_events
        failures = [e for e in events if e["type"] == "delivery_failure"]
        self.assertEqual(len(failures), 1)
        self.assertIn("TimeoutError", failures[0]["reason"])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
