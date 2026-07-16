"""Unit tests for ``bridge.promise_gate``.

Covers the new forward-deferral class, the legacy behavioral-change
class, the LLM-mocked / heuristic-fallback paths, the kill switch, the
classifier_verdict short-circuit (drafter delegation), the SDK timeout
discriminator, the recovery template anti-leak, and the
``cli_check_or_exit`` exception-swallow semantics.

Plan: docs/plans/sdlc-1219.md (issue #1219).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from unittest.mock import MagicMock, patch

import pytest

import bridge.promise_gate as promise_gate
from bridge.promise_gate import (
    PromiseVerdict,
    _detect_empty_promise,
    _evaluate_promise_heuristic,
    _format_recovery_template,
    cli_check_or_exit,
    evaluate_promise,
)

pytestmark = [pytest.mark.unit, pytest.mark.sdlc]


# === Stub for drafter ClassificationResult (removed from message_drafter) ===
# The promise_gate uses duck typing on classifier_verdict (getattr), so any
# object with .output_type and .nudge_feedback attributes works.


class _OutputType(Enum):
    """Stub for tests that need to supply a classifier_verdict to evaluate_promise."""

    QUESTION = "question"
    STATUS_UPDATE = "status"
    COMPLETION = "completion"
    BLOCKER = "blocker"
    ERROR = "error"


@dataclass
class _ClassificationResult:
    """Stub dataclass for drafter classification verdicts in tests."""

    output_type: _OutputType
    confidence: float = 0.9
    reason: str = ""
    nudge_feedback: str | None = None
    was_rejected_completion: bool = False
    has_workarounds: bool = False


# === Helpers ===


def _mock_llm_block_message(
    action: str = "block", reason: str = "test", class_: str | None = "forward_deferral"
):
    """Build a fake anthropic Message with a single ``promise_verdict`` tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "promise_verdict"
    block.input = {"action": action, "reason": reason, "class_": class_}
    msg = MagicMock()
    msg.content = [block]
    return msg


def _patch_llm(verdict_action, *, reason: str = "test", class_: str | None = None):
    """Patch ``_evaluate_promise_async`` to return a specific verdict (or None)."""

    async def _fake(text):
        if verdict_action is None:
            return None
        return PromiseVerdict(action=verdict_action, reason=reason, class_=class_)

    return patch("bridge.promise_gate._evaluate_promise_async", side_effect=_fake)


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    """Redirect the audit log to a per-test file and disable session_event ORM lookups."""
    log_path = tmp_path / "classification_audit.jsonl"
    monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)
    # Force session_event lookup miss for tests not exercising the real-session path.
    monkeypatch.setattr(promise_gate, "_emit_session_event_if_real", lambda *a, **k: None)


# === Empty-input / kill-switch / classifier short-circuit ===


