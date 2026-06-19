"""Unit tests for the wrap-up guard floor (pm_floor_delivered).

Covers the two fixes introduced to prevent OPERATOR_TERMINAL_MESSAGE
from being returned instead of a real response:

1. Non-empty prefix-less PM text is delivered directly via
   _on_user_payload with exit_reason=pm_floor_delivered, and
   _route_pm_classification is NOT called.
2. Empty pm_text still falls through to the transcript fallback path.
3. _is_non_clean_granite_exit() returns False for pm_floor_delivered.
4. PM_TURN_CONTRACT_REMINDER is appended to the dev_text handoff write.
"""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

import pytest

from agent.granite_container.container import (
    PM_TURN_CONTRACT_REMINDER,
    Container,
    ContainerResult,
)
from agent.granite_container.granite_classifier import ClassificationResult
from agent.granite_container.pty_driver import IdleResult, PTYDriver
from agent.session_executor import _CLEAN_GRANITE_EXIT_REASONS, _is_non_clean_granite_exit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _idle_result(buffer_text: str = "fake", saw_idle: bool = True) -> IdleResult:
    return IdleResult(
        saw_idle=saw_idle,
        buffer=buffer_text,
        idle_marker="bypass permissions on",
        elapsed_ms=100,
    )


def _mock_driver(session_id: str = "mock-pm") -> MagicMock:
    mock = MagicMock(spec=PTYDriver)
    mock.read_until_idle.return_value = _idle_result()
    mock.last_resume_uuid.return_value = None
    mock.isalive.return_value = True
    mock._session_id = session_id
    return mock


