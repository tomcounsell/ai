"""Tests for the runner init-hang first-output deadline + circuit breaker (#2181).

Covers the three focused deliverables:

  1. First-output deadline predicate ``_first_output_deadline_exceeded`` — a
     never-communicated (``communicated=False``) session past
     ``FIRST_OUTPUT_DEADLINE_S`` is flagged, INDEPENDENT of the flat-CPU hang
     probe; a session that has produced output, or one still inside the
     deadline, is not.
  2. Circuit breaker — ``_apply_recovery_transition`` with
     ``reason_kind="init_hang"`` finalizes the session terminal (``failed``
     remote / ``abandoned`` local) on the FIRST occurrence and NEVER requeues to
     ``pending``.
  3. Diagnostics — ``_log_init_hang_stderr`` drains and logs a killed
     subprocess's buffered stderr, and never raises.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import agent.session_health as session_health

# ---------------------------------------------------------------------------
# Deliverable 1 — first-output deadline predicate
# ---------------------------------------------------------------------------


class TestFirstOutputDeadlinePredicate:
    """``_first_output_deadline_exceeded`` — the never-communicated leg."""

    @staticmethod
    def _entry(**overrides):
        defaults = dict(last_tool_use_at=None, last_turn_at=None, last_stdout_at=None)
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_never_communicated_past_deadline_flagged(self):
        from agent import agent_session_queue as q

        entry = self._entry()  # derive_sdk_ever_output False
        acquired_at = 0.0  # long ago
        with patch(
            "agent.agent_session_queue.time.time", return_value=q.FIRST_OUTPUT_DEADLINE_S + 5
        ):
            assert q._first_output_deadline_exceeded(entry, acquired_at) is True

    def test_never_communicated_inside_deadline_not_flagged(self):
        from agent import agent_session_queue as q

        entry = self._entry()
        acquired_at = 0.0
        with patch(
            "agent.agent_session_queue.time.time",
            return_value=q.FIRST_OUTPUT_DEADLINE_S - 60,
        ):
            assert q._first_output_deadline_exceeded(entry, acquired_at) is False

    def test_communicated_session_never_flagged(self):
        """A session that emitted a stdout event (last_stdout_at) is never flagged,
        no matter how old — the blind spot leg only targets communicated=False."""
        from agent import agent_session_queue as q

        entry = self._entry(last_stdout_at=datetime.now(UTC))
        acquired_at = 0.0
        with patch(
            "agent.agent_session_queue.time.time",
            return_value=q.FIRST_OUTPUT_DEADLINE_S * 10,
        ):
            assert q._first_output_deadline_exceeded(entry, acquired_at) is False

    def test_deadline_below_progress_catch_all(self):
        """The first-output deadline must fire meaningfully sooner than the 1800s
        SESSION_PROGRESS_DEADLINE_S catch-all it front-runs."""
        from agent import agent_session_queue as q

        assert q.FIRST_OUTPUT_DEADLINE_S < q.SESSION_PROGRESS_DEADLINE_S


# ---------------------------------------------------------------------------
# Deliverable 2 — init-hang circuit breaker in _apply_recovery_transition
# ---------------------------------------------------------------------------


def _recovery_entry(**overrides):
    """A running remote session on its first recovery attempt, never-communicated."""
    now = datetime.now(UTC)
    saves: list[list[str]] = []

    def _save(update_fields=None, **_kw):
        saves.append(list(update_fields) if update_fields else [])

    defaults = dict(
        agent_session_id="ih-sess-1",
        id="ih-sess-1",
        session_id="sid-ih-1",
        status="running",
        project_key="test-init-hang",
        current_tool_name=None,
        last_tool_use_at=None,
        last_turn_at=None,
        last_stdout_at=None,
        last_heartbeat_at=now,
        created_at=now - timedelta(seconds=1400),
        started_at=None,
        response_delivered_at=None,
        claude_pid=None,
        claude_session_uuid=None,
        extra_context={},
        worker_key="telegram-cyndra",
        recovery_attempts=0,
        reprieve_count=0,
        is_project_keyed=True,
        get_children=MagicMock(return_value=[]),
        save=_save,
        delete=lambda **_kw: None,
        _saves=saves,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def _drive_recovery(entry, worker_key, reason_kind):
    """Drive ``_apply_recovery_transition`` with the confirm-dead + terminal seams
    stubbed, returning the finalize/transition calls it made."""
    finalize_calls: list[tuple] = []
    transition_calls: list[tuple] = []

    def _fake_finalize(e, status, **kw):
        finalize_calls.append((status, kw.get("reason")))

    def _fake_transition(e, status, **kw):
        transition_calls.append((status, kw.get("reason")))

    kill_result = SimpleNamespace(confirmed_dead=True, signal_sent=False)

    with (
        patch.object(session_health, "_delivery_belongs_to_current_run", lambda e: False),
        patch.object(
            session_health,
            "_confirm_subprocess_dead",
            lambda *a, **k: kill_result,
        ),
        patch.object(session_health, "_deliver_deferred_self_draft_fallback", AsyncMock()),
        patch.object(session_health, "_deliver_terminal_interrupt_notice", AsyncMock()),
        patch.dict(
            "sys.modules",
            {
                "popoto.redis_db": SimpleNamespace(POPOTO_REDIS_DB=MagicMock()),
                "models.session_lifecycle": SimpleNamespace(
                    StatusConflictError=type("StatusConflictError", (Exception,), {}),
                    finalize_session=_fake_finalize,
                    transition_status=_fake_transition,
                ),
            },
        ),
        patch("agent.cancel_reason.set_cancel_reason", lambda *a, **k: None),
    ):
        await session_health._apply_recovery_transition(
            entry,
            reason="init hang — runner produced no output",
            reason_kind=reason_kind,
            handle=None,
            worker_key=worker_key,
        )
    return finalize_calls, transition_calls


class TestInitHangCircuitBreaker:
    async def test_init_hang_finalizes_failed_never_requeues(self):
        """A remote init_hang on the FIRST attempt finalizes ``failed`` and never
        transitions to ``pending`` (the circuit breaker)."""
        entry = _recovery_entry(recovery_attempts=0)
        finalize_calls, transition_calls = await _drive_recovery(
            entry, "telegram-cyndra", "init_hang"
        )
        assert any(status == "failed" for status, _ in finalize_calls)
        assert all(status != "pending" for status, _ in transition_calls)
        assert transition_calls == []  # never requeued

    async def test_progress_deadline_first_attempt_requeues(self):
        """Control: the SAME first-attempt session under ``progress_deadline``
        (a communicated session's genuine stall) DOES requeue to ``pending`` —
        proving init_hang's terminal behavior is the distinguishing change."""
        entry = _recovery_entry(recovery_attempts=0)
        finalize_calls, transition_calls = await _drive_recovery(
            entry, "telegram-cyndra", "progress_deadline"
        )
        assert any(status == "pending" for status, _ in transition_calls)
        assert all(status != "failed" for status, _ in finalize_calls)

    async def test_init_hang_local_abandons(self):
        """A local init_hang finalizes ``abandoned`` (terminal), still no requeue."""
        entry = _recovery_entry(recovery_attempts=0)
        finalize_calls, transition_calls = await _drive_recovery(entry, "local-dev", "init_hang")
        assert any(status == "abandoned" for status, _ in finalize_calls)
        assert transition_calls == []


# ---------------------------------------------------------------------------
# Deliverable 3 — diagnostics
# ---------------------------------------------------------------------------


class _FakeStderr:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self, n: int = -1) -> bytes:
        return self._data


