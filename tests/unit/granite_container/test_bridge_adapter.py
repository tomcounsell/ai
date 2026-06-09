"""Tests for the BridgeAdapter (plan #1572, Task 3)."""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from agent.granite_container.bridge_adapter import BridgeAdapter
from agent.granite_container.pty_pool import PTYPool


@dataclass
class _FakeSession:
    """A minimal stand-in for AgentSession with session_events."""

    session_id: str = "test-session-id"
    chat_id: int = 12345
    telegram_message_id: int = 67890
    session_events: list[dict] = field(default_factory=list)


def _make_pool(size: int = 1) -> PTYPool:
    """Build a pool. Spawn is mocked in the test's `_patch_spawn`
    context — the pool is `initialize()`'d in that context. The
    pool's pid registry is a temp file so the test never touches
    `data/granite_pty_pids.json` on disk."""
    import tempfile

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    return PTYPool(pool_size=size, pid_registry_path=tmp.name)


def _patch_spawn():
    return patch("agent.granite_container.pty_pool.PTYDriver.spawn", lambda self: None)


def _make_initialized_pool(size: int = 1) -> PTYPool:
    """Build a pool, initialize it with mocked spawn, and return it.
    The pool's pid registry is a temp file so the test never touches
    `data/granite_pty_pids.json` on disk."""
    pool = _make_pool(size=size)
    with _patch_spawn():
        asyncio.run(pool.initialize())
    return pool


def _patch_container_run_with_result(result_factory: Any):
    """Patch `Container.run` to call result_factory() and return its
    output. The adapter is single-shot, so this lets us exercise the
    adapter's flow without driving a real container."""
    return patch(
        "agent.granite_container.bridge_adapter.Container.run",
        lambda self: result_factory(),
    )


def _make_container_result(
    exit_reason: str = "pm_complete",
    exit_message: str = "Trailing summary.",
    turns: int = 1,
    compliance_misses: int = 0,
):
    """Build a ContainerResult-like object (MagicMock) with the
    attributes the BridgeAdapter reads."""
    result = MagicMock()
    result.exit_reason = exit_reason
    result.exit_message = exit_message
    result.turns = [MagicMock()] * turns
    result.classification_compliance_misses = compliance_misses
    return result


