"""Unit tests for agent.tui_interaction_capture.

Covers prompt classification (slash_command / human_steering), triviality and
length gating, ordinal derivation, snippet stripping/truncation, fail-silent
behavior on recorder/Memory raise, and summarize-and-store distillation.
"""

from unittest.mock import patch

import pytest

import agent.tui_interaction_capture as cap

# ---------------------------------------------------------------------------
# capture_prompt_event — empty / no-op guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", ["", "   ", "\n\t ", None])
def test_capture_prompt_event_empty_is_noop(prompt):
    """Empty / whitespace-only / None prompts record nothing."""
    with patch.object(cap, "record_telemetry_event") as rec:
        cap.capture_prompt_event("sess-1", prompt)
        rec.assert_not_called()


def test_capture_prompt_event_no_session_id_is_noop():
    with patch.object(cap, "record_telemetry_event") as rec:
        cap.capture_prompt_event("", "do something substantial here please")
        rec.assert_not_called()


# ---------------------------------------------------------------------------
# capture_prompt_event — slash commands
# ---------------------------------------------------------------------------


def test_slash_command_is_always_recorded():
    """A `/`-prefixed prompt is always a slash_command (no triviality gate)."""
    with (
        patch.object(cap, "read_session_timeline", return_value=[]),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", "/do-test")
        rec.assert_called_once()
        sid, event = rec.call_args[0]
        assert sid == "sess-1"
        assert event["type"] == "slash_command"
        assert event["command"] == "do-test"


def test_slash_command_name_stops_at_whitespace():
    with (
        patch.object(cap, "read_session_timeline", return_value=[]),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", "  /do-plan some args here  ")
        _, event = rec.call_args[0]
        assert event["type"] == "slash_command"
        assert event["command"] == "do-plan"


def test_bare_slash_short_command_still_signal():
    """Even a trivially short slash command is recorded (no length gate)."""
    with (
        patch.object(cap, "read_session_timeline", return_value=[]),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", "/x")
        _, event = rec.call_args[0]
        assert event["type"] == "slash_command"
        assert event["command"] == "x"


# ---------------------------------------------------------------------------
# capture_prompt_event — human steering
# ---------------------------------------------------------------------------


def test_first_nonslash_prompt_is_not_steering():
    """Ordinal 0 (the first prompt) is the initial instruction, not a steer."""
    long_prompt = "Please refactor the entire authentication subsystem now."
    with (
        patch.object(cap, "read_session_timeline", return_value=[]),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", long_prompt)
        rec.assert_not_called()


def test_second_nonslash_prompt_is_steering():
    """Ordinal > 0 with a substantive prompt is a human_steering event."""
    long_prompt = "Actually, switch the storage backend to Postgres instead please."
    timeline = [{"type": "slash_command", "command": "do-build"}]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", long_prompt)
        rec.assert_called_once()
        _, event = rec.call_args[0]
        assert event["type"] == "human_steering"
        assert event["ordinal"] == 1
        assert event["snippet"]


@pytest.mark.parametrize("trivial", ["ok", "yes", "continue", "  LGTM  ", "got it"])
def test_trivial_steering_is_gated(trivial):
    timeline = [{"type": "human_steering", "ordinal": 0}]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", trivial)
        rec.assert_not_called()


def test_short_steering_is_gated():
    """Below _MIN_STEERING_LENGTH → no event (even when ordinal > 0)."""
    timeline = [{"type": "human_steering", "ordinal": 0}]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", "do the thing")
        rec.assert_not_called()


def test_steering_snippet_truncated_to_120():
    long = "x" * 500
    # Pad to clear the min-length gate trivially (x*500 already does).
    timeline = [{"type": "slash_command"}]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", long)
        _, event = rec.call_args[0]
        assert len(event["snippet"]) <= 120


def test_steering_snippet_strips_private():
    secret = (
        "Please update the authentication module to use the key "
        "<private>sk-supersecret-token-value</private> for the production environment now."
    )
    timeline = [{"type": "slash_command"}]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap, "record_telemetry_event") as rec,
    ):
        cap.capture_prompt_event("sess-1", secret)
        _, event = rec.call_args[0]
        assert "sk-supersecret" not in event["snippet"]


# ---------------------------------------------------------------------------
# capture_prompt_event — fail-silent
# ---------------------------------------------------------------------------


def test_capture_prompt_event_swallows_recorder_exception():
    with (
        patch.object(cap, "read_session_timeline", return_value=[]),
        patch.object(cap, "record_telemetry_event", side_effect=RuntimeError("boom")),
    ):
        # Must not raise.
        cap.capture_prompt_event("sess-1", "/do-test")


def test_capture_prompt_event_swallows_timeline_exception():
    with patch.object(cap, "read_session_timeline", side_effect=RuntimeError("boom")):
        cap.capture_prompt_event("sess-1", "a substantive steering message that is long enough")


# ---------------------------------------------------------------------------
# summarize_and_store
# ---------------------------------------------------------------------------


def test_summarize_no_session_id_is_noop():
    with patch.object(cap.Memory, "safe_save") as save:
        cap.summarize_and_store("", "valor")
        save.assert_not_called()


def test_summarize_none_project_key_skips_write():
    timeline = [{"type": "slash_command", "command": "do-test"}]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap.Memory, "safe_save") as save,
    ):
        cap.summarize_and_store("sess-1", None)
        save.assert_not_called()


def test_summarize_empty_timeline_no_save():
    with (
        patch.object(cap, "read_session_timeline", return_value=[]),
        patch.object(cap.Memory, "safe_save") as save,
    ):
        cap.summarize_and_store("sess-1", "valor")
        save.assert_not_called()


def test_summarize_no_interaction_signal_skips():
    """Only tool_use events (no slash/steering) → noise, no save."""
    timeline = [
        {"type": "tool_use", "name": "Edit"},
        {"type": "tool_use", "name": "Read"},
    ]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap.Memory, "safe_save") as save,
    ):
        cap.summarize_and_store("sess-1", "valor")
        save.assert_not_called()


def test_summarize_saves_with_correct_shape():
    timeline = [
        {"type": "slash_command", "command": "do-plan"},
        {"type": "slash_command", "command": "do-build"},
        {"type": "human_steering", "ordinal": 2, "snippet": "switch backend"},
        {"type": "tool_use", "name": "Edit"},
        {"type": "tool_use", "name": "Read"},
        {"type": "idle_gap", "gap_seconds": 90.0},
    ]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap.Memory, "safe_save") as save,
    ):
        cap.summarize_and_store("sess-1", "valor")
        save.assert_called_once()
        kwargs = save.call_args.kwargs
        assert kwargs["agent_id"] == "tui-sess-1"
        assert kwargs["project_key"] == "valor"
        assert kwargs["source"] == cap.SOURCE_HUMAN
        assert kwargs["importance"] == 1.0
        assert kwargs["metadata"] == {
            "category": "pattern",
            "tags": ["tui-interaction"],
        }
        assert len(kwargs["content"]) <= 500
        # The distilled string should mention the slash sequence and approvals.
        content = kwargs["content"]
        assert "do-plan" in content and "do-build" in content
        assert "2 tools" in content or "approved 2" in content


def test_summarize_swallows_save_exception():
    timeline = [{"type": "slash_command", "command": "do-test"}]
    with (
        patch.object(cap, "read_session_timeline", return_value=timeline),
        patch.object(cap.Memory, "safe_save", side_effect=RuntimeError("boom")),
    ):
        # Must not raise.
        cap.summarize_and_store("sess-1", "valor")
