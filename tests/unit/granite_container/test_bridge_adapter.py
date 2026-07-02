"""Tests for the BridgeAdapter (plan #1572, Task 3)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from agent.granite_container.bridge_adapter import (
    BridgeAdapter,
    _transcript_path_from_spec,
)
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


def _make_initialized_pool(size: int = 1, cwd: str | None = "/tmp") -> PTYPool:
    """Build a pool, initialize it with mocked spawn, and return it.
    The pool's pid registry is a temp file so the test never touches
    `data/granite_pty_pids.json` on disk.

    The default cwd matches the `/tmp` working_dir the tests pass to
    `adapter.run`, so a spec with no per-session env/persona matches
    the pool defaults and the prewarmed (mock-spawned) pair is reused
    — without this, spawn-on-acquire would spawn a REAL `claude`."""
    pool = _make_pool(size=size)
    with _patch_spawn():
        asyncio.run(pool.initialize(cwd=cwd))
    return pool


@contextlib.contextmanager
def _patch_container_run_with_result(result_factory: Any):
    """Patch `Container.run` to call result_factory() and return its
    output, AND neutralize `PTYDriver.spawn` for the duration.

    The adapter is single-shot, so this lets us exercise the adapter's
    flow without driving a real container. The spawn patch is required
    because BridgeAdapter always carries per-session session-ids, so
    `_needs_session_spawn` now forces a spawn-on-acquire inside
    `acquire_pair` (Finding 1) — without neutralizing spawn the adapter
    would exec the real `claude` binary (issue #1632)."""
    with (
        patch(
            "agent.granite_container.bridge_adapter.Container.run",
            lambda self: result_factory(),
        ),
        _patch_spawn(),
    ):
        yield


def _make_container_result(
    exit_reason: str = "pm_complete",
    exit_message: str = "Trailing summary.",
    turns: int = 1,
    compliance_misses: int = 0,
    user_facing_routed: bool = False,
    transcript_fallback_count: int = 0,
):
    """Build a ContainerResult-like object (MagicMock) with the
    attributes the BridgeAdapter reads."""
    result = MagicMock()
    result.exit_reason = exit_reason
    result.exit_message = exit_message
    result.turns = [MagicMock()] * turns
    result.classification_compliance_misses = compliance_misses
    result.user_facing_routed = user_facing_routed
    result.transcript_fallback_count = transcript_fallback_count
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
            transcript_fallback_count=2,
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
        self.assertEqual(ev["transcript_fallback_count"], 2)
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
        """BridgeAdapter.run forwards the pool-acquired (pm, dev) pair
        to Container as pm_pty/dev_pty kwargs, and Container does NOT
        spawn a fresh pair on top of it.

        BridgeAdapter always carries per-session session-ids, so the
        pool performs a spawn-on-acquire (Finding 1): the prewarmed
        pair is spawned at pool.initialize, then replaced by a
        per-session pair at acquire time. That is the pool's job — the
        regression this guards is Container spawning ADDITIONAL PTYs on
        top of the pair it already received. We assert Container got a
        non-None pool pair and did not invoke spawn itself."""
        from unittest.mock import patch as _patch

        pool = _make_pool(size=1)
        session = _FakeSession()

        # Track every spawn and the pair handed to Container. The fake
        # spawn is a FULL fake — never call through to the original
        # spawn body, which execs the real `claude` binary (issue #1632
        # mode 3: orphaned ~250MB claude processes memory-crash the box).
        spawned: list[str] = []
        seen: dict = {}

        def _tracking_spawn(self):  # type: ignore[no-untyped-def]
            spawned.append(self.role)

        def _fake_container(**kwargs):
            seen.update(kwargs)
            container = MagicMock()
            container.run = lambda: _make_container_result()
            return container

        with (
            _patch("agent.granite_container.pty_pool.PTYDriver.spawn", _tracking_spawn),
            _patch(
                "agent.granite_container.bridge_adapter.Container",
                side_effect=_fake_container,
            ),
        ):
            asyncio.run(pool.initialize(cwd="/tmp"))
            adapter = BridgeAdapter(
                agent_session=session,
                project_key="t",
                transport="telegram",
                pool=pool,
                resolve_callbacks=lambda p, t: (None, None),
            )
            spawned.clear()  # ignore prewarm spawns from initialize()
            asyncio.run(adapter.run("hello", "/tmp"))

        # Container received the pool's acquired pair...
        self.assertIsNotNone(seen.get("pm_pty"))
        self.assertIsNotNone(seen.get("dev_pty"))
        # ...and the per-session spawn-on-acquire produced exactly one
        # pm + one dev — no extra spawns leaking past pool_size × 2.
        self.assertEqual(sorted(spawned), ["dev", "pm"])

    def test_run_marks_pool_pair_as_released(self) -> None:
        """The (pm, dev) pair handed to Container is marked
        _released_to_pool=True so Container._close_pair does not
        double-close them — the pool's __aexit__ owns the close.

        BridgeAdapter always carries per-session session-ids, so the
        pool performs a spawn-on-acquire and the pair the container
        receives is the per-session respawned pair, NOT the original
        prewarmed pair (Finding 1). The release contract applies to
        whichever pair is actually active for the session."""
        pool = _make_pool(size=1)
        with _patch_spawn():
            asyncio.run(pool.initialize(cwd="/tmp"))
        session = _FakeSession()

        # Capture the pair actually handed to the Container.
        seen: dict = {}

        def _fake_container(**kwargs):
            seen.update(kwargs)
            container = MagicMock()
            container.run = lambda: _make_container_result()
            return container

        with (
            patch(
                "agent.granite_container.bridge_adapter.Container",
                side_effect=_fake_container,
            ),
            _patch_spawn(),
        ):
            adapter = BridgeAdapter(
                agent_session=session,
                project_key="t",
                transport="telegram",
                pool=pool,
                resolve_callbacks=lambda p, t: (None, None),
            )
            asyncio.run(adapter.run("hello", "/tmp"))

        # The active pair handed to Container is marked pool-owned.
        active_pm = seen.get("pm_pty")
        active_dev = seen.get("dev_pty")
        self.assertIsNotNone(active_pm)
        self.assertIsNotNone(active_dev)
        self.assertTrue(getattr(active_pm, "_released_to_pool", False))
        self.assertTrue(getattr(active_dev, "_released_to_pool", False))


@dataclass
class _SavingFakeSession(_FakeSession):
    """A _FakeSession with a recording `save(update_fields=...)`."""

    saved_calls: list = field(default_factory=list)
    updated_at: Any = None
    last_turn_at: Any = None

    def save(self, update_fields=None) -> None:
        self.saved_calls.append(list(update_fields or []))


class TestBridgeAdapterSessionEventPersistence(unittest.TestCase):
    """PR #1612 review B3: `_append_session_event` must PERSIST the
    append. The in-memory mutation alone never reaches Redis — the
    executor's post-run saves exclude `session_events` and
    finalization loads a fresh copy by session_id."""

    def test_exit_summary_append_saves_with_update_fields(self) -> None:
        session = _SavingFakeSession()
        pool = _make_initialized_pool()
        result = _make_container_result(exit_reason="pm_complete")

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (None, None),
        )
        with _patch_container_run_with_result(lambda: result):

            async def _runner() -> str:
                return await adapter.run("test", "/tmp")

            asyncio.run(_runner())

        # The exit_summary append was persisted with the model's
        # documented partial-save pattern.
        self.assertEqual(len(session.session_events), 1)
        self.assertIn(["session_events", "updated_at"], session.saved_calls)
        self.assertIsNotNone(session.updated_at)

    def test_anomaly_append_saves_too(self) -> None:
        session = _SavingFakeSession()
        pool = _make_initialized_pool()
        result = _make_container_result(exit_reason="pm_hang")

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (None, None),
        )
        with _patch_container_run_with_result(lambda: result):

            async def _runner() -> str:
                return await adapter.run("test", "/tmp")

            asyncio.run(_runner())

        # exit_summary + exit_anomaly — one persisting save each.
        saves = [c for c in session.saved_calls if c == ["session_events", "updated_at"]]
        self.assertEqual(len(saves), 2)

    def test_save_failure_is_silent(self) -> None:
        """A failing save must not crash the run (fail-silent
        observability contract)."""

        class _ExplodingSession(_SavingFakeSession):
            def save(self, update_fields=None) -> None:
                raise RuntimeError("redis down")

        session = _ExplodingSession()
        pool = _make_initialized_pool()
        result = _make_container_result(exit_reason="pm_complete")

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (None, None),
        )
        with _patch_container_run_with_result(lambda: result):

            async def _runner() -> str:
                return await adapter.run("test", "/tmp")

            # Must not raise.
            ret = asyncio.run(_runner())
        self.assertEqual(ret, "")