class TestInitHangStderrDiagnostics:
    async def test_logs_buffered_stderr(self):
        from agent.session_runner.harness import claude

        proc = SimpleNamespace(stderr=_FakeStderr(b"MCP server 'x' failed to connect"))
        with patch.object(claude.logger, "warning") as warn:
            await claude._log_init_hang_stderr(proc, "sid-123")
        assert warn.called
        logged = " ".join(str(a) for a in warn.call_args[0])
        assert "sid-123" in logged or "sid-123" in str(warn.call_args)

    async def test_empty_stderr_does_not_raise(self):
        from agent.session_runner.harness import claude

        proc = SimpleNamespace(stderr=_FakeStderr(b""))
        await claude._log_init_hang_stderr(proc, "sid-empty")  # must not raise

    async def test_missing_stderr_is_noop(self):
        from agent.session_runner.harness import claude

        proc = SimpleNamespace(stderr=None)
        await claude._log_init_hang_stderr(proc, "sid-none")  # must not raise

    async def test_read_timeout_is_swallowed(self, monkeypatch):
        from agent.session_runner.harness import claude

        class _HangStderr:
            async def read(self, n: int = -1) -> bytes:
                import asyncio

                await asyncio.sleep(10)
                return b""

        monkeypatch.setattr(claude, "INIT_HANG_STDERR_TIMEOUT_S", 0.01)
        proc = SimpleNamespace(stderr=_HangStderr())
        await claude._log_init_hang_stderr(proc, "sid-hang")  # must return, not hang
