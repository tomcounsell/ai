"""Exit-path finalize guarantee for ``_execute_agent_session`` (issue #3209).

The finalize guard used to sit in the ``try`` body behind
``if not chat_state.defer_reaction:``, so only a NORMAL RETURN reached it. A
raise walked straight past it into the ``finally`` with the row still
``running``, where nothing else would ever finalize it — a phantom-``running``
row until the health-check sweep, and a lane whose worktree cleanup its own
busy check then refuses.

The guard now lives in the ``finally`` and is keyed on
``status == "running"``, not on ``defer_reaction``. These tests pin the four
behaviours that re-keying has to get right:

1. raise  -> row reaches a terminal status
2. cancel -> row stays ``running`` (owned by the health checker / startup
   recovery, which requeue it; see the test for why finalizing here would
   silently retire that retry loop)
3. nudge main path     -> ``pending`` survives
4. nudge fallback path -> ``pending`` continuation survives AND the stranded
   original row is finalized

Real integration tests against the local Redis test DB (autouse
``redis_test_db`` fixture) — no mocks for the ORM/Redis layer, only the
runner is faked. Session ids are uuid-suffixed so parallel runs never collide.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from agent.session_executor import _execute_agent_session
from models.agent_session import AgentSession
from models.session_lifecycle import get_authoritative_session

_TERMINAL = {"completed", "failed", "killed", "abandoned", "cancelled"}


class FakeSessionRunner:
    """Never spawns a real Claude subprocess. ``on_run`` may raise."""

    on_run = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs

    async def run(self, user_message: str):
        if type(self).on_run is not None:
            type(self).on_run()
        from agent.session_runner import RunSummary

        return RunSummary(exit_reason="pm_complete", turn_count=1)


@pytest.fixture(autouse=True)
def _reset_fake_runner():
    FakeSessionRunner.on_run = None
    yield
    FakeSessionRunner.on_run = None


def _patch_runner():
    return patch("agent.session_runner.SessionRunner", FakeSessionRunner)


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _make_session(session_id: str, **overrides) -> AgentSession:
    defaults = dict(
        session_id=session_id,
        session_type="teammate",
        project_key="test-exit-finalize-guard",
        working_dir="/tmp",
        status="running",
        chat_id=f"chat-{uuid.uuid4().hex[:8]}",
        message_text="hello",
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )
    defaults.update(overrides)
    return AgentSession.create(**defaults)


# ---------------------------------------------------------------------------
# 1. The raise path — the gap #3209 exists to close
# ---------------------------------------------------------------------------


class TestRaisingExitFinalizes:
    @pytest.mark.asyncio
    async def test_raise_after_task_bound_leaves_no_phantom_running_row(self, redis_test_db):
        """A raise from the post-run teardown, AFTER ``task`` is bound and
        BEFORE the guard's old in-``try`` position.

        The runner fails, so ``_maybe_send_failure_notice`` runs — and that
        call is not wrapped in a try. Making it raise walks the exception
        straight past where the old guard sat.

        RED on the pre-#3209 code: the guard was in the ``try`` body, the
        exception jumped over it, and the row stayed ``running`` forever.
        """
        sid = _sid("exit-guard-raise")
        session = _make_session(sid)

        def _boom():
            raise RuntimeError("runner blew up")

        FakeSessionRunner.on_run = _boom

        with (
            _patch_runner(),
            patch(
                "agent.session_executor._maybe_send_failure_notice",
                new=AsyncMock(side_effect=RuntimeError("boom mid-teardown")),
            ),
        ):
            with pytest.raises(RuntimeError, match="boom mid-teardown"):
                await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        assert reloaded.status != "running", (
            "a raising exit must not leave a phantom-running row — nothing "
            "downstream of the executor owns finalizing it"
        )
        assert reloaded.status in _TERMINAL

    @pytest.mark.asyncio
    async def test_raise_before_task_bound_finalizes_failed_not_completed(self, redis_test_db):
        """``task`` is unbound when the body raises early. The honest status
        is ``failed``: ``_runner_final_status(None, None)`` returns
        ``"completed"``, which would report a crash-before-start as a
        success."""
        sid = _sid("exit-guard-early-raise")
        session = _make_session(sid)

        with (
            _patch_runner(),
            patch("agent.BackgroundTask", side_effect=RuntimeError("boom before task bound")),
        ):
            with pytest.raises(RuntimeError):
                await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        assert reloaded.status == "failed"


# ---------------------------------------------------------------------------
# 2. The cancellation path — deliberately NOT finalized here
# ---------------------------------------------------------------------------


class TestCancelledExitLeavesRecoveryOwner:
    @pytest.mark.asyncio
    async def test_cancelled_exit_leaves_row_running_for_its_recovery_owner(self, redis_test_db):
        """Cancellation is the one exit whose terminal transition the executor
        does not own, and the guard must stay off it.

        The health checker cancels ``handle.task`` and then decides the row's
        fate inside the same await — usually ``transition_status(entry,
        "pending")`` to requeue for another attempt
        (``_agent_session_health_check``'s recovery branch).
        ``transition_status`` rejects a terminal source status, so a finalize
        from the executor's ``finally`` would win the race, turn that requeue
        into a swallowed ``StatusConflictError``, and silently retire the
        retry loop for every no-progress session. Worker-shutdown
        cancellation is owned the same way, by
        ``_recover_interrupted_agent_sessions_startup``.
        """
        sid = _sid("exit-guard-cancel")
        session = _make_session(sid)

        def _cancel(*_args, **_kwargs):
            raise asyncio.CancelledError()

        with (
            _patch_runner(),
            patch("agent.BackgroundTask", side_effect=_cancel),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        assert reloaded.status == "running", (
            "the executor must leave a cancelled session running so the health "
            "checker's requeue (transition_status -> pending) is still legal"
        )


# ---------------------------------------------------------------------------
# 3 + 4. Both _enqueue_nudge paths keep their pending write
# ---------------------------------------------------------------------------


class TestNudgePendingStateSurvives:
    @pytest.mark.asyncio
    async def test_main_path_pending_survives_the_guard(self, redis_test_db):
        """``_enqueue_nudge``'s main path moves THIS row to ``pending`` via
        ``transition_status``. The guard's ``status == "running"`` predicate
        must no-op on it — the old ``defer_reaction`` gate is what used to
        protect this write, and re-keying must not regress it."""
        from agent.session_executor import _finalize_if_still_running
        from models.session_lifecycle import transition_status

        sid = _sid("exit-guard-nudge-main")
        session = _make_session(sid)
        session.auto_continue_count = 1
        transition_status(session, "pending", reason="nudge re-enqueue (test)")

        _finalize_if_still_running(sid, None, None, reason="test")

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        assert reloaded.status == "pending", "the nudge's authoritative write was clobbered"
        assert reloaded.auto_continue_count == 1

    @pytest.mark.asyncio
    async def test_fallback_path_pending_continuation_survives_and_original_finalizes(
        self, redis_test_db
    ):
        """The subtle one. ``_enqueue_nudge``'s fallback path creates a FRESH
        ``pending`` record under the same ``session_id`` and leaves the
        ORIGINAL row untouched at ``running``.

        ``get_authoritative_session`` prefers a ``running`` record, so the
        guard finalizes exactly that stranded original — the phantom — while
        the continuation (a distinct record, its own ``agent_session_id``)
        stays ``pending`` for the worker to pop.
        """
        from agent.session_executor import _finalize_if_still_running

        sid = _sid("exit-guard-nudge-fallback")
        original = _make_session(sid)
        continuation = _make_session(
            sid,
            status="pending",
            chat_id=original.chat_id,
            message_text="continue",
            sender_name="System (auto-continue)",
            auto_continue_count=1,
        )
        assert continuation.id != original.id

        _finalize_if_still_running(sid, None, None, reason="test")

        reloaded_continuation = AgentSession.get_by_id(continuation.id)
        assert reloaded_continuation is not None
        assert reloaded_continuation.status == "pending", (
            "the nudge fallback's continuation record must survive untouched — "
            "it is the row the worker will pop"
        )
        assert reloaded_continuation.auto_continue_count == 1

        reloaded_original = AgentSession.get_by_id(original.id)
        assert reloaded_original is not None
        assert reloaded_original.status != "running", (
            "the fallback path leaves the ORIGINAL row running with no other "
            "owner — that is the phantom the guard exists to clear"
        )

    @pytest.mark.asyncio
    async def test_guard_no_ops_on_an_already_terminal_row(self, redis_test_db):
        """The happy path: ``complete_transcript`` already finalized, so the
        guard re-reads a terminal row and does nothing."""
        from agent.session_executor import _finalize_if_still_running
        from models.session_lifecycle import finalize_session

        sid = _sid("exit-guard-noop")
        session = _make_session(sid)
        finalize_session(
            session,
            "completed",
            reason="test: already finalized",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )

        _finalize_if_still_running(sid, None, None, reason="test")

        assert get_authoritative_session(sid).status == "completed"
