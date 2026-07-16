"""Tests for the tool-call delivery contract (plan #1035 §D4).

Covers classify_delivery_outcome's four outcomes:
  send | react | continue | silent

Also runs an end-to-end smoke of the stop_hook review gate:
- First stop with non-empty output → returns {"decision": "block"} and caches
  review state.
- Second stop with a send_message tool invocation → clears state and
  returns {} (review gate complete).

The transcript tails in these tests mimic the JSONL structure the Claude
Agent SDK writes, since stop_hook reads a raw chunk of transcript bytes
via _read_transcript_tail.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from agent.hooks.stop import (
    _review_state,
    classify_delivery_outcome,
    stop_hook,
)


class TestClassifyDeliveryOutcome:
    """Five outcomes from plan §D4, one test each."""

    def test_send_as_is(self):
        # Transcript contains send_message.py with the draft text verbatim.
        draft = "All 42 tests passed, committed abc1234."
        tail = (
            '{"type":"tool_use","name":"Bash","input":{"command":'
            f'"python tools/send_message.py {draft!r}"' + "}}"
        )
        assert classify_delivery_outcome(tail) == "send"

    def test_edit_and_send(self):
        # Agent invoked send_message with DIFFERENT text than the draft.
        # classify_delivery_outcome doesn't compare text — presence of the
        # tool invocation alone = "send".
        tail = (
            '{"type":"tool_use","name":"Bash","input":{"command":'
            "\"python tools/send_message.py 'revised text — I edited the draft'\"}}"
        )
        assert classify_delivery_outcome(tail) == "send"

    def test_react(self):
        tail = (
            '{"type":"tool_use","name":"Bash","input":{"command":'
            '"python tools/react_with_emoji.py excited"}}'
        )
        assert classify_delivery_outcome(tail) == "react"

    def test_silent(self):
        # No send, no react, no other tool_use activity.
        tail = "the agent just emitted prose with no tool call whatsoever"
        assert classify_delivery_outcome(tail) == "silent"

    def test_continue(self):
        # Some other tool_use block (Bash grep) but no delivery tool.
        tail = '{"type":"tool_use","name":"Bash","input":{"command":"grep -r foo src/"}}'
        assert classify_delivery_outcome(tail) == "continue"

    def test_empty_transcript_is_silent(self):
        assert classify_delivery_outcome("") == "silent"

    def test_send_and_react_both_present_prefers_send(self):
        # If somehow both tools appear, "send" wins (it's checked first).
        tail = "python tools/send_message.py 'hi'\npython tools/react_with_emoji.py 'excited'"
        assert classify_delivery_outcome(tail) == "send"

    def test_binary_garbage_tail_is_silent_never_raises(self):
        """A malformed / binary-ish transcript tail (e.g. a decode-replaced
        chunk with no recognizable tool_use marker) classifies as 'silent' and
        never raises — the classifier is a pure regex scan over text."""
        # Bytes decoded with errors='replace' leave U+FFFD replacement chars;
        # mix in NULs and control bytes to simulate a corrupt tail.
        garbage = "\x00\xff�\x01\x02 � garbage \x00 no tool marker here �"
        assert classify_delivery_outcome(garbage) == "silent"

    def test_garbage_with_embedded_null_bytes_is_silent(self):
        """Even control characters adjacent to real words must not crash or
        false-positive into send/react."""
        garbage = "\x00\x00send\x00message\x00\x00 not a real invocation \x07\x08"
        # 'send' and 'message' appear but not the 'tools/send_message.py' token.
        assert classify_delivery_outcome(garbage) == "silent"


# ---------------------------------------------------------------------------
# End-to-end smoke of stop_hook review gate (first stop → block, second
# stop with send tool_use → clear state + return empty dict).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_review_state():
    """Reset the module-level review state between tests."""
    _review_state.clear()
    yield
    _review_state.clear()


@pytest.fixture
def telegram_env(monkeypatch):
    """Mark the session as user-triggered so the review gate runs."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
    monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
    yield


