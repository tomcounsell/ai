"""Unit tests for agent.crash_signature — extractor normalization and determinism guardrail.

Tests follow TDD RED -> GREEN -> REFACTOR.  All tests are pure Python — no Redis,
no subprocess, no external I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.crash_signature import (
    NON_RESUMABLE_DETERMINISTIC,
    CrashSignatureKey,
    extract_signature,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _turn_start() -> dict:
    return {"type": "turn_start", "ts": "2024-01-01T00:00:00Z"}


def _turn_end() -> dict:
    return {"type": "turn_end", "ts": "2024-01-01T00:01:00Z"}


def _status_transition(to: str, kill: dict | None = None) -> dict:
    data: dict = {"to": to}
    if kill:
        data["kill"] = kill
    return {"type": "status_transition", "data": data}


def _idle_gap(seconds: float) -> dict:
    return {"type": "idle_gap", "data": {"gap_seconds": seconds}}


def _kill_dict(confirmed_dead: bool, signal_sent: str) -> dict:
    return {"confirmed_dead": confirmed_dead, "signal_sent": signal_sent}


@dataclass
class _FakeSession:
    startup_failure_kind: str | None = None


# ---------------------------------------------------------------------------
# Basic normalization cases
# ---------------------------------------------------------------------------


class TestNormalizationCases:
    def test_kill_confirmed_dead_produces_stable_token(self):
        """kill dict with confirmed_dead=True appears in the human form."""
        events = [
            _turn_start(),
            _status_transition("killed", kill=_kill_dict(True, "SIGKILL")),
        ]
        key = extract_signature(events)
        assert "dead=true" in key.human_form
        assert "sig=SIGKILL" in key.human_form
        assert "to=killed" in key.human_form

    def test_idle_gap_then_fail_medium_bucket(self):
        """Idle gap of 10 minutes gets bucketed as 'medium'."""
        events = [
            _turn_start(),
            _idle_gap(600),  # 10 minutes
            _status_transition("failed"),
        ]
        key = extract_signature(events)
        assert "idle_gap[medium]" in key.human_form

    def test_idle_gap_short_bucket(self):
        """Idle gap of 1 minute gets bucketed as 'short'."""
        events = [
            _turn_start(),
            _idle_gap(60),
            _status_transition("failed"),
        ]
        key = extract_signature(events)
        assert "idle_gap[short]" in key.human_form

    def test_idle_gap_long_bucket(self):
        """Idle gap over 30 minutes gets bucketed as 'long'."""
        events = [
            _turn_start(),
            _idle_gap(2000),
            _status_transition("failed"),
        ]
        key = extract_signature(events)
        assert "idle_gap[long]" in key.human_form

    def test_abandoned_no_kill_dict(self):
        """abandoned transition without a kill dict does not include dead/sig tokens."""
        events = [
            _turn_start(),
            _status_transition("abandoned"),
        ]
        key = extract_signature(events)
        assert "to=abandoned" in key.human_form
        assert "dead=" not in key.human_form
        assert "sig=" not in key.human_form

    def test_operator_kill_sigterm(self):
        """SIGTERM kill shows confirmed_dead and signal in human form."""
        events = [
            _turn_start(),
            _status_transition("killed", kill=_kill_dict(False, "SIGTERM")),
        ]
        key = extract_signature(events)
        assert "dead=false" in key.human_form
        assert "sig=SIGTERM" in key.human_form


# ---------------------------------------------------------------------------
# Hash stability
# ---------------------------------------------------------------------------


class TestHashStability:
    def test_same_logical_crash_same_hash(self):
        """Two identical traces produce the same hash."""
        events_a = [_turn_start(), _status_transition("failed")]
        events_b = [_turn_start(), _status_transition("failed")]
        key_a = extract_signature(events_a)
        key_b = extract_signature(events_b)
        assert key_a.hash == key_b.hash

    def test_different_crash_different_hash(self):
        """Different terminal statuses produce different hashes."""
        events_a = [_turn_start(), _status_transition("failed")]
        events_b = [_turn_start(), _status_transition("killed")]
        assert extract_signature(events_a).hash != extract_signature(events_b).hash

    def test_idle_gap_bucket_changes_hash(self):
        """Different idle gap buckets produce different hashes."""
        events_short = [_turn_start(), _idle_gap(60), _status_transition("failed")]
        events_long = [_turn_start(), _idle_gap(5000), _status_transition("failed")]
        assert extract_signature(events_short).hash != extract_signature(events_long).hash

    def test_kill_signal_changes_hash(self):
        """SIGKILL vs SIGTERM produce different hashes."""
        events_kill = [
            _turn_start(),
            _status_transition("killed", kill=_kill_dict(True, "SIGKILL")),
        ]
        events_term = [
            _turn_start(),
            _status_transition("killed", kill=_kill_dict(False, "SIGTERM")),
        ]
        assert extract_signature(events_kill).hash != extract_signature(events_term).hash

    def test_hash_is_16_hex_chars(self):
        """Hash is exactly 16 hex characters."""
        events = [_turn_start(), _status_transition("failed")]
        key = extract_signature(events)
        assert len(key.hash) == 16
        assert all(c in "0123456789abcdef" for c in key.hash)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_events_returns_unclassifiable(self):
        """Empty event list returns 'unclassifiable' signature, not an exception."""
        key = extract_signature([])
        assert key.human_form == "unclassifiable"
        assert key.resumable is True
        assert key.signature_class == "unclassifiable"

    def test_only_unknown_events_returns_non_resumable(self):
        """Events with no turn_start -> NON_RESUMABLE_DETERMINISTIC."""
        events = [
            {"type": "token_usage", "data": {"input": 100}},
            {"type": "tool_use", "data": {"name": "bash"}},
        ]
        key = extract_signature(events)
        assert key.signature_class == NON_RESUMABLE_DETERMINISTIC
        assert key.resumable is False

    def test_telemetry_truncated_marker_adds_prefix(self):
        """telemetry_truncated marker adds 'truncated' prefix to human form."""
        events = [
            _turn_start(),
            {"type": "telemetry_truncated"},
            _status_transition("failed"),
        ]
        key = extract_signature(events)
        assert key.human_form.startswith("truncated")

    def test_unknown_event_type_included_in_form(self):
        """Unknown event types are included with an 'unknown[type]' token."""
        events = [
            _turn_start(),
            {"type": "future_event_type"},
            _status_transition("failed"),
        ]
        key = extract_signature(events)
        assert "unknown[future_event_type]" in key.human_form

    def test_returns_crash_signature_key_dataclass(self):
        """extract_signature always returns a CrashSignatureKey instance."""
        key = extract_signature([])
        assert isinstance(key, CrashSignatureKey)

    def test_escalated_defaults_to_false(self):
        """escalated field defaults to False on new signatures."""
        events = [_turn_start(), _status_transition("failed")]
        key = extract_signature(events)
        assert key.escalated is False


# ---------------------------------------------------------------------------
# Determinism guardrail
# ---------------------------------------------------------------------------


class TestDeterminismGuardrail:
    def test_plateau_startup_failure_is_non_resumable_deterministic(self):
        """session.startup_failure_kind='plateau' -> NON_RESUMABLE_DETERMINISTIC."""
        session = _FakeSession(startup_failure_kind="plateau")
        events = [_turn_start(), _status_transition("failed")]
        key = extract_signature(events, session=session)
        assert key.signature_class == NON_RESUMABLE_DETERMINISTIC
        assert key.resumable is False

    def test_plateau_takes_precedence_over_turn_start(self):
        """plateau guardrail fires even if turn_start is present."""
        session = _FakeSession(startup_failure_kind="plateau")
        events = [_turn_start(), _status_transition("failed")]
        key = extract_signature(events, session=session)
        assert key.signature_class == NON_RESUMABLE_DETERMINISTIC

    def test_no_turn_start_is_non_resumable_deterministic(self):
        """Trace with no turn_start event -> NON_RESUMABLE_DETERMINISTIC."""
        events = [{"type": "status_transition", "data": {"to": "failed"}}]
        key = extract_signature(events)
        assert key.signature_class == NON_RESUMABLE_DETERMINISTIC
        assert key.resumable is False

    def test_ceiling_with_turn_start_is_resumable(self):
        """session.startup_failure_kind='ceiling' + turn_start -> resumable."""
        session = _FakeSession(startup_failure_kind="ceiling")
        events = [_turn_start(), _status_transition("failed")]
        key = extract_signature(events, session=session)
        assert key.resumable is True
        assert key.signature_class != NON_RESUMABLE_DETERMINISTIC

    def test_ceiling_adds_ceiling_prefix(self):
        """ceiling startup failure adds 'ceiling' token to human form."""
        session = _FakeSession(startup_failure_kind="ceiling")
        events = [_turn_start(), _status_transition("failed")]
        key = extract_signature(events, session=session)
        assert "ceiling" in key.human_form

    def test_no_session_no_turn_start_is_non_resumable(self):
        """No session arg + no turn_start -> NON_RESUMABLE_DETERMINISTIC."""
        events = [{"type": "status_transition", "data": {"to": "failed"}}]
        key = extract_signature(events, session=None)
        assert key.signature_class == NON_RESUMABLE_DETERMINISTIC
        assert key.resumable is False

    def test_no_session_with_turn_start_is_not_non_resumable(self):
        """No session arg + has turn_start -> NOT NON_RESUMABLE_DETERMINISTIC."""
        events = [_turn_start(), _status_transition("failed")]
        key = extract_signature(events, session=None)
        assert key.signature_class != NON_RESUMABLE_DETERMINISTIC
        assert key.resumable is True

    def test_plateau_human_form_contains_plateau_label(self):
        """plateau NON_RESUMABLE form includes 'plateau' in human form."""
        session = _FakeSession(startup_failure_kind="plateau")
        events = [_turn_start(), _status_transition("failed")]
        key = extract_signature(events, session=session)
        assert "plateau" in key.human_form

    def test_no_turn_start_human_form_contains_no_turn_start_label(self):
        """no_turn_start NON_RESUMABLE form includes 'no_turn_start' in human form."""
        events = [{"type": "status_transition", "data": {"to": "failed"}}]
        key = extract_signature(events)
        assert "no_turn_start" in key.human_form


# ---------------------------------------------------------------------------
# Never-raises contract
# ---------------------------------------------------------------------------


class TestNeverRaises:
    def test_extract_signature_never_raises_on_garbage_input(self):
        """extract_signature handles completely malformed input without raising."""
        garbage = [{"not_a_type": "whatever"}, None, {}, 42]  # type: ignore[list-item]
        # Should not raise
        key = extract_signature(garbage)  # type: ignore[arg-type]
        assert isinstance(key, CrashSignatureKey)

    def test_extract_signature_handles_missing_data_keys(self):
        """Events missing expected nested keys do not raise."""
        events = [
            _turn_start(),
            {"type": "status_transition"},  # no 'data' key
        ]
        key = extract_signature(events)
        assert isinstance(key, CrashSignatureKey)
