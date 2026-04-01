"""Tests for the stop hook delivery review gate."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from agent.hooks.stop import (
    _build_review_prompt,
    _detect_false_stop,
    _is_telegram_triggered,
    _parse_delivery_choice,
    _read_transcript_tail,
    _review_state,
)


class TestIsTelegramTriggered:
    def test_both_env_vars_set(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "456")
        assert _is_telegram_triggered() is True

    def test_missing_chat_id(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "456")
        assert _is_telegram_triggered() is False

    def test_missing_reply_to(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)
        assert _is_telegram_triggered() is False

    def test_both_missing(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)
        assert _is_telegram_triggered() is False


class TestReadTranscriptTail:
    def test_reads_file_tail(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("A" * 5000)
            f.flush()
            result = _read_transcript_tail({"transcript_path": f.name}, max_chars=100)
            assert len(result) == 100
            assert result == "A" * 100
        os.unlink(f.name)

    def test_empty_path(self):
        assert _read_transcript_tail({}) == ""
        assert _read_transcript_tail({"transcript_path": ""}) == ""

    def test_missing_file(self):
        assert _read_transcript_tail({"transcript_path": "/nonexistent/file.txt"}) == ""


class TestDetectFalseStop:
    def test_short_promise(self):
        assert _detect_false_stop("I started the PR review.") is True
        assert _detect_false_stop("Let me check the logs.") is True
        assert _detect_false_stop("I'm going to investigate this.") is True

    def test_long_substantive_output(self):
        # Long output with substance should not be flagged
        assert _detect_false_stop("A" * 600) is False

    def test_actual_answer(self):
        # Short but substantive answer without promise patterns
        assert _detect_false_stop("The bug is in line 42 of response.py.") is False

    def test_empty_output(self):
        assert _detect_false_stop("") is False
        assert _detect_false_stop("   ") is False


class TestParseDeliveryChoice:
    def test_send(self):
        result = _parse_delivery_choice("SEND")
        assert result["delivery_action"] == "send"

    def test_send_case_insensitive(self):
        result = _parse_delivery_choice("send")
        assert result["delivery_action"] == "send"

    def test_edit_with_text(self):
        result = _parse_delivery_choice("EDIT: Here is my revised response.")
        assert result["delivery_action"] == "send"
        assert result["delivery_text"] == "Here is my revised response."

    def test_edit_empty_text_falls_back_to_send(self):
        result = _parse_delivery_choice("EDIT:")
        assert result["delivery_action"] == "send"
        assert "delivery_text" not in result

    def test_react_with_emoji(self):
        result = _parse_delivery_choice("REACT: 😁")
        assert result["delivery_action"] == "react"
        assert result["delivery_emoji"] == "😁"

    def test_react_no_emoji_defaults_to_thumbs_up(self):
        result = _parse_delivery_choice("REACT:")
        assert result["delivery_action"] == "react"
        assert result["delivery_emoji"] == "👍"

    def test_silent(self):
        result = _parse_delivery_choice("SILENT")
        assert result["delivery_action"] == "silent"

    def test_continue(self):
        result = _parse_delivery_choice("CONTINUE")
        assert result["delivery_action"] == "continue"

    def test_unparseable_defaults_to_send(self):
        result = _parse_delivery_choice("I don't understand the choices")
        assert result["delivery_action"] == "send"

    def test_multiline_finds_choice_at_end(self):
        text = "Some reasoning about the draft...\nI think it's good.\nSEND"
        result = _parse_delivery_choice(text)
        assert result["delivery_action"] == "send"

    def test_empty_string(self):
        result = _parse_delivery_choice("")
        assert result["delivery_action"] == "send"

    def test_edit_multiline(self):
        text = "Let me revise this.\nEDIT: The config is in settings.py, line 42."
        result = _parse_delivery_choice(text)
        assert result["delivery_action"] == "send"
        assert "settings.py" in result["delivery_text"]


class TestBuildReviewPrompt:
    def test_contains_draft(self):
        prompt = _build_review_prompt("Hello, here is my answer.", is_false_stop=False)
        assert "Hello, here is my answer." in prompt

    def test_contains_all_choices(self):
        prompt = _build_review_prompt("draft", is_false_stop=False)
        assert "SEND" in prompt
        assert "EDIT" in prompt
        assert "REACT" in prompt
        assert "SILENT" in prompt
        assert "CONTINUE" in prompt

    def test_false_stop_warning(self):
        prompt = _build_review_prompt("draft", is_false_stop=True)
        assert "stopped too early" in prompt or "didn't finish" in prompt

    def test_no_false_stop_warning(self):
        prompt = _build_review_prompt("draft", is_false_stop=False)
        assert "didn't finish" not in prompt


class TestReviewState:
    def setup_method(self):
        """Clear review state between tests."""
        _review_state.clear()

    def test_state_starts_empty(self):
        assert len(_review_state) == 0

    def test_state_tracks_sessions(self):
        _review_state["test-session-1"] = 1234567890.0
        assert "test-session-1" in _review_state

    def test_state_cleanup(self):
        _review_state["test-session-1"] = 1234567890.0
        _review_state.pop("test-session-1", None)
        assert "test-session-1" not in _review_state


class TestStopHookIntegration:
    """Integration tests for the full stop_hook function."""

    @pytest.fixture(autouse=True)
    def clean_state(self):
        _review_state.clear()
        yield
        _review_state.clear()

    @pytest.mark.asyncio
    async def test_non_telegram_session_passes_through(self, monkeypatch):
        """Non-Telegram sessions skip the review gate entirely."""
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        from agent.hooks.stop import stop_hook

        with patch("agent.hooks.stop._has_pm_messages", return_value=False):
            result = await stop_hook(
                {"session_id": "test-1", "transcript_path": ""},
                None,
                {},
            )
        assert result == {}

    @pytest.mark.asyncio
    async def test_pm_bypass_skips_gate(self, monkeypatch):
        """Sessions where PM already self-messaged skip the review gate."""
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "456")

        from agent.hooks.stop import stop_hook

        with patch("agent.hooks.stop._has_pm_messages", return_value=True):
            result = await stop_hook(
                {"session_id": "test-2", "transcript_path": ""},
                None,
                {},
            )
        assert result == {}

    @pytest.mark.asyncio
    async def test_first_stop_blocks_with_review(self, monkeypatch):
        """First stop on a Telegram session triggers the review gate."""
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "456")

        # Create a transcript file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("The answer is 42. This is a substantive response about the question.")
            transcript_path = f.name

        from agent.hooks.stop import stop_hook

        with (
            patch("agent.hooks.stop._has_pm_messages", return_value=False),
            patch(
                "agent.hooks.stop._generate_draft",
                new_callable=AsyncMock,
                return_value="The answer is 42.",
            ),
        ):
            result = await stop_hook(
                {"session_id": "test-3", "transcript_path": transcript_path},
                None,
                {},
            )

        os.unlink(transcript_path)

        assert result.get("decision") == "block"
        assert "DELIVERY REVIEW" in result.get("reason", "")
        assert "SEND" in result.get("reason", "")

    @pytest.mark.asyncio
    async def test_second_stop_allows_completion(self, monkeypatch):
        """Second stop parses delivery choice and allows completion."""
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "456")

        # Pre-seed review state (simulating first stop already happened)
        _review_state["test-4"] = 1234567890.0

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("SEND")
            transcript_path = f.name

        from agent.hooks.stop import stop_hook

        with (
            patch("agent.hooks.stop._has_pm_messages", return_value=False),
            patch("agent.hooks.stop._write_delivery_to_session"),
            patch(
                "agent.hooks.stop._generate_draft",
                new_callable=AsyncMock,
                return_value="draft text",
            ),
        ):
            result = await stop_hook(
                {"session_id": "test-4", "transcript_path": transcript_path},
                None,
                {},
            )

        os.unlink(transcript_path)

        assert result == {}
        assert "test-4" not in _review_state

    @pytest.mark.asyncio
    async def test_continue_choice_resets_state(self, monkeypatch):
        """CONTINUE choice blocks again and resets review state."""
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "456")

        _review_state["test-5"] = 1234567890.0

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("CONTINUE")
            transcript_path = f.name

        from agent.hooks.stop import stop_hook

        with patch("agent.hooks.stop._has_pm_messages", return_value=False):
            result = await stop_hook(
                {"session_id": "test-5", "transcript_path": transcript_path},
                None,
                {},
            )

        os.unlink(transcript_path)

        assert result.get("decision") == "block"
        assert "Resuming" in result.get("reason", "")
        assert "test-5" not in _review_state