class TestBridgeAdapterLastTurnAt(unittest.TestCase):
    """PR #1612 review TD1: the adapter restores the two-tier
    no-progress detector's sub-check A by bumping `last_turn_at`
    on each classified turn via the container's `on_turn` hook."""

    def test_bump_sets_field_and_saves(self) -> None:
        session = _SavingFakeSession()
        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=_make_pool(),
            resolve_callbacks=lambda p, t: (None, None),
        )
        adapter._bump_last_turn_at()
        self.assertIsNotNone(session.last_turn_at)
        self.assertIn(["last_turn_at"], session.saved_calls)

    def test_bump_survives_save_failure(self) -> None:
        class _ExplodingSession(_SavingFakeSession):
            def save(self, update_fields=None) -> None:
                raise RuntimeError("redis down")

        adapter = BridgeAdapter(
            agent_session=_ExplodingSession(),
            project_key="test-project",
            transport="telegram",
            pool=_make_pool(),
            resolve_callbacks=lambda p, t: (None, None),
        )
        adapter._bump_last_turn_at()  # must not raise

    def test_run_passes_bump_as_on_turn(self) -> None:
        """The container receives the adapter's bump as `on_turn`."""
        session = _SavingFakeSession()
        pool = _make_initialized_pool()
        seen: dict = {}

        fake_result = _make_container_result(exit_reason="pm_complete")

        def _fake_container(**kwargs):
            seen.update(kwargs)
            container = MagicMock()
            container.run = lambda: fake_result
            return container

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (None, None),
        )
        with (
            patch(
                "agent.granite_container.bridge_adapter.Container",
                side_effect=_fake_container,
            ),
            _patch_spawn(),
        ):
            asyncio.run(adapter.run("test", "/tmp"))

        self.assertEqual(seen.get("on_turn"), adapter._bump_last_turn_at)


