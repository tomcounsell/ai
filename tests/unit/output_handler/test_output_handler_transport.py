"""Tests for agent/output_handler.py: transport derivation and routing.

Covers react-transport derivation, transport-aware routing, and the
drafter's ordering above transport selection.
Split out of the former ``tests/unit/test_output_handler.py`` monolith (#2879). The
``output_handler`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agent.output_handler import (
    FileOutputHandler,
    TelegramRelayOutputHandler,
)


class TestReactTransportDerivation:
    """Issue #2629 / react-transport-derivation plan: react() consults the
    same ``_resolve_transport`` that ``send()`` uses, so a chatless session's
    completion reaction never enqueues a dead ``telegram:outbox:0`` write.

    Cases (a)-(f) from the plan's Success Criteria. (g) and (h) — the
    executor-level guard and the ``agent_session or session`` fallback —
    live in ``tests/unit/test_session_executor_runner_dispatch.py``'s
    ``TestReactTransportExecutorGuards`` (that file already owns the
    ``_execute_agent_session`` harness: ``FakeSessionRunner``, ``_make_session``,
    ``_patch_runner``/``_patch_worktree``, ``redis_test_db``).

    Red-first note: the production change (utils/peer.py, output_handler.py,
    session_executor.py, session_state.py, email_bridge.py, telegram_bridge.py,
    react_with_emoji.py) landed on this branch across commits f5a9721f9,
    91d8940bb, e96d2400a *before* this test class was written -- a prior BUILD
    dispatch died mid-task and was resumed, so true red-first ordering (tests
    written against unmodified main, proven red, then implementation) was not
    preserved. This suite instead pins the shipped behavior and was verified
    green against the current HEAD.
    """

    class _ReflectionSession:
        """Shape of a reflection-scheduler session: placeholder chat, no transport."""

        session_id = "0_1234567890"
        chat_id = "0"
        project_key = "valor"
        extra_context: dict = {}

    def _make_handler(self, mock_redis=None, file_handler=None):
        handler = TelegramRelayOutputHandler(
            redis_url="redis://localhost:6379/0",
            file_handler=file_handler,
        )
        handler._redis = mock_redis if mock_redis is not None else MagicMock()
        return handler

    # ── (a) chatless session -> no telegram:outbox:* write ─────────────────

    def test_a_system_transport_session_writes_no_outbox_key(self, caplog):
        """A chatless (system-transport) session's react() makes zero Redis
        writes, and the pre-existing outbox-RPUSH error log (guarded by
        except Exception around the RPUSH) is not emitted -- it returns
        before reaching that code at all (Failure Path Strategy item (b))."""
        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        with caplog.at_level("ERROR"):
            asyncio.run(handler.react("0", 0, "✅", self._ReflectionSession()))

        mock_r.rpush.assert_not_called()
        mock_r.expire.assert_not_called()
        assert not any(
            "telegram" in rec.message.lower() and rec.levelname == "ERROR" for rec in caplog.records
        )

    # ── (b) session=None -> unchanged telegram RPUSH (back-compat) ─────────

    def test_b_session_none_back_compat_unchanged_telegram_rpush(self):
        """Explicit back-compat pin: a bare 3-arg-shaped call (session
        defaults to None) still RPUSHes to telegram:outbox:{chat_id} exactly
        as it did before the signature widened."""
        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        asyncio.run(handler.react("chat-1", 42, "\U0001f44d"))

        mock_r.rpush.assert_called_once()
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:chat-1"
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["chat_id"] == "chat-1"
        assert payload["reply_to"] == 42
        assert payload["emoji"] == "\U0001f44d"
        mock_r.expire.assert_called_once_with("telegram:outbox:chat-1", 3600)

    # ── (c) explicit real peer wins over a system-addressee session ────────

    def test_c_explicit_real_peer_chat_id_wins_over_system_session(self):
        """A chatless session whose OWN chat_id derives to system, called with
        an explicit real numeric chat_id argument (the CLI cross-chat-send
        shape), still reaches telegram -- mirrors
        _resolve_transport's own explicit-peer-wins precedence."""
        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        asyncio.run(handler.react("-100123", 42, "\U0001f44d", self._ReflectionSession()))

        mock_r.rpush.assert_called_once()
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:-100123"
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["chat_id"] == "-100123"

    # ── (d) _resolve_transport raising -> telegram fallback, no crash ──────

    def test_d_resolve_transport_raising_falls_back_to_telegram(self):
        """A descriptor-polluted extra_context (a string, not a dict) makes
        ``extra.get("transport")`` raise AttributeError inside
        _resolve_transport. react() must not crash and must fall back to the
        status-quo telegram RPUSH (Risk 3)."""

        class PollutedSession:
            session_id = "sess-polluted"
            chat_id = "0"
            project_key = "valor"
            extra_context = "polluted"  # str, not dict -- .get() raises

        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        # Must not raise.
        asyncio.run(handler.react("0", 0, "\U0001f44d", PollutedSession()))

        mock_r.rpush.assert_called_once()
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:0"

    # ── (e) file dual-write still occurs on the system path ────────────────

    def test_e_file_dual_write_occurs_on_system_path(self):
        """The FileOutputHandler dual-write is the audit trail for a dropped
        system-transport reaction -- it must still fire."""
        with tempfile.TemporaryDirectory() as tmp:
            file_handler = FileOutputHandler(log_dir=Path(tmp))
            handler = self._make_handler(file_handler=file_handler)

            asyncio.run(handler.react("0", 0, "✅", self._ReflectionSession()))

            log_file = Path(tmp) / "0_1234567890.log"
            assert log_file.exists()
            content = log_file.read_text()
            assert "REACTION" in content

    # ── (f) emoji=None: unchanged telegram RPUSH, no-op on system path ─────

    def test_f_emoji_none_still_rpushes_unchanged_on_telegram_path(self):
        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        asyncio.run(handler.react("chat-1", 42, None))

        mock_r.rpush.assert_called_once()
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["emoji"] is None

    def test_f_emoji_none_still_noops_on_system_path(self):
        mock_r = MagicMock()
        handler = self._make_handler(mock_redis=mock_r)

        asyncio.run(handler.react("0", 0, None, self._ReflectionSession()))

        mock_r.rpush.assert_not_called()