class TestBridgeAdapterSendCbResolution(unittest.TestCase):
    def test_send_cb_none_uses_log_only_callbacks(self) -> None:
        """BRIDGE-1 regression test: when resolve_callbacks returns
        (None, None), the adapter sets logger-only no-op callbacks
        and runs the container to completion without crashing."""
        session = _FakeSession()
        pool = _make_pool()

        def _resolve(project_key: str, transport: str):
            return (None, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        # The adapter's callback identity is the log-only fallback.
        self.assertEqual(adapter._on_user_payload, adapter._log_only_user)
        self.assertEqual(adapter._on_complete_payload, adapter._log_only_complete)

    def test_send_cb_set_uses_wrapped_callbacks(self) -> None:
        session = _FakeSession()
        pool = _make_pool()
        captured_calls: list[tuple] = []

        def _send_cb(chat_id, payload, reply_to, session):
            captured_calls.append((chat_id, payload, reply_to, session))

        def _resolve(project_key: str, transport: str):
            return (_send_cb, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        # The adapter's callback identity is the wrapped (sync) callable.
        self.assertNotEqual(adapter._on_user_payload, adapter._log_only_user)
        self.assertNotEqual(adapter._on_complete_payload, adapter._log_only_complete)


class TestBridgeAdapterSessionEvents(unittest.TestCase):
    def test_run_publishes_exit_summary_event(self) -> None:
        """Successful run writes a `session_events` entry with the
        expected fields."""
        session = _FakeSession()
        pool = _make_initialized_pool()
        result = _make_container_result(
            exit_reason="pm_complete",
            exit_message="Trailing summary.",
            turns=3,
            compliance_misses=1,
        )

        def _resolve(project_key: str, transport: str):
            return (None, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        with _patch_container_run_with_result(lambda: result):
            # Run via asyncio so to_thread works.
            async def _runner() -> str:
                return await adapter.run("test user message", "/tmp")

            ret = asyncio.run(_runner())
        self.assertEqual(ret, "")
        # The session_events list should have one entry: exit_summary.
        self.assertEqual(len(session.session_events), 1)
        ev = session.session_events[0]
        self.assertEqual(ev["type"], "exit_summary")
        self.assertEqual(ev["exit_reason"], "pm_complete")
        self.assertEqual(ev["turns"], 3)
        self.assertEqual(ev["compliance_misses"], 1)
        self.assertIn("ts", ev)


class TestBridgeAdapterExitAnomaly(unittest.TestCase):
    """OPS-1: when result.exit_reason in (pm_hang, dev_hang,
    startup_unresolved), the adapter logs at ERROR and appends a
    session_events entry of type exit_anomaly."""

    def test_pm_hang_writes_exit_anomaly_event(self) -> None:
        session = _FakeSession()
        pool = _make_initialized_pool()
        result = _make_container_result(
            exit_reason="pm_hang", exit_message="PM did not idle in 120s"
        )

        def _resolve(project_key: str, transport: str):
            return (None, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        with _patch_container_run_with_result(lambda: result):

            async def _runner() -> str:
                return await adapter.run("test", "/tmp")

            asyncio.run(_runner())
        types = [e["type"] for e in session.session_events]
        self.assertIn("exit_anomaly", types)
        anomaly = next(e for e in session.session_events if e["type"] == "exit_anomaly")
        self.assertEqual(anomaly["exit_reason"], "pm_hang")

    def test_dev_hang_writes_exit_anomaly_event(self) -> None:
        session = _FakeSession()
        pool = _make_initialized_pool()
        result = _make_container_result(exit_reason="dev_hang")

        def _resolve(project_key: str, transport: str):
            return (None, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        with _patch_container_run_with_result(lambda: result):

            async def _runner() -> str:
                return await adapter.run("test", "/tmp")

            asyncio.run(_runner())
        types = [e["type"] for e in session.session_events]
        self.assertIn("exit_anomaly", types)

    def test_startup_unresolved_writes_exit_anomaly_event(self) -> None:
        session = _FakeSession()
        pool = _make_initialized_pool()
        result = _make_container_result(exit_reason="startup_unresolved")

        def _resolve(project_key: str, transport: str):
            return (None, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        with _patch_container_run_with_result(lambda: result):

            async def _runner() -> str:
                return await adapter.run("test", "/tmp")

            asyncio.run(_runner())
        types = [e["type"] for e in session.session_events]
        self.assertIn("exit_anomaly", types)

    def test_pm_complete_does_not_write_exit_anomaly_event(self) -> None:
        """Sanity: only hang / unresolved write exit_anomaly.
        pm_complete / pm_user / pm_max_turns are not anomalies."""
        session = _FakeSession()
        pool = _make_initialized_pool()
        result = _make_container_result(exit_reason="pm_complete")

        def _resolve(project_key: str, transport: str):
            return (None, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        with _patch_container_run_with_result(lambda: result):

            async def _runner() -> str:
                return await adapter.run("test", "/tmp")

            asyncio.run(_runner())
        types = [e["type"] for e in session.session_events]
        self.assertNotIn("exit_anomaly", types)


class TestBridgeAdapterCallbackInvocation(unittest.TestCase):
    """The container's per-turn hooks (on_user_payload, on_complete_payload)
    are exercised by the container itself. These tests verify the
    adapter's wrappers around send_cb."""

    def test_log_only_user_logs_but_does_not_raise(self) -> None:
        session = _FakeSession()
        pool = _make_pool()

        def _resolve(project_key: str, transport: str):
            return (None, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        # The log-only fallback should not raise on a payload.
        adapter._log_only_user("hello user payload")
        # No session_events entry was added (logging is separate).
        self.assertEqual(session.session_events, [])

    def test_log_only_complete_logs_but_does_not_raise(self) -> None:
        session = _FakeSession()
        pool = _make_pool()

        def _resolve(project_key: str, transport: str):
            return (None, None)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=_resolve,
        )
        adapter._log_only_complete("Trailing summary.")
        self.assertEqual(session.session_events, [])


class TestBridgeAdapterPrewarmedPair(unittest.TestCase):
    """BridgeAdapter passes the pool's pre-warmed pair to Container,
    instead of letting Container spawn a fresh pair on top of it.

    Regression test for the pool double-spawn: when the pool's
    pre-warmed pair is discarded, every session runs 2N `claude`
    PTYs (N from the pool, N from Container), which regresses
    issue #1572's orphan-leak acceptance criterion
    (`ps aux | grep claude --permission-mode` ≤ pool_size × 2).
    """

    def test_run_passes_pool_pair_to_container(self) -> None:
        """BridgeAdapter.run forwards the acquired (pm, dev) pair
        to Container as pm_pty/dev_pty kwargs, and Container does
        NOT call PTYDriver.spawn."""
        from unittest.mock import patch as _patch

        from agent.granite_container.pty_driver import PTYDriver

        pool = _make_pool(size=1)
        session = _FakeSession()

        # Capture which (role, id) pairs are spawned. The pool's
        # prewarm is the only spawn that should happen. We patch
        # the spawn AT the module level (the path pty_driver.spawn
        # looks up at call time) and init the pool inside the
        # patch so prewarm is captured.
        spawned: list[tuple[str, int]] = []
        original_spawn = PTYDriver.spawn

        def _tracking_spawn(self):  # type: ignore[no-untyped-def]
            spawned.append((self.role, id(self)))
            return original_spawn(self)

        with (
            _patch("agent.granite_container.pty_driver.PTYDriver.spawn", _tracking_spawn),
            _patch_container_run_with_result(lambda: _make_container_result()),
        ):
            asyncio.run(pool.initialize())
            adapter = BridgeAdapter(
                agent_session=session,
                project_key="t",
                transport="telegram",
                pool=pool,
                resolve_callbacks=lambda p, t: (None, None),
            )
            asyncio.run(adapter.run("hello", "/tmp"))

        # Exactly one pm + one dev spawn — both from the pool's
        # prewarm. Zero additional spawns from Container.
        roles = sorted(r for r, _ in spawned)
        self.assertEqual(roles, ["dev", "pm"])
        # No duplicate roles (i.e., Container did NOT spawn).
        role_counts: dict[str, int] = {}
        for r, _ in spawned:
            role_counts[r] = role_counts.get(r, 0) + 1
        for r, count in role_counts.items():
            self.assertEqual(
                count, 1, f"role {r!r} spawned {count}x, expected 1x (Container spawned fresh)"
            )

    def test_run_marks_pool_pair_as_released(self) -> None:
        """The pool's (pm, dev) PTYs are marked _released_to_pool=True
        so Container._close_pair does not double-close them."""
        pool = _make_initialized_pool(size=1)
        session = _FakeSession()

        # Find the prewarmed pair.
        slots = pool._slots
        self.assertEqual(len(slots), 1)
        prewarmed_pm, prewarmed_dev = slots[0].pty_pair

        with (
            _patch_container_run_with_result(lambda: _make_container_result()),
        ):
            adapter = BridgeAdapter(
                agent_session=session,
                project_key="t",
                transport="telegram",
                pool=pool,
                resolve_callbacks=lambda p, t: (None, None),
            )
            asyncio.run(adapter.run("hello", "/tmp"))

        # Both prewarmed PTYs are marked as pool-owned.
        self.assertTrue(getattr(prewarmed_pm, "_released_to_pool", False))
        self.assertTrue(getattr(prewarmed_dev, "_released_to_pool", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