class _SpecFakeDriver:
    """Fake PTYDriver recording per-session spawn kwargs."""

    instances: list = []

    def __init__(
        self,
        role: str = "pm",
        cwd: str | None = None,
        model: str | None = None,
        env: dict | None = None,
        append_system_prompt: str | None = None,
        session_id: str | None = None,
        settings_path: str | None = None,
    ) -> None:
        self.role = role
        self.cwd = cwd
        self.model = model
        self.env = env
        self.append_system_prompt = append_system_prompt
        self._session_id = session_id
        self._settings_path = settings_path
        self._child = None  # treated as live by _pair_is_live
        self.closed = False
        _SpecFakeDriver.instances.append(self)

    def spawn(self) -> None:
        pass

    def isalive(self) -> bool:
        return True

    def close(self, force: bool = True) -> None:
        self.closed = True


class TestBridgeAdapterSpawnOnAcquire(unittest.TestCase):
    """PR #1612 review B1+B2: per-session env / model can only be
    injected at process spawn, so the adapter's spawn spec must trigger
    a fresh per-session pair at acquire time — and the spec must carry
    the session's cwd, env, and model override.

    Note: pm_system_prompt / --append-system-prompt is removed (issue #1692).
    Persona is now delivered via prime commands inside the TUI."""

    def setUp(self) -> None:
        _SpecFakeDriver.instances = []

    def test_session_env_and_model_reach_the_spawn(self) -> None:
        session = _FakeSession()
        session_env = {
            "SESSION_TYPE": "pm",
            "AGENT_SESSION_ID": "as-123",
            "CLAUDE_CODE_TASK_LIST_ID": "tl-1",
        }

        with patch("agent.granite_container.pty_pool.PTYDriver", _SpecFakeDriver):
            pool = _make_pool(size=1)
            asyncio.run(pool.initialize(cwd="/tmp"))
            adapter = BridgeAdapter(
                agent_session=session,
                project_key="t",
                transport="telegram",
                pool=pool,
                resolve_callbacks=lambda p, t: (None, None),
                session_env=session_env,
                pm_model="haiku",
            )
            with _patch_container_run_with_result(lambda: _make_container_result()):
                asyncio.run(adapter.run("hello", "/worktrees/slug"))

        # 2 prewarm + 2 per-session spawns.
        self.assertEqual(len(_SpecFakeDriver.instances), 4)
        session_pm = next(
            d for d in _SpecFakeDriver.instances if d.role == "pm" and d.env is not None
        )
        session_dev = next(
            d for d in _SpecFakeDriver.instances if d.role == "dev" and d.env is not None
        )
        # The session pair carries the per-session cwd + env and model.
        # No --append-system-prompt is set (persona comes from prime commands).
        self.assertEqual(session_pm.cwd, "/worktrees/slug")
        self.assertEqual(session_dev.cwd, "/worktrees/slug")
        self.assertEqual(session_pm.env, session_env)
        self.assertEqual(session_pm.model, "haiku")
        # The prewarmed pair was closed when it was replaced.
        prewarmed = [d for d in _SpecFakeDriver.instances if d.env is None]
        self.assertEqual(len(prewarmed), 2)
        for d in prewarmed:
            self.assertTrue(d.closed, f"prewarmed {d.role} PTY was not closed")

    def test_session_ids_force_spawn_even_without_env_or_model(self) -> None:
        """A spec matching the pool defaults (same cwd, no env, no
        persona, no model) STILL spawns a per-session pair, because
        BridgeAdapter always carries per-session session-ids and
        `_needs_session_spawn` now treats session-ids as a per-session
        identity (Finding 1, latent bug 1). Without this, the prewarmed
        pair (whose claude auto-generates its own UUID) would be reused
        and the transcript path the container computes from the spec's
        session-ids would never match — the empty-read churn."""
        session = _FakeSession()

        with patch("agent.granite_container.pty_pool.PTYDriver", _SpecFakeDriver):
            pool = _make_pool(size=1)
            asyncio.run(pool.initialize(cwd="/tmp"))
            adapter = BridgeAdapter(
                agent_session=session,
                project_key="t",
                transport="telegram",
                pool=pool,
                resolve_callbacks=lambda p, t: (None, None),
            )
            with _patch_container_run_with_result(lambda: _make_container_result()):
                asyncio.run(adapter.run("hello", "/tmp"))

        # 2 prewarm + 2 per-session spawns: the session-ids force a spawn.
        self.assertEqual(len(_SpecFakeDriver.instances), 4)
        # The per-session pair carries the spec's session-ids.
        session_pms = [
            d for d in _SpecFakeDriver.instances if d.role == "pm" and d._session_id is not None
        ]
        self.assertEqual(len(session_pms), 1)


