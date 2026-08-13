"""Wiring tests for the context-recall advisory (#2694).

Separate from ``test_context_recall.py`` (which owns the module's own units)
because these pin the *integration* guarantees the plan and its critique treat
as load-bearing:

* an outbound check that raises still reaches ``_persist_routing_fields`` —
  otherwise a silent failure here would stop writing ``session.context_summary``
  and disable the inbound half of this very feature;
* a context-recall-only bounce that fails to steer delivers ``draft.text``,
  not the raw pre-drafter text via the narration fallback;
* one bounce spends exactly one unit of the self-draft budget;
* the composed inbound prompt orders advisory → injection banner → message;
* the interjection advisory never breaks abort and never displaces the human's
  own steering message.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.message_drafter import (
    CONTEXT_RECALL_SELF_DRAFT_INSTRUCTION,
    SELF_DRAFT_INSTRUCTION,
    MessageDraft,
)

ADVISORY = "[context-recall] read the history: valor-telegram read --chat-id -1001 -n 10"


def _make_handler():
    from agent.output_handler import TelegramRelayOutputHandler

    h = TelegramRelayOutputHandler()
    h._redis = MagicMock()
    return h


def _session(session_id="sess-ctx", chat_id="-1001"):
    s = MagicMock()
    s.session_id = session_id
    s.chat_id = chat_id
    s.extra_context = {}
    return s


def _clean_draft(text="Which PR do you mean?"):
    return MessageDraft(
        text=text,
        full_output_file=None,
        needs_self_draft=False,
        artifacts={},
        context_summary="asked which PR",
        expectations=None,
    )


class TestPersistRoutingFieldsSurvivesAFailingCheck:
    """The critique's highest-value finding.

    The outbound call sits inside the drafter's outer ``try``, whose handler
    skips the rest of the body — including ``_persist_routing_fields``, the only
    writer of ``session.context_summary``. That field is what the inbound intake
    classifier reads, so an escaping exception would degrade intent routing for
    the next message AND disable the inbound advisory. The feature must not be
    able to disable itself.
    """

    def test_raising_check_still_persists_context_summary(self, monkeypatch):
        handler = _make_handler()
        session = _session()

        async def boom(_text):
            raise RuntimeError("context-recall exploded")

        monkeypatch.setattr("bridge.context_recall.check_outbound_context_recall", boom)

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=_clean_draft())):
            asyncio.run(handler.send("123", "Which PR do you mean?", 0, session=session))

        assert session.context_summary == "asked which PR"
        handler._redis.rpush.assert_called_once()

    def test_import_error_still_persists_context_summary(self, monkeypatch):
        """A module-level fail-open cannot catch a failing import of that module."""
        import builtins

        handler = _make_handler()
        session = _session()
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "bridge.context_recall":
                raise ImportError("simulated import failure")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=_clean_draft())):
            asyncio.run(handler.send("123", "Which PR do you mean?", 0, session=session))

        assert session.context_summary == "asked which PR"
        handler._redis.rpush.assert_called_once()

    def test_raising_check_sends_the_message(self, monkeypatch):
        """Fail-open: a broken check never holds a message."""
        handler = _make_handler()

        async def boom(_text):
            raise RuntimeError("nope")

        monkeypatch.setattr("bridge.context_recall.check_outbound_context_recall", boom)

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=_clean_draft())):
            asyncio.run(handler.send("123", "Which PR do you mean?", 0, session=_session()))

        args, _ = handler._redis.rpush.call_args
        assert json.loads(args[1])["text"] == "Which PR do you mean?"


class TestOutboundBounce:
    def _advise(self, monkeypatch, advised=True):
        from bridge.context_recall import ContextRecallVerdict

        async def fake(_text):
            return ContextRecallVerdict(advised=advised, reason="referent clarification")

        monkeypatch.setattr("bridge.context_recall.check_outbound_context_recall", fake)

    def test_clean_question_is_held_and_bounced(self, monkeypatch):
        """The headline case: needs_self_draft=False, yet the message is held."""
        handler = _make_handler()
        self._advise(monkeypatch)
        # Pin the exact advisory text so the assertion below is load-bearing:
        # a broken advisory-wiring path (e.g. the advisory silently dropped
        # before reaching the pushed instruction) must fail this test.
        monkeypatch.setattr(
            "bridge.context_recall.build_context_recall_advisory", lambda **_: ADVISORY
        )
        pushed = []

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=_clean_draft())),
            patch(
                "agent.steering.push_steering_message",
                side_effect=lambda *a, **k: pushed.append((a, k)),
            ),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
        ):
            asyncio.run(handler.send("123", "Which PR do you mean?", 0, session=_session()))

        handler._redis.rpush.assert_not_called()
        assert len(pushed) == 1
        instruction = pushed[0][0][1]
        assert ADVISORY in instruction

    def test_instruction_does_not_claim_a_validator_violation(self, monkeypatch):
        """SELF_DRAFT_INSTRUCTION's wire-format claim is false for this bounce."""
        handler = _make_handler()
        self._advise(monkeypatch)
        pushed = []

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=_clean_draft())),
            patch(
                "agent.steering.push_steering_message",
                side_effect=lambda *a, **k: pushed.append((a, k)),
            ),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
        ):
            asyncio.run(handler.send("123", "Which PR do you mean?", 0, session=_session()))

        instruction = pushed[0][0][1]
        assert instruction.startswith(CONTEXT_RECALL_SELF_DRAFT_INSTRUCTION)
        assert SELF_DRAFT_INSTRUCTION not in instruction

    def test_real_violation_keeps_the_validator_instruction_verbatim(self, monkeypatch):
        handler = _make_handler()
        self._advise(monkeypatch)
        pushed = []
        violated = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
            context_summary="c",
            expectations=None,
        )

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=violated)),
            patch(
                "agent.steering.push_steering_message",
                side_effect=lambda *a, **k: pushed.append((a, k)),
            ),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
        ):
            asyncio.run(handler.send("123", "Which PR do you mean?", 0, session=_session()))

        assert pushed[0][0][1].startswith(SELF_DRAFT_INSTRUCTION)

    def test_unusable_chat_id_does_not_bounce(self, monkeypatch):
        """No advisory to give means nothing to say — send as drafted."""
        handler = _make_handler()
        self._advise(monkeypatch)

        with patch("bridge.message_drafter.draft_message", AsyncMock(return_value=_clean_draft())):
            asyncio.run(
                handler.send("123", "Which PR do you mean?", 0, session=_session(chat_id="0"))
            )

        handler._redis.rpush.assert_called_once()

    def test_one_bounce_spends_exactly_one_budget_unit(self, monkeypatch):
        """A drafter violation AND a context-recall trigger on the same message
        make a single _inject_self_draft_steering call, so the Redis INCR fires
        once, not twice."""
        handler = _make_handler()
        self._advise(monkeypatch)
        violated = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
            context_summary="c",
            expectations=None,
        )
        bump = MagicMock(return_value=1)

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=violated)),
            patch("agent.steering.push_steering_message"),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.bump_self_draft_attempts", bump),
        ):
            asyncio.run(handler.send("123", "Which PR do you mean?", 0, session=_session()))

        assert bump.call_count == 1


