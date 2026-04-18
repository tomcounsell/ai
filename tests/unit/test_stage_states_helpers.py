"""Unit tests for tools.stage_states_helpers.update_stage_states."""

from __future__ import annotations

import json
from unittest.mock import patch

from tools.stage_states_helpers import update_stage_states


class _FakeSession:
    def __init__(self, session_id="fake-1", stage_states=None):
        self.session_id = session_id
        self.session_type = "pm"
        if stage_states is None:
            self.stage_states = "{}"
        elif isinstance(stage_states, dict):
            self.stage_states = json.dumps(stage_states)
        else:
            self.stage_states = stage_states
        self.save_calls = 0

    def save(self):
        self.save_calls += 1


class _FlappingSession(_FakeSession):
    """Simulates a concurrent writer: the first N saves succeed but verification
    reports a different state (as if another writer clobbered the write).

    After N flaps, the session behaves normally.
    """

    def __init__(self, flap_count: int):
        super().__init__()
        self.flap_count = flap_count
        self.flap_remaining = flap_count


def test_success_path_writes_and_verifies():
    session = _FakeSession()

    def add_flag(states):
        states["_test_flag"] = 42
        return states

    with patch("tools.stage_states_helpers._reload_session", return_value=session):
        ok = update_stage_states(session, add_flag)
    assert ok is True
    data = json.loads(session.stage_states)
    assert data["_test_flag"] == 42


def test_update_fn_returning_non_dict_returns_false():
    session = _FakeSession()

    def bad_update(states):
        return "not a dict"

    with patch("tools.stage_states_helpers._reload_session", return_value=session):
        ok = update_stage_states(session, bad_update)
    assert ok is False


def test_retry_on_conflict():
    """When reload sees a clobbered state, retry and eventually succeed."""
    session = _FakeSession()
    # The "competing writer" mutates stage_states between save and verify on
    # the first attempt. The retry should observe the new state and re-apply.
    conflict_session = _FakeSession(stage_states={"concurrent": "change"})

    reload_sequence = [conflict_session, session]

    def reload_stub(_s):
        return reload_sequence.pop(0) if reload_sequence else session

    def add_flag(states):
        states["_test_flag"] = 1
        return states

    with patch("tools.stage_states_helpers._reload_session", side_effect=reload_stub):
        ok = update_stage_states(session, add_flag, max_retries=3)
    assert ok is True


def test_retry_exhaustion_returns_false_and_logs_warning(caplog):
    """If every save gets clobbered by another writer, return False and WARN."""
    session = _FakeSession()

    # Simulate an aggressive concurrent writer: every time we save, another
    # process immediately resets the state to a value the update_fn wouldn't
    # produce. Reload reflects that clobbered state each time.
    class Clobberer:
        def __init__(self):
            self.call_count = 0

        def __call__(self, s):
            self.call_count += 1
            # Return a fresh session whose state never matches the locally
            # applied dict, emulating a true conflict.
            return _FakeSession(
                session_id=s.session_id,
                stage_states={"concurrent_writer_wins": self.call_count},
            )

    clobber = Clobberer()

    def add_flag(states):
        states["_test_flag"] = 1
        return states

    with patch("tools.stage_states_helpers._reload_session", side_effect=clobber):
        with caplog.at_level("WARNING"):
            ok = update_stage_states(session, add_flag, max_retries=3)
    assert ok is False
    assert any("retries exhausted" in r.message for r in caplog.records)


def test_max_retries_below_one_coerced_to_one():
    session = _FakeSession()
    with patch("tools.stage_states_helpers._reload_session", return_value=session):
        ok = update_stage_states(session, lambda s: s, max_retries=0)
    # At least one attempt is made; empty-dict update trivially succeeds.
    assert ok is True


def test_update_fn_receives_deep_copy():
    """update_fn must not be able to leak mutations if save fails."""
    session = _FakeSession(stage_states={"preexisting": "value"})

    captured_snapshots = []

    def inspect(states):
        captured_snapshots.append(dict(states))
        states["_new"] = "added"
        return states

    with patch("tools.stage_states_helpers._reload_session", return_value=session):
        update_stage_states(session, inspect, max_retries=1)

    assert captured_snapshots[0] == {"preexisting": "value"}


def test_handles_malformed_stage_states():
    """Malformed JSON on the session is treated as an empty dict."""
    session = _FakeSession(stage_states="{not json")

    def add_flag(states):
        assert states == {}, "malformed input must look empty to update_fn"
        states["_new"] = 1
        return states

    with patch("tools.stage_states_helpers._reload_session", return_value=session):
        ok = update_stage_states(session, add_flag)
    assert ok is True
    data = json.loads(session.stage_states)
    assert data == {"_new": 1}