class TestDeliverSyncReturnsBool(unittest.TestCase):
    """_deliver_sync returns True on confirmed delivery, False on failure (concern C1, #1647)."""

    def _make_adapter_with_sync_cb(self, cb):
        """Adapter with a sync send_cb (not a coroutine) for testing."""

        @dataclass
        class _Session:
            session_id: str = "s1"
            chat_id: int = 1
            telegram_message_id: int = 2
            session_events: list = field(default_factory=list)

        pool = _make_pool()
        adapter = BridgeAdapter(
            agent_session=_Session(),
            project_key="test",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (cb, None),
        )
        return adapter

    def test_sync_send_cb_success_returns_true(self) -> None:
        """Sync send_cb that does not raise → True."""
        calls: list = []

        def _sync_cb(chat_id, payload, reply_to, session):
            calls.append(payload)

        adapter = self._make_adapter_with_sync_cb(_sync_cb)
        result = adapter._deliver_sync(_sync_cb, 1, "hello", None, None, 5.0)
        self.assertTrue(result)
        self.assertEqual(calls, ["hello"])

    def test_sync_send_cb_raises_returns_false(self) -> None:
        """Sync send_cb that raises → False, no crash."""

        def _failing_cb(chat_id, payload, reply_to, session):
            raise RuntimeError("delivery failed")

        adapter = self._make_adapter_with_sync_cb(_failing_cb)
        result = adapter._deliver_sync(_failing_cb, 1, "hello", None, None, 5.0)
        self.assertFalse(result)

    def test_no_loop_returns_false(self) -> None:
        """No captured event loop → False."""

        async def _async_cb(chat_id, payload, reply_to, session):
            pass

        adapter = self._make_adapter_with_sync_cb(_async_cb)
        adapter._loop = None  # No loop captured
        result = adapter._deliver_sync(_async_cb, 1, "hello", None, None, 5.0)
        self.assertFalse(result)


