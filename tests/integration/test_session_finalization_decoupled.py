"""Integration test for extraction-finalization decoupling (hotfix #1055).

Verifies that the hotfix decouples post-session memory extraction from
session finalization — the session completes and the PM nudge fires promptly,
while extraction is still pending, even if extraction would stall indefinitely.

NOTE (PR #1056 review nit): the plan's Test Impact section called for a
Popoto-backed assertion that the PM's ``queued_steering_messages`` grew by
exactly 1 after extraction was deferred. The ``TestSessionFinalizationDecoupled``
class below uses a simpler ``asyncio.Event`` to signal the PM nudge, because
spinning up a real Popoto + pyrogram + harness-subprocess stack was judged
too heavy for the scheduler-boundary contract being verified there. The
follow-up test class ``TestPMSteeringPopotoIntegration`` below (issue #1057)
now provides the Popoto-backed coverage end-to-end — real ``AgentSession``
pair, real ``steer_session``, real ``push_steering_message``, real Redis
partial-save — asserting the PM's ``queued_steering_messages`` list grew by
exactly 1 within the 5-second SLO.

The full ``_execute_agent_session`` flow requires substantial Redis/Popoto
infrastructure (AgentSession creation, pyrogram bridge, harness subprocess,
session lifecycle transitions). This test focuses on the narrower contract
that the hotfix establishes at the scheduler boundary:

  1. The synchronous ``_schedule_post_session_extraction`` returns promptly
     even when extraction would block for 30+ seconds.
  2. The scheduled task is still pending (``.done() is False``) at the moment
     the scheduler returns.
  3. A simulated "PM nudge" call that runs after the scheduler returns completes
     in the 5-second bounded window — not blocked by extraction latency.
  4. ``drain_pending_extractions`` cancels cooperatively-suspended tasks
     within its timeout budget.
  5. Cancellation does not propagate past the fire-and-forget wrapper's
     ``try/except`` structure (CancelledError is re-raised cleanly for drain).

The 5-second budget is the user-visible SLO: a dev session producing a
30-second stall in extraction must still finalize and nudge the PM within
5 seconds — the core promise of this hotfix.

Why cooperative ``asyncio.sleep`` rather than sync ``time.sleep(40)``?
Real production flows AsyncAnthropic → httpx → asyncio.wait_for, all of
which are cancellable at every await point. A sync ``time.sleep`` in the
coroutine body is uncancellable until the next await — a pathology that
does not reflect production behavior. The double-timeout in
``memory_extraction.py`` (SDK timeout=30s + asyncio.wait_for=35s) hard-caps
the production latency in all cases.
"""

import asyncio
import logging
import time

import pytest


@pytest.fixture(autouse=True)
def _clear_pending_tasks():
    from agent import session_executor as se

    se._pending_extraction_tasks.clear()
    yield
    for task in list(se._pending_extraction_tasks.values()):
        if not task.done():
            task.cancel()
    se._pending_extraction_tasks.clear()


