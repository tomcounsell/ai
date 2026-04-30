"""Tests for conditional session_events emission (cycle-2 C-NEW-1, C-NEW-4).

The promise gate has a two-channel telemetry design with documented
asymmetry:

1. **Audit JSONL** — universal. Fires on every gate call regardless
   of session_id provenance.
2. **session_events** — conditional. Fires only when
   ``AgentSession.query.get(session_id)`` returns a real session.
   Synthetic ``cli-{epoch}`` IDs result in audit-only telemetry.

The mixed ``session_id`` provenance across the four CLIs:

* ``send_telegram.py`` reads real ``VALOR_SESSION_ID`` from the worker.
* ``valor_telegram.py`` and ``valor_email.py`` use synthetic ``cli-{epoch}``.
* ``send_message.py`` accepts whatever its caller passes.

Plan: docs/plans/sdlc-1219.md (issue #1219).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import bridge.promise_gate as promise_gate
from bridge.promise_gate import PromiseVerdict, _emit_session_event_if_real

pytestmark = [pytest.mark.unit, pytest.mark.sdlc]


class TestEmitSessionEventIfReal:
    def test_none_session_id_silently_skips(self):
        # Should not raise; should not call AgentSession.query.get.
        _emit_session_event_if_real(None, {"type": "promise_gate.blocked"})

    def test_empty_string_session_id_silently_skips(self):
        _emit_session_event_if_real("", {"type": "promise_gate.blocked"})

    def test_synthetic_cli_id_silently_skips(self):
        """``AgentSession.query.get('cli-123')`` returns None → no append."""
        fake_session_query = MagicMock()
        fake_session_query.get.return_value = None
        with patch(
            "models.agent_session.AgentSession.query",
            new=fake_session_query,
        ):
            _emit_session_event_if_real("cli-1234567890", {"type": "promise_gate.blocked"})
        # Lookup attempted, returned None, no save call possible.
        fake_session_query.get.assert_called_once_with("cli-1234567890")

    def test_real_session_id_appends_event(self):
        fake_session = MagicMock()
        fake_session.session_events = []

        def _save():
            pass

        fake_session.save = _save
        fake_session_query = MagicMock()
        fake_session_query.get.return_value = fake_session

        with patch(
            "models.agent_session.AgentSession.query",
            new=fake_session_query,
        ):
            _emit_session_event_if_real(
                "real-session-abc",
                {"type": "promise_gate.blocked", "reason": "forward_deferral"},
            )

        # Event was appended.
        assert len(fake_session.session_events) == 1
        assert fake_session.session_events[0]["type"] == "promise_gate.blocked"

    def test_lookup_exception_silently_skips(self):
        """If the ORM lookup raises (e.g. Popoto schema migration), no-op."""
        fake_session_query = MagicMock()
        fake_session_query.get.side_effect = AttributeError("schema mismatch")
        with patch(
            "models.agent_session.AgentSession.query",
            new=fake_session_query,
        ):
            # Must not raise.
            _emit_session_event_if_real(
                "real-session-abc",
                {"type": "promise_gate.blocked"},
            )


class TestSessionEventsConditionalDuringEvaluatePromise:
    """Integration of the session_events emitter inside ``evaluate_promise``.

    Verifies that BLOCK verdicts attempt session_events emission, and
    that synthetic IDs result in a no-op while real-session IDs append
    the event.
    """

    def test_block_with_synthetic_id_attempts_lookup_no_append(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        # Restore the real emitter (pytest fixture in test_promise_gate.py
        # blanks it out, but that fixture is module-scoped to that file).
        # We don't import that fixture here.

        fake_session_query = MagicMock()
        fake_session_query.get.return_value = None  # synthetic ID → miss

        async def _llm(text):
            return PromiseVerdict(
                action="block", reason="forward-deferral", class_="forward_deferral"
            )

        with (
            patch("bridge.promise_gate._evaluate_promise_async", side_effect=_llm),
            patch("models.agent_session.AgentSession.query", new=fake_session_query),
        ):
            from bridge.promise_gate import evaluate_promise

            v = evaluate_promise(
                "I'll come back with thoughts",
                transport="telegram",
                session_id="cli-1234567890",
            )

        assert v.action == "block"
        # ORM lookup was attempted; returned None.
        fake_session_query.get.assert_called_with("cli-1234567890")

    def test_block_with_real_id_appends_event(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        fake_session = MagicMock()
        fake_session.session_events = []
        fake_session.save = lambda: None
        fake_session_query = MagicMock()
        fake_session_query.get.return_value = fake_session

        async def _llm(text):
            return PromiseVerdict(
                action="block", reason="forward-deferral", class_="forward_deferral"
            )

        with (
            patch("bridge.promise_gate._evaluate_promise_async", side_effect=_llm),
            patch("models.agent_session.AgentSession.query", new=fake_session_query),
        ):
            from bridge.promise_gate import evaluate_promise

            v = evaluate_promise(
                "I'll come back with thoughts",
                transport="telegram",
                session_id="real-session-abc",
            )

        assert v.action == "block"
        # session_events was appended on the fake session.
        assert len(fake_session.session_events) == 1
        assert fake_session.session_events[0]["type"] == "promise_gate.blocked"