class TestEmptyInputAndKillSwitch:
    def test_empty_string_returns_allow_no_audit(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)
        v = evaluate_promise("", transport="telegram")
        assert v.action == "allow"
        assert v.reason == "empty_input"
        # No audit entry on empty input.
        assert not log_path.exists()

    def test_none_returns_allow_no_audit(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)
        v = evaluate_promise(None, transport="telegram")
        assert v.action == "allow"
        assert v.reason == "empty_input"
        assert not log_path.exists()

    def test_whitespace_only_returns_allow_no_audit(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)
        v = evaluate_promise("   \n\t  ", transport="telegram")
        assert v.action == "allow"
        assert not log_path.exists()

    def test_kill_switch_returns_allow_no_llm_call(self, monkeypatch):
        """PROMISE_GATE_ENABLED=false → ALLOW unconditionally, LLM is NOT called."""
        monkeypatch.setenv("PROMISE_GATE_ENABLED", "false")
        async_mock = MagicMock()
        with patch("bridge.promise_gate._evaluate_promise_async", async_mock):
            v = evaluate_promise(
                "I'll come back with thoughts later",
                transport="telegram",
            )
        assert v.action == "allow"
        assert v.reason == "gate_disabled"
        async_mock.assert_not_called()

    def test_kill_switch_default_on_when_unset(self, monkeypatch):
        monkeypatch.delenv("PROMISE_GATE_ENABLED", raising=False)
        with _patch_llm("allow"):
            v = evaluate_promise("hello world", transport="telegram")
        assert v.action == "allow"

    def test_kill_switch_default_on_when_empty(self, monkeypatch):
        """``PROMISE_GATE_ENABLED=`` (empty string) → gate enabled (default-on).

        Per plan §Failure Path Test Strategy → Kill Switch Coverage:
        empty string, unset, and any value not in the allow-set MUST be
        treated as default-on. A stray ``PROMISE_GATE_ENABLED=`` in an
        env file must NOT silently disable the gate.

        Asserted by mocking the LLM and observing that it IS called
        (which only happens when the gate is enabled — the kill-switch
        branch returns ALLOW with ``reason="gate_disabled"`` *before*
        any LLM call).
        """
        monkeypatch.setenv("PROMISE_GATE_ENABLED", "")
        async_mock = MagicMock()

        async def _fake(_text):
            async_mock(_text)
            return PromiseVerdict(action="allow", reason="ok", class_=None)

        with patch("bridge.promise_gate._evaluate_promise_async", side_effect=_fake):
            v = evaluate_promise("hello world", transport="telegram")

        assert v.action == "allow"
        # Gate WAS enabled — LLM was consulted, reason is the LLM's
        # reason, not the kill-switch sentinel.
        assert v.reason != "gate_disabled"
        async_mock.assert_called_once()

    def test_kill_switch_default_on_when_whitespace(self, monkeypatch):
        """Whitespace-only env var → treated as empty → default-on.

        No operator would intend whitespace as a disable signal. The
        gate normalizes whitespace-only values to the default before
        the allow-set check.
        """
        monkeypatch.setenv("PROMISE_GATE_ENABLED", "   ")
        async_mock = MagicMock()

        async def _fake(_text):
            async_mock(_text)
            return PromiseVerdict(action="allow", reason="ok", class_=None)

        with patch("bridge.promise_gate._evaluate_promise_async", side_effect=_fake):
            v = evaluate_promise("hello world", transport="telegram")

        assert v.action == "allow"
        assert v.reason != "gate_disabled"
        async_mock.assert_called_once()

    def test_kill_switch_disabled_when_explicit_false(self, monkeypatch):
        """Explicit ``PROMISE_GATE_ENABLED=false`` → gate disabled, LLM NOT called.

        Companion to the empty-string test: confirms the only way to
        disable the gate is an explicit non-empty value not in the
        allow-set. ``"false"`` is the canonical disable value.
        """
        monkeypatch.setenv("PROMISE_GATE_ENABLED", "false")
        async_mock = MagicMock()
        with patch("bridge.promise_gate._evaluate_promise_async", async_mock):
            v = evaluate_promise(
                "I'll come back with thoughts later",
                transport="telegram",
            )
        assert v.action == "allow"
        assert v.reason == "gate_disabled"
        async_mock.assert_not_called()

    def test_kill_switch_disabled_when_explicit_zero(self, monkeypatch):
        """``PROMISE_GATE_ENABLED=0`` → gate disabled (not in allow-set)."""
        monkeypatch.setenv("PROMISE_GATE_ENABLED", "0")
        async_mock = MagicMock()
        with patch("bridge.promise_gate._evaluate_promise_async", async_mock):
            v = evaluate_promise(
                "I'll come back with thoughts later",
                transport="telegram",
            )
        assert v.action == "allow"
        assert v.reason == "gate_disabled"
        async_mock.assert_not_called()

    def test_classifier_verdict_status_with_nudge_blocks(self):
        """Drafter delegation: STATUS_UPDATE + nudge_feedback → BLOCK, no LLM call."""
        result = _ClassificationResult(
            output_type=_OutputType.STATUS_UPDATE,
            confidence=0.95,
            reason="Forward-deferral",
            nudge_feedback="You said 'will come back' — empty promise",
        )
        async_mock = MagicMock()
        with patch("bridge.promise_gate._evaluate_promise_async", async_mock):
            v = evaluate_promise(
                "I'll come back with thoughts",
                transport="drafter",
                classifier_verdict=result,
            )
        assert v.action == "block"
        async_mock.assert_not_called()

    def test_classifier_verdict_completion_allows(self):
        result = _ClassificationResult(
            output_type=_OutputType.COMPLETION,
            confidence=0.9,
            reason="Done",
            nudge_feedback=None,
        )
        async_mock = MagicMock()
        with patch("bridge.promise_gate._evaluate_promise_async", async_mock):
            v = evaluate_promise(
                "Updated foo.py. Committed abc1234.",
                transport="drafter",
                classifier_verdict=result,
            )
        assert v.action == "allow"
        async_mock.assert_not_called()


# === Forward-deferral class (LLM-mocked path) ===


FORWARD_DEFERRAL_PHRASES = [
    "I'll come back with the analysis later.",
    "Will follow up after I check the logs.",
    "Stay tuned for the deployment results.",
    "More to come on the migration plan.",
    "I'll report back with findings tomorrow.",
]