class TestTransportAwareRouting:
    """Tests for transport-aware default routing in TelegramRelayOutputHandler.

    Design rule (set by user 2026-04-30): the default Stop drafter / OutputHandler
    routes the agent's final reply through the **same medium that spawned the
    session**. ``extra_context.transport == "email"`` redirects writes from
    ``telegram:outbox:<sid>`` to ``email:outbox:<sid>`` with an email-shaped
    payload that ``bridge/email_relay.py`` can deliver. Sessions without a
    transport key, or with ``transport == "telegram"``, preserve the existing
    Telegram behavior.

    Reactions (``react()``) are nonsensical for email — there is no email
    equivalent of an emoji reaction. For ``transport=email`` sessions,
    ``react()`` becomes a silent no-op (single INFO log).
    """

    def _make_handler(self, mock_redis=None):
        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        if mock_redis is not None:
            h._redis = mock_redis
        else:
            h._redis = MagicMock()
        return h

    def _email_session(
        self,
        session_id: str = "email-sess",
        message_id: str = "<orig-msg@example.com>",
        from_addr: str = "customer@example.com",
        subject: str = "Original subject",
        to_addrs=None,
        cc_addrs=None,
    ):
        """Build a fake email-spawned session matching bridge/email_bridge.py."""
        s = MagicMock()
        s.session_id = session_id
        s.extra_context = {
            "transport": "email",
            "email_message_id": message_id,
            "email_from": from_addr,
            "email_to_addrs": to_addrs or [],
            "email_cc_addrs": cc_addrs or [],
            "email_subject": subject,
        }
        return s

    # ── 1. Telegram-spawned session writes to telegram outbox (regression) ──

    def test_telegram_session_writes_to_telegram_outbox(self):
        """Sessions with transport=telegram (or no transport set) must continue
        to write to telegram:outbox — back-compat with the existing behavior."""
        handler = self._make_handler()

        s = MagicMock()
        s.session_id = "tg-sess"
        s.extra_context = {"transport": "telegram"}

        # Stub the drafter so we don't need its internals.
        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("123456", "Hello world", 0, session=s))

        handler._redis.rpush.assert_called_once()
        key = handler._redis.rpush.call_args[0][0]
        assert key == "telegram:outbox:tg-sess"

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["chat_id"] == "123456"
        assert payload["text"] == "Hello world"
        assert payload["session_id"] == "tg-sess"

    # ── 2. Email-spawned session writes to email outbox with correct payload ──

    def test_email_session_writes_to_email_outbox(self):
        """transport=email must route writes to email:outbox with the unified
        payload shape (matching tools/send_message.py::_send_via_email and the
        relay's expected schema in bridge/email_relay.py)."""
        handler = self._make_handler()

        session = self._email_session(
            session_id="email-sess",
            message_id="<orig@example.com>",
            from_addr="customer@example.com",
            subject="My setup question",
        )

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(
                handler.send(
                    chat_id="customer@example.com",
                    text="Here is the answer.",
                    reply_to_msg_id=0,
                    session=session,
                )
            )

        handler._redis.rpush.assert_called_once()
        key = handler._redis.rpush.call_args[0][0]
        assert key == "email:outbox:email-sess", f"Expected email outbox key, got {key}"

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        # Match the unified email payload schema from bridge/email_relay.py.
        # The handler emits ``to`` as a list to carry the reply-all recipient
        # set (primary + To/CC minus self). With no extra recipients stamped
        # on the session, the list collapses to just the primary recipient.
        assert payload["session_id"] == "email-sess"
        assert payload["to"] == ["customer@example.com"]
        # Subject prefixed with "Re:" (worker-reply semantics).
        assert payload["subject"] == "Re: My setup question"
        assert payload["body"] == "Here is the answer."
        assert payload["in_reply_to"] == "<orig@example.com>"
        assert payload["references"] == "<orig@example.com>"
        assert "timestamp" in payload
        # No telegram-only fields leak through.
        assert "chat_id" not in payload
        assert "reply_to" not in payload

    # ── 3. Email-spawned session with "Re:" subject does NOT double-prefix ──

    def test_email_session_does_not_double_prefix_re(self):
        """If the original subject already starts with "Re:" (any case), the
        reply must not become "Re: Re: ...". Match the worker reply semantics
        in bridge/email_bridge.py::_build_reply_mime."""
        handler = self._make_handler()

        session = self._email_session(subject="Re: My setup question")

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("customer@example.com", "ok", 0, session=session))

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["subject"] == "Re: My setup question"

    def test_email_session_does_not_double_prefix_re_lowercase(self):
        """Case-insensitive: "re: foo" should not become "Re: re: foo"."""
        handler = self._make_handler()

        session = self._email_session(subject="re: lowercase")

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("customer@example.com", "ok", 0, session=session))

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        # The original casing is preserved verbatim (no prefix added).
        assert payload["subject"] == "re: lowercase"

    # ── 4. Reactions on email sessions are dropped silently ──

    def test_email_session_send_never_queues_reaction_payload(self):
        """For transport=email sessions, send() must NOT queue any reaction
        payload (including the RTR suppress 👀 reaction). Email has no emoji-
        reaction concept; queueing one would orphan a payload that nothing
        consumes. The transport-aware short-circuit at the top of send()
        bypasses the entire RTR/reaction path.
        """
        handler = self._make_handler()
        session = self._email_session()

        # Force the RTR module to return "suppress" — if the email branch
        # didn't short-circuit, this would queue a reaction.
        from bridge.read_the_room import RoomVerdict

        suppress_verdict = RoomVerdict(action="suppress", reason="testing", revised_text=None)

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=RuntimeError("skip drafter")),
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=suppress_verdict),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="customer@example.com",
                    text="Body that would normally be RTR-suppressed.",
                    reply_to_msg_id=42,  # truthy so the RTR suppress would fire
                    session=session,
                )
            )

        # Per the unified-handler contract: when RTR suppresses on an email
        # session, the payload is dropped entirely. Email has no reaction
        # concept and the canonical pipeline now runs RTR for both transports,
        # so an email suppression is fully silent (zero rpush, zero reaction).
        assert handler._redis.rpush.call_count == 0

    def test_send_with_empty_text_for_email_is_noop(self):
        """Empty text on an email session must NOT queue an empty email."""
        handler = self._make_handler()

        session = self._email_session()

        asyncio.run(handler.send("customer@example.com", "", 0, session=session))

        handler._redis.rpush.assert_not_called()

    # ── 5. Missing transport defaults to telegram (back-compat) ──

    def test_missing_transport_defaults_to_telegram(self):
        """A session whose extra_context lacks the transport key — older
        sessions, or any session created before the email path existed —
        must continue to route to telegram:outbox."""
        handler = self._make_handler()

        s = MagicMock()
        s.session_id = "no-transport"
        s.extra_context = {}  # no transport key

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("123", "Hello", 0, session=s))

        key = handler._redis.rpush.call_args[0][0]
        assert key == "telegram:outbox:no-transport"

    def test_extra_context_none_defaults_to_telegram(self):
        """If extra_context itself is None, default to telegram."""
        handler = self._make_handler()

        s = MagicMock()
        s.session_id = "ctx-none"
        s.extra_context = None

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(handler.send("123", "Hello", 0, session=s))

        key = handler._redis.rpush.call_args[0][0]
        assert key == "telegram:outbox:ctx-none"

    # ── 6. Email payload includes from_addr from SMTP_USER env ──

    def test_email_payload_includes_from_addr_from_env(self):
        """When SMTP_USER is set, the payload's from_addr must mirror it so
        the email_relay sends with the correct envelope sender."""
        import os as _os

        handler = self._make_handler()
        session = self._email_session()

        old_smtp_user = _os.environ.get("SMTP_USER")
        _os.environ["SMTP_USER"] = "valor@yuda.me"
        try:
            with patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=RuntimeError("skip drafter")),
            ):
                asyncio.run(handler.send("customer@example.com", "Hi", 0, session=session))
        finally:
            if old_smtp_user is None:
                _os.environ.pop("SMTP_USER", None)
            else:
                _os.environ["SMTP_USER"] = old_smtp_user

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["from_addr"] == "valor@yuda.me"


