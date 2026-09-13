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

1. raise  -> row reaches a terminal status, and specifically ``failed`` even
   when ``task.error`` is falsy — a session whose executor raised did not
   complete, and ``_runner_final_status`` has no notion of unwinding
2. cancel -> row stays ``running`` (owned by the health checker / startup
   recovery, which requeue it; see the test for why finalizing here would
   silently retire that retry loop), on both the constructor-raise and the
   production ``await task.run(...)`` shapes
3. nudge main path     -> ``pending`` survives
4. nudge fallback path -> the ``pending`` continuation is the only row, and
   survives

Both nudge tests drive ``_enqueue_nudge`` itself rather than replaying the
state it writes, so they pin the mechanism and not just the conclusion.

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
from models.session_lifecycle import TERMINAL_STATUSES, get_authoritative_session


class FakeBackgroundTask:
    """Stands in for ``BackgroundTask`` so a raise (or a cancel) can land with
    ``task`` bound and ``task.error`` falsy — the shape ``_runner_final_status``
    reads as ``"completed"``.

    ``on_run`` is what the executor's ``await task.run(...)`` raises.
    """

    on_run = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.error = None

    async def run(self, coro, send_result=False):
        coro.close()
        if type(self).on_run is not None:
            raise type(self).on_run
        return ""


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
    FakeBackgroundTask.on_run = None
    yield
    FakeSessionRunner.on_run = None
    FakeBackgroundTask.on_run = None


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
        assert reloaded.status in TERMINAL_STATUSES

    @pytest.mark.asyncio
    async def test_raise_with_clean_task_finalizes_failed_not_completed(self, redis_test_db):
        """The status-honesty case: the executor unwinds on an exception while
        ``task`` is bound and ``task.error`` is falsy.

        ``_runner_final_status(None, agent_session)`` returns ``"completed"``
        there — it has no notion of the function unwinding — so without the
        ``raised`` flag the guard records a session that crashed as a success.
        Beyond the false status, that also disagrees with the worker's own
        ``failed`` write in ``agent_session_queue``'s outer ``finally``, and a
        terminal -> different-terminal write raises ``StatusConflictError``.

        Drives the real path: the raise comes out of ``await task.run(...)``
        inside ``_execute_agent_session``, not a hand-built row state.
        """
        sid = _sid("exit-guard-clean-raise")
        session = _make_session(sid)

        FakeBackgroundTask.on_run = RuntimeError("boom with a clean task")

        with (
            _patch_runner(),
            patch("agent.BackgroundTask", FakeBackgroundTask),
        ):
            with pytest.raises(RuntimeError, match="boom with a clean task"):
                await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        assert reloaded.status == "failed", (
            "a session whose executor raised did not complete — recording "
            "`completed` here is a false terminal status and collides with the "
            "worker's own `failed` write"
        )

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

    @pytest.mark.asyncio
    async def test_cancel_with_task_bound_also_leaves_row_running(self, redis_test_db):
        """The production shape: the health checker cancels ``handle.task``, so
        the ``CancelledError`` surfaces out of ``await task.run(...)`` with
        ``task`` already bound — not out of the ``BackgroundTask`` constructor.

        ``except asyncio.CancelledError`` must win over the ``except
        BaseException`` clause that flags a raising exit, or a cancel would be
        finalized ``failed`` and the requeue loop would be retired.
        """
        sid = _sid("exit-guard-cancel-bound")
        session = _make_session(sid)

        FakeBackgroundTask.on_run = asyncio.CancelledError()

        with (
            _patch_runner(),
            patch("agent.BackgroundTask", FakeBackgroundTask),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        assert reloaded.status == "running", (
            "a cancel landing on `await task.run(...)` is still a cancel — the "
            "raising-exit flag must not claim it"
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
        protect this write, and re-keying must not regress it.

        Drives ``_enqueue_nudge`` itself rather than replaying its write, so
        the test pins the mechanism and not just the conclusion.
        """
        from agent.session_executor import _enqueue_nudge, _finalize_if_still_running

        sid = _sid("exit-guard-nudge-main")
        session = _make_session(sid)

        await _enqueue_nudge(
            session,
            branch_name="",
            task_list_id="",
            auto_continue_count=1,
            output_msg="partial output",
        )

        after_nudge = get_authoritative_session(sid)
        assert after_nudge is not None
        assert after_nudge.status == "pending", "precondition: the nudge wrote pending"

        _finalize_if_still_running(sid, None, None, reason="test")

        reloaded = get_authoritative_session(sid)
        assert reloaded is not None
        assert reloaded.status == "pending", "the nudge's authoritative write was clobbered"
        assert reloaded.auto_continue_count == 1

    @pytest.mark.asyncio
    async def test_fallback_path_leaves_only_a_pending_continuation(self, redis_test_db):
        """``_enqueue_nudge``'s fallback path is entered ONLY because
        ``get_authoritative_session(orig_session_id)`` returned ``None`` — no
        row for that ``session_id`` was visible at all. So the state it
        produces is exactly one row, the fresh ``pending`` continuation, and
        the guard must no-op on it.

        This is the correction to the PR's original claim that the fallback
        "leaves the ORIGINAL row at ``running``" for the guard to clear. It
        does not: the trigger for the fallback is the absence of that row.

        Drives the real fallback by deleting the row (via the ORM) before
        calling ``_enqueue_nudge``, which is what makes the re-read return
        ``None``.
        """
        from agent.session_executor import _enqueue_nudge, _finalize_if_still_running

        sid = _sid("exit-guard-nudge-fallback")
        original = _make_session(sid)
        original.delete()
        assert get_authoritative_session(sid) is None, "precondition: fallback trigger"

        await _enqueue_nudge(
            original,
            branch_name="",
            task_list_id="",
            auto_continue_count=1,
            output_msg="partial output",
        )

        continuation = get_authoritative_session(sid)
        assert continuation is not None, "the fallback must have recreated the session"
        assert continuation.status == "pending"
        assert continuation.id != original.id, "the continuation is a distinct record"

        _finalize_if_still_running(sid, None, None, reason="test")

        reloaded = get_authoritative_session(sid)
        assert reloaded is not None
        assert reloaded.status == "pending", (
            "the nudge fallback's continuation record must survive untouched — "
            "it is the row the worker will pop"
        )
        assert reloaded.auto_continue_count == 1

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
