"""Completeness and classification tests for the ``ExitReason`` StrEnum (#2004 T1.1).

Every exit-reason vocabulary member declares its classification at the member
(``is_clean`` / ``wrapup_eligible`` / ``is_anomaly``); the router's frozensets
are derived from the enum, so adding a member without thinking about its
disposition fails here — never silently.

String VALUES are pinned byte-identical to the pre-enum vocabulary: telemetry
(session_events ``exit_summary`` entries, ``AgentSession.exit_reason``)
depends on them.
"""

import pytest

from agent.session_runner.router import (
    ANOMALY_EXIT_REASONS,
    CLEAN_EXIT_REASONS,
    WRAPUP_ELIGIBLE_EXIT_REASONS,
    ExitReason,
    TurnFailure,
)

# The exact disposition sets, by telemetry value. These mirror the pre-enum
# frozensets in router.py — a member may belong to several sets (pm_complete
# is clean AND wrapup-eligible) or none (turn-level slugs like empty_output
# are translated by the runner before reaching summary classification).
EXPECTED_CLEAN = {
    "pm_complete",
    "pm_user",
    "pm_needs_human",
    "pm_floor_delivered",
    "steer_abort",
}
EXPECTED_WRAPUP_ELIGIBLE = {
    "pm_complete",
    "pm_user",
    "pm_needs_human",
    "pm_max_turns",
    "pm_floor_delivered",
}
EXPECTED_ANOMALY = {
    "pm_hang",
    "dev_hang",
    "pm_no_user_message",
    "exception",
    "error",
}

# The full pinned vocabulary. Byte-identical to the pre-enum strings.
EXPECTED_VALUES = {
    # Adapter default (RunSummary before any terminal classification).
    "in_progress",
    # Summary-level terminal reasons.
    "pm_complete",
    "pm_user",
    "pm_needs_human",
    "pm_floor_delivered",
    "pm_no_user_message",
    "pm_empty_turn",
    "pm_max_turns",
    "steer_abort",
    "turn_timeout",
    "error",
    "exception",
    # Historical vocabulary preserved for telemetry continuity.
    "pm_hang",
    "dev_hang",
    # Turn-level reasons minted by the role driver.
    "empty_output",
    "headless_turn_timeout",
    "headless_thinking_corruption",
    "headless_subprocess_error",
    "headless_binary_missing",
    "headless_nonzero_exit_no_result",
}


class TestExitReasonCompleteness:
    def test_vocabulary_values_byte_identical(self):
        """The enum covers exactly the pinned vocabulary, byte-identical."""
        assert {r.value for r in ExitReason} == EXPECTED_VALUES

    @pytest.mark.parametrize("member", list(ExitReason), ids=lambda r: r.value)
    def test_every_member_declares_classification(self, member):
        """No member is unclassified: all three flags are real bools declared
        at the member, and each flag matches the expected disposition set."""
        assert isinstance(member.is_clean, bool)
        assert isinstance(member.wrapup_eligible, bool)
        assert isinstance(member.is_anomaly, bool)
        assert member.is_clean == (member.value in EXPECTED_CLEAN)
        assert member.wrapup_eligible == (member.value in EXPECTED_WRAPUP_ELIGIBLE)
        assert member.is_anomaly == (member.value in EXPECTED_ANOMALY)

    def test_frozensets_derived_from_enum(self):
        """The router frozensets are exactly the enum-derived disposition sets."""
        assert CLEAN_EXIT_REASONS == EXPECTED_CLEAN
        assert WRAPUP_ELIGIBLE_EXIT_REASONS == EXPECTED_WRAPUP_ELIGIBLE
        assert ANOMALY_EXIT_REASONS == EXPECTED_ANOMALY

    def test_plain_string_membership_still_works(self):
        """Existing import sites compare raw strings against the frozensets
        (e.g. ``AgentSession.exit_reason not in CLEAN_EXIT_REASONS`` in
        session_executor). StrEnum members ARE str — hash and equality must
        both hold in each direction."""
        assert "pm_complete" in CLEAN_EXIT_REASONS
        assert "error" in ANOMALY_EXIT_REASONS
        assert "error" not in CLEAN_EXIT_REASONS
        assert ExitReason.PM_COMPLETE in frozenset({"pm_complete"})
        assert ExitReason.PM_USER == "pm_user"

    def test_str_serialization_is_the_telemetry_value(self):
        """``str(member)`` and f-string interpolation yield the raw value —
        the wire/telemetry representation is unchanged."""
        assert str(ExitReason.ERROR) == "error"
        assert f"{ExitReason.PM_MAX_TURNS}" == "pm_max_turns"


class TestTurnFailure:
    def test_reason_and_detail_are_separate(self):
        failure = TurnFailure(ExitReason.HEADLESS_SUBPROCESS_ERROR, "broken pipe")
        assert failure.reason is ExitReason.HEADLESS_SUBPROCESS_ERROR
        assert failure.detail == "broken pipe"

    def test_str_matches_legacy_wire_format(self):
        """The serialized form is byte-identical to the pre-enum smuggled
        string (``"reason: detail"``), so exit_message telemetry is stable."""
        failure = TurnFailure(ExitReason.HEADLESS_THINKING_CORRUPTION, "bad block")
        assert str(failure) == "headless_thinking_corruption: bad block"

    def test_str_without_detail_is_bare_reason(self):
        assert str(TurnFailure(ExitReason.HEADLESS_TURN_TIMEOUT)) == "headless_turn_timeout"
