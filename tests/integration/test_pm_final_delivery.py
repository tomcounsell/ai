"""Integration tests for PM final-delivery protocol (issue #1058).

Three end-to-end scenarios covering the plan's failure modes:

1. Happy-path MERGE success — completion runner delivers a summary via
   send_cb within 60s.
2. Empty harness result — runner falls back to the supplied summary context.
3. CancelledError mid-completion-turn — runner delivers the "interrupted"
   line (dedup'd by Redis) and re-raises for shutdown.

Pattern follows `tests/integration/test_session_finalization_decoupled.py`:
we exercise the runner boundary with real asyncio tasks and patched harness/
Redis layers, rather than spinning up the full Popoto + pyrogram + harness-
subprocess stack.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import session_completion

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_completion_tasks():
    session_completion._pending_completion_tasks.clear()
    yield
    for t in list(session_completion._pending_completion_tasks.values()):
        if not t.done():
            t.cancel()
    session_completion._pending_completion_tasks.clear()


@pytest.fixture
def parent():
    p = MagicMock()
    p.agent_session_id = f"parent-{int(time.time() * 1000)}"
    p.session_id = f"tg_valor_-111_{int(time.time() * 1000)}"
    p.chat_id = "-111"
    p.telegram_message_id = 42
    p.project_key = "valor"
    p.transport = None
    p.project_config = {"working_directory": "/tmp"}
    p.save = MagicMock()
    return p


def _redis_ok():
    db = MagicMock()
    db.set = MagicMock(return_value=True)
    db.exists = MagicMock(return_value=False)
    return db


# -----------------------------------------------------------------------------
# Happy path: MERGE success → summary delivered within 60s
# -----------------------------------------------------------------------------


class TestHappyPathMergeSuccess:
    async def test_happy_path_merge_success_delivers_summary_within_60s(self, parent):
        """The runner's end-to-end latency budget: final message on Telegram
        within 60s of pipeline completion. The internal harness call is the
        dominant cost; we stub it to <1s and verify the boundary behavior."""

        send_cb = AsyncMock(return_value=None)
        summary = (
            "I built issue #1058. Cleaned up the marker protocol and shipped the "
            "dedicated completion-turn runner."
        )

        async def _fake_harness(**_kw):
            await asyncio.sleep(0.05)
            return summary

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_fake_harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="some-uuid"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            started_at = time.monotonic()
            task = session_completion.schedule_pipeline_completion(
                parent,
                "MERGE completed with outcome=success.",
                send_cb,
                parent.chat_id,
                parent.telegram_message_id,
            )
            assert task is not None
            await asyncio.wait_for(task, timeout=60.0)
            elapsed = time.monotonic() - started_at

        assert elapsed < 60.0, f"SLO miss: completion took {elapsed:.2f}s"
        send_cb.assert_awaited_once()
        [args] = [c.args for c in send_cb.await_args_list]
        assert args[0] == parent.chat_id
        assert args[1] == summary
        _fs.assert_called_once()
        assert _fs.call_args.args[1] == "completed"
        assert parent.response_delivered_at is not None


# -----------------------------------------------------------------------------
# Empty / failing harness → degraded-fallback message delivered (D6 v2)
# -----------------------------------------------------------------------------


class TestDegradedFallbackDelivery:
    """Under the D6(c) v2 contract from plan #1129:
    - Drafter failures (empty / exception / _HARNESS_NOT_FOUND_PREFIX) MUST
      still deliver a user-visible message (no silent fail, never return
      empty).
    - That message is the `_build_degraded_fallback` output — visibly
      prefixed so operators can see the drafter was unavailable.
    - Session still finalizes to 'completed'.
    """

    async def test_empty_harness_delivers_degraded_fallback(self, parent):
        send_cb = AsyncMock(return_value=None)
        fallback_ctx = "Stage MERGE completed with outcome=success. Result preview: tests green."

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=AsyncMock(return_value="")),
            patch("agent.sdk_client._get_prior_session_uuid", return_value=None),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            task = session_completion.schedule_pipeline_completion(
                parent, fallback_ctx, send_cb, parent.chat_id, None
            )
            assert task is not None
            await asyncio.wait_for(task, timeout=10.0)

        send_cb.assert_awaited_once()
        delivered = send_cb.await_args.args[1]
        # D6 v2 contract: degraded-fallback prefix + truncated context.
        assert delivered.startswith("[drafter unavailable — pipeline completed]")
        assert fallback_ctx in delivered
        _fs.assert_called_once()
        assert _fs.call_args.args[1] == "completed"

    async def test_harness_exception_delivers_degraded_fallback(self, parent):
        send_cb = AsyncMock(return_value=None)

        async def _boom(**_kw):
            raise RuntimeError("harness exploded mid-turn")

        fallback_ctx = "MERGE outcome=success. Harness-error fallback."
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_boom),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            task = session_completion.schedule_pipeline_completion(
                parent, fallback_ctx, send_cb, parent.chat_id, None
            )
            assert task is not None
            await asyncio.wait_for(task, timeout=10.0)

        send_cb.assert_awaited_once()
        delivered = send_cb.await_args.args[1]
        assert delivered.startswith("[drafter unavailable — pipeline completed]")
        assert fallback_ctx in delivered
        _fs.assert_called_once()


# -----------------------------------------------------------------------------
# Two-pass flow: both passes fire, both pinned to model=opus
# -----------------------------------------------------------------------------


class TestTwoPassFlow:
    async def test_two_passes_both_use_opus(self, parent):
        send_cb = AsyncMock(return_value=None)
        captured_models: list = []

        async def _capture(**kw):
            captured_models.append(kw.get("model"))
            return "draft" if len(captured_models) == 1 else "refined"

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_capture),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="pm-uuid"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            task = session_completion.schedule_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )
            assert task is not None
            await asyncio.wait_for(task, timeout=10.0)

        # Both passes fired and both pinned model="opus".
        assert len(captured_models) == 2, f"Expected 2 harness calls, got {captured_models}"
        assert captured_models == ["opus", "opus"]
        # Refined text wins.
        assert send_cb.await_args.args[1] == "refined"


# -----------------------------------------------------------------------------
# CancelledError → interrupted message
# -----------------------------------------------------------------------------


class TestCancelledErrorInterrupted:
    async def test_cancelled_error_delivers_interrupted_message(self, parent):
        send_cb = AsyncMock(return_value=None)

        async def _slow_harness(**_kw):
            await asyncio.sleep(60.0)
            return "never"

        # Acquire pipeline_complete_pending AND interrupted-sent on first call
        # each. Both return True.
        redis_db = MagicMock()
        redis_db.set = MagicMock(return_value=True)
        redis_db.exists = MagicMock(return_value=False)

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
            patch("agent.sdk_client.get_response_via_harness", new=_slow_harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
        ):
            task = session_completion.schedule_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )
            assert task is not None
            # Drain cancels the runner mid-harness.
            await session_completion.drain_pending_completions(timeout=0.2)
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                pass

        # The runner's CancelledError handler best-effort delivers the
        # interrupted message and re-raises.
        interrupted = [
            c.args
            for c in send_cb.await_args_list
            if isinstance(c.args[1], str) and "interrupted" in c.args[1].lower()
        ]
        assert len(interrupted) == 1
        assert "resume automatically" in interrupted[0][1]

    async def test_cancelled_then_second_cancel_does_not_duplicate_interrupted(self, parent):
        """Risk 6 flap-dedup: two cancellations of two runners for the same
        session within 120s must result in exactly one interrupted message."""
        send_cb = AsyncMock(return_value=None)

        async def _slow(**_kw):
            await asyncio.sleep(60.0)
            return "never"

        redis_db = MagicMock()
        # pipeline_complete_pending acquired for both calls,
        # interrupted-sent acquired first call, False (held) second call.
        redis_db.set = MagicMock(side_effect=[True, True, True, False])
        redis_db.exists = MagicMock(return_value=False)

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
            patch("agent.sdk_client.get_response_via_harness", new=_slow),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
        ):
            # First runner: start, cancel, record interrupted.
            first = session_completion.schedule_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )
            assert first is not None
            await session_completion.drain_pending_completions(timeout=0.2)
            try:
                await asyncio.wait_for(first, timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                pass

            # Simulate a flapping second runner with a fresh parent (different
            # agent_session_id so scheduler dedup doesn't short-circuit) but
            # the same session_id for the interrupted-sent lock.
            parent2 = MagicMock()
            parent2.agent_session_id = f"{parent.agent_session_id}-retry"
            parent2.session_id = parent.session_id  # same session → same dedup key
            parent2.chat_id = parent.chat_id
            parent2.telegram_message_id = parent.telegram_message_id
            parent2.project_key = parent.project_key
            parent2.transport = None
            parent2.project_config = parent.project_config
            parent2.save = MagicMock()

            second = session_completion.schedule_pipeline_completion(
                parent2, "ctx", send_cb, parent2.chat_id, None
            )
            assert second is not None
            await session_completion.drain_pending_completions(timeout=0.2)
            try:
                await asyncio.wait_for(second, timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                pass

        interrupted = [
            c.args
            for c in send_cb.await_args_list
            if isinstance(c.args[1], str) and "interrupted" in c.args[1].lower()
        ]
        assert len(interrupted) == 1, (
            f"Expected exactly one interrupted delivery, got {len(interrupted)}"
        )
