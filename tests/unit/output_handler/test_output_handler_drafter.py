"""Tests for agent/output_handler.py: message-drafter wiring.

Covers the drafter call inside the handler and its failure-recovery
fallbacks.
Split out of the former ``tests/unit/test_output_handler.py`` monolith (#2879). The
``output_handler`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


class TestDrafterInHandler:
    """Tests for the drafter-at-the-handler fix (originally in the message
    drafter plan, now always-on).

    TelegramRelayOutputHandler.send must route its text through draft_message
    before writing to Redis. This closes the worker-bypass gap where worker-
    executed PM sessions previously wrote raw oversize text straight to the
    outbox and triggered MessageTooLongError at the relay.
    """

    def _make_handler(self):
        from unittest.mock import MagicMock

        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler()
        h._redis = MagicMock()
        return h

    def test_send_invokes_draft_message(self):
        """send() must call bridge.message_drafter.draft_message unconditionally."""
        from unittest.mock import AsyncMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        drafted = MessageDraft(
            text="drafted version",
            full_output_file=None,
            artifacts={},
        )
        mock_draft = AsyncMock(return_value=drafted)

        with patch("bridge.message_drafter.draft_message", mock_draft):
            # A '?' forces full drafter path (short-output early-return skips).
            asyncio.run(handler.send("123", "Raw agent output? Maybe ask the human.", 0))

        mock_draft.assert_awaited_once()
        # Redis got the *drafted* text, not the raw input
        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        assert payload["text"] == "drafted version"

    def test_send_includes_file_paths_when_drafter_returns_file(self):
        """If the draft has a full_output_file, the payload carries file_paths."""
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        drafted = MessageDraft(
            text="short caption",
            full_output_file=Path("/tmp/valor_full_output_xyz.txt"),
            artifacts={},
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            # Force long enough to skip early-return
            asyncio.run(handler.send("123", "Long text? Y" * 100, 0))

        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        assert payload["text"] == "short caption"
        assert payload["file_paths"] == ["/tmp/valor_full_output_xyz.txt"]

    def test_send_falls_back_to_raw_text_on_drafter_exception(self):
        """Drafter exception must NOT block delivery — fall back to raw text."""
        from unittest.mock import AsyncMock, patch

        handler = self._make_handler()
        mock_draft = AsyncMock(side_effect=RuntimeError("drafter broken"))

        with patch("bridge.message_drafter.draft_message", mock_draft):
            asyncio.run(handler.send("123", "Raw text survives? yes.", 0))

        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        # Raw text reached the outbox even though drafter raised.
        assert payload["text"] == "Raw text survives? yes."

    def test_routing_fields_persisted_on_passthrough(self):
        """On the verbatim pass-through path, context_summary (deterministic) is
        persisted to the session. The drafter's open_questions is drafter-local
        and never persisted (#2708: Job expectations are the obligation record)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-passthrough"

        # Drafter returns verbatim text with context_summary set but open_questions=None
        # (the raw text had no ## Open Questions section)
        drafted = MessageDraft(
            text="Fixed the drafter. All tests passing.",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary="Fixed the drafter",
            open_questions=None,  # No new questions from this turn
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Fixed the drafter? Short.", 0, session=session))

        # context_summary WAS written (it's non-None)
        assert session.context_summary == "Fixed the drafter"
        # Delivery happened.
        handler._redis.rpush.assert_called_once()


class TestDrafterFailureRecovery:
    """Tests for restored drafter-failure recovery paths (PR #1077 review tech debt).

    When the consolidation folded bridge/response.py::send_response_with_files
    into TelegramRelayOutputHandler.send, three recovery paths were dropped.
    These tests exercise the restored branches:

    1. ``needs_self_draft`` → inject ``SELF_DRAFT_INSTRUCTION`` via steering.
    2. Self-draft loop prevention via ``peek_steering_sender``.
    3. Narration fallback substitution when steering is unavailable.
    4. Persistence of ``context_summary`` on success (open_questions is drafter-local).
    """

    def _make_handler(self):
        from unittest.mock import MagicMock

        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler()
        h._redis = MagicMock()
        return h

    # ── 1. needs_self_draft injects steering and defers delivery ──

    def test_needs_self_draft_pushes_steering_and_defers_outbox_write(self):
        """When drafter returns needs_self_draft=True, steering is injected
        and the outbox write is skipped (delivery deferred to agent turn)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-self-draft"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", "Needs a self draft? yes", 0, session=session))

        # Steering was pushed with the drafter-fallback sender tag.
        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        assert args[0] == "sess-self-draft"
        assert kwargs.get("sender") == "drafter-fallback" or (
            len(args) > 2 and args[2] == "drafter-fallback"
        )

        # Outbox write was skipped (delivery deferred).
        handler._redis.rpush.assert_not_called()

    # ── 2. Loop prevention: don't push steering twice for the same session ──

    def test_needs_self_draft_skips_steering_if_already_pending(self):
        """If peek_steering_sender returns 'drafter-fallback' (already pending),
        skip pushing a second steering and fall through to narration gate."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-loop-guard"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch(
                "agent.steering.peek_steering_sender",
                return_value="drafter-fallback",
            ),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            # Non-narration raw text to prove narration fallback does NOT fire.
            raw = "Here is the actual result: see https://example.com/foo for details."
            asyncio.run(handler.send("123", raw, 0, session=session))

        # Steering must NOT be pushed a second time.
        mock_push.assert_not_called()
        # Outbox was written (no deferral).
        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        # Raw text survives since it is not narration-only.
        assert payload["text"] == raw

    # ── 2b. Violation-aware self-draft instruction (critique B2) ──
    # _inject_self_draft_steering(self, session, draft) composes the base
    # SELF_DRAFT_INSTRUCTION plus a targeted addendum when the deferred
    # draft carries a local_file_path_reference violation — telling the
    # agent to attach the file via `tools/send_message.py --file <path>`
    # instead of re-pasting a dead local path.

    def test_local_file_path_violation_adds_attach_addendum_to_steering(self):
        """A local_file_path_reference violation appends the attach-via-
        --file addendum to the pushed steering instruction."""
        from bridge.message_drafter import LOCAL_FILE_PATH_RULE, MessageDraft, Violation

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-local-path-addendum"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
            violations=[
                Violation(rule=LOCAL_FILE_PATH_RULE, line=1, snippet="/tmp/x.txt"),
            ],
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", "Done. Saved to /tmp/x.txt.", 0, session=session))

        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        assert args[0] == "sess-local-path-addendum"
        instruction = args[1]
        assert "tools/send_message.py" in instruction
        assert "--file" in instruction

    def test_non_local_path_violation_uses_base_instruction_without_addendum(self):
        """A non-local-path violation (e.g. markdown table) pushes the base
        SELF_DRAFT_INSTRUCTION unchanged — no attach-via-file addendum."""
        from bridge.message_drafter import SELF_DRAFT_INSTRUCTION, MessageDraft, Violation

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-table-violation"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
            violations=[
                Violation(rule="no_markdown_tables", line=2, snippet="| --- | --- |"),
            ],
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", "| a | b |\n| --- | --- |", 0, session=session))

        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        instruction = args[1]
        assert instruction == SELF_DRAFT_INSTRUCTION
        assert "--file" not in instruction
        assert "tools/send_message.py" not in instruction

    def test_e2e_short_local_path_reply_pushes_addendum_via_real_draft_message(self):
        """End-to-end: a real draft_message() call (not mocked) on short
        local-path text proves the full chain — drafter short-output path
        -> handler -> violation-aware steering addendum."""
        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-e2e-local-path"
        session.is_sdlc = False  # keep is_sdlc False so the short-output path fires

        raw = "Done. Saved to /tmp/x.txt."

        with (
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", raw, 0, session=session))

        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        assert args[0] == "sess-e2e-local-path"
        instruction = args[1]
        assert "tools/send_message.py" in instruction
        assert "--file" in instruction

        # Delivery was deferred — no outbox write.
        handler._redis.rpush.assert_not_called()

    # ── 3. Narration fallback triggers when steering unavailable ──

    def test_narration_fallback_substitutes_when_steering_skipped(self):
        """When needs_self_draft=True, steering loop-guard blocks it, AND the
        raw text is pure narration, substitute NARRATION_FALLBACK_MESSAGE."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft
        from bridge.message_quality import NARRATION_FALLBACK_MESSAGE

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-narration"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
        )

        # Pure process narration → is_narration_only returns True.
        narration_text = "Let me check the logs. Now let me look at the config."

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch(
                "agent.steering.peek_steering_sender",
                return_value="drafter-fallback",  # skips steering
            ),
            patch("agent.steering.push_steering_message") as mock_push,
        ):
            asyncio.run(handler.send("123", narration_text, 0, session=session))

        mock_push.assert_not_called()
        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        assert payload["text"] == NARRATION_FALLBACK_MESSAGE

    def test_narration_fallback_skipped_when_text_has_substance(self):
        """If raw text is substantive (not pure narration), the fallback
        message must NOT be substituted — deliver the raw text instead."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft
        from bridge.message_quality import NARRATION_FALLBACK_MESSAGE

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-substance"

        drafted = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
        )

        substantive_text = "Let me check the config. Found the bug at agent/output_handler.py:42."

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch(
                "agent.steering.peek_steering_sender",
                return_value="drafter-fallback",
            ),
        ):
            asyncio.run(handler.send("123", substantive_text, 0, session=session))

        args, _ = handler._redis.rpush.call_args
        payload = json.loads(args[1])
        assert payload["text"] == substantive_text
        assert payload["text"] != NARRATION_FALLBACK_MESSAGE

    # ── 4. context_summary persisted on success (durability plan #2494) ──

    def test_routing_fields_persisted_on_successful_draft(self):
        """When drafter returns context_summary it is written back and saved.

        Durability plan #2494 deleted the write-only AgentSession field, so the
        drafter's ``open_questions`` is never persisted here."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()

        # Build a session that records field assignments.
        session = MagicMock()
        session.session_id = "sess-routing"

        drafted = MessageDraft(
            text="final drafted text",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary="Investigating the router bug",
            open_questions="Needs a yes/no from human",
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Raw? yes raw.", 0, session=session))

        assert session.context_summary == "Investigating the router bug"
        session.save.assert_called_once()

    def test_routing_fields_persisted_when_context_summary_present(self):
        """context_summary is persisted whenever non-None — the old was_drafted
        gate has been removed; it is written when present, regardless of draft
        path. (Durability plan #2494: open_questions is never persisted.)"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-persist-always"

        drafted = MessageDraft(
            text="short raw text",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary="Should be persisted now",
            open_questions="And this too",
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Short? yes.", 0, session=session))

        # save() IS called because context_summary is non-None
        assert session.context_summary == "Should be persisted now"
        session.save.assert_called_once()

    def test_routing_fields_not_persisted_when_none(self):
        """When both context_summary and open_questions are None, save() is not called."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-no-persist"

        drafted = MessageDraft(
            text="short raw text",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary=None,
            open_questions=None,
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            asyncio.run(handler.send("123", "Short? yes.", 0, session=session))

        # save() must not have been called since both fields are None.
        session.save.assert_not_called()

    def test_routing_field_persistence_failure_is_silent(self):
        """A save() exception must NOT propagate — delivery must still succeed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-save-fails"
        session.save.side_effect = RuntimeError("redis write failed")

        drafted = MessageDraft(
            text="drafted text",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary="topic",
            open_questions=None,
        )

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)):
            # Must not raise.
            asyncio.run(handler.send("123", "Text? yes.", 0, session=session))

        # Delivery still happened.
        handler._redis.rpush.assert_called_once()

    def test_self_draft_attempts_bound_terminates_loop(self):
        """After SELF_DRAFT_MAX_ATTEMPTS (2) concurrent injections, the next call
        must NOT push additional steering — it falls through to the narration
        fallback instead.

        Two CONCURRENT flagged send() coroutines are launched via asyncio.gather
        so both race to bump the real Redis INCR counter simultaneously. This
        verifies atomicity: the counter must reach exactly 2 (no lost increments
        under TOCTOU). A third sequential call must then be blocked by the
        exhausted budget.

        Uses the real ``bump_self_draft_attempts`` (real Redis INCR) — skips
        gracefully when Redis is unreachable.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        import pytest

        from agent.steering import (
            SELF_DRAFT_MAX_ATTEMPTS,
            _get_redis,
            _self_draft_attempts_key,
            reset_self_draft_attempts,
        )

        # Verify real Redis is reachable before proceeding.  We use _get_redis()
        # — the same connection that bump_self_draft_attempts uses — so we stay
        # on whatever db the autouse redis_test_db fixture redirected popoto to
        # (a unique per-process db claimed from the pool; see
        # tests/db_claim.py::claim_test_db, issue #2060).
        try:
            r = _get_redis()
            r.ping()
        except Exception:
            pytest.skip("Redis not available — skipping real-counter concurrency test")

        from bridge.message_drafter import MessageDraft

        session_id = "sess-concurrent-budget-test"

        # Clean up any leftover key from a previous run.
        reset_self_draft_attempts(session_id)

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = session_id

        drafted_flagged = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
        )

        # Track how many times steering was pushed.
        push_call_count = 0

        def counting_push(sid, text, sender=None, **kwargs):
            nonlocal push_call_count
            push_call_count += 1

        try:
            with (
                patch(
                    "bridge.message_drafter.draft_message",
                    AsyncMock(return_value=drafted_flagged),
                ),
                patch("agent.steering.peek_steering_sender", return_value=None),
                patch("agent.steering.push_steering_message", side_effect=counting_push),
            ):
                # Two CONCURRENT flagged send() calls — both race to bump the
                # Redis counter at the same time. asyncio.gather runs them in the
                # same event loop so both coroutines interleave; the Redis INCR
                # is still atomic, so no increment is lost.
                async def _run_concurrent():
                    await asyncio.gather(
                        handler.send("123", "Needs a self draft? yes", 0, session=session),
                        handler.send("123", "Needs a self draft again? yes", 0, session=session),
                    )

                asyncio.run(_run_concurrent())

                # Verify: both concurrent calls incremented the counter — no lost
                # increments under concurrent access.  Read via _get_redis() to
                # stay on the same db that bump_self_draft_attempts wrote to.
                redis_key = _self_draft_attempts_key(session_id)
                actual_count = int(r.get(redis_key) or 0)
                assert actual_count == SELF_DRAFT_MAX_ATTEMPTS, (
                    f"Redis counter should be {SELF_DRAFT_MAX_ATTEMPTS} after "
                    f"{SELF_DRAFT_MAX_ATTEMPTS} concurrent bumps, got {actual_count}"
                )

                # Both concurrent calls were within budget → both pushed steering.
                assert push_call_count == SELF_DRAFT_MAX_ATTEMPTS, (
                    f"Expected {SELF_DRAFT_MAX_ATTEMPTS} pushes from concurrent calls, "
                    f"got {push_call_count}"
                )

                # Third call: budget exhausted → steering must NOT be pushed, and
                # session.save() must NOT be called on the steering path (no
                # full-hash save for a budget-exhausted deferral).
                session.save.reset_mock()
                asyncio.run(
                    handler.send(
                        "123", "Needs a self draft? yes but budget gone", 0, session=session
                    )
                )

            assert push_call_count == SELF_DRAFT_MAX_ATTEMPTS, (
                f"Third call must not push steering after budget exhaustion; "
                f"total pushes: {push_call_count}"
            )
            session.save.assert_not_called()
        finally:
            # Always clean up the Redis key so subsequent runs start fresh.
            reset_self_draft_attempts(session_id)

    def test_self_draft_attempts_reset_pinned_before_early_return(self):
        """Counter reset fires on the clean (not needs_self_draft) branch BEFORE
        any steering_deferred early-return.

        Two scenarios must both reset the counter:
        1. Normal clean delivery writes to the outbox.
        2. Clean delivery that is suppressed by the redundancy filter (returns
           early from send() before the final rpush) ALSO resets the counter.

        This test validates scenario 1. Scenario 2 is harder to isolate here
        because the redundancy filter requires an SDLC session; the code path
        is covered by the code reading `else: if session_id: reset_self_draft_attempts`.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-reset-test"

        drafted_clean = MessageDraft(
            text="Clean output text.",
            full_output_file=None,
            needs_self_draft=False,
            artifacts={},
            context_summary=None,
            open_questions=None,
        )

        reset_was_called = {"flag": False}

        def fake_reset(sid):
            reset_was_called["flag"] = True

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted_clean)),
            patch("agent.steering.reset_self_draft_attempts", side_effect=fake_reset),
        ):
            asyncio.run(handler.send("123", "Clean text? yes.", 0, session=session))

        assert reset_was_called["flag"], (
            "reset_self_draft_attempts must be called on clean delivery path"
        )
        # Delivery also happened.
        handler._redis.rpush.assert_called_once()
