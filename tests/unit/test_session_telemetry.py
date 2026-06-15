"""Unit tests for agent.session_telemetry recorder."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

import agent.session_telemetry as telemetry_mod
from agent.session_telemetry import (
    IDLE_GAP_THRESHOLD,
    read_session_timeline,
    record_telemetry_event,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_module_state(session_id: str) -> None:
    """Remove per-session module-level state so tests are independent."""
    telemetry_mod._locks.pop(session_id, None)
    telemetry_mod._last_event_monotonic.pop(session_id, None)
    telemetry_mod._event_counts.pop(session_id, None)
    telemetry_mod._truncated.discard(session_id)
    # Close and evict open file handle if present
    fh = telemetry_mod._handles.pop(session_id, None)
    if fh:
        try:
            fh.close()
        except Exception:
            pass


@pytest.fixture()
def tmp_telemetry(tmp_path, monkeypatch):
    """Redirect the telemetry directory to a temp path for the duration of the test."""
    monkeypatch.setattr(telemetry_mod, "_TELEMETRY_DIR_RELATIVE", tmp_path / "session_telemetry")
    yield tmp_path / "session_telemetry"


@pytest.fixture(autouse=True)
def _reset_module_state(request):
    """After each test, clean up any per-session state the test created."""
    yield
    # Best-effort: flush and close all open handles created during this test
    for sid in list(telemetry_mod._handles.keys()):
        fh = telemetry_mod._handles.pop(sid, None)
        if fh:
            try:
                fh.close()
            except Exception:
                pass
    telemetry_mod._locks.clear()
    telemetry_mod._last_event_monotonic.clear()
    telemetry_mod._event_counts.clear()
    telemetry_mod._truncated.clear()


# ---------------------------------------------------------------------------
# Basic recording
# ---------------------------------------------------------------------------


class TestRecordBasicEvent:
    def test_record_basic_event(self, tmp_telemetry):
        """record_telemetry_event writes a valid JSONL file for the session."""
        session_id = "test-basic-001"
        record_telemetry_event(session_id, {"type": "turn_start"})

        trace = tmp_telemetry / f"{session_id}.jsonl"
        assert trace.exists(), "JSONL trace file should be created"

        lines = [json.loads(ln) for ln in trace.read_text().strip().splitlines()]
        assert len(lines) == 1
        ev = lines[0]
        assert ev["type"] == "turn_start"
        assert ev["session_id"] == session_id
        assert "ts" in ev

    def test_record_none_session_id(self, tmp_telemetry):
        """Passing None session_id is a no-op — no file is created."""
        record_telemetry_event(None, {"type": "turn_start"})
        assert list(tmp_telemetry.glob("*.jsonl")) == []

    def test_record_empty_session_id(self, tmp_telemetry):
        """Passing '' session_id is a no-op — no file is created."""
        record_telemetry_event("", {"type": "turn_start"})
        assert list(tmp_telemetry.glob("*.jsonl")) == []


# ---------------------------------------------------------------------------
# Unknown / missing event type
# ---------------------------------------------------------------------------


class TestUnknownEventType:
    def test_unknown_event_type_recorded(self, tmp_telemetry):
        """Event with no 'type' key is stored as {'type':'unknown','raw':...}."""
        session_id = "test-unknown-001"
        record_telemetry_event(session_id, {"foo": "bar"})

        trace = tmp_telemetry / f"{session_id}.jsonl"
        events = [json.loads(ln) for ln in trace.read_text().strip().splitlines()]
        assert len(events) == 1
        ev = events[0]
        assert ev["type"] == "unknown"
        assert ev["raw"] == {"foo": "bar"}

    def test_unknown_event_type_empty_string(self, tmp_telemetry):
        """Event with type='' is stored as {'type':'unknown','raw':...}."""
        session_id = "test-unknown-002"
        record_telemetry_event(session_id, {"type": "", "data": 42})

        trace = tmp_telemetry / f"{session_id}.jsonl"
        events = [json.loads(ln) for ln in trace.read_text().strip().splitlines()]
        assert events[0]["type"] == "unknown"
        assert events[0]["raw"]["data"] == 42


# ---------------------------------------------------------------------------
# read_session_timeline
# ---------------------------------------------------------------------------


class TestReadSessionTimeline:
    def test_read_empty_session(self, tmp_telemetry):
        """read_session_timeline returns [] for a non-existent session."""
        result = read_session_timeline("no-such-session-xyz")
        assert result == []

    def test_read_malformed_jsonl(self, tmp_telemetry):
        """Malformed JSONL lines are skipped; good lines are returned."""
        session_id = "test-malformed-001"
        trace = tmp_telemetry / f"{session_id}.jsonl"
        tmp_telemetry.mkdir(parents=True, exist_ok=True)
        trace.write_text(
            "NOT_VALID_JSON\n"
            '{"type":"turn_start","session_id":"test-malformed-001","ts":"2026-01-01T00:00:00Z"}\n'
        )

        events = read_session_timeline(session_id)
        assert len(events) == 1
        assert events[0]["type"] == "turn_start"

    def test_read_timeline_with_limit(self, tmp_telemetry):
        """read_session_timeline respects the limit parameter."""
        session_id = "test-limit-001"
        for i in range(5):
            record_telemetry_event(session_id, {"type": "turn_start", "i": i})

        events = read_session_timeline(session_id, limit=3)
        assert len(events) == 3


# ---------------------------------------------------------------------------
# Idle gap detection
# ---------------------------------------------------------------------------


class TestIdleGap:
    def test_idle_gap_emitted(self, tmp_telemetry):
        """Two events far apart in time produce a synthetic idle_gap event."""
        session_id = "test-idle-001"
        base_time = 1_000_000.0

        # First event at t=0
        with patch("agent.session_telemetry.time") as mock_time:
            mock_time.monotonic.return_value = base_time
            record_telemetry_event(session_id, {"type": "turn_start"})

        # Second event well beyond IDLE_GAP_THRESHOLD
        gap = IDLE_GAP_THRESHOLD + 10.0
        with patch("agent.session_telemetry.time") as mock_time:
            mock_time.monotonic.return_value = base_time + gap
            record_telemetry_event(session_id, {"type": "turn_end"})

        events = read_session_timeline(session_id)
        types = [e["type"] for e in events]
        assert "idle_gap" in types

        idle_ev = next(e for e in events if e["type"] == "idle_gap")
        assert idle_ev["gap_seconds"] == pytest.approx(gap, abs=0.01)

    def test_no_idle_gap_when_under_threshold(self, tmp_telemetry):
        """Two events close together do NOT produce an idle_gap event."""
        session_id = "test-idle-002"
        base_time = 2_000_000.0

        with patch("agent.session_telemetry.time") as mock_time:
            mock_time.monotonic.return_value = base_time
            record_telemetry_event(session_id, {"type": "turn_start"})

        with patch("agent.session_telemetry.time") as mock_time:
            mock_time.monotonic.return_value = base_time + (IDLE_GAP_THRESHOLD - 1.0)
            record_telemetry_event(session_id, {"type": "turn_end"})

        events = read_session_timeline(session_id)
        types = [e["type"] for e in events]
        assert "idle_gap" not in types


# ---------------------------------------------------------------------------
# Cap / truncation
# ---------------------------------------------------------------------------


class TestCapAndTruncation:
    def test_cap_and_truncation_marker(self, tmp_telemetry, monkeypatch):
        """Once MAX_EVENTS_PER_SESSION is reached, a truncation marker is written."""
        session_id = "test-cap-001"
        # Temporarily lower the cap so the test is fast
        small_cap = 5
        monkeypatch.setattr(telemetry_mod, "MAX_EVENTS_PER_SESSION", small_cap)

        # Fill up to the cap
        for i in range(small_cap):
            record_telemetry_event(session_id, {"type": "turn_start", "i": i})

        # This event should be blocked (session is now truncated)
        record_telemetry_event(session_id, {"type": "turn_start", "i": 9999})

        events = read_session_timeline(session_id)
        types = [e["type"] for e in events]
        assert "telemetry_truncated" in types

        # No events after the truncation marker
        trunc_idx = types.index("telemetry_truncated")
        assert trunc_idx == len(types) - 1, "telemetry_truncated should be the last event"

        # The session is now in _truncated; a further write must be silently ignored
        prev_count = len(events)
        record_telemetry_event(session_id, {"type": "turn_end"})
        assert len(read_session_timeline(session_id)) == prev_count


# ---------------------------------------------------------------------------
# Fail-silent guarantee
# ---------------------------------------------------------------------------


class TestFailSilent:
    def test_fail_silent_on_os_error(self, tmp_telemetry):
        """record_telemetry_event swallows OSError and never raises."""
        session_id = "test-fail-001"
        with patch.object(telemetry_mod, "_get_handle", side_effect=OSError("disk full")):
            result = record_telemetry_event(session_id, {"type": "turn_start"})
        # Must not raise, must return None
        assert result is None

    def test_fail_silent_returns_none(self, tmp_telemetry):
        """record_telemetry_event always returns None regardless of outcome."""
        session_id = "test-fail-002"
        result = record_telemetry_event(session_id, {"type": "turn_start"})
        assert result is None


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    def test_concurrent_writes_safe(self, tmp_telemetry):
        """10 threads × 5 events each produce exactly 50 valid JSON lines."""
        session_id = "test-concurrent-001"
        num_threads = 10
        events_per_thread = 5

        def write_events():
            for i in range(events_per_thread):
                record_telemetry_event(session_id, {"type": "turn_start", "val": i})

        threads = [threading.Thread(target=write_events) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        trace = tmp_telemetry / f"{session_id}.jsonl"
        lines = trace.read_text().strip().splitlines()
        # Each line must be valid JSON
        parsed = [json.loads(ln) for ln in lines]
        assert len(parsed) == num_threads * events_per_thread


# ---------------------------------------------------------------------------
# Integration with session_lifecycle.transition_status
# ---------------------------------------------------------------------------


def _make_session(session_id="test-lc-ts-001", status="pending"):
    """Create a minimal mock AgentSession for lifecycle integration tests."""
    session = MagicMock()
    session.session_id = session_id
    session.status = status
    session.project_key = "test"
    session.parent_agent_session_id = None
    session._saved_field_values = {}
    return session


class TestLifecycleIntegration:
    """Verify transition_status() calls record_telemetry_event with correct payload."""

    def test_emit_status_transition_via_lifecycle(self):
        """transition_status emits a status_transition telemetry event."""
        from models.session_lifecycle import transition_status

        session = _make_session()

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch("agent.session_telemetry.record_telemetry_event") as mock_record,
        ):
            mock_cas.return_value = None  # skip CAS check
            session.save = MagicMock()
            transition_status(session, "running", reason="test", emit_telemetry=True)

        assert mock_record.called, "record_telemetry_event should have been called"
        call_args = mock_record.call_args
        sid_arg, event_arg = call_args[0]
        assert sid_arg == session.session_id
        assert event_arg["type"] == "status_transition"
        assert event_arg["to"] == "running"
        assert event_arg["kill"] is None

    def test_emit_telemetry_false_suppresses(self):
        """transition_status with emit_telemetry=False does not call record_telemetry_event."""
        from models.session_lifecycle import transition_status

        session = _make_session()

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch("agent.session_telemetry.record_telemetry_event") as mock_record,
        ):
            mock_cas.return_value = None
            session.save = MagicMock()
            transition_status(session, "running", reason="test", emit_telemetry=False)

        assert not mock_record.called, "record_telemetry_event should NOT have been called"


class TestStatusTransitionTextRenderer:
    """Verify the CLI text-path renders 'from'/'to' keys correctly."""

    def test_status_transition_renders_states(self):
        """status_transition events show real states, not '? -> ?'."""
        import json
        import subprocess
        import sys

        from agent.session_telemetry import _get_telemetry_dir

        session_id = "unit-render-status-001"
        tdir = _get_telemetry_dir()
        trace = tdir / f"{session_id}.jsonl"
        tdir.mkdir(parents=True, exist_ok=True)
        event = {
            "session_id": session_id,
            "ts": "2026-01-01T00:00:00Z",
            "type": "status_transition",
            "from": "running",
            "to": "completed",
            "reason": "finished",
        }
        trace.write_text(json.dumps(event) + "\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "tools.valor_session", "telemetry", "--id", session_id],
                capture_output=True,
                text=True,
                cwd="/Users/valorengels/src/ai/.claude/worktrees/agent-a3810b945a05c10e3",
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "running" in result.stdout, f"Got: {result.stdout!r}"
            assert "completed" in result.stdout, f"Got: {result.stdout!r}"
            assert "? -> ?" not in result.stdout, f"Wrong keys still used: {result.stdout!r}"
        finally:
            trace.unlink(missing_ok=True)


class TestFinalizeSession:
    """Verify finalize_session reaps per-session entries from the module maps."""

    def test_finalize_evicts_session_from_all_maps(self, tmp_path, monkeypatch):
        """After finalize_session, session_id is gone from all module maps."""
        import agent.session_telemetry as st

        monkeypatch.setattr(st, "_TELEMETRY_DIR_RELATIVE", tmp_path / "telemetry")

        session_id = "unit-finalize-evict-001"

        # Record one event to populate the maps
        st.record_telemetry_event(session_id, {"type": "turn_start"})

        # Maps should be populated
        assert session_id in st._locks
        assert session_id in st._event_counts

        # Finalize the session
        st.finalize_session(session_id)

        # All per-session entries should be gone
        assert session_id not in st._locks, "_locks not reaped"
        assert session_id not in st._event_counts, "_event_counts not reaped"
        assert session_id not in st._last_event_monotonic, "_last_event_monotonic not reaped"
        assert session_id not in st._truncated, "_truncated not reaped"
        assert session_id not in st._handles, "_handles not reaped"

    def test_finalize_unknown_session_is_noop(self):
        """finalize_session on an unknown session_id is a safe no-op."""
        import agent.session_telemetry as st

        # Should not raise
        st.finalize_session("does-not-exist-xyz999")
        st.finalize_session("")
        st.finalize_session(None)