class TestBudgetExhaustionKeepsTheDraft:
    """The narration fallback must stay keyed to its ORIGINAL trigger."""

    def _advise(self, monkeypatch):
        from bridge.context_recall import ContextRecallVerdict

        async def fake(_text):
            return ContextRecallVerdict(advised=True, reason="r")

        monkeypatch.setattr("bridge.context_recall.check_outbound_context_recall", fake)

    def test_context_recall_only_bounce_delivers_draft_text(self, monkeypatch):
        handler = _make_handler()
        self._advise(monkeypatch)
        draft = _clean_draft(text="Which PR do you mean?")

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=draft)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            # Budget exhausted -> _inject_self_draft_steering returns False.
            patch("agent.steering.bump_self_draft_attempts", return_value=99),
        ):
            asyncio.run(
                handler.send(
                    "123",
                    "Let me investigate. Which PR do you mean?",
                    0,
                    session=_session(),
                )
            )

        handler._redis.rpush.assert_called_once()
        args, _ = handler._redis.rpush.call_args
        # draft.text, NOT _apply_narration_fallback(raw text), which would have
        # substituted the narration fallback message for this narration-shaped
        # raw input.
        assert json.loads(args[1])["text"] == "Which PR do you mean?"

    def test_genuine_violation_still_gets_the_narration_fallback(self, monkeypatch):
        """The guard narrows the trigger without disabling it."""
        handler = _make_handler()
        self._advise(monkeypatch)
        violated = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
            context_summary="c",
            expectations=None,
        )
        raw = "Let me investigate. Which PR do you mean?"

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=violated)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.bump_self_draft_attempts", return_value=99),
            patch.object(type(handler), "_apply_narration_fallback", return_value="FALLBACK") as fb,
        ):
            asyncio.run(handler.send("123", raw, 0, session=_session()))

        fb.assert_called_once_with(raw)
        args, _ = handler._redis.rpush.call_args
        assert json.loads(args[1])["text"] == "FALLBACK"