class TestForwardDeferralLLMMocked:
    @pytest.mark.parametrize("phrase", FORWARD_DEFERRAL_PHRASES)
    def test_standalone_phrase_blocks(self, phrase):
        with _patch_llm("block", reason="forward-deferral", class_="forward_deferral"):
            v = evaluate_promise(phrase, transport="telegram")
        assert v.action == "block"
        assert v.class_ == "forward_deferral"

    @pytest.mark.parametrize("phrase", FORWARD_DEFERRAL_PHRASES)
    def test_combined_with_substantive_content_blocks(self, phrase):
        """B2 decided rule: deferral + substantive content → BLOCK regardless."""
        text = f"Found three issues in `bridge/foo.py`. Committed abc1234. {phrase}"
        with _patch_llm(
            "block", reason="forward-deferral with substantive", class_="forward_deferral"
        ):
            v = evaluate_promise(text, transport="telegram")
        assert v.action == "block"

    @pytest.mark.parametrize("phrase", FORWARD_DEFERRAL_PHRASES)
    def test_with_scheduled_delivery_allows(self, phrase):
        """Forward-deferral + queued session ID → ALLOW (verifiable autonomous delivery)."""
        text = f"I queued session abc1234ef. {phrase}"
        with _patch_llm("allow", reason="scheduled-delivery present"):
            v = evaluate_promise(text, transport="telegram")
        assert v.action == "allow"

    def test_ambiguous_followup_blocks(self):
        text = "I'll send a follow-up email later."
        with _patch_llm("block", reason="ambiguous", class_="forward_deferral"):
            v = evaluate_promise(text, transport="telegram")
        assert v.action == "block"


# === Heuristic-fallback (LLM unavailable) ===


class TestHeuristicFallback:
    @pytest.mark.parametrize("phrase", FORWARD_DEFERRAL_PHRASES)
    def test_forward_deferral_heuristic_blocks(self, phrase):
        with _patch_llm(None):  # LLM returns None → fall through to heuristic
            v = evaluate_promise(phrase, transport="telegram")
        assert v.action == "block"
        assert v.class_ == "forward_deferral"

    @pytest.mark.parametrize("phrase", FORWARD_DEFERRAL_PHRASES)
    def test_forward_deferral_with_scheduled_delivery_allows_heuristic(self, phrase):
        text = f"I queued session abc1234ef. {phrase}"
        with _patch_llm(None):
            v = evaluate_promise(text, transport="telegram")
        assert v.action == "allow"

    def test_behavioral_change_without_evidence_blocks_heuristic(self):
        with _patch_llm(None):
            v = evaluate_promise("Got it, will do.", transport="telegram")
        assert v.action == "block"
        assert v.class_ == "behavioral_change"

    def test_behavioral_change_with_commit_allows_heuristic(self):
        with _patch_llm(None):
            v = evaluate_promise(
                "Got it. Updated the summarizer. Committed abc1234.",
                transport="telegram",
            )
        assert v.action == "allow"

    def test_normal_text_allows_heuristic(self):
        with _patch_llm(None):
            v = evaluate_promise(
                "Running tests now, found 3 issues so far.",
                transport="telegram",
            )
        assert v.action == "allow"

    def test_llm_exception_falls_through_to_heuristic(self):
        async def _raise(text):
            raise RuntimeError("simulated LLM failure")

        with patch("bridge.promise_gate._evaluate_promise_async", side_effect=_raise):
            v = evaluate_promise("I'll come back with thoughts", transport="telegram")
        # Heuristic catches the forward-deferral.
        assert v.action == "block"
        assert v.class_ == "forward_deferral"


# === Direct heuristic tests (regex-only, no LLM) ===


class TestEvaluatePromiseHeuristic:
    @pytest.mark.parametrize("phrase", FORWARD_DEFERRAL_PHRASES)
    def test_forward_deferral_pattern_matches(self, phrase):
        v = _evaluate_promise_heuristic(phrase)
        assert v.action == "block"
        assert v.class_ == "forward_deferral"

    def test_empty_input_allows(self):
        v = _evaluate_promise_heuristic("")
        assert v.action == "allow"

    def test_legitimate_completion_allows(self):
        v = _evaluate_promise_heuristic(
            "Updated bridge/promise_gate.py. Committed abc1234ef. All tests pass."
        )
        assert v.action == "allow"


class TestDetectEmptyPromiseShim:
    """Backward-compat shim used by ``bridge.message_drafter._classify_with_heuristics``."""

    def test_behavioral_change_without_evidence(self):
        assert _detect_empty_promise("got it, will do.") is True

    def test_forward_deferral_without_evidence(self):
        assert _detect_empty_promise("i'll come back with thoughts") is True

    def test_with_evidence(self):
        assert _detect_empty_promise("got it. updated foo.py. committed abc1234.") is False

    def test_normal_text(self):
        assert _detect_empty_promise("running tests now") is False


