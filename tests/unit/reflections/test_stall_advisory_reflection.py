"""Unit tests for reflections.stall_advisory.run_stall_advisory (issue #1538).

Everything is mocked — no real Redis, no real AgentSession, no Telegram subprocess.
The reflection's job is to compose classify_session_stall() + read_session_timeline()
+ optional Telegram alert. Tests fence each boundary.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# We import the module — not the function — so we can monkeypatch its internals.
import reflections.stall_advisory as stall_advisory_mod
from agent.session_stall_classifier import (
    NEVER_STARTED_CONFIRM_MARGIN_SECS,
    NEVER_STARTED_GRACE_SECS,
)
from reflections.stall_advisory import run_stall_advisory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Age a "never-started" fixture must reach before classify_session_stall() will
# confirm it as stalled: the grace window plus the cold-start confirmation
# margin, both imported from the classifier so this fixture tracks the
# thresholds as they evolve (issue #2092 — a hardcoded 700s literal went stale
# after the #2069/#2071 grace-widening). The extra buffer keeps the fixture
# comfortably past the confirm threshold.
_STALLED_FIXTURE_AGE_SECS = NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS + 300


def _fake_session(
    session_id: str = "test-session-1",
    status: str = "running",
    started_at=None,
    created_at=None,
) -> SimpleNamespace:
    now = time.time()
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=session_id,
        status=status,
        started_at=started_at,
        created_at=created_at if created_at is not None else (now - _STALLED_FIXTURE_AGE_SECS),
    )


def _patch_probe_sessions(monkeypatch, sessions: list) -> None:
    """Patch AgentSession.query.filter to return the given sessions list."""
    mock_qs = MagicMock()
    mock_qs.return_value = sessions
    mock_session_cls = MagicMock()
    mock_session_cls.query.filter.return_value = sessions
    monkeypatch.setattr(stall_advisory_mod, "AgentSession", mock_session_cls, raising=False)


def _patch_terminal_statuses(monkeypatch) -> None:
    """Patch TERMINAL_STATUSES to the real set without importing models."""
    terminal = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})
    monkeypatch.setattr(stall_advisory_mod, "TERMINAL_STATUSES", terminal, raising=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_imports(monkeypatch):
    """Patch the heavy imports that run_stall_advisory does internally."""
    # We need to intercept the imports inside run_stall_advisory.
    # The function does `from models.agent_session import AgentSession` and
    # `from models.session_lifecycle import TERMINAL_STATUSES` inside the body.
    # We patch the names in the stall_advisory module namespace.
    terminal = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})
    monkeypatch.setattr(stall_advisory_mod, "TERMINAL_STATUSES", terminal, raising=False)


# ---------------------------------------------------------------------------
# 1. Return shape invariant
# ---------------------------------------------------------------------------


class TestReturnShape:
    def test_always_has_status_findings_summary(self, monkeypatch):
        # With no sessions, the function must still return the three keys.
        _patch_probe_sessions(monkeypatch, [])
        result = _run_with_patched_imports(monkeypatch, [])
        assert "status" in result
        assert "findings" in result
        assert "summary" in result
        assert isinstance(result["findings"], list)
        assert isinstance(result["summary"], str)

    def test_no_sessions_returns_ok(self, monkeypatch):
        result = _run_with_patched_imports(monkeypatch, [])
        assert result["status"] == "ok"
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# 2. Per-session exception isolation
# ---------------------------------------------------------------------------


class TestPerSessionExceptionIsolation:
    def test_one_session_raises_others_still_classified(self, monkeypatch):
        sess_ok = _fake_session("ok-session", status="running")
        sess_bad = _fake_session("bad-session", status="running")

        call_count = {"n": 0}

        def _mock_timeline(session_id):
            call_count["n"] += 1
            if session_id == "bad-session":
                raise RuntimeError("deliberate boom")
            # Return a recent turn_end so the verdict is healthy.
            return [{"type": "turn_end", "ts": time.time() - 10}]

        result = _run_with_patched_imports(
            monkeypatch,
            [sess_ok, sess_bad],
            timeline_fn=_mock_timeline,
        )

        assert result["status"] in {"ok", "warn"}
        # bad-session was skipped, but ok-session was processed.
        assert call_count["n"] == 2  # both attempted

    def test_all_sessions_raise_returns_ok_with_empty_findings(self, monkeypatch):
        sessions = [_fake_session("s1"), _fake_session("s2")]

        def _always_raise(session_id):
            raise RuntimeError("boom")

        result = _run_with_patched_imports(
            monkeypatch,
            sessions,
            timeline_fn=_always_raise,
        )
        # Both sessions skipped — no findings, status=ok.
        assert result["status"] == "ok"
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# 3. Telegram flag gate: OFF by default
# ---------------------------------------------------------------------------


class TestTelegramFlagOff:
    def test_params_none_no_telegram_sent(self, monkeypatch):
        sess = _fake_session("stalled-s1", status="running")
        # Provide a stalled session to ensure there's something to alert about.
        events = []  # no events, long elapsed → will classify as stalled

        with patch.object(stall_advisory_mod, "_send_alert") as mock_alert:
            _run_with_patched_imports(monkeypatch, [sess], timeline_fn=lambda _: events)
            mock_alert.assert_not_called()

    def test_params_empty_dict_no_telegram_sent(self, monkeypatch):
        sess = _fake_session("stalled-s2", status="running")
        with patch.object(stall_advisory_mod, "_send_alert") as mock_alert:
            _run_with_patched_imports(monkeypatch, [sess], timeline_fn=lambda _: [], params={})
            mock_alert.assert_not_called()

    def test_telegram_enabled_false_no_telegram_sent(self, monkeypatch):
        sess = _fake_session("stalled-s3", status="running")
        with patch.object(stall_advisory_mod, "_send_alert") as mock_alert:
            _run_with_patched_imports(
                monkeypatch,
                [sess],
                timeline_fn=lambda _: [],
                params={"stall_advisory_telegram_enabled": False},
            )
            mock_alert.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Telegram flag gate: ON
# ---------------------------------------------------------------------------


class TestTelegramFlagOn:
    def test_enabled_with_stalled_session_sends_alert(self, monkeypatch):
        # session aged past the never-started confirm threshold, status=running,
        # no events → stalled/never_started
        sess = _fake_session("stalled-enabled", status="running")

        with patch.object(stall_advisory_mod, "_send_alert") as mock_alert:
            result = _run_with_patched_imports(
                monkeypatch,
                [sess],
                timeline_fn=lambda _: [],
                params={"stall_advisory_telegram_enabled": True},
            )
            # There should be a finding and an alert should have been sent.
            assert result["status"] == "warn"
            mock_alert.assert_called_once()

    def test_enabled_with_all_healthy_sessions_no_alert(self, monkeypatch):
        # Return a turn_start + recent turn_end so:
        # 1. has_turn_start=True (never-started branch skipped)
        # 2. recent_turn_ts < IDLE_SUSPECT_SECS → healthy/recent_turn_activity
        now = time.time()
        recent_events = [
            {"type": "turn_start", "ts": now - 35},
            {"type": "turn_end", "ts": now - 10},
        ]
        sess = _fake_session("healthy-s", status="running")

        with patch.object(stall_advisory_mod, "_send_alert") as mock_alert:
            result = _run_with_patched_imports(
                monkeypatch,
                [sess],
                timeline_fn=lambda _: recent_events,
                params={"stall_advisory_telegram_enabled": True},
            )
            assert result["status"] == "ok"
            mock_alert.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Session filtering: non-probe statuses skipped
# ---------------------------------------------------------------------------


class TestSessionFiltering:
    def test_pending_sessions_not_classified(self, monkeypatch):
        # pending is not in _RUNNING_PROBE_STATUSES; AgentSession.query.filter
        # already gates on status__in=_RUNNING_PROBE_STATUSES, so pending should
        # never appear. But the reflection also skips terminal statuses.
        # To test the reflection's own TERMINAL_STATUSES guard, we inject a
        # "completed" session and verify it's dropped.
        sess_terminal = _fake_session("completed-s", status="completed")

        call_tracker = {"called": False}

        def _track_timeline(session_id):
            call_tracker["called"] = True
            return []

        _run_with_patched_imports(
            monkeypatch,
            [sess_terminal],
            timeline_fn=_track_timeline,
        )
        # completed is in TERMINAL_STATUSES — reflection filters it out before classification.
        assert not call_tracker["called"]

    def test_running_sessions_are_classified(self, monkeypatch):
        call_tracker = {"called": False}

        def _track_timeline(session_id):
            call_tracker["called"] = True
            return [{"type": "turn_end", "ts": time.time() - 10}]

        sess = _fake_session("running-s", status="running")
        _run_with_patched_imports(monkeypatch, [sess], timeline_fn=_track_timeline)
        assert call_tracker["called"]


# ---------------------------------------------------------------------------
# Internal runner helper (avoids repeating all the patch boilerplate)
# ---------------------------------------------------------------------------


def _run_with_patched_imports(
    monkeypatch,
    sessions: list,
    *,
    timeline_fn=None,
    params=None,
) -> dict:
    """Run run_stall_advisory with mocked internals.

    Patches:
    - AgentSession.query.filter → returns `sessions`
    - TERMINAL_STATUSES → standard set
    - read_session_timeline → timeline_fn (defaults to returning [])
    - _RUNNING_PROBE_STATUSES / classify_session_stall → real imports (not mocked)
    """
    if timeline_fn is None:

        def timeline_fn(_):
            return []

    # We need to intercept the function-level imports inside run_stall_advisory.
    # Strategy: patch sys.modules entries so the `from X import Y` lines inside
    # run_stall_advisory pick up our stubs.

    mock_as_module = MagicMock()
    mock_as_cls = MagicMock()
    mock_as_cls.query.filter.return_value = sessions
    mock_as_module.AgentSession = mock_as_cls

    mock_sl_module = MagicMock()
    terminal = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})
    mock_sl_module.TERMINAL_STATUSES = terminal

    mock_tel_module = MagicMock()
    mock_tel_module.read_session_timeline.side_effect = timeline_fn

    # Use sys.modules patching so local `from X import Y` lines see our stubs.
    import sys

    existing_as = sys.modules.get("models.agent_session")
    existing_sl = sys.modules.get("models.session_lifecycle")
    existing_tel = sys.modules.get("agent.session_telemetry")

    sys.modules["models.agent_session"] = mock_as_module
    sys.modules["models.session_lifecycle"] = mock_sl_module
    sys.modules["agent.session_telemetry"] = mock_tel_module

    try:
        result = run_stall_advisory(params=params)
    finally:
        # Restore original modules (or remove if they weren't there).
        if existing_as is not None:
            sys.modules["models.agent_session"] = existing_as
        else:
            sys.modules.pop("models.agent_session", None)

        if existing_sl is not None:
            sys.modules["models.session_lifecycle"] = existing_sl
        else:
            sys.modules.pop("models.session_lifecycle", None)

        if existing_tel is not None:
            sys.modules["agent.session_telemetry"] = existing_tel
        else:
            sys.modules.pop("agent.session_telemetry", None)

    return result