class TestDrafterHoistedAboveTransport:
    """Issue #1369: the drafter must run ONCE for both telegram and email
    transports, before the transport branch. These tests confirm the hoist
    and the email-side propagation (reply-all ``to`` list, attachments,
    suppression-drops-payload contract)."""

    def _make_handler(self):
        h = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        h._redis = MagicMock()
        return h

    def _telegram_session(self, session_id="hoist-tg"):
        s = MagicMock()
        s.session_id = session_id
        s.extra_context = {"transport": "telegram"}
        s.is_sdlc = False
        s.recent_sent_drafts = []
        return s

    def _email_session(
        self,
        session_id="hoist-email",
        to_addrs=None,
        cc_addrs=None,
        subject="The thread",
        message_id="<orig@example.com>",
    ):
        s = MagicMock()
        s.session_id = session_id
        s.extra_context = {
            "transport": "email",
            "email_subject": subject,
            "email_message_id": message_id,
            "email_to_addrs": to_addrs or [],
            "email_cc_addrs": cc_addrs or [],
        }
        s.is_sdlc = False
        s.recent_sent_drafts = []
        return s

    def test_drafter_called_once_for_telegram(self):
        """A telegram send must invoke ``draft_message`` exactly once."""
        handler = self._make_handler()
        session = self._telegram_session()
        draft_stub = MagicMock(
            text="drafted telegram text",
            full_output_file=None,
            artifacts={},
            needs_self_draft=False,
            open_questions=None,
            context_summary=None,
        )

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(return_value=draft_stub),
        ) as mock_draft:
            asyncio.run(handler.send("123", "raw text", 0, session=session))

        assert mock_draft.await_count == 1
        # Drafted text reached the outbox, not raw.
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["text"] == "drafted telegram text"

    def test_drafter_called_once_for_email(self):
        """An email send must invoke ``draft_message`` exactly once. This is
        the regression that closes #1369: previously the email branch never
        ran the drafter."""
        handler = self._make_handler()
        session = self._email_session()
        draft_stub = MagicMock(
            text="drafted email body",
            full_output_file=None,
            artifacts={},
            needs_self_draft=False,
            open_questions=None,
            context_summary=None,
        )

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(return_value=draft_stub),
        ) as mock_draft:
            asyncio.run(handler.send("customer@example.com", "raw", 0, session=session))

        assert mock_draft.await_count == 1
        # The drafter ran with ``medium="email"`` so the per-medium format
        # rules apply (no markdown on the wire for email).
        assert mock_draft.await_args.kwargs["medium"] == "email"
        # Drafted body landed in the email payload.
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["body"] == "drafted email body"

    def test_email_payload_carries_reply_all_recipients(self):
        """The email outbox payload's ``to`` field is a list combining the
        primary recipient with ``extra_context.email_to_addrs`` and
        ``email_cc_addrs``, minus the SMTP user (own address)."""
        handler = self._make_handler()
        session = self._email_session(
            to_addrs=["primary@example.com", "team@example.com"],
            cc_addrs=["watcher@example.com"],
        )

        import os as _os

        old_smtp = _os.environ.get("SMTP_USER")
        _os.environ["SMTP_USER"] = "bot@ourdomain.com"
        try:
            with patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=RuntimeError("skip drafter")),
            ):
                asyncio.run(
                    handler.send(
                        "primary@example.com",
                        "reply body",
                        0,
                        session=session,
                    )
                )
        finally:
            if old_smtp is None:
                _os.environ.pop("SMTP_USER", None)
            else:
                _os.environ["SMTP_USER"] = old_smtp

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        # Primary first, then To/CC entries (dedup the primary; drop SMTP user).
        assert payload["to"] == [
            "primary@example.com",
            "team@example.com",
            "watcher@example.com",
        ]

    def test_email_payload_drops_own_smtp_user_from_reply_all(self):
        """When the SMTP user appears in the original To/CC, it is filtered
        out of the reply-all list (we don't reply to ourselves)."""
        handler = self._make_handler()
        session = self._email_session(
            to_addrs=["bot@ourdomain.com", "team@example.com"],
            cc_addrs=["bot@ourdomain.com"],
        )

        import os as _os

        old_smtp = _os.environ.get("SMTP_USER")
        _os.environ["SMTP_USER"] = "bot@ourdomain.com"
        try:
            with patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=RuntimeError("skip drafter")),
            ):
                asyncio.run(handler.send("customer@example.com", "body", 0, session=session))
        finally:
            if old_smtp is None:
                _os.environ.pop("SMTP_USER", None)
            else:
                _os.environ["SMTP_USER"] = old_smtp

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        # bot@ourdomain.com must NOT appear anywhere in the to list.
        addrs_lower = [a.lower() for a in payload["to"]]
        assert "bot@ourdomain.com" not in addrs_lower

    def test_cli_file_paths_propagate_to_telegram_outbox(self):
        """CLI-supplied ``file_paths`` are forwarded into the telegram outbox
        payload (and merged with any drafter overflow file)."""
        handler = self._make_handler()
        session = self._telegram_session()

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(
                handler.send(
                    "12345",
                    "body",
                    0,
                    session=session,
                    file_paths=["/tmp/a.png", "/tmp/b.txt"],
                )
            )

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["file_paths"] == ["/tmp/a.png", "/tmp/b.txt"]

    def test_cli_file_paths_propagate_to_email_outbox(self):
        """CLI-supplied ``file_paths`` are forwarded into the email outbox
        payload as ``attachments`` (the relay's expected key)."""
        handler = self._make_handler()
        session = self._email_session()

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("skip drafter")),
        ):
            asyncio.run(
                handler.send(
                    "customer@example.com",
                    "see attached",
                    0,
                    session=session,
                    file_paths=["/tmp/report.pdf"],
                )
            )

        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["attachments"] == ["/tmp/report.pdf"]