# === Recovery template anti-leak ===


class TestRecoveryTemplate:
    def test_template_does_not_mention_valor_operator_mode(self):
        v = PromiseVerdict(action="block", reason="test", class_="forward_deferral")
        rendered = _format_recovery_template("I'll come back with X", v)
        assert "VALOR_OPERATOR_MODE" not in rendered

    def test_template_does_not_mention_no_promise_gate(self):
        v = PromiseVerdict(action="block", reason="test", class_="forward_deferral")
        rendered = _format_recovery_template("I'll come back with X", v)
        assert "--no-promise-gate" not in rendered
        assert "no-promise-gate" not in rendered

    def test_template_does_not_mention_promise_gate_enabled(self):
        v = PromiseVerdict(action="block", reason="test", class_="forward_deferral")
        rendered = _format_recovery_template("I'll come back with X", v)
        assert "PROMISE_GATE_ENABLED" not in rendered

    def test_template_includes_recovery_shapes(self):
        v = PromiseVerdict(action="block", reason="test", class_="forward_deferral")
        rendered = _format_recovery_template("I'll come back with X", v)
        assert "I did X" in rendered
        assert "I didn't do X" in rendered

    def test_template_quotes_offending_phrase(self):
        v = PromiseVerdict(action="block", reason="test", class_="forward_deferral")
        rendered = _format_recovery_template("I'll come back with X", v)
        assert "'i'll come back'" in rendered or "i'll come back" in rendered.lower()


# === cli_check_or_exit semantics ===