class TestAdvisoriesCoexist:
    def test_promise_advisory_and_context_advisory_both_appear(self):
        """Neither clobbers the other in the composed instruction."""
        handler = _make_handler()
        session = _session()
        draft = MessageDraft(
            text="",
            full_output_file=None,
            needs_self_draft=True,
            artifacts={},
            context_summary="c",
            expectations=None,
        )
        draft.promise_advisory = "PROMISE-ADVISORY-TEXT"
        pushed = []

        with (
            patch(
                "agent.steering.push_steering_message",
                side_effect=lambda *a, **k: pushed.append((a, k)),
            ),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
        ):
            ok = handler._inject_self_draft_steering(
                session, draft, context_advisory="CONTEXT-ADVISORY-TEXT"
            )

        assert ok is True
        instruction = pushed[0][0][1]
        assert "PROMISE-ADVISORY-TEXT" in instruction
        assert "CONTEXT-ADVISORY-TEXT" in instruction


class TestInboundPromptOrdering:
    """advisory → injection banner → untrusted text.

    The banner is an OPEN-ENDED prefix with no closing delimiter (see the
    contract comment in bridge/injection_inspection.py), so the untrusted zone
    runs to the end of the prompt. An advisory placed after the banner would sit
    inside the zone the PM is told to distrust, and an attacker could forge an
    identical line.
    """

    def test_advisory_precedes_banner_which_precedes_message(self):
        advisory = "ADVISORY-LINE"
        banner = "BANNER-LINE ----- SCREEN DELIMITER (untrusted content follows) -----"
        message = "MESSAGE-BODY"

        # Mirrors the two prepend blocks in agent/session_executor.py: the
        # banner is prepended first, then the advisory in front of it.
        composed = f"{banner}\n\n{message}"
        composed = f"{advisory}\n\n{composed}"

        assert composed.index(advisory) < composed.index(banner) < composed.index(message)

    def test_session_executor_prepends_in_that_order(self):
        """Source-level pin: reordering the two blocks would flip the indices."""
        from pathlib import Path

        src = Path("agent/session_executor.py").read_text()
        banner_stmt = 'enriched_text = f"{_inj_banner}\\n\\n{enriched_text}"'
        advisory_stmt = 'enriched_text = f"{_ctx_advisory}\\n\\n{enriched_text}"'
        assert banner_stmt in src
        assert advisory_stmt in src
        # The advisory prepend runs LAST, which puts it FIRST in the string.
        assert src.index(banner_stmt) < src.index(advisory_stmt)


class TestCallSitesWireUpBeforeDispatch:
    """Source-level pin: the two call sites inside main() that populate
    _ctx_recall_advisory / extra_overrides must run before the
    dispatch_telegram_session(...) call that consumes them. Two previous
    review rounds found these call sites reachable by nothing in the suite --
    mutating either to a no-op left 194 tests green. This pin makes a reorder
    or deletion of either call site fail loudly."""

    def test_build_and_merge_calls_precede_the_consuming_dispatch(self):
        from pathlib import Path

        src = Path("bridge/telegram_bridge.py").read_text()
        build_call = "_ctx_recall_advisory = _build_context_recall_advisory_for_intent("
        merge_call = "extra_overrides = _merge_context_recall_into_extra_overrides("
        # Unique to the specific dispatch_telegram_session(...) call that
        # actually receives extra_overrides -- there are 3 dispatch call
        # sites in this file, so anchoring on "dispatch_telegram_session("
        # alone would match the wrong (earlier) one.
        dispatch_marker = "extra_context_overrides=extra_overrides,"

        assert build_call in src
        assert merge_call in src
        assert dispatch_marker in src
        assert src.index(build_call) < src.index(dispatch_marker)
        assert src.index(merge_call) < src.index(dispatch_marker)


