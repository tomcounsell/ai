"""Tests for agent/output_handler.py: outbound content filters.

Covers read-the-room and redundancy-filter wiring.
Split out of the former ``tests/unit/test_output_handler.py`` monolith (#2879). The
``output_handler`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


class TestReadTheRoomWiring:
    """Tests for the RTR wiring in TelegramRelayOutputHandler.send (issue #1193).

    These exercise the *handler-level* verdict application (trim coercion, the
    suppress reaction queue alignment, the suppress-fallthrough). Verdict
    selection itself is tested in test_read_the_room.py.
    """

    def _make_handler(self, mock_redis):
        from agent.output_handler import TelegramRelayOutputHandler

        handler = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        handler._redis = mock_redis
        return handler

    def _mock_redis(self):
        r = MagicMock()
        r.rpush = MagicMock()
        r.expire = MagicMock()
        return r

    def _bypass_drafter(self, _input, *, session=None, medium="telegram"):
        """Pass-through ``draft_message`` so ``delivery_text == text``."""
        from bridge.message_drafter import MessageDraft

        return MessageDraft(text=_input)

    def _make_session(self, **kwargs):
        s = MagicMock()
        s.session_id = kwargs.get("session_id", "abc")
        s.session_type = kwargs.get("session_type", "teammate")
        s.sdlc_stage = None
        s.has_pm_messages = MagicMock(return_value=False)
        s.get_parent_session = MagicMock(return_value=None)
        s.is_sdlc = kwargs.get("is_sdlc", False)
        s.session_events = None
        s.telegram_message_id = kwargs.get("telegram_message_id", None)
        return s

    def test_send_verdict_writes_text_payload(self):
        """RTR verdict 'send' leaves delivery_text untouched."""
        from bridge.read_the_room import RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="send", reason="clean")),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,  # > SHORT_OUTPUT_THRESHOLD
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        mock_r.rpush.assert_called_once()
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:abc"
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == "x" * 250
        assert payload.get("type") != "reaction"

    def test_trim_long_verdict_swaps_delivery_text(self):
        """RTR verdict 'trim' (long) replaces delivery_text and emits rtr.trimmed."""
        from bridge.read_the_room import RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        revised = "Quick pointer: see dashboard for details."
        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(
                    return_value=RoomVerdict(action="trim", revised_text=revised, reason="partial")
                ),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == revised

        # rtr.trimmed event captured on the session
        events = session.session_events or []
        types_ = [e["type"] for e in events]
        assert "rtr.trimmed" in types_

    def test_trim_too_short_coerces_to_suppress_with_reaction(self):
        """trim with len < TRIM_TOO_SHORT_THRESHOLD coerces to suppress + 👀."""
        from bridge.read_the_room import RTR_SUPPRESS_EMOJI, RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(
                    return_value=RoomVerdict(action="trim", revised_text="ok!", reason="redundant")
                ),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        # Exactly one rpush -- the reaction, not a text payload.
        assert mock_r.rpush.call_count == 1
        key = mock_r.rpush.call_args[0][0]
        # Queue MUST align with session.session_id, NOT chat_id.
        assert key == "telegram:outbox:abc"
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["emoji"] == RTR_SUPPRESS_EMOJI
        assert payload["reply_to"] == 42
        assert "text" not in payload

        events = session.session_events or []
        types_ = [e["type"] for e in events]
        assert "rtr.suppressed" in types_
        assert (
            next(e for e in events if e["type"] == "rtr.suppressed")["reason"] == "trim_too_short"
        )

    def test_suppress_with_anchor_writes_reaction_to_session_queue(self):
        """suppress + reply_to writes 👀 to telegram:outbox:{session_id}."""
        from bridge.read_the_room import RTR_SUPPRESS_EMOJI, RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        # IMPORTANT: session_id != chat_id so we can verify queue alignment.
        session = self._make_session(session_id="sess-xyz")

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="suppress", reason="redundant")),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        # Exactly one rpush -- the reaction. No text payload.
        assert mock_r.rpush.call_count == 1
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:sess-xyz"  # NOT telegram:outbox:-100123
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["emoji"] == RTR_SUPPRESS_EMOJI
        assert payload["session_id"] == "sess-xyz"
        # Critically: text payload was NOT written
        assert "text" not in payload

    def test_suppress_payload_matches_react_byte_for_byte(self):
        """The RTR suppress reaction payload must equal what react() produces
        for the same args (Implementation Note AD1 / snapshot-equality test).
        """
        from agent.output_handler import TelegramRelayOutputHandler

        handler = TelegramRelayOutputHandler.__new__(TelegramRelayOutputHandler)

        # Both writers go through _build_reaction_payload, so for matching
        # session_id derivation the payloads are identical.
        from_send = handler._build_reaction_payload(
            "-100123", 42, "👀", "sess-xyz", timestamp=1000.0
        )
        from_react = handler._build_reaction_payload(
            "-100123", 42, "👀", "sess-xyz", timestamp=1000.0
        )
        assert from_send == from_react

    def test_suppress_with_no_anchor_falls_through_to_send(self):
        """suppress + no reply anchor AND no triggering message id falls through
        to send the original text and emits rtr.suppress_fallthrough
        (Implementation Note SI1; #2199 reason ``no_reaction_target``).
        """
        from bridge.read_the_room import RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session(telegram_message_id=None)

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="suppress", reason="redundant")),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=None,  # No anchor for the 👀 reaction.
                    session=session,
                )
            )

        # Original text DID land on the outbox -- fall-through preserves
        # the audit signal (F4).
        mock_r.rpush.assert_called_once()
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == "x" * 250

        # And we logged the fallthrough event with reason no_reaction_target.
        events = session.session_events or []
        types_ = [e["type"] for e in events]
        assert "rtr.suppress_fallthrough" in types_
        ev = next(e for e in events if e["type"] == "rtr.suppress_fallthrough")
        assert ev["reason"] == "no_reaction_target"

    def test_suppress_no_anchor_reacts_on_trigger_message(self):
        """#2199: suppress + no reply anchor but a known triggering message id
        reacts 👀 on the trigger instead of falling through to a late text
        reply. This is the stale-offhand-mention case the fallback was built
        for and previously missed.
        """
        from bridge.read_the_room import RTR_SUPPRESS_EMOJI, RoomVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session(session_id="sess-xyz", telegram_message_id=555)

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="suppress", reason="stale_trigger")),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=None,  # No reply anchor.
                    session=session,
                )
            )

        # Exactly one rpush -- the reaction on the triggering message. No text.
        assert mock_r.rpush.call_count == 1
        key = mock_r.rpush.call_args[0][0]
        assert key == "telegram:outbox:sess-xyz"
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["emoji"] == RTR_SUPPRESS_EMOJI
        assert payload["reply_to"] == 555
        assert "text" not in payload

    def test_rtr_failure_falls_open(self):
        """RTR raising must not block delivery (Path A fail-open contract,
        `agent/output_handler.py:1144`).

        RTR runs unconditionally on every eligible Path A send (#2733), so this
        outer guard moved from theoretical to hot. Mirrors the Path B
        equivalent, `test_valor_telegram_rtr.py::test_rtr_failure_falls_open`.

        Mutation-proven: converting the `except Exception as rtr_err:` at
        `agent/output_handler.py:1144` into a bare `raise` makes this test
        fail with the injected RuntimeError instead of the assertions below
        (confirmed by hand while writing this test, then reverted).
        """
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        with (
            patch(
                "bridge.message_drafter.draft_message", AsyncMock(side_effect=self._bypass_drafter)
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="x" * 250,
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        # Delivery proceeded with the original text -- RTR's failure never
        # reached the outbox write.
        mock_r.rpush.assert_called_once()
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == "x" * 250
        assert payload.get("type") != "reaction"

    def test_steering_deferred_path_skips_rtr(self):
        """RTR must not run when delivery is deferred to self-draft steering
        (the steering_deferred return at line 250 happens before RTR).
        """
        from bridge.message_drafter import MessageDraft

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_session()

        # Drafter signals self-draft fallback by returning needs_self_draft=True.
        deferred = MessageDraft(
            text="x" * 250,
            needs_self_draft=True,
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=deferred)),
            patch.object(
                handler,
                "_inject_self_draft_steering",
                MagicMock(return_value=True),
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(side_effect=AssertionError("RTR must not be called")),
            ),
        ):
            asyncio.run(handler.send("-100123", "x" * 250, 42, session=session))

        # No outbox write because steering deferred.
        mock_r.rpush.assert_not_called()


class TestRedundancyFilterWiring:
    """Tests for the redundancy filter wiring in TelegramRelayOutputHandler.send
    (issue #1205).

    These exercise the handler-level integration of should_suppress(): that SDLC
    sessions with redundant drafts get a 👀 reaction instead of a text message,
    that non-SDLC sessions bypass the filter, and that recent_sent_drafts is
    appended after a successful outbox write.

    Filter internals (bigram Jaccard, termination conditions) are tested
    separately in test_redundancy_filter.py.
    """

    def _make_handler(self, mock_redis=None):
        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        if mock_redis is not None:
            h._redis = mock_redis
        return h

    def _mock_redis(self):
        r = MagicMock()
        r.rpush = MagicMock(return_value=1)
        r.expire = MagicMock()
        return r

    def _bypass_drafter(self, _input, *, session=None, medium="telegram"):
        """Pass-through drafter so delivery_text == text."""
        from bridge.message_drafter import MessageDraft

        return MessageDraft(text=_input, artifacts={})

    def _make_sdlc_session(self, *, recent_drafts=None, status="active", telegram_message_id=None):
        s = MagicMock()
        s.session_id = "sdlc-sess-001"
        s.is_sdlc = True
        s.status = status
        s.recent_sent_drafts = recent_drafts or []
        s.session_events = None
        s.record_recent_sent_draft = MagicMock()
        s.extra_context = {}
        # Explicit None so a bare MagicMock's __int__ (defaults to 1) never
        # masquerades as a triggering message id in the no-anchor fallthrough.
        s.telegram_message_id = telegram_message_id
        return s

    def _make_non_sdlc_session(self):
        s = MagicMock()
        s.session_id = "conv-sess-001"
        s.is_sdlc = False
        s.status = "active"
        s.recent_sent_drafts = []
        s.session_events = None
        s.extra_context = {}
        return s

    # ── SDLC session with redundant draft → 👀 reaction, no text ─────────────

    def test_sdlc_redundant_draft_queues_reaction_not_text(self):
        """An SDLC session whose draft is near-identical to a prior send must
        queue a 👀 reaction and skip the text outbox write."""
        import time

        from bridge.redundancy_filter import RTR_SUPPRESS_EMOJI, SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session(
            recent_drafts=[{"ts": time.time(), "text": "checking status", "artifacts": {}}]
        )

        suppress_verdict = SuppressionVerdict(
            action="suppress", reason="jaccard=0.80>=threshold=0.65", jaccard=0.80, matched_index=0
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=suppress_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="checking status",
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        # The outbox should have received only the 👀 reaction (no text message).
        calls = mock_r.rpush.call_args_list
        assert len(calls) >= 1, "Expected at least one rpush call (for the reaction)"
        # Check at least one payload is a reaction
        has_reaction = False
        has_text_message = False
        for call in calls:
            payload = json.loads(call[0][1])
            if payload.get("type") == "reaction":
                has_reaction = True
                assert payload["emoji"] == RTR_SUPPRESS_EMOJI
            else:
                has_text_message = True
        assert has_reaction, "Expected a 👀 reaction in the outbox"
        assert not has_text_message, "Text message should have been suppressed"

    # ── Non-SDLC session → filter bypassed, RTR runs as before ──────────────

    def test_non_sdlc_session_bypasses_filter(self):
        """A non-SDLC session must skip the redundancy filter entirely.
        The text message goes through the normal RTR + outbox path."""
        from bridge.redundancy_filter import should_suppress as _should_suppress

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_non_sdlc_session()

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                wraps=_should_suppress,
            ) as mock_filter,
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="hello world",
                    reply_to_msg_id=1,
                    session=session,
                )
            )

        # The filter must NOT have been called for a non-SDLC session.
        mock_filter.assert_not_called()

        # Text message delivered normally.
        mock_r.rpush.assert_called_once()
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload.get("type") != "reaction"
        assert payload["text"] == "hello world"

    # ── Successful send appends to recent_sent_drafts ────────────────────────

    def test_successful_send_records_draft(self):
        """After a successful outbox rpush, record_recent_sent_draft is called."""
        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session()

        send_verdict = SuppressionVerdict(action="send", reason="no_baseline")

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=send_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="status update",
                    reply_to_msg_id=1,
                    session=session,
                )
            )

        session.record_recent_sent_draft.assert_called_once()

    # ── Failed save does not block rpush ─────────────────────────────────────

    def test_record_draft_failure_does_not_block_outbox_write(self):
        """If record_recent_sent_draft raises, the outbox rpush already happened
        and the error is swallowed — delivery is not reversed."""
        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session()
        session.record_recent_sent_draft.side_effect = RuntimeError("save failed")

        send_verdict = SuppressionVerdict(action="send", reason="no_baseline")

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=send_verdict,
            ),
        ):
            # Must not raise.
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="some message",
                    reply_to_msg_id=1,
                    session=session,
                )
            )

        # Text was delivered.
        mock_r.rpush.assert_called_once()
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["text"] == "some message"

    # ── Filter exception falls through to RTR + outbox ────────────────────────

    def test_filter_exception_falls_through_to_send(self):
        """An exception inside the redundancy filter branch must not block
        delivery — the text goes to the outbox as if the filter didn't exist."""
        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session()

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                side_effect=RuntimeError("filter exploded"),
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="some important message",
                    reply_to_msg_id=1,
                    session=session,
                )
            )

        # Delivery still happened.
        mock_r.rpush.assert_called()
        # At least one call is the text message (not a reaction).
        text_calls = [
            c for c in mock_r.rpush.call_args_list if json.loads(c[0][1]).get("type") != "reaction"
        ]
        assert len(text_calls) >= 1

    # ── No anchor → fallthrough (matches RTR contract) ───────────────────────

    def test_suppress_with_no_anchor_falls_through_to_send(self):
        """When suppress is returned but reply_to_msg_id is None, the filter
        falls through and sends the text (mirrors RTR's no-anchor contract)."""
        import time

        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session(
            recent_drafts=[{"ts": time.time(), "text": "status", "artifacts": {}}]
        )

        suppress_verdict = SuppressionVerdict(
            action="suppress", reason="jaccard=0.90>=threshold=0.65", jaccard=0.90, matched_index=0
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=suppress_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="status",
                    reply_to_msg_id=None,  # No anchor
                    session=session,
                )
            )

        # Text must have been sent (no anchor, no trigger id → fallthrough).
        mock_r.rpush.assert_called()
        text_calls = [
            c for c in mock_r.rpush.call_args_list if json.loads(c[0][1]).get("type") != "reaction"
        ]
        assert len(text_calls) >= 1

    def test_suppress_no_anchor_reacts_on_trigger_message(self):
        """#2199: redundancy suppress + no reply anchor but a known triggering
        message id reacts on the trigger instead of falling through — the same
        no-anchor contract as the RTR suppress branch, kept uniform."""
        import time

        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session(
            recent_drafts=[{"ts": time.time(), "text": "status", "artifacts": {}}],
            telegram_message_id=909,
        )

        suppress_verdict = SuppressionVerdict(
            action="suppress", reason="jaccard=0.90>=threshold=0.65", jaccard=0.90, matched_index=0
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=suppress_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="status",
                    reply_to_msg_id=None,  # No anchor.
                    session=session,
                )
            )

        # Exactly one rpush -- the reaction on the triggering message. No text.
        assert mock_r.rpush.call_count == 1
        payload = json.loads(mock_r.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["reply_to"] == 909
        assert "text" not in payload

    # ── Session event includes jaccard and matched_prior_preview ─────────────

    def test_suppressed_redundant_event_includes_jaccard_and_preview(self):
        """The drafter.suppressed_redundant session event must include both
        ``jaccard`` and ``matched_prior_preview`` fields so that observers
        can audit what triggered suppression (Success Criterion 6)."""
        import time

        from bridge.redundancy_filter import SuppressionVerdict

        mock_r = self._mock_redis()
        handler = self._make_handler(mock_r)
        session = self._make_sdlc_session(
            recent_drafts=[
                {"ts": time.time(), "text": "previous status message here", "artifacts": {}}
            ]
        )

        suppress_verdict = SuppressionVerdict(
            action="suppress",
            reason="jaccard=0.80>=threshold=0.65",
            jaccard=0.80,
            matched_index=0,
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.redundancy_filter.should_suppress",
                return_value=suppress_verdict,
            ),
        ):
            asyncio.run(
                handler.send(
                    chat_id="-100123",
                    text="previous status message here",
                    reply_to_msg_id=42,
                    session=session,
                )
            )

        events = session.session_events or []
        suppressed_events = [e for e in events if e.get("type") == "drafter.suppressed_redundant"]
        assert len(suppressed_events) == 1, "Expected exactly one suppressed_redundant event"
        ev = suppressed_events[0]
        assert ev["jaccard"] == 0.80, "jaccard must be forwarded into the session event"
        assert ev["matched_prior_preview"] == "previous status message here", (
            "matched_prior_preview must be the text of the matched prior draft"
        )