class TestUserFacingRoutedPropagation(unittest.TestCase):
    """S3 (issue #1647): user_facing_routed flows through adapter → agent_session → executor."""

    def _run_adapter(self, exit_reason="pm_user", user_facing_routed=True):
        """Run the adapter with a mocked container result and sync send_cb."""
        # dataclass and field are imported at module level

        @dataclass
        class _Session:
            session_id: str = "s1"
            chat_id: int = 1
            telegram_message_id: int = 2
            session_events: list = field(default_factory=list)
            user_facing_routed: bool = False

        def _save(update_fields=None):
            pass

        session = _Session()
        session.save = _save  # type: ignore

        pool = _make_initialized_pool()
        deliveries: list[str] = []

        def _send_cb(chat_id, payload, reply_to, sess):
            deliveries.append(payload)

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (_send_cb, None),
        )

        result = _make_container_result(
            exit_reason=exit_reason,
            user_facing_routed=user_facing_routed,
        )
        adapter._publish_exit_summary(result)
        return session, adapter

    def test_successful_delivery_sets_agent_session_user_facing_routed(self) -> None:
        """When result.user_facing_routed=True, _publish_exit_summary sets
        agent_session.user_facing_routed=True (issue #1647)."""
        session, adapter = self._run_adapter(user_facing_routed=True)
        self.assertTrue(session.user_facing_routed)

    def test_no_delivery_leaves_user_facing_routed_false(self) -> None:
        """When result.user_facing_routed=False and adapter._user_facing_routed=False,
        agent_session.user_facing_routed is NOT set to True."""
        session, adapter = self._run_adapter(user_facing_routed=False)
        self.assertFalse(session.user_facing_routed)

    def test_adapter_flag_propagates_on_successful_sync_delivery(self) -> None:
        """When a sync send_cb succeeds in _make_user_callback,
        self._user_facing_routed is set to True (issue #1647, concern C1)."""
        # dataclass and field are imported at module level

        @dataclass
        class _Session:
            session_id: str = "s1"
            chat_id: int = 1
            telegram_message_id: int = 2
            session_events: list = field(default_factory=list)

        pool = _make_pool()
        calls: list[str] = []

        def _sync_cb(chat_id, payload, reply_to, sess):
            calls.append(payload)

        adapter = BridgeAdapter(
            agent_session=_Session(),
            project_key="test",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (_sync_cb, None),
        )
        # Initially False.
        self.assertFalse(adapter._user_facing_routed)
        # Call the user callback directly.
        adapter._on_user_payload("hello user")
        # After successful sync delivery, flag is True.
        self.assertTrue(adapter._user_facing_routed)
        self.assertEqual(calls, ["hello user"])