class TestAckSteeringRoutedAdvisory:
    """The interjection branch pushes a SEPARATE message, at the back."""

    def _call(self, *, text, context_advisory, pushes):
        from bridge import telegram_bridge

        message = MagicMock()
        message.media = None
        message.id = 7
        event = MagicMock()
        event.chat_id = -1001

        async def run():
            with (
                patch.object(
                    telegram_bridge,
                    "push_steering_message",
                    side_effect=lambda *a, **k: pushes.append((a, k)),
                ),
                patch.object(telegram_bridge, "set_reaction", AsyncMock()),
                patch.object(
                    telegram_bridge,
                    "record_telegram_message_handled",
                    AsyncMock(),
                ),
            ):
                await telegram_bridge._ack_steering_routed(
                    MagicMock(),
                    event,
                    message,
                    session_id="sess-1",
                    sender_name="human",
                    text=text,
                    log_context="test",
                    context_advisory=context_advisory,
                )

        asyncio.run(run())

    def test_no_advisory_pushes_exactly_one_message(self):
        """The four non-classifier call sites pass nothing and are unchanged."""
        pushes = []
        self._call(text="do the thing", context_advisory=None, pushes=pushes)
        assert len(pushes) == 1

    def test_advisory_pushes_a_second_separate_message_at_the_back(self):
        pushes = []
        self._call(text="yes", context_advisory=ADVISORY, pushes=pushes)
        assert len(pushes) == 2
        # Human's message first, advisory second — never the reverse.
        assert pushes[0][0][1] == "yes"
        assert pushes[1][0][1] == ADVISORY
        assert pushes[1][1]["front"] is False
        assert pushes[1][0][2] == "intake-classifier"

    def test_abort_survives_an_advisory(self):
        """Appending would have destroyed abort — the exact-match keyword check
        must still see the human's bare string."""
        pushes = []
        self._call(text="stop", context_advisory=ADVISORY, pushes=pushes)
        assert pushes[0][0][1] == "stop"
        assert pushes[0][1]["is_abort"] is True
        assert pushes[1][1]["is_abort"] is False

    def test_advisory_is_never_concatenated_into_the_human_text(self):
        pushes = []
        self._call(text="yes", context_advisory=ADVISORY, pushes=pushes)
        assert ADVISORY not in pushes[0][0][1]

    def test_advisory_push_failure_does_not_affect_the_human_message(self):
        from bridge import telegram_bridge

        calls = []

        def flaky(*a, **k):
            calls.append(a)
            if len(calls) > 1:
                raise RuntimeError("redis down")

        message = MagicMock()
        message.media = None
        message.id = 7
        event = MagicMock()
        event.chat_id = -1001

        async def run():
            with (
                patch.object(telegram_bridge, "push_steering_message", side_effect=flaky),
                patch.object(telegram_bridge, "set_reaction", AsyncMock()),
                patch.object(
                    telegram_bridge,
                    "record_telegram_message_handled",
                    AsyncMock(),
                ),
            ):
                await telegram_bridge._ack_steering_routed(
                    MagicMock(),
                    event,
                    message,
                    session_id="sess-1",
                    sender_name="human",
                    text="yes",
                    log_context="test",
                    context_advisory=ADVISORY,
                )

        asyncio.run(run())  # must not raise
        assert calls[0][1] == "yes"


class TestExtraContextSeed:
    """new_work branch: the seed keeps `None` when there is no advisory."""

    @pytest.mark.parametrize(
        "seed,advisory,expected",
        [
            (None, None, None),
            (None, ADVISORY, {"context_recall_advisory": ADVISORY}),
            ({"injection_risk_banner": "B"}, None, {"injection_risk_banner": "B"}),
            (
                {"injection_risk_banner": "B"},
                ADVISORY,
                {"injection_risk_banner": "B", "context_recall_advisory": ADVISORY},
            ),
        ],
    )
    def test_merge_semantics(self, seed, advisory, expected):
        from bridge.telegram_bridge import _merge_context_recall_into_extra_overrides

        assert _merge_context_recall_into_extra_overrides(seed, advisory) == expected


class TestBuildContextRecallAdvisoryForIntent:
    """Direct coverage of the #2694 build-site helper (mutation-verified)."""

    def test_returns_none_and_does_not_call_the_builder_when_not_advised(self, monkeypatch):
        from bridge.telegram_bridge import _build_context_recall_advisory_for_intent

        def explode(**_kwargs):
            raise AssertionError("build_context_recall_advisory must not be called")

        monkeypatch.setattr("bridge.context_recall.build_context_recall_advisory", explode)

        result = _build_context_recall_advisory_for_intent(
            {"context_recall_advised": False}, chat_id="-1001", project_name="p"
        )
        assert result is None

    def test_calls_through_with_the_right_args_and_returns_the_advisory(self, monkeypatch):
        from bridge.telegram_bridge import _build_context_recall_advisory_for_intent

        calls = []

        def fake(**kwargs):
            calls.append(kwargs)
            return ADVISORY

        monkeypatch.setattr("bridge.context_recall.build_context_recall_advisory", fake)

        result = _build_context_recall_advisory_for_intent(
            {"context_recall_advised": True, "context_recall_reason": "referent clarification"},
            chat_id="-1001",
            project_name="p",
        )

        assert result == ADVISORY
        assert calls == [
            {"chat_id": "-1001", "medium": "telegram", "reason": "referent clarification"}
        ]

    def test_returns_none_fail_quiet_when_the_builder_raises(self, monkeypatch):
        from bridge.telegram_bridge import _build_context_recall_advisory_for_intent

        def explode(**_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("bridge.context_recall.build_context_recall_advisory", explode)

        result = _build_context_recall_advisory_for_intent(
            {"context_recall_advised": True, "context_recall_reason": "r"},
            chat_id="-1001",
            project_name="p",
        )
        assert result is None