def _make_container(on_user_payload=None) -> Container:
    """Build a minimal Container with mocked PTYs."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    c = Container.__new__(Container)
    c._pm_pty = _mock_driver("pm-session")
    c._dev_pty = _mock_driver("dev-session")
    c._on_user_payload = on_user_payload
    c._last_dev_report = "Dev built the feature."
    c.max_turns = 3
    return c


# ---------------------------------------------------------------------------
# Test 1: Non-empty prefix-less text → pm_floor_delivered
# ---------------------------------------------------------------------------


def test_wrapup_floor_delivers_prefixless_text():
    """Non-empty prefix-less PM text is delivered via on_user_payload with
    exit_reason=pm_floor_delivered. _route_pm_classification is not called."""
    delivered = []
    container = _make_container(on_user_payload=delivered.append)

    result = ContainerResult(
        session_id="s1",
        user_message="hello",
        exit_reason="pm_complete",
        user_facing_routed=False,
    )
    # Set a non-None transcript path so last_assistant_text is actually called
    # (the guard's `if pm_transcript else ""` short-circuits to "" otherwise).
    result.pm_transcript_path = "/fake/pm_transcript.jsonl"

    # patch last_assistant_text to return prefix-less text
    # patch classify_pm_prefix to return an unknown (prefix-less) classification
    prefixless_text = "Here is the summary without any prefix token."
    prefixless_classification = ClassificationResult(
        destination="unknown",
        payload="",
        compliance_miss=True,
        raw_first_line="Here is the summary",
    )

    with (
        patch(
            "agent.granite_container.container.last_assistant_text",
            return_value=prefixless_text,
        ),
        patch(
            "agent.granite_container.container.text_bearing_count",
            return_value=0,
        ),
        patch(
            "agent.granite_container.container.classify_pm_prefix",
            return_value=prefixless_classification,
        ) as mock_classify,
        patch.object(container, "_route_pm_classification") as mock_route,
    ):
        # _cycle_idle must return (True, ...) for the guard loop to proceed
        with patch.object(
            container,
            "_cycle_idle",
            return_value=(True, "pm buffer", "idle-mark", 100),
        ):
            container._run_wrapup_guard(result)

    # Payload delivered
    assert delivered == [prefixless_text.strip()]
    assert result.user_facing_routed is True
    assert result.exit_reason == "pm_floor_delivered"

    # classify_pm_prefix was called (to check destination)
    mock_classify.assert_called_once()

    # _route_pm_classification was NOT called (bypassed for prefix-less text)
    mock_route.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Empty pm_text → transcript fallback incremented, no delivery
# ---------------------------------------------------------------------------


def test_wrapup_guard_empty_pm_text_increments_fallback():
    """Empty pm_text from last_assistant_text goes to transcript fallback
    and eventually delivers OPERATOR_TERMINAL_MESSAGE."""
    from agent.granite_container.container import OPERATOR_TERMINAL_MESSAGE

    delivered = []
    container = _make_container(on_user_payload=delivered.append)

    result = ContainerResult(
        session_id="s2",
        user_message="hello",
        exit_reason="pm_complete",
        user_facing_routed=False,
    )
    # Set a non-None transcript path so last_assistant_text is called (not short-circuited).
    result.pm_transcript_path = "/fake/pm_transcript.jsonl"

    with (
        patch("agent.granite_container.container.last_assistant_text", return_value=""),
        patch("agent.granite_container.container.text_bearing_count", return_value=0),
        patch("agent.granite_container.container._log_transcript_read_diagnostic") as mock_diag,
    ):
        with patch.object(
            container,
            "_cycle_idle",
            return_value=(True, "pm buffer", "idle-mark", 100),
        ):
            container._run_wrapup_guard(result)

    # transcript fallback logged
    mock_diag.assert_called_once()
    assert result.transcript_fallback_count >= 1

    # canned message delivered
    assert OPERATOR_TERMINAL_MESSAGE in delivered
    assert result.exit_reason == "pm_no_user_message"


# ---------------------------------------------------------------------------
# Test 3: _is_non_clean_granite_exit returns False for pm_floor_delivered
# ---------------------------------------------------------------------------


def test_pm_floor_delivered_is_clean_exit():
    """pm_floor_delivered must be in _CLEAN_GRANITE_EXIT_REASONS so
    _is_non_clean_granite_exit returns False (no REACTION_ERROR)."""
    assert "pm_floor_delivered" in _CLEAN_GRANITE_EXIT_REASONS

    session = MagicMock()
    session.exit_reason = "pm_floor_delivered"
    assert _is_non_clean_granite_exit(session) is False


@pytest.mark.parametrize(
    "exit_reason,expected_non_clean",
    [
        ("pm_complete", False),
        ("pm_user", False),
        ("pm_floor_delivered", False),
        ("pm_no_user_message", True),
        ("pm_max_turns", True),
        ("dev_hang", True),
        ("pm_hang", True),
        ("exception", True),
        ("startup_unresolved", True),
    ],
)
def test_clean_exit_reason_classification(exit_reason, expected_non_clean):
    """Verify the full clean/non-clean classification table."""
    session = MagicMock()
    session.exit_reason = exit_reason
    result = _is_non_clean_granite_exit(session)
    assert result is expected_non_clean, (
        f"exit_reason={exit_reason!r}: expected non_clean={expected_non_clean}, got {result}"
    )


# ---------------------------------------------------------------------------
# Test 4: PM_TURN_CONTRACT_REMINDER appended to dev_text handoff write
# ---------------------------------------------------------------------------


def test_pm_turn_contract_reminder_appended_in_dev_handoff():
    """The PM_TURN_CONTRACT_REMINDER constant is appended to dev_text
    in the _route_pm_classification dev branch, so the PM cannot lose
    the routing-prefix contract across turns."""
    assert PM_TURN_CONTRACT_REMINDER, "Constant must be non-empty"
    assert "[/user]" in PM_TURN_CONTRACT_REMINDER
    assert "[/complete]" in PM_TURN_CONTRACT_REMINDER
    assert "[/dev]" in PM_TURN_CONTRACT_REMINDER

    dev_report = "The feature is done."
    container = _make_container()

    result = ContainerResult(
        session_id="s4",
        user_message="build this",
        exit_reason="in_progress",
    )
    result.dev_transcript_path = "/fake/dev.jsonl"

    # Build a fake dev builder with proper attributes for the dev route.
    fake_builder = MagicMock()
    fake_builder.last_dev_ms = 42
    fake_builder.last_dev_marker = "idle-mark"
    fake_builder.last_hung = False
    fake_builder.last_dev_buf = b"some output"
    fake_builder.run_turn.return_value = dev_report

    # A classification that says [/dev] with non-empty payload (avoids the
    # empty-payload short-circuit that writes PM_COMPLIANCE_NUDGE instead).
    dev_classification = ClassificationResult(
        destination="dev",
        payload="build the feature",
        compliance_miss=False,
        raw_first_line="[/dev]",
        harness=None,
    )

    with (
        patch.object(container, "_get_builder", return_value=fake_builder),
        patch.object(
            container,
            "_cycle_idle",
            return_value=(True, "pm buffer", "idle-mark", 100),
        ),
        patch(
            "agent.granite_container.container.last_assistant_text",
            return_value=dev_report,
        ),
    ):
        container._route_pm_classification(
            dev_classification,
            pm_buf="[/dev]\nbuild the feature",
            turn_index=0,
            result=result,
        )

    # Confirm _pm_pty.write was called and that the reminder was appended
    write_calls = container._pm_pty.write.call_args_list
    assert write_calls, "_pm_pty.write must have been called at least once"
    last_written = write_calls[-1][0][0]  # positional arg of last call
    assert PM_TURN_CONTRACT_REMINDER in last_written, (
        f"PM_TURN_CONTRACT_REMINDER not found in last write to PM PTY.\nWritten: {last_written!r}"
    )
    # Confirm the dev report text is also present (reminder is appended, not replacing)
    assert dev_report in last_written, (
        f"dev_report text not found in PM write. Written: {last_written!r}"
    )