class TestStopHookReviewGateFlow:
    """First stop blocks with a draft; second stop (with send) clears the gate."""

    @pytest.mark.asyncio
    async def test_first_stop_blocks_with_draft(self, telegram_env):
        session_id = "smoke-session-first"
        # Write a transcript tail containing some substantive agent output.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("All 42 tests passed. Committed abc1234 and pushed.\n")
            path = f.name
        try:
            input_data = {
                "session_id": session_id,
                "transcript_path": path,
            }

            # Mock draft_message and _is_child_session so we don't hit the
            # Haiku API or AgentSession.query.
            fake_draft = "All 42 tests passed (committed abc1234)."

            async def _fake_generate(*args, **kwargs):
                return fake_draft

            with (
                patch("agent.hooks.stop._is_child_session", return_value=False),
                patch(
                    "agent.hooks.stop._generate_draft",
                    new=AsyncMock(side_effect=_fake_generate),
                ),
                # Short-circuit the SDLC branch check — it tries to import
                # agent.sdk_client which is heavy and unrelated to this test.
                patch(
                    "agent.sdk_client._check_no_direct_main_push",
                    return_value=None,
                ),
            ):
                result = await stop_hook(input_data, tool_use_id=None, context=None)

            assert result.get("decision") == "block"
            assert "reason" in result
            # Draft text must appear in the review prompt the agent sees.
            assert fake_draft in result["reason"]
            assert "tools/send_message.py" in result["reason"]
            # Review state is now cached for this session
            assert session_id in _review_state
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_second_stop_with_send_clears_state(self, telegram_env):
        session_id = "smoke-session-second"
        # Pre-seed review state as if the first stop already ran.
        import time

        _review_state[session_id] = {
            "timestamp": time.time(),
            "draft": "previous draft",
            "medium": "telegram",
        }

        # Transcript tail showing the agent invoked send_message.py.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"type":"tool_use","name":"Bash","input":{"command":'
                "\"python tools/send_message.py 'the final message'\"}}\n"
            )
            path = f.name
        try:
            input_data = {
                "session_id": session_id,
                "transcript_path": path,
            }

            with (
                patch("agent.hooks.stop._is_child_session", return_value=False),
                patch(
                    "agent.sdk_client._check_no_direct_main_push",
                    return_value=None,
                ),
            ):
                result = await stop_hook(input_data, tool_use_id=None, context=None)

            # Gate cleared: empty dict, session removed from review state.
            assert result == {}
            assert session_id not in _review_state
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_worker_restart_between_stops_re_presents_gate(self, telegram_env):
        """Documented accepted behavior: ``_review_state`` is process-local, so
        a worker restart between the first and second stop drops the cached
        review state and the gate RE-PRESENTS (blocks with a fresh draft) rather
        than silently completing. Simulated by clearing ``_review_state`` between
        two stop invocations."""
        session_id = "smoke-session-restart"

        # ── First stop: gate activates, state cached. ──
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("All 42 tests passed. Committed abc1234 and pushed.\n")
            path1 = f.name

        fake_draft = "All 42 tests passed (committed abc1234)."

        async def _fake_generate(*args, **kwargs):
            return fake_draft

        try:
            with (
                patch("agent.hooks.stop._is_child_session", return_value=False),
                patch(
                    "agent.hooks.stop._generate_draft",
                    new=AsyncMock(side_effect=_fake_generate),
                ),
                patch("agent.sdk_client._check_no_direct_main_push", return_value=None),
            ):
                first = await stop_hook(
                    {"session_id": session_id, "transcript_path": path1},
                    tool_use_id=None,
                    context=None,
                )
            assert first.get("decision") == "block"
            assert session_id in _review_state
        finally:
            os.unlink(path1)

        # ── Worker restart: process-local review state is lost. ──
        _review_state.clear()
        assert session_id not in _review_state

        # ── Next stop after restart: MUST re-enter the gate (block + fresh
        # draft), NOT treat this as the second (completing) stop. ──
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"type":"tool_use","name":"Bash","input":{"command":'
                "\"python tools/send_message.py 'the final message'\"}}\n"
            )
            path2 = f.name
        try:
            with (
                patch("agent.hooks.stop._is_child_session", return_value=False),
                patch(
                    "agent.hooks.stop._generate_draft",
                    new=AsyncMock(side_effect=_fake_generate),
                ),
                patch("agent.sdk_client._check_no_direct_main_push", return_value=None),
            ):
                second = await stop_hook(
                    {"session_id": session_id, "transcript_path": path2},
                    tool_use_id=None,
                    context=None,
                )
            # Gate re-presents: a fresh block with the draft, state re-cached.
            assert second.get("decision") == "block"
            assert fake_draft in second["reason"]
            assert session_id in _review_state
        finally:
            os.unlink(path2)

    @pytest.mark.asyncio
    async def test_second_stop_with_continue_blocks_and_resets(self, telegram_env):
        """When the agent kept working (other tool_use, no send/react), the gate
        blocks with a 'Resuming work' reason and resets the state so the NEXT
        stop re-enters the gate."""
        session_id = "smoke-session-continue"
        import time

        _review_state[session_id] = {
            "timestamp": time.time(),
            "draft": "previous draft",
            "medium": "telegram",
        }

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write('{"type":"tool_use","name":"Bash","input":{"command":"grep -r foo src/"}}\n')
            path = f.name
        try:
            input_data = {
                "session_id": session_id,
                "transcript_path": path,
            }
            with (
                patch("agent.hooks.stop._is_child_session", return_value=False),
                patch(
                    "agent.sdk_client._check_no_direct_main_push",
                    return_value=None,
                ),
            ):
                result = await stop_hook(input_data, tool_use_id=None, context=None)

            assert result.get("decision") == "block"
            assert "Resuming work" in result["reason"]
            # Continue path resets state so next stop re-enters the gate.
            assert session_id not in _review_state
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Tool -> canonical-handler routing (issue #1369)
#
# These tests assert that ``tools/send_message.py`` ALWAYS routes through
# ``agent.output_handler.TelegramRelayOutputHandler.send`` for both telegram
# and email transports — there must be exactly one canonical handler call
# site, no raw rpush in the default tool path, and no import of the
# synchronous SMTP ``EmailOutputHandler`` from the tool process.
# ---------------------------------------------------------------------------


class TestToolCallHandlerRouting:
    """The tool process delegates to TelegramRelayOutputHandler.send for both
    transports. Each test mocks the handler and asserts call arity + kwargs.
    """

    def _stub_session(self, monkeypatch, session=object()):
        """Patch ``tools.send_message._lookup_session`` to return ``session``."""
        import tools.send_message as sm

        monkeypatch.setattr(sm, "_lookup_session", lambda sid: session)
        # Bypass the promise gate; it's tested elsewhere.
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

    def test_telegram_path_invokes_canonical_handler(self, monkeypatch):
        """``_send_via_telegram`` must call TelegramRelayOutputHandler.send
        exactly once with the chat_id, drafted text, reply-to int, the
        reconstituted session, and the CLI-supplied file_paths."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import tools.send_message as sm

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "7")
        monkeypatch.setenv("VALOR_SESSION_ID", "sess-tg")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        fake_session = MagicMock()
        fake_session.session_id = "sess-tg"
        fake_session.extra_context = {"transport": "telegram"}
        self._stub_session(monkeypatch, session=fake_session)

        mock_send = AsyncMock(return_value=None)
        with patch("agent.output_handler.TelegramRelayOutputHandler.send", new=mock_send):
            sm._send_via_telegram("hello world", None)

        mock_send.assert_awaited_once()
        args, kwargs = mock_send.call_args
        # When ``send`` is patched on the class, the bound-method call from
        # the tool's handler instance descriptor-binds ``self`` AHEAD of
        # AsyncMock, so call_args records (self, chat_id, text, reply_to_msg_id).
        # Confirm by searching for the chat_id wherever it landed.
        positional = list(args)
        assert "12345" in positional, f"chat_id missing from args: {positional}"
        assert "hello world" in positional, f"text missing from args: {positional}"
        assert 7 in positional, f"reply_to_msg_id missing from args: {positional}"
        assert kwargs["session"] is fake_session
        assert kwargs.get("file_paths") is None

    def test_email_path_invokes_same_canonical_handler(self, monkeypatch):
        """``_send_via_email`` must call THE SAME handler class
        (TelegramRelayOutputHandler.send) — NOT EmailOutputHandler.send.
        This asserts the single-canonical-handler convergence."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import tools.send_message as sm

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-email")
        monkeypatch.setenv("EMAIL_REPLY_TO", "customer@example.com")
        monkeypatch.setenv("EMAIL_SUBJECT", "Re: hi")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        fake_session = MagicMock()
        fake_session.session_id = "sess-email"
        fake_session.extra_context = {"transport": "email"}
        self._stub_session(monkeypatch, session=fake_session)

        mock_send = AsyncMock(return_value=None)
        with patch("agent.output_handler.TelegramRelayOutputHandler.send", new=mock_send):
            sm._send_via_email("hello via email", None)

        mock_send.assert_awaited_once()
        args, kwargs = mock_send.call_args
        positional = list(args)
        assert "customer@example.com" in positional, f"recipient missing from args: {positional}"
        assert "hello via email" in positional, f"text missing from args: {positional}"
        assert 0 in positional, f"reply_to_msg_id sentinel missing: {positional}"
        assert kwargs["session"] is fake_session

    def test_missing_session_fails_closed_by_default(self, monkeypatch):
        """Missing AgentSession + ALLOW_LEGACY_RPUSH_FALLBACK unset must
        cause a non-zero exit — the tool refuses to silently bypass the
        canonical handler."""
        import tools.send_message as sm

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("VALOR_SESSION_ID", "missing-sess")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: None)
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

        with pytest.raises(SystemExit) as excinfo:
            sm._send_via_telegram("hi", None)
        assert excinfo.value.code != 0

    def test_missing_session_with_fallback_flag_uses_raw_rpush(self, monkeypatch):
        """Missing AgentSession + ALLOW_LEGACY_RPUSH_FALLBACK=1 must call the
        legacy raw-rpush helper and exit 0 (diagnostic opt-in path)."""
        from unittest.mock import patch

        import tools.send_message as sm

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("VALOR_SESSION_ID", "missing-sess")
        monkeypatch.setenv("ALLOW_LEGACY_RPUSH_FALLBACK", "1")
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: None)
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

        with patch.object(sm, "_legacy_telegram_rpush") as mock_legacy:
            sm._send_via_telegram("hi", None)
            mock_legacy.assert_called_once()

    def test_tool_does_not_import_email_output_handler(self):
        """Static guarantee: ``tools/send_message.py`` must NOT reference
        EmailOutputHandler. The tool talks only to the queue-side handler;
        importing the synchronous SMTP handler would couple it to the wrong
        layer (per #1369 design decision)."""
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "tools" / "send_message.py"
        contents = src.read_text()
        assert "EmailOutputHandler" not in contents, (
            "tools/send_message.py must not reference EmailOutputHandler — "
            "the canonical queue-side handler is TelegramRelayOutputHandler."
        )