class TestSessionFinalizationDecoupled:
    """End-to-end behavioral test for the decoupling guarantee (hotfix #1055)."""

    @pytest.mark.asyncio
    async def test_scheduler_returns_within_5s_window_with_hung_anthropic(
        self, caplog, monkeypatch
    ):
        """The 5-second user-visible SLO: scheduler returns promptly even with hung extraction.

        This is the core hotfix #1055 guarantee — a 40-second stall in extraction
        must NOT delay finalization and PM nudge beyond the 5-second SLO. We
        stub run_post_session_extraction itself (the top-level async entry point)
        to block on asyncio.sleep(30); in production the outer asyncio.wait_for
        inside memory_extraction.py caps this at 35s anyway, but the point of
        Layer 2 is that even an uncapped 30s stall cannot delay the PM nudge.
        """
        from agent import session_executor as se

        caplog.set_level(logging.INFO, logger="agent.session_executor")

        # NOTE (PR #1056 review nit): plan called for time.sleep(40); we use
        # asyncio.sleep(30) instead because production flows through
        # AsyncAnthropic + httpx + asyncio.wait_for — all cancellable at await
        # points. A sync time.sleep in the coroutine body is uncancellable
        # until the next await, which does not reflect production. The
        # unit-test sibling `test_real_asyncio_wait_for_fires_with_tightened_constants`
        # exercises the real timeout path against a cooperative hang with the
        # hard-timeout shortened via monkeypatch. The scheduler-level SLO this
        # test asserts is identical whether the inner coroutine yields or not.
        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(30)  # Long stall; scheduler must not await.

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        pm_nudge_called = asyncio.Event()

        async def _fake_handle_dev_completion(*args, **kwargs):
            # Simulates the PM nudge that must run AFTER the scheduler returns.
            pm_nudge_called.set()

        start = time.time()

        # Exact call-shape _execute_agent_session uses post-fix:
        # synchronous schedule (no await), then await the PM nudge.
        se._schedule_post_session_extraction("sess-int-1", "A" * 200)
        await _fake_handle_dev_completion()

        elapsed = time.time() - start

        # The entire post-finalization sequence must complete within the 5s SLO.
        assert elapsed < 5.0, (
            f"scheduler + PM-nudge path took {elapsed:.2f}s — violates the 5s SLO. "
            "Extraction latency MUST NOT block session finalization (hotfix #1055)."
        )
        assert pm_nudge_called.is_set(), "PM nudge must fire"

        # The extraction task should still be pending.
        task = se._pending_extraction_tasks.get("sess-int-1")
        assert task is not None, "extraction task must still be registered"
        assert task.done() is False, (
            "extraction task MUST still be pending at this point — "
            "proves scheduler and nudge ran ahead of extraction completion"
        )

        # Cleanup: cancel the cooperatively-suspended task.
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_drain_cancels_cooperative_extraction_on_shutdown(self, caplog, monkeypatch):
        """drain_pending_extractions cancels cooperatively-suspended tasks within timeout.

        Uses asyncio.sleep (not time.sleep) because real production extraction
        goes through AsyncAnthropic + httpx + asyncio.wait_for — all cooperative
        cancellation points. A sync time.sleep in the coroutine body is NOT
        cancellable until the next await, so the production code path does
        not suffer from that pathology.
        """
        from agent import session_executor as se

        caplog.set_level(logging.WARNING, logger="agent.session_executor")

        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(30)  # Cooperative; cancellable at await point.

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        se._schedule_post_session_extraction("sess-drain-1", "A" * 200)
        task = se._pending_extraction_tasks.get("sess-drain-1")
        assert task is not None

        # Drain with a short timeout; the cooperative sleep will be cancelled
        # promptly when drain calls task.cancel().
        start = time.time()
        await se.drain_pending_extractions(timeout=0.5)
        elapsed = time.time() - start

        # Drain budget: ~0.5s timeout + cancel overhead.
        assert elapsed < 2.0, (
            f"drain exceeded its 0.5s timeout by too much (took {elapsed:.2f}s total)"
        )
        # Drain called task.cancel(); give one event-loop tick for the task
        # to finalize in "cancelled" state before we assert.
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except (TimeoutError, asyncio.CancelledError, Exception):
            pass
        assert task.done() or task.cancelled(), "drain must have cancelled the hung task"
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("did not complete" in msg for msg in warning_messages), (
            f"drain should log WARNING when cancelling hung tasks (got {warning_messages})"
        )

    @pytest.mark.asyncio
    async def test_cancellation_does_not_crash_via_wrapper(self, monkeypatch):
        """Cancelling a cooperative-suspended extraction raises CancelledError cleanly.

        Uses asyncio.sleep so cancellation is prompt. Real production flows
        through AsyncAnthropic httpx calls, which are cancellable at every
        await point, plus an outer asyncio.wait_for that hard-caps latency.
        """
        from agent import session_executor as se

        async def _slow_extract(session_id, response_text, project_key=None):
            await asyncio.sleep(30)

        monkeypatch.setattr(
            "agent.memory_extraction.run_post_session_extraction",
            _slow_extract,
        )

        se._schedule_post_session_extraction("sess-cancel-safe", "A" * 200)
        task = se._pending_extraction_tasks.get("sess-cancel-safe")

        task.cancel()

        # Awaiting a cancelled task should raise CancelledError — the
        # critical invariant here is that it raises CancelledError
        # cleanly, NOT a wrapped/swallowed exception that would make
        # shutdown drainage ambiguous.
        with pytest.raises(asyncio.CancelledError):
            await task