class TestExitAnomalyAllowlist(unittest.TestCase):
    """C9: pm_no_user_message is in the anomaly allowlist; pm_max_turns is NOT (issue #1647)."""

    def _run_with_exit_reason(self, exit_reason: str):
        # dataclass and field are imported at module level

        @dataclass
        class _Session:
            session_id: str = "s1"
            session_events: list = field(default_factory=list)

        session = _Session()
        pool = _make_pool()
        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (None, None),
        )
        result = _make_container_result(exit_reason=exit_reason)
        adapter._maybe_publish_exit_anomaly(result)
        return session

    def test_pm_no_user_message_writes_anomaly_event(self) -> None:
        """pm_no_user_message is in the anomaly allowlist and publishes an event."""
        session = self._run_with_exit_reason("pm_no_user_message")
        types = [e["type"] for e in session.session_events]
        self.assertIn("exit_anomaly", types)

    def test_pm_max_turns_does_not_write_anomaly(self) -> None:
        """pm_max_turns is NOT in the anomaly allowlist (concern C9)."""
        session = self._run_with_exit_reason("pm_max_turns")
        types = [e["type"] for e in session.session_events]
        self.assertNotIn("exit_anomaly", types)

    def test_pm_complete_does_not_write_anomaly(self) -> None:
        """pm_complete is NOT in the anomaly allowlist."""
        session = self._run_with_exit_reason("pm_complete")
        types = [e["type"] for e in session.session_events]
        self.assertNotIn("exit_anomaly", types)