# ---------------------------------------------------------------------------
# Per-path contract tests (Failure Path Test Strategy → "Contract tests")
#
# One test per delivery path asserting input → outbox payload with the REAL
# handler and a fake Redis (MagicMock captured via the handler's own redis
# seam). No handler is mocked — only the drafter (bypassed to a verbatim
# pass-through so short canned text needs no Haiku) and the Redis client.
# The five paths mirror the plan's registry:
#   1. CLI telegram         (tools/send_message.py → telegram:outbox)
#   2. CLI email            (tools/send_message.py → email:outbox)
#   3. Worker silent path   (registered handler.send → telegram:outbox)
#   4. System notice        (deliver_system_notice → outbox / file fallback)
#   5. Sync flush           (flush_deferred_self_draft_sync → telegram:outbox)
# ---------------------------------------------------------------------------


class TestDeliveryPathContracts:
    """input → outbox payload, one test per delivery path, real handler."""

    # ── Path 1: CLI telegram → telegram:outbox payload shape ──────────────
    def test_contract_cli_telegram_payload_shape(self, monkeypatch):
        import json as _json
        from unittest.mock import MagicMock, patch

        import tools.send_message as sm
        from agent.output_handler import TelegramRelayOutputHandler

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "7")
        monkeypatch.setenv("VALOR_SESSION_ID", "contract-tg")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        session = MagicMock()
        session.session_id = "contract-tg"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: session)
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

        mock_redis = MagicMock()
        with (
            patch.object(TelegramRelayOutputHandler, "_get_redis", return_value=mock_redis),
            patch(
                "bridge.message_drafter.draft_message",
                new=_AsyncPassthrough(),
            ),
        ):
            sm._send_via_telegram("status: all green", None)

        mock_redis.rpush.assert_called_once()
        assert mock_redis.rpush.call_args[0][0] == "telegram:outbox:contract-tg"
        payload = _json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["chat_id"] == "12345"
        assert payload["reply_to"] == 7
        assert payload["text"] == "status: all green"
        assert payload["session_id"] == "contract-tg"
        assert isinstance(payload["timestamp"], float)
        # No attachments supplied → the file_paths key is omitted entirely.
        assert "file_paths" not in payload

    def test_contract_cli_telegram_includes_file_paths(self, monkeypatch, tmp_path):
        import json as _json
        from unittest.mock import MagicMock, patch

        import tools.send_message as sm
        from agent.output_handler import TelegramRelayOutputHandler

        attachment = tmp_path / "shot.png"
        attachment.write_bytes(b"\x89PNG fake")

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "7")
        monkeypatch.setenv("VALOR_SESSION_ID", "contract-tg-file")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        session = MagicMock()
        session.session_id = "contract-tg-file"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: session)
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

        mock_redis = MagicMock()
        with (
            patch.object(TelegramRelayOutputHandler, "_get_redis", return_value=mock_redis),
            patch("bridge.message_drafter.draft_message", new=_AsyncPassthrough()),
        ):
            sm._send_via_telegram("see attached", [str(attachment)])

        payload = _json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["file_paths"] == [str(attachment)]

    # ── Path 2: CLI email → email:outbox payload shape ────────────────────
    def test_contract_cli_email_payload_shape(self, monkeypatch):
        import json as _json
        from unittest.mock import MagicMock, patch

        import tools.send_message as sm
        from agent.output_handler import TelegramRelayOutputHandler

        monkeypatch.setenv("VALOR_SESSION_ID", "contract-email")
        monkeypatch.setenv("EMAIL_REPLY_TO", "customer@example.com")
        monkeypatch.setenv("EMAIL_SUBJECT", "Setup question")
        monkeypatch.setenv("EMAIL_IN_REPLY_TO", "<orig@example.com>")
        monkeypatch.setenv("SMTP_USER", "bot@ourdomain.com")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        session = MagicMock()
        session.session_id = "contract-email"
        session.extra_context = {
            "transport": "email",
            "email_subject": "Setup question",
            "email_message_id": "<orig@example.com>",
            "email_to_addrs": ["customer@example.com", "team@example.com"],
            "email_cc_addrs": ["watcher@example.com"],
        }
        session.is_sdlc = False
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: session)
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

        mock_redis = MagicMock()
        with (
            patch.object(TelegramRelayOutputHandler, "_get_redis", return_value=mock_redis),
            patch("bridge.message_drafter.draft_message", new=_AsyncPassthrough(medium="email")),
        ):
            sm._send_via_email("Here is the answer.")

        mock_redis.rpush.assert_called_once()
        assert mock_redis.rpush.call_args[0][0] == "email:outbox:contract-email"
        payload = _json.loads(mock_redis.rpush.call_args[0][1])
        # Reply-all: primary first, then To/CC minus self (bot@ourdomain.com).
        assert payload["to"] == [
            "customer@example.com",
            "team@example.com",
            "watcher@example.com",
        ]
        assert payload["subject"] == "Re: Setup question"
        assert payload["body"] == "Here is the answer."
        assert payload["in_reply_to"] == "<orig@example.com>"
        assert payload["references"] == "<orig@example.com>"
        assert payload["from_addr"] == "bot@ourdomain.com"
        # Telegram-only fields must NOT leak into the email payload.
        assert "chat_id" not in payload
        assert "reply_to" not in payload

    # ── Path 3: Worker silent path (registered handler.send) ──────────────
    def test_contract_worker_silent_path_payload_shape(self):
        """The worker's registered callback IS TelegramRelayOutputHandler.send;
        invoking it directly produces the SAME telegram payload shape as the
        CLI path (single canonical entrypoint)."""
        import json as _json
        from unittest.mock import MagicMock, patch

        from agent.output_handler import DeliveryOutcome, TelegramRelayOutputHandler

        handler = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        handler._redis = MagicMock()

        session = MagicMock()
        session.session_id = "contract-worker"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False

        with patch("bridge.message_drafter.draft_message", new=_AsyncPassthrough()):
            outcome = asyncio.run(handler.send("12345", "silent worker reply", 7, session=session))

        assert outcome == DeliveryOutcome.sent
        handler._redis.rpush.assert_called_once()
        assert handler._redis.rpush.call_args[0][0] == "telegram:outbox:contract-worker"
        payload = _json.loads(handler._redis.rpush.call_args[0][1])
        # Identical shape to the CLI telegram contract above.
        assert set(payload.keys()) == {"chat_id", "reply_to", "text", "session_id", "timestamp"}
        assert payload["chat_id"] == "12345"
        assert payload["reply_to"] == 7
        assert payload["text"] == "silent worker reply"
        assert payload["session_id"] == "contract-worker"

    # ── Path 4: System notice (registered handler + file fallback) ────────
    def test_contract_system_notice_registered_handler(self):
        import json as _json
        from unittest.mock import MagicMock, patch

        from agent.output_handler import TelegramRelayOutputHandler, deliver_system_notice

        entry = MagicMock()
        entry.session_id = "contract-notice"
        entry.agent_session_id = "contract-notice"
        entry.chat_id = "99999"
        entry.telegram_message_id = 3
        entry.project_key = "contract-proj"
        entry.extra_context = {"transport": "telegram"}

        handler = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        handler._redis = MagicMock()

        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(handler.send, None),
            ),
            patch("bridge.message_drafter.draft_message", new=_AsyncPassthrough()),
        ):
            result = asyncio.run(deliver_system_notice(entry, "Service recovered."))

        assert result is True
        assert handler._redis.rpush.call_args[0][0] == "telegram:outbox:contract-notice"
        payload = _json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["text"] == "Service recovered."
        assert payload["chat_id"] == "99999"
        assert payload["reply_to"] == 3

    def test_contract_system_notice_file_fallback_when_unregistered(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock, patch

        import agent.output_handler as oh
        from agent.output_handler import deliver_system_notice

        monkeypatch.setattr(oh, "WORKER_LOGS_DIR", tmp_path)

        entry = MagicMock()
        entry.session_id = "contract-notice-file"
        entry.agent_session_id = "contract-notice-file"
        entry.chat_id = "99999"
        entry.telegram_message_id = 0
        entry.project_key = "contract-proj"
        entry.extra_context = {"transport": "telegram"}

        with patch(
            "agent.agent_session_queue._resolve_callbacks",
            return_value=(None, None),
        ):
            result = asyncio.run(deliver_system_notice(entry, "Fallback path notice."))

        assert result is True
        log_file = tmp_path / "contract-notice-file.log"
        assert log_file.exists()
        assert "Fallback path notice." in log_file.read_text()

    # ── Path 5: Sync flush → telegram payload via build_telegram_outbox_payload
    def test_contract_sync_flush_uses_shared_payload_builder(self, monkeypatch):
        import json as _json
        from unittest.mock import MagicMock, patch

        import agent.session_health as sh
        from agent.output_handler import build_telegram_outbox_payload

        source = MagicMock()
        source.session_id = "contract-flush"
        source.chat_id = "77777"
        source.telegram_message_id = 5
        source.project_key = "contract-proj"
        source.extra_context = {
            "transport": "telegram",
            "deferred_self_draft_pending": True,
            "deferred_self_draft_text": "All tests passing, PR #123 merged to main.",
        }

        fake_redis = MagicMock()
        with (
            patch.object(sh, "get_authoritative_session", return_value=source),
            patch("popoto.redis_db.POPOTO_REDIS_DB", fake_redis),
        ):
            session = MagicMock()
            session.session_id = "contract-flush"
            sh.flush_deferred_self_draft_sync(session, status="completed")

        fake_redis.rpush.assert_called_once()
        assert fake_redis.rpush.call_args[0][0] == "telegram:outbox:contract-flush"
        payload = _json.loads(fake_redis.rpush.call_args[0][1])

        # The wire shape MUST be exactly what build_telegram_outbox_payload
        # emits (the handler's own outbox write uses the same builder).
        expected = build_telegram_outbox_payload(
            "77777", "All tests passing, PR #123 merged to main.", 5, "contract-flush"
        )
        assert set(payload.keys()) == set(expected.keys())
        assert payload["chat_id"] == "77777"
        assert payload["reply_to"] == 5
        assert payload["text"] == "All tests passing, PR #123 merged to main."
        assert payload["session_id"] == "contract-flush"
        assert "file_paths" not in payload  # sync flush never carries attachments


class _AsyncPassthrough:
    """Awaitable stand-in for ``draft_message`` returning verbatim text.

    Used in place of ``AsyncMock`` where the passed text must be echoed back
    as ``MessageDraft.text`` (a plain AsyncMock returns a fixed value)."""

    def __init__(self, medium: str = "telegram"):
        self._medium = medium

    async def __call__(self, text, *, session=None, medium="telegram"):
        from bridge.message_drafter import MessageDraft

        return MessageDraft(text=text, artifacts={})


# ---------------------------------------------------------------------------
# CLI surfaces the DeliveryOutcome verdict (not a false "Queued")
#
# tools/send_message.py must print the handler's returned DeliveryOutcome name
# at each branch — a suppression/defer verdict is a pipeline result, not an
# error, and must be visible to the agent so it can rephrase and resend.
# ---------------------------------------------------------------------------


class TestCliSurfacesDeliveryOutcome:
    """Each DeliveryOutcome branch prints its name to stdout (exit 0)."""

    def _stub(self, monkeypatch, sm, session):
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: session)
        from bridge import promise_gate

        monkeypatch.setattr(promise_gate, "cli_check_or_exit", lambda *a, **kw: None)

    @pytest.mark.parametrize(
        "outcome",
        [
            "sent",
            "suppressed_redundant",
            "suppressed_rtr",
            "deferred_self_draft",
            "dropped_empty",
        ],
    )
    def test_telegram_cli_prints_each_outcome(self, outcome, monkeypatch, capsys):
        from unittest.mock import AsyncMock, MagicMock, patch

        import tools.send_message as sm
        from agent.output_handler import DeliveryOutcome

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "7")
        monkeypatch.setenv("VALOR_SESSION_ID", "cli-outcome-tg")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        session = MagicMock()
        session.session_id = "cli-outcome-tg"
        session.extra_context = {"transport": "telegram"}
        self._stub(monkeypatch, sm, session)

        verdict = DeliveryOutcome(outcome)
        with patch(
            "agent.output_handler.TelegramRelayOutputHandler.send",
            new=AsyncMock(return_value=verdict),
        ):
            sm._send_via_telegram("some text", None)

        out = capsys.readouterr().out
        assert outcome in out, f"expected outcome '{outcome}' in CLI output, got: {out!r}"
        # A suppression/defer verdict must NOT be mislabeled "Queued".
        if outcome != "sent":
            assert "Queued" not in out

    @pytest.mark.parametrize(
        "outcome",
        ["sent", "suppressed_redundant", "deferred_self_draft"],
    )
    def test_email_cli_prints_each_outcome(self, outcome, monkeypatch, capsys):
        from unittest.mock import AsyncMock, MagicMock, patch

        import tools.send_message as sm
        from agent.output_handler import DeliveryOutcome

        monkeypatch.setenv("VALOR_SESSION_ID", "cli-outcome-email")
        monkeypatch.setenv("EMAIL_REPLY_TO", "customer@example.com")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        session = MagicMock()
        session.session_id = "cli-outcome-email"
        session.extra_context = {"transport": "email"}
        self._stub(monkeypatch, sm, session)

        verdict = DeliveryOutcome(outcome)
        with patch(
            "agent.output_handler.TelegramRelayOutputHandler.send",
            new=AsyncMock(return_value=verdict),
        ):
            sm._send_via_email("some email body")

        out = capsys.readouterr().out
        assert outcome in out, f"expected outcome '{outcome}' in CLI output, got: {out!r}"
        if outcome != "sent":
            assert "Queued" not in out