class TestCliCheckOrExit:
    def test_block_exits_with_recovery_template_to_stderr(self, capsys):
        with _patch_llm("block", reason="test", class_="forward_deferral"):
            with pytest.raises(SystemExit) as exc_info:
                cli_check_or_exit(
                    "I'll come back with thoughts",
                    transport="telegram",
                    session_id="cli-123",
                )
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Empty forward-deferral promise blocked" in captured.err
        assert "VALOR_OPERATOR_MODE" not in captured.err
        assert "--no-promise-gate" not in captured.err
        assert "PROMISE_GATE_ENABLED" not in captured.err

    def test_allow_returns_silently(self, capsys):
        with _patch_llm("allow"):
            cli_check_or_exit(
                "Updated foo.py. Committed abc1234.",
                transport="telegram",
                session_id="cli-123",
            )
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""

    def test_no_no_gate_kwarg_in_signature(self):
        """cli_check_or_exit must NOT accept a ``no_gate`` kwarg (cycle-2 B-NEW-2)."""
        with pytest.raises(TypeError):
            cli_check_or_exit(  # type: ignore[call-arg]
                "hello",
                transport="telegram",
                session_id="cli-123",
                no_gate=True,
            )

    def test_unexpected_runtime_error_is_swallowed(self, caplog):
        """cycle-3 C-CYCLE3-3: unexpected evaluate_promise raise must NOT block delivery."""

        def _raise(text, **kwargs):
            raise RuntimeError("simulated infrastructure failure")

        with patch("bridge.promise_gate.evaluate_promise", side_effect=_raise):
            # Should NOT raise SystemExit; should NOT raise RuntimeError.
            cli_check_or_exit("I'll come back with X", transport="telegram", session_id=None)
        # Warning logged.
        assert any("unexpected error" in r.message for r in caplog.records)

    def test_unexpected_import_error_is_swallowed(self):
        def _raise(text, **kwargs):
            raise ImportError("circular import")

        with patch("bridge.promise_gate.evaluate_promise", side_effect=_raise):
            cli_check_or_exit("any", transport="telegram", session_id=None)

    def test_unexpected_attribute_error_is_swallowed(self):
        def _raise(text, **kwargs):
            raise AttributeError("Popoto schema migration")

        with patch("bridge.promise_gate.evaluate_promise", side_effect=_raise):
            cli_check_or_exit("any", transport="telegram", session_id=None)

    def test_exception_writes_audit_with_cli_exception_source(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        def _raise(text, **kwargs):
            raise RuntimeError("simulated")

        with patch("bridge.promise_gate.evaluate_promise", side_effect=_raise):
            cli_check_or_exit("hello", transport="telegram", session_id="cli-123")

        assert log_path.exists()
        contents = log_path.read_text()
        assert "promise_gate_cli_exception" in contents


# === SDK timeout (mocked) ===


class TestSDKTimeout:
    def test_timeout_falls_through_to_heuristic_with_timeout_source(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)

        async def _timeout(text):
            # Simulate the LLM helper returning None *because* of a timeout —
            # this matches the behaviour of _evaluate_promise_async on
            # APITimeoutError (returns None). The timeout discriminator is
            # surfaced by the caller's _PromiseTimeout exception path; here
            # we exercise the simpler "LLM returned None → heuristic" route.
            return None

        with patch("bridge.promise_gate._evaluate_promise_async", side_effect=_timeout):
            v = evaluate_promise(
                "I'll come back with thoughts",
                transport="telegram",
                session_id=None,
            )
        # Heuristic catches the forward-deferral, and the audit log records
        # one of the heuristic-source discriminators.
        assert v.action == "block"
        assert log_path.exists()
        contents = log_path.read_text()
        assert "promise_gate_heuristic" in contents or "promise_gate_timeout" in contents


# === Audit JSONL ordering / kill-switch first-write ===


class TestAuditOrdering:
    def test_kill_switch_writes_audit_before_returning(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)
        monkeypatch.setenv("PROMISE_GATE_ENABLED", "false")

        v = evaluate_promise(
            "I'll come back with thoughts",
            transport="telegram",
            session_id="cli-123",
        )
        assert v.action == "allow"
        assert log_path.exists()
        contents = log_path.read_text()
        assert "promise_gate_disabled" in contents
        assert "cli-123" in contents

    def test_drafter_delegation_writes_audit(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)
        result = _ClassificationResult(
            output_type=_OutputType.COMPLETION,
            confidence=0.9,
            reason="Done",
        )
        evaluate_promise(
            "Updated foo.py. Committed abc1234.",
            transport="drafter",
            session_id="real-session-id",
            classifier_verdict=result,
        )
        contents = log_path.read_text()
        assert "promise_gate_drafter_delegation" in contents

    def test_llm_path_writes_audit_with_llm_source(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)
        with _patch_llm("allow"):
            evaluate_promise("hello world", transport="telegram", session_id="cli-123")
        contents = log_path.read_text()
        assert "promise_gate_llm" in contents

    def test_empty_input_writes_no_audit(self, tmp_path, monkeypatch):
        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(promise_gate, "_AUDIT_LOG_PATH", log_path)
        evaluate_promise("", transport="telegram", session_id="cli-123")
        evaluate_promise("   ", transport="telegram", session_id="cli-123")
        evaluate_promise(None, transport="telegram", session_id="cli-123")
        assert not log_path.exists()


class TestRunAsyncSafelyNoLeak:
    """`_run_async_safely` must not leak the coroutine when a loop is running.

    Regression guard for #2120: on the running-event-loop branch,
    ``asyncio.run`` raises before touching the coroutine, so the eagerly-created
    ``_evaluate_promise_async(text)`` coroutine must be explicitly closed —
    otherwise it leaks ``coroutine '_evaluate_promise_async' was never awaited``
    at GC/teardown and wedges the full suite.
    """

    def test_closes_coro_on_running_loop_no_warning(self):
        import asyncio
        import warnings

        from bridge.promise_gate import _evaluate_promise_async, _run_async_safely

        async def _drive():
            # A loop IS running here (mirrors pytest-asyncio / any async caller).
            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                result = _run_async_safely(_evaluate_promise_async("I will do this later."))
                # Fallthrough contract: returns None (LLM unavailable), heuristic
                # takes over in the caller.
                assert result is None
                # The coroutine we handed in must already be closed, not merely
                # dropped — closed coroutines cannot be awaited.
                # (If it had leaked, the -W error filter would not have caught it
                # here since finalization is deferred; the explicit close below is
                # the real assertion.)

        asyncio.run(_drive())

        # Force a collection under an error filter: a leaked coroutine would be
        # finalized here and raise. A properly closed one produces nothing.
        import gc
        import warnings as _w

        with _w.catch_warnings():
            _w.simplefilter("error", RuntimeWarning)
            gc.collect()

    def test_running_loop_branch_actually_closes(self):
        """Directly assert the coroutine object is closed (state check)."""
        import asyncio

        from bridge.promise_gate import _run_async_safely

        closed = {"seen": False}

        async def _sentinel():
            return 1

        async def _drive():
            coro = _sentinel()
            result = _run_async_safely(coro)
            assert result is None
            # A closed coroutine raises RuntimeError when awaited; use getcoroutinestate.
            import inspect

            closed["seen"] = inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED

        asyncio.run(_drive())
        assert closed["seen"], "coroutine was not closed on the running-loop branch"