class TestPtySlotPersistence(unittest.TestCase):
    """Issue #1663 regression guard: pty_slot from acquire_pair must reach
    AgentSession.pty_slot via _publish_exit_summary.

    The BLOCKER was that the 3-tuple `(pm, dev, slot.idx)` from acquire_pair
    was unpacked but slot.idx was never stamped onto ContainerResult before the
    exit-summary path ran. These tests drive the REAL acquire_pair context
    manager (not a mocked tuple) to ensure the regression cannot return silently.
    """

    def test_real_acquire_pair_stamps_pty_slot_on_session(self) -> None:
        """End-to-end regression: pty_slot from the real acquire_pair context
        manager reaches AgentSession.pty_slot after a successful run.

        Uses the real PTYPool.acquire_pair (not a mocked tuple), so any future
        regression that breaks the 3-tuple yield or the slot-index stamp will
        fail here instead of silently passing with a mock that always returns
        the right shape.
        """
        session = _SavingFakeSession()
        pool = _make_initialized_pool(size=1)

        # Peek at the slot index before the run so we can assert it reaches
        # agent_session.pty_slot. The slot is still idle — we only read idx.
        expected_slot_idx = pool._slots[0].idx  # always 0 for a 1-slot pool

        result = _make_container_result(exit_reason="pm_complete")
        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (None, None),
        )

        with _patch_container_run_with_result(lambda: result):

            async def _runner() -> str:
                return await adapter.run("hello", "/tmp")

            asyncio.run(_runner())

        # The slot index must have been stamped onto the session.
        self.assertEqual(
            session.pty_slot,
            expected_slot_idx,
            f"expected pty_slot={expected_slot_idx!r}, got {session.pty_slot!r} — "
            "slot index did not flow from acquire_pair through to AgentSession.pty_slot",
        )
        # The save call must have included pty_slot in update_fields.
        pty_slot_saves = [c for c in session.saved_calls if "pty_slot" in c]
        self.assertTrue(
            pty_slot_saves,
            f"pty_slot was never included in a save(update_fields=...) call; "
            f"saved_calls: {session.saved_calls!r}",
        )

    def test_partial_data_warning_when_pm_pid_set_but_pty_slot_none(self) -> None:
        """When result.pm_pid is set but result.pty_slot is None, the adapter
        logs a warning containing '[bridge-adapter] pm_pid set but pty_slot is None'.

        This guards the slot-capture regression path: if acquire_pair stops
        yielding slot.idx (e.g. reverted to a 2-tuple), pm_pid will be populated
        by Container but pty_slot will be absent — the warning fires so the
        operator knows to look at the acquire_pair yield shape.
        """
        import logging

        session = _SavingFakeSession()
        pool = _make_initialized_pool(size=1)

        # Build a result with pm_pid set but pty_slot explicitly None.
        # This simulates a regression where slot.idx is no longer stamped.
        result = _make_container_result(exit_reason="pm_complete")
        result.pm_pid = 12345  # type: ignore[attr-defined]
        result.pty_slot = None  # type: ignore[attr-defined]

        adapter = BridgeAdapter(
            agent_session=session,
            project_key="test-project",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (None, None),
        )

        warning_messages: list[str] = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.levelno == logging.WARNING:
                    warning_messages.append(self.format(record))

        handler = _CapturingHandler()
        adapter_logger = logging.getLogger("agent.granite_container.bridge_adapter")
        adapter_logger.addHandler(handler)
        try:
            # Call _publish_exit_summary directly — no full run needed.
            adapter._publish_exit_summary(result)
        finally:
            adapter_logger.removeHandler(handler)

        matching = [m for m in warning_messages if "pm_pid set but pty_slot is None" in m]
        self.assertTrue(
            matching,
            f"expected a warning containing 'pm_pid set but pty_slot is None'; "
            f"warnings captured: {warning_messages!r}",
        )


class TestTranscriptPathSlug(unittest.TestCase):
    """Regression: dotted cwds must slug the dot to '-' like Claude Code does.

    Every bridge session runs in a synthetic ``.worktrees/dev-{id}`` worktree.
    Claude Code slugifies a cwd by replacing BOTH ``/`` and ``.`` with ``-``.
    Replacing only ``/`` produced a path Claude Code never writes to, so the PM
    transcript read came back ``file-missing`` every turn and the run shipped
    OPERATOR_TERMINAL_MESSAGE instead of the PM's real reply.
    """

    def test_dotted_worktree_cwd_replaces_dot_with_dash(self) -> None:
        # Use a tmp-free, symlink-stable absolute path. realpath() on a
        # non-existent path is an identity transform, so the slug is
        # deterministic without touching the filesystem.
        cwd = "/Users/x/src/ai/.worktrees/dev-5732c769"
        uuid = "319a6bb5-aef2-4f92-9a86-7459eb3dee2a"
        path = _transcript_path_from_spec(cwd, uuid)

        # The '.worktrees' segment must become '--worktrees' (slash + dot both
        # collapse to '-'), matching Claude Code's on-disk directory naming.
        self.assertIn("-Users-x-src-ai--worktrees-dev-5732c769", path)
        self.assertNotIn(".worktrees", path)
        self.assertTrue(path.endswith(f"{uuid}.jsonl"))

    def test_slug_matches_claude_codes_directory_naming(self) -> None:
        # Full assertion against the exact expected path.
        cwd = "/Users/x/src/ai/.worktrees/dev-abc"
        uuid = "u"
        home = os.path.expanduser("~")
        expected = os.path.join(
            home,
            ".claude",
            "projects",
            "-Users-x-src-ai--worktrees-dev-abc",
            "u.jsonl",
        )
        self.assertEqual(_transcript_path_from_spec(cwd, uuid), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
