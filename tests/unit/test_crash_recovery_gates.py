"""Unit tests for the crash-recovery gate helpers (#1917).

Covers the pure decision helpers added to ``reflections/crash_recovery.py`` for
the auto-resume feature:

- ``_is_transient_clean_kill_to_failed`` — inline derivation of the known-transient
  tool-wedge shape (confirmed-dead clean kill to ``failed``) that the deterministic
  first-retry floor acts on (Gap 3a, critique C4).
- ``_machine_owns_project`` — the single-machine ownership gate (Gap 3b): only the
  machine that owns a session's project resumes it; everyone else proposes.

Both helpers are fail-soft: any malformed input or lookup error resolves to the
safe default (no floor / not-owned), never an exception.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from reflections.crash_recovery import (
    _is_transient_clean_kill_to_failed,
    _machine_owns_project,
)

pytestmark = pytest.mark.sdlc


def _transition(*, to: str = "failed", confirmed_dead=True, nested: bool = False) -> dict:
    """Build a status_transition event, optionally nesting under ``data``."""
    kill = {"confirmed_dead": confirmed_dead, "signal_sent": "SIGKILL"}
    if nested:
        return {"type": "status_transition", "data": {"to": to, "kill": kill}}
    return {"type": "status_transition", "from": "running", "to": to, "kill": kill}


# ---------------------------------------------------------------------------
# _is_transient_clean_kill_to_failed
# ---------------------------------------------------------------------------


class TestIsTransientCleanKillToFailed:
    def test_confirmed_dead_kill_to_failed_is_transient(self):
        events = [{"type": "turn_start"}, _transition(to="failed", confirmed_dead=True)]
        assert _is_transient_clean_kill_to_failed(events) is True

    def test_nested_data_form_is_transient(self):
        events = [_transition(to="failed", confirmed_dead=True, nested=True)]
        assert _is_transient_clean_kill_to_failed(events) is True

    def test_string_true_confirmed_dead_is_transient(self):
        events = [{"type": "status_transition", "to": "failed", "kill": {"confirmed_dead": "true"}}]
        assert _is_transient_clean_kill_to_failed(events) is True

    def test_uses_last_transition_only(self):
        """An earlier non-matching transition does not veto a later matching one."""
        events = [
            _transition(to="running", confirmed_dead=False),
            _transition(to="failed", confirmed_dead=True),
        ]
        assert _is_transient_clean_kill_to_failed(events) is True

    def test_terminal_abandoned_is_not_transient(self):
        events = [_transition(to="abandoned", confirmed_dead=True)]
        assert _is_transient_clean_kill_to_failed(events) is False

    def test_not_confirmed_dead_is_not_transient(self):
        events = [_transition(to="failed", confirmed_dead=False)]
        assert _is_transient_clean_kill_to_failed(events) is False

    def test_missing_kill_dict_is_not_transient(self):
        events = [{"type": "status_transition", "to": "failed"}]
        assert _is_transient_clean_kill_to_failed(events) is False

    def test_no_status_transition_is_not_transient(self):
        events = [{"type": "turn_start"}, {"type": "idle_gap", "gap_seconds": 10.0}]
        assert _is_transient_clean_kill_to_failed(events) is False

    def test_empty_events_is_not_transient(self):
        assert _is_transient_clean_kill_to_failed([]) is False

    def test_malformed_kill_type_is_fail_soft(self):
        """A non-dict kill payload resolves to not-transient, never raises."""
        events = [{"type": "status_transition", "to": "failed", "kill": "not-a-dict"}]
        assert _is_transient_clean_kill_to_failed(events) is False


# ---------------------------------------------------------------------------
# _machine_owns_project
# ---------------------------------------------------------------------------


class TestMachineOwnsProject:
    def test_none_project_key_is_not_owned(self):
        assert _machine_owns_project(None) is False

    def test_empty_project_key_is_not_owned(self):
        assert _machine_owns_project("") is False

    def test_owned_project_returns_true(self):
        with (
            patch("config.machine.get_machine_name", return_value="My-Machine"),
            patch(
                "tools.reflection_machine_filter._load_project_machines",
                return_value={"myproj": "my-machine"},
            ),
        ):
            assert _machine_owns_project("myproj") is True

    def test_project_owned_by_other_machine_returns_false(self):
        with (
            patch("config.machine.get_machine_name", return_value="My-Machine"),
            patch(
                "tools.reflection_machine_filter._load_project_machines",
                return_value={"myproj": "some-other-box"},
            ),
        ):
            assert _machine_owns_project("myproj") is False

    def test_unknown_project_key_is_not_owned(self):
        with (
            patch("config.machine.get_machine_name", return_value="My-Machine"),
            patch(
                "tools.reflection_machine_filter._load_project_machines",
                return_value={"otherproj": "my-machine"},
            ),
        ):
            assert _machine_owns_project("myproj") is False

    def test_lookup_error_is_fail_soft_not_owned(self):
        """Any lookup exception resolves to not-owned (propose), never propagates."""
        with patch(
            "tools.reflection_machine_filter._load_project_machines",
            side_effect=RuntimeError("boom"),
        ):
            assert _machine_owns_project("myproj") is False


# ---------------------------------------------------------------------------
# Settings default for the deterministic floor
# ---------------------------------------------------------------------------


class TestDeterministicFloorSetting:
    def test_default_floor_attempts_is_one(self):
        from config.settings import settings

        assert settings.features.crash_autoresume_deterministic_floor_attempts == 1

    def test_floor_attempts_bounds(self):
        """Field is bounded ge=0, le=5 — 0 disables, out-of-range rejected."""
        from pydantic import ValidationError

        from config.settings import FeatureSettings

        assert FeatureSettings(crash_autoresume_deterministic_floor_attempts=0)
        assert FeatureSettings(crash_autoresume_deterministic_floor_attempts=5)
        with pytest.raises(ValidationError):
            FeatureSettings(crash_autoresume_deterministic_floor_attempts=-1)
        with pytest.raises(ValidationError):
            FeatureSettings(crash_autoresume_deterministic_floor_attempts=6)
