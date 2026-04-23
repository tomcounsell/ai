"""Unit tests for `_deliver_pipeline_completion` 2-pass flow (plan #1129 D6).

Focused on D6(b) 2-pass drafter + D6(c) no-silent-fail + always-finalize
contract. Companion to `test_deliver_pipeline_completion.py` (which covers
locking, CancelledError, and dedup).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import session_completion


# ---------------------------------------------------------------------------
# Fixtures (mirror test_deliver_pipeline_completion.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def parent():
    p = MagicMock()
    p.agent_session_id = "parent-abc-123"
    p.session_id = "tg_valor_-123_456"
    p.chat_id = "-123"
    p.telegram_message_id = 456
    p.project_key = "valor"
    p.transport = None
    p.project_config = {"working_directory": "/tmp"}
    p.response_delivered_at = None
    p.save = MagicMock()
    return p


@pytest.fixture
def send_cb():
    return AsyncMock(return_value=None)


def _redis_ok():
    db = MagicMock()
    db.set = MagicMock(return_value=True)
    db.exists = MagicMock(return_value=False)
    db.incr = MagicMock(return_value=1)
    db.expire = MagicMock(return_value=True)
    return db


# ---------------------------------------------------------------------------
# Happy path: both passes succeed
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_two_passes_refined_text_delivered(self, parent, send_cb):
        """Pass 1 drafts, Pass 2 refines, refined text wins."""
        # Alternate: first call returns Pass 1 draft, second returns refined.
        harness = AsyncMock(side_effect=["first draft text", "refined polished text"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="pm-uuid-1"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "pipeline summary", send_cb, parent.chat_id, None
            )

        assert harness.await_count == 2
        send_cb.assert_awaited_once()
        assert send_cb.await_args.args[1] == "refined polished text"
        _fs.assert_called_once()
        assert _fs.call_args.args[1] == "completed"

    async def test_both_passes_use_model_opus(self, parent, send_cb):
        harness = AsyncMock(side_effect=["draft", "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="pm-uuid-1"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "pipeline summary", send_cb, parent.chat_id, None
            )

        assert harness.await_args_list[0].kwargs["model"] == "opus"
        assert harness.await_args_list[1].kwargs["model"] == "opus"

    async def test_pass1_session_id_is_none(self, parent, send_cb):
        """S-1: Pass 1 must use session_id=None to avoid UUID writeback."""
        harness = AsyncMock(side_effect=["draft", "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="pm-uuid-1"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        pass1_kwargs = harness.await_args_list[0].kwargs
        assert pass1_kwargs["session_id"] is None
        # Pass 1 DOES keep prior_uuid (resumes from PM).
        assert pass1_kwargs["prior_uuid"] == "pm-uuid-1"

    async def test_pass2_uuid_isolation(self, parent, send_cb):
        """ADV-2: Pass 2 must use prior_uuid=None AND session_id=None."""
        harness = AsyncMock(side_effect=["draft", "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="pm-uuid-1"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        pass2_kwargs = harness.await_args_list[1].kwargs
        assert pass2_kwargs["prior_uuid"] is None
        assert pass2_kwargs["session_id"] is None
        assert pass2_kwargs["full_context_message"] is None

    async def test_pass2_prompt_embeds_draft(self, parent, send_cb):
        """Pass 2's prompt must include Pass 1's draft verbatim."""
        draft = "This is Pass 1 output containing unique token ZZZBACON."
        harness = AsyncMock(side_effect=[draft, "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        pass2_message = harness.await_args_list[1].kwargs["message"]
        assert "ZZZBACON" in pass2_message
        assert pass2_message.startswith(session_completion._COMPLETION_REVIEW_PROMPT_PREFIX)


# ---------------------------------------------------------------------------
# Pass 1 failure modes
# ---------------------------------------------------------------------------


class TestPass1Failures:
    async def test_pass1_empty_delivers_degraded_fallback(self, parent, send_cb):
        harness = AsyncMock(return_value="")
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            # Must NOT raise.
            await session_completion._deliver_pipeline_completion(
                parent, "context body", send_cb, parent.chat_id, None
            )

        # Pass 2 is skipped when Pass 1 fails.
        assert harness.await_count == 1
        send_cb.assert_awaited_once()
        delivered = send_cb.await_args.args[1]
        assert delivered.startswith("[drafter unavailable — pipeline completed]")
        assert "context body" in delivered
        # Session is still finalized.
        _fs.assert_called_once()
        assert _fs.call_args.args[1] == "completed"

    async def test_pass1_exception_delivers_degraded_fallback(self, parent, send_cb):
        async def _raise(**_kw):
            raise RuntimeError("anthropic down")

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_raise),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            # Must NOT raise — must deliver degraded fallback.
            await session_completion._deliver_pipeline_completion(
                parent, "context body", send_cb, parent.chat_id, None
            )

        send_cb.assert_awaited_once()
        assert send_cb.await_args.args[1].startswith(
            "[drafter unavailable — pipeline completed]"
        )
        _fs.assert_called_once()

    async def test_pass1_sentinel_delivers_degraded_fallback(self, parent, send_cb):
        """Harness-not-found sentinel MUST NOT be delivered as a user message."""
        sentinel = "Error: CLI harness not found — some detail here"
        harness = AsyncMock(return_value=sentinel)
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "context body", send_cb, parent.chat_id, None
            )

        # Only Pass 1 ran (Pass 2 is skipped on sentinel).
        assert harness.await_count == 1
        delivered = send_cb.await_args.args[1]
        assert "Error: CLI harness not found" not in delivered
        assert delivered.startswith("[drafter unavailable — pipeline completed]")

    async def test_pass1_failure_emits_degraded_counter(self, parent, send_cb):
        redis_db = _redis_ok()
        harness = AsyncMock(return_value="")  # Pass 1 empty
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_db),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        # Counter key should be hit.
        incr_keys = [call.args[0] for call in redis_db.incr.call_args_list]
        assert any(
            k.startswith("completion_runner:degraded_fallback:daily:") for k in incr_keys
        ), f"No degraded_fallback counter key incremented; keys: {incr_keys}"


# ---------------------------------------------------------------------------
# Pass 2 failure modes
# ---------------------------------------------------------------------------


class TestPass2Failures:
    async def test_pass2_empty_falls_back_to_pass1_draft(self, parent, send_cb):
        harness = AsyncMock(side_effect=["pass1 real draft", ""])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        assert harness.await_count == 2
        assert send_cb.await_args.args[1] == "pass1 real draft"
        _fs.assert_called_once()

    async def test_pass2_exception_falls_back_to_pass1_draft(self, parent, send_cb):
        # Side-effect: first call OK, second raises.
        async def _side(**kw):
            if _side.count == 0:
                _side.count += 1
                return "pass1 real draft"
            raise RuntimeError("refine failed")

        _side.count = 0
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_side),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            # Must NOT re-raise (downgraded from v1).
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        assert send_cb.await_args.args[1] == "pass1 real draft"
        _fs.assert_called_once()

    async def test_pass2_sentinel_falls_back_to_pass1_draft(self, parent, send_cb):
        sentinel = "Error: CLI harness not found — detail"
        harness = AsyncMock(side_effect=["pass1 real draft", sentinel])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        delivered = send_cb.await_args.args[1]
        assert delivered == "pass1 real draft"
        assert "Error: CLI harness not found" not in delivered


# ---------------------------------------------------------------------------
# send_cb + response_delivered_at gate (ADV-2)
# ---------------------------------------------------------------------------


class TestDeliveryGate:
    async def test_no_send_cb_skips_stamp_but_finalizes(self, parent):
        """ADV-2: no send_cb means no delivery_attempted → no stamp, still finalize."""
        harness = AsyncMock(side_effect=["draft", "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", None, parent.chat_id, None
            )

        # Stamp must NOT fire when send_cb is None.
        assert parent.response_delivered_at is None
        # finalize_session still runs.
        _fs.assert_called_once()

    async def test_no_chat_id_skips_stamp_but_finalizes(self, parent, send_cb):
        harness = AsyncMock(side_effect=["draft", "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, None, None
            )

        send_cb.assert_not_awaited()
        assert parent.response_delivered_at is None
        _fs.assert_called_once()

    async def test_send_cb_failure_stamps_and_finalizes(self, parent):
        """If send_cb was attempted but failed, stamp fires; finalize still runs."""

        async def _boom(*a, **kw):
            raise RuntimeError("transport down")

        harness = AsyncMock(side_effect=["draft", "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            # Must NOT re-raise.
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", _boom, parent.chat_id, None
            )

        # delivery_attempted=True (boom came after send_cb was called) → stamp.
        assert parent.response_delivered_at is not None
        _fs.assert_called_once()


# ---------------------------------------------------------------------------
# Always-finalize on Pass 1 raise (D6(c))
# ---------------------------------------------------------------------------


class TestAlwaysFinalize:
    async def test_pass1_exception_still_finalizes(self, parent, send_cb):
        async def _raise(**_kw):
            raise RuntimeError("drafter dead")

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=_raise),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session") as _fs,
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        _fs.assert_called_once()
        assert _fs.call_args.args[1] == "completed"


# ---------------------------------------------------------------------------
# ADV-1 brace-safety (literal { / } in summary_context and draft)
# ---------------------------------------------------------------------------


class TestBraceSafety:
    async def test_pass1_prompt_survives_braces_in_summary(self, parent, send_cb):
        """Literal `{`/`}` in summary_context must not crash Pass 1 prompt build."""
        summary_with_braces = "Output: {'status': 'ok', 'count': 3}"
        harness = AsyncMock(side_effect=["draft", "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            # No KeyError/IndexError/ValueError from .format().
            await session_completion._deliver_pipeline_completion(
                parent, summary_with_braces, send_cb, parent.chat_id, None
            )

        # Pass 1's prompt should contain the braces verbatim.
        pass1_message = harness.await_args_list[0].kwargs["message"]
        assert "{'status': 'ok', 'count': 3}" in pass1_message

    async def test_pass2_prompt_survives_braces_in_draft(self, parent, send_cb):
        """Literal `{`/`}` in Pass 1 draft must not crash Pass 2 prompt build."""
        draft_with_braces = "Run succeeded: {'files': 3, 'tests': 12}"
        harness = AsyncMock(side_effect=[draft_with_braces, "refined"])
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB", _redis_ok()),
            patch("agent.sdk_client.get_response_via_harness", new=harness),
            patch("agent.sdk_client._get_prior_session_uuid", return_value="u"),
            patch("models.session_lifecycle.finalize_session"),
        ):
            await session_completion._deliver_pipeline_completion(
                parent, "ctx", send_cb, parent.chat_id, None
            )

        pass2_message = harness.await_args_list[1].kwargs["message"]
        assert "{'files': 3, 'tests': 12}" in pass2_message
