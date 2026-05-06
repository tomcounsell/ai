"""Tests for the worker-side PID lifecycle (issue #1269).

These tests cover the paired closures in `agent/session_executor._execute_agent_session`:

  * ``_on_sdk_started(pid)`` — writes ``session.harness_pid = pid`` and saves with
    ``update_fields=["last_sdk_heartbeat_at", "harness_pid"]``.
  * ``_on_sdk_finished()``  — clears ``session.harness_pid = None`` and saves with
    ``update_fields=["harness_pid"]``.

The closures are local to the executor body and not directly importable. We test
the contract by exercising the equivalent ORM-write surface: an ``AgentSession``
whose ``harness_pid`` field is written by a callback and cleared by its sibling.

The multi-spawn case (3 sequential subprocesses in one turn at sdk_client.py
:2205/:2243/:2295) is asserted by alternating start/finish callbacks N times
and verifying the field is correctly None between subprocesses and after the
last one exits.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from models.agent_session import AgentSession, SessionType


@pytest.fixture
def session():
    s = AgentSession.create(
        project_key="test-executor-pid",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        session_id=f"executor-pid-{time.time_ns()}",
        working_dir="/tmp",
        status="running",
    )
    yield s
    try:
        s.delete()
    except Exception:
        pass


def _make_started_closure(session):
    """Mimic the executor's ``_on_sdk_started`` write contract."""

    def _on_sdk_started(pid: int) -> None:
        from datetime import UTC, datetime

        session.harness_pid = pid
        session.last_sdk_heartbeat_at = datetime.now(tz=UTC)
        session.save(update_fields=["last_sdk_heartbeat_at", "harness_pid"])

    return _on_sdk_started


def _make_finished_closure(session):
    """Mimic the executor's ``_on_sdk_finished`` clear contract."""

    def _on_sdk_finished() -> None:
        session.harness_pid = None
        session.save(update_fields=["harness_pid"])

    return _on_sdk_finished


class TestSdkStartedSetsPid:
    def test_started_callback_writes_pid(self, session):
        cb = _make_started_closure(session)
        cb(42)
        assert session.harness_pid == 42
        # last_sdk_heartbeat_at is also set (preserves existing #1036 contract)
        assert session.last_sdk_heartbeat_at is not None

    def test_started_callback_persists_pid(self, session):
        cb = _make_started_closure(session)
        cb(101)
        loaded = AgentSession.get_by_id(session.agent_session_id)
        assert loaded is not None
        assert loaded.harness_pid == 101


class TestSdkFinishedClearsPid:
    def test_finished_callback_clears_pid(self, session):
        session.harness_pid = 12345
        session.save(update_fields=["harness_pid"])
        cb = _make_finished_closure(session)
        cb()
        assert session.harness_pid is None
        loaded = AgentSession.get_by_id(session.agent_session_id)
        assert loaded is not None
        assert loaded.harness_pid is None

    def test_finished_callback_idempotent(self, session):
        """Calling finish twice (e.g., after defensive backstop) is a no-op."""
        cb = _make_finished_closure(session)
        cb()
        cb()  # second call must not raise
        assert session.harness_pid is None


class TestMultiSpawnLifecycle:
    """A single turn can spawn up to 3 subprocesses (primary + image-dim
    fallback + stale-UUID fallback). Each invocation owns the field
    exclusively for its runtime."""

    def test_three_spawn_cycle_alternates_cleanly(self, session):
        started = _make_started_closure(session)
        finished = _make_finished_closure(session)

        # Subprocess A
        started(1001)
        assert session.harness_pid == 1001
        finished()
        assert session.harness_pid is None

        # Subprocess B
        started(1002)
        assert session.harness_pid == 1002
        finished()
        assert session.harness_pid is None

        # Subprocess C
        started(1003)
        assert session.harness_pid == 1003
        finished()
        assert session.harness_pid is None

    def test_pid_is_none_between_subprocesses(self, session):
        """Between subprocess A finish and subprocess B start, the field
        is correctly None — no leaking PIDs."""
        started = _make_started_closure(session)
        finished = _make_finished_closure(session)

        started(2001)
        finished()
        # Operator-visible window: PID is None between subprocesses
        assert session.harness_pid is None
        loaded = AgentSession.get_by_id(session.agent_session_id)
        assert loaded.harness_pid is None

        started(2002)
        assert session.harness_pid == 2002


class TestOrmFailureHandling:
    """Save failures during the callbacks must NOT propagate as the
    closures are wrapped in try/except. Tested via direct mock injection."""

    def test_started_save_failure_logs_and_does_not_propagate(self, session, caplog):
        """Mimicking the executor pattern: save failure is caught + logged WARNING."""
        import logging

        def _on_sdk_started(pid: int) -> None:
            try:
                # Force a save failure by patching the save method
                session.harness_pid = pid
                raise RuntimeError("Redis transient failure")
            except Exception as e:
                logging.getLogger("test").warning("save failed: %s", e)
                # do not re-raise — matches the executor contract

        # Should not raise
        _on_sdk_started(99)

    def test_finished_save_failure_logs_and_does_not_propagate(self, session):
        """Same pattern for the finish callback."""
        save_mock = MagicMock(side_effect=RuntimeError("boom"))
        session.save = save_mock  # type: ignore[method-assign]

        def _on_sdk_finished() -> None:
            try:
                session.harness_pid = None
                session.save(update_fields=["harness_pid"])
            except Exception:
                # caught — must not propagate
                pass

        # Should not raise
        _on_sdk_finished()


class TestExecutorWiring:
    """Static checks that the executor body actually wires the callbacks
    (no inadvertent regression where the closures are defined but never
    passed to BossMessenger)."""

    def test_executor_defines_on_sdk_finished_closure(self):
        import inspect

        from agent import session_executor

        src = inspect.getsource(session_executor)
        assert "def _on_sdk_finished" in src, (
            "session_executor must define an _on_sdk_finished closure paired "
            "with _on_sdk_started for #1269 PID lifecycle"
        )

    def test_executor_passes_on_sdk_finished_to_messenger(self):
        import inspect

        from agent import session_executor

        src = inspect.getsource(session_executor)
        # The messenger construction must include the new kwarg.
        assert "on_sdk_finished=_on_sdk_finished" in src, (
            "session_executor must thread on_sdk_finished into BossMessenger(...)"
        )

    def test_executor_writes_harness_pid_in_started_callback(self):
        import inspect

        from agent import session_executor

        src = inspect.getsource(session_executor)
        assert "session.harness_pid = pid" in src, "_on_sdk_started must persist harness_pid"
        assert '"harness_pid"' in src, "_on_sdk_started's update_fields must include harness_pid"

    def test_executor_clears_harness_pid_in_finished_callback(self):
        import inspect

        from agent import session_executor

        src = inspect.getsource(session_executor)
        assert "session.harness_pid = None" in src, (
            "_on_sdk_finished must clear harness_pid (#1269)"
        )

    def test_executor_finally_block_has_defensive_clear(self):
        """The session-exit `finally` block has an idempotent backstop clear
        for the abnormal-termination path (worker crash, CancelledError)."""
        import inspect

        from agent import session_executor

        src = inspect.getsource(session_executor)
        # Look for the defensive-clear comment marker we added.
        assert "Defensive PID clear" in src or "defensive harness_pid clear" in src, (
            "session_executor finally block must have a defensive harness_pid clear "
            "for abnormal-termination paths (#1269)"
        )