class TestPMSteeringPopotoIntegration:
    """Popoto-backed end-to-end steering test (issue #1057, follow-up to hotfix #1055).

    Exercises the post-finalization slice of ``_execute_agent_session``:
    ``_schedule_post_session_extraction`` → ``_handle_dev_session_completion``
    against a real Popoto ``AgentSession`` pair (PM + Dev) with NO patches on
    ``steer_session``, ``push_steering_message``, or ``AgentSession``. Asserts
    that after a dev session completes and extraction is deferred, the PM's
    ``queued_steering_messages`` list on the Popoto record grew by **exactly 1**
    within the 5-second SLO window — the contract the shipped
    ``TestSessionFinalizationDecoupled`` class deferred to this follow-up.
    """

    @pytest.mark.asyncio
    async def test_pm_queued_steering_messages_grew_by_exactly_one_within_5s(self, redis_test_db):
        """PM's ``queued_steering_messages`` grows by exactly 1 within the 5s SLO.

        Real Popoto PM + Dev sessions. Real ``steer_session``. Real
        ``push_steering_message``. Extraction is stubbed to stall past the 5s SLO
        so the test proves the PM inbox write happens ahead of extraction
        completion. No patches on the steering chain itself — only on side-effect
        helpers (``_extract_issue_number`` to skip ``gh`` subprocess,
        ``_call_ensure_worker`` to suppress worker-ping, and the defensive
        harness stub for parity with sibling tests).
        """
        # Canonical import path (plan C2 note): import via the agent_session_queue
        # re-export, NOT from agent.session_completion where the function is
        # defined. Matches production call sites (agent/session_executor.py:16).
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.agent_session_queue import _handle_dev_session_completion
        from agent.pipeline_state import PipelineStateMachine
        from agent.session_executor import (
            _pending_extraction_tasks,
            _schedule_post_session_extraction,
        )
        from models.agent_session import AgentSession

        # ------------------------------------------------------------------
        # Fixture setup: real PM + Dev AgentSession pair.
        # ------------------------------------------------------------------
        now = datetime.now(tz=UTC)
        pm = AgentSession.create(
            session_id=f"pm-1057-{time.time_ns()}",
            session_type="pm",
            project_key="test-1057-popoto",
            status="active",
            chat_id="1057",
            sender_name="TestUser",
            message_text="BUILD issue #1057",
            created_at=now,
            started_at=now,
            updated_at=now,
            turn_count=0,
            tool_call_count=0,
        )

        # Advance PM to BUILD (non-terminal) so is_pipeline_complete() returns
        # False and _handle_dev_session_completion reaches the steer_session
        # branch (not the pipeline-complete branch). Mirrors the pattern in
        # tests/integration/test_parent_child_round_trip.py:158-167.
        sm = PipelineStateMachine(pm)
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")
        sm.complete_stage("PLAN")
        sm.start_stage("CRITIQUE")
        sm.complete_stage("CRITIQUE")
        sm.start_stage("BUILD")

        # Reload PM so subsequent reads see the updated stage_states AND any
        # queued_steering_messages writes made during the test.
        pm = next(iter(AgentSession.query.filter(session_id=pm.session_id)))
        assert PipelineStateMachine(pm).current_stage() == "BUILD", (
            "PM must be at non-terminal BUILD stage so steering branch is taken "
            "(not is_pipeline_complete's completion branch)"
        )

        dev = AgentSession.create(
            session_id=f"dev-1057-{time.time_ns()}",
            session_type="dev",
            project_key="test-1057-popoto",
            status="active",
            chat_id="1057",
            sender_name="TestUser",
            message_text="Stage: BUILD",
            parent_agent_session_id=pm.agent_session_id,
            created_at=now,
            started_at=now,
            updated_at=now,
            turn_count=0,
            tool_call_count=0,
        )

        try:
            # --------------------------------------------------------------
            # Patches: stall extraction, short-circuit side effects.
            # Deliberately NOT patched: steer_session, push_steering_message,
            # AgentSession.* (these are the code under test).
            # --------------------------------------------------------------
            async def _slow_extract(session_id, response_text, project_key=None):
                await asyncio.sleep(10)  # Longer than the 5s SLO — task stays pending.

            baseline = pm.queued_steering_messages or []
            baseline_len = len(baseline)
            assert baseline_len == 0, (
                f"Fresh PM fixture must start with empty queue; got {baseline_len}"
            )

            result = "PR created at https://github.com/test/repo/pull/42. BUILD complete."

            with (
                patch(
                    "agent.memory_extraction.run_post_session_extraction",
                    side_effect=_slow_extract,
                ),
                patch(
                    "agent.session_completion._extract_issue_number",
                    return_value=None,
                ),
                patch(
                    "agent.session_executor._call_ensure_worker",
                    MagicMock(),
                ),
                patch(
                    "agent.sdk_client.get_response_via_harness",
                    AsyncMock(return_value=result),
                ),
            ):
                # Exact call-shape from agent/session_executor.py:1478-1507:
                # synchronous schedule (no await), then await the dev-completion
                # handler. No complete_transcript call — deliberate omission per
                # plan (C1 note): this test's scope is the post-finalization slice
                # only. The #987 transcript-ordering invariant has direct coverage
                # in sibling tests (test_parent_child_round_trip.py, session-
                # executor unit tests).
                t0 = time.monotonic()
                _schedule_post_session_extraction(dev.session_id, result)
                await _handle_dev_session_completion(
                    session=dev,
                    agent_session=dev,
                    result=result,
                )
                elapsed = time.monotonic() - t0

            # --------------------------------------------------------------
            # Assertions: the PM inbox grew by exactly 1 within the SLO window,
            # and the extraction task is still pending.
            # --------------------------------------------------------------
            pm_reloaded = next(iter(AgentSession.query.filter(session_id=pm.session_id)))
            new_queue = pm_reloaded.queued_steering_messages or []
            new_len = len(new_queue)

            assert new_len == baseline_len + 1 == 1, (
                f"PM queued_steering_messages must grow by exactly 1: "
                f"baseline={baseline_len}, after={new_len}, "
                f"delta={new_len - baseline_len}, elapsed={elapsed:.3f}s"
            )

            assert elapsed < 5.0, (
                f"_schedule_post_session_extraction + _handle_dev_session_completion "
                f"took {elapsed:.3f}s — violates the 5s SLO from hotfix #1055"
            )

            last_msg = new_queue[-1]
            assert "Dev session completed" in last_msg, (
                f"Steering message must include the completion preamble; got: {last_msg!r}"
            )
            assert "BUILD" in last_msg, (
                f"Steering message must include the stage label; got: {last_msg!r}"
            )

            # Prove the extraction stall did not delay the inbox write — the
            # task is still pending in the registry at assertion time.
            assert dev.session_id in _pending_extraction_tasks, (
                "Extraction task must still be registered after _handle_dev_session_completion"
            )
            extraction_task = _pending_extraction_tasks[dev.session_id]
            assert extraction_task.done() is False, (
                "Extraction task must still be pending — proves steering ran ahead of extraction"
            )

        finally:
            # --------------------------------------------------------------
            # Cleanup: cancel any lingering extraction task (autouse fixture
            # also does this), and delete both sessions via Popoto ORM
            # (respects CLAUDE.md Manual Testing Hygiene despite redis_test_db
            # flushdb on teardown).
            # --------------------------------------------------------------
            task = _pending_extraction_tasks.get(dev.session_id)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                dev.delete()
            except Exception:
                pass
            try:
                pm.delete()
            except Exception:
                pass
