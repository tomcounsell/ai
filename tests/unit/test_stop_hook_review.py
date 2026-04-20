"""Tests for the stop hook delivery review gate (tool-call variant, #1035)."""

from __future__ import annotations

import os
import tempfile

from agent.hooks.stop import (
    _build_review_prompt,
    _detect_false_stop,
    _is_user_triggered,
    _read_transcript_tail,
    _resolve_medium,
    _review_state,
    classify_delivery_outcome,
)


class TestIsUserTriggered:
    def test_telegram_chat_id_set(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
        assert _is_user_triggered() is True

    def test_email_reply_to_set(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
        assert _is_user_triggered() is True

    def test_neither_set(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
        assert _is_user_triggered() is False


class TestResolveMedium:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("VALOR_TRANSPORT", "slack")
        assert _resolve_medium("nonexistent-session") == "slack"

    def test_email_heuristic_when_email_env(self, monkeypatch):
        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert _resolve_medium("nonexistent-session") == "email"

    def test_default_telegram(self, monkeypatch):
        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert _resolve_medium("nonexistent-session") == "telegram"


class TestClassifyDeliveryOutcome:
    def test_send_message_tool(self):
        tail = '{"type": "tool_use", "input": {"command": "python tools/send_message.py \'hi\'"}}'
        assert classify_delivery_outcome(tail) == "send"

    def test_legacy_send_telegram(self):
        tail = 'python tools/send_telegram.py "hello"'
        assert classify_delivery_outcome(tail) == "send"

    def test_react_with_emoji(self):
        tail = "python tools/react_with_emoji.py excited"
        assert classify_delivery_outcome(tail) == "react"

    def test_continue_with_other_tool_use(self):
        tail = '{"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}'
        assert classify_delivery_outcome(tail) == "continue"

    def test_silent_on_empty_tail(self):
        assert classify_delivery_outcome("") == "silent"

    def test_silent_with_no_tool_use(self):
        tail = "the agent just emitted some text, no tool call at all"
        assert classify_delivery_outcome(tail) == "silent"


class TestBuildReviewPrompt:
    def test_includes_draft_and_tools(self):
        prompt = _build_review_prompt("Hello world", "telegram", is_false_stop=False)
        assert "Hello world" in prompt
        assert "tools/send_message.py" in prompt
        assert "tools/react_with_emoji.py" in prompt
        assert "medium=telegram" in prompt

    def test_includes_false_stop_warning(self):
        prompt = _build_review_prompt("draft", "telegram", is_false_stop=True)
        assert "intent without substance" in prompt

    def test_email_medium_shown(self):
        prompt = _build_review_prompt("draft", "email", is_false_stop=False)
        assert "medium=email" in prompt


class TestDetectFalseStop:
    def test_detects_investigation_pattern(self):
        tail = "Let me check the logs..."
        assert _detect_false_stop(tail) is True

    def test_long_tail_is_never_false_stop(self):
        tail = "I " + "x" * 600
        assert _detect_false_stop(tail) is False

    def test_substantive_text_not_false_stop(self):
        tail = "All 42 tests passed, committed abc1234 and pushed to origin/main."
        assert _detect_false_stop(tail) is False


class TestReadTranscriptTail:
    def test_reads_last_n_chars(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("line 1\n")
            f.write("line 2\n")
            f.write("line 3\n")
            path = f.name
        try:
            tail = _read_transcript_tail({"transcript_path": path}, max_chars=10)
            assert "line" in tail
        finally:
            os.unlink(path)

    def test_missing_path_returns_empty(self):
        assert _read_transcript_tail({"transcript_path": ""}) == ""
        assert _read_transcript_tail({}) == ""


class TestReviewStateSentinel:
    def test_review_state_is_module_dict(self):
        _review_state.clear()
        assert isinstance(_review_state, dict)
        assert len(_review_state) == 0
