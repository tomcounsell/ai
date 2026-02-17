"""Unit tests for monitoring/session_watchdog.py."""

import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from popoto.exceptions import ModelException

from monitoring.session_watchdog import (
    assess_session_health,
    check_all_sessions,
    detect_error_cascade,
    detect_repetition,
    read_recent_tool_calls,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pre_event(tool_name, tool_input=None):
    return {
        "event": "pre_tool_use",
        "tool_name": tool_name,
        "tool_input": tool_input or {},
        "start_time": time.time(),
    }


def _make_post_event(tool_name, output_preview=""):
    return {
        "event": "post_tool_use",
        "tool_name": tool_name,
        "tool_output_preview": output_preview,
        "end_time": time.time(),
    }


def _make_session(
    session_id="test-123",
    last_activity=None,
    started_at=None,
    tool_call_count=10,
):
    return SimpleNamespace(
        session_id=session_id,
        last_activity=last_activity or time.time(),
        started_at=started_at or time.time() - 300,
        tool_call_count=tool_call_count,
        project_key="test",
        chat_id="12345",
    )


LOGS_BASE = Path(__file__).resolve().parent.parent.parent / "logs" / "sessions"


@pytest.fixture()
def session_log_dir():
    """Create and clean up a session log directory."""
    sid = f"test-watchdog-{int(time.time() * 1000)}"
    log_dir = LOGS_BASE / sid
    log_dir.mkdir(parents=True, exist_ok=True)
    yield sid, log_dir
    shutil.rmtree(log_dir, ignore_errors=True)


# ===================================================================
# detect_repetition
# ===================================================================


class TestDetectRepetition:
    def test_empty_events(self):
        is_looping, tool, count = detect_repetition([])
        assert is_looping is False
        assert tool is None
        assert count == 0

    def test_varied_calls_no_loop(self):
        events = [
            _make_pre_event("ToolA"),
            _make_pre_event("ToolB"),
            _make_pre_event("ToolC"),
            _make_pre_event("ToolD"),
            _make_pre_event("ToolE"),
        ]
        is_looping, tool, count = detect_repetition(events)
        assert is_looping is False
        assert tool is None
        assert count == 1

    def test_below_threshold(self):
        """3 events < threshold of 5, returns early with count=0."""
        events = [_make_pre_event("Bash", {"command": "ls"}) for _ in range(3)]
        is_looping, tool, count = detect_repetition(events)
        assert is_looping is False
        assert tool is None
        assert count == 0

    def test_exact_threshold(self):
        events = [_make_pre_event("Bash", {"command": "ls"}) for _ in range(5)]
        is_looping, tool, count = detect_repetition(events)
        assert is_looping is True
        assert tool == "Bash"
        assert count == 5

    def test_above_threshold(self):
        events = [_make_pre_event("Read", {"path": "/f"}) for _ in range(8)]
        is_looping, tool, count = detect_repetition(events)
        assert is_looping is True
        assert tool == "Read"
        assert count == 8

    def test_mixed_then_loop(self):
        events = [
            _make_pre_event("ToolA"),
            _make_pre_event("ToolB"),
            _make_pre_event("ToolC"),
        ] + [_make_pre_event("Bash", {"command": "echo hi"}) for _ in range(5)]
        is_looping, tool, count = detect_repetition(events)
        assert is_looping is True
        assert tool == "Bash"
        assert count == 5

    def test_only_post_events_ignored(self):
        events = [_make_post_event("Bash", "ok") for _ in range(10)]
        is_looping, tool, count = detect_repetition(events)
        assert is_looping is False
        assert tool is None
        assert count == 0

    def test_custom_threshold(self):
        events = [_make_pre_event("Bash", {"command": "ls"}) for _ in range(3)]
        is_looping, tool, count = detect_repetition(events, threshold=3)
        assert is_looping is True
        assert tool == "Bash"
        assert count == 3

    def test_fingerprint_includes_input(self):
        events = [_make_pre_event("Bash", {"command": f"echo {i}"}) for i in range(5)]
        is_looping, tool, count = detect_repetition(events)
        assert is_looping is False
        assert count == 1


# ===================================================================
# detect_error_cascade
# ===================================================================


class TestDetectErrorCascade:
    def test_empty_events(self):
        has_cascade, count = detect_error_cascade([])
        assert has_cascade is False
        assert count == 0

    def test_no_errors(self):
        events = [_make_post_event("Bash", "Success") for _ in range(20)]
        has_cascade, count = detect_error_cascade(events)
        assert has_cascade is False
        assert count == 0

    def test_few_errors_below_threshold(self):
        events = [_make_post_event("Bash", "ok") for _ in range(18)]
        events.append(_make_post_event("Bash", "Error: something"))
        events.append(_make_post_event("Bash", "Traceback (most recent)"))
        has_cascade, count = detect_error_cascade(events)
        assert has_cascade is False
        assert count == 2

    def test_threshold_met(self):
        events = [_make_post_event("Bash", "ok") for _ in range(15)]
        events.extend([_make_post_event("Bash", "Error: fail") for _ in range(5)])
        has_cascade, count = detect_error_cascade(events)
        assert has_cascade is True
        assert count == 5

    def test_all_errors(self):
        events = [_make_post_event("Bash", "Traceback: crash") for _ in range(10)]
        has_cascade, count = detect_error_cascade(events)
        assert has_cascade is True
        assert count == 10

    def test_respects_window(self):
        old_errors = [_make_post_event("Bash", "Error: old") for _ in range(30)]
        clean = [_make_post_event("Bash", "ok") for _ in range(20)]
        events = old_errors + clean
        has_cascade, count = detect_error_cascade(events)
        assert has_cascade is False
        assert count == 0

    def test_only_pre_events_ignored(self):
        events = [_make_pre_event("Bash", {"command": "bad"}) for _ in range(20)]
        has_cascade, count = detect_error_cascade(events)
        assert has_cascade is False
        assert count == 0

    def test_error_keywords(self):
        events = [
            _make_post_event("a", "Error occurred"),
            _make_post_event("b", "Traceback (most recent call last)"),
            _make_post_event("c", "Unhandled exception"),
            _make_post_event("d", "Operation failed"),
            _make_post_event("e", "Fatal crash"),
        ]
        has_cascade, count = detect_error_cascade(events)
        assert has_cascade is True
        assert count == 5


# ===================================================================
# read_recent_tool_calls
# ===================================================================


class TestReadRecentToolCalls:
    def test_missing_file(self):
        result = read_recent_tool_calls("nonexistent-session-id-999")
        assert result == []

    def test_valid_file(self, session_log_dir):
        sid, log_dir = session_log_dir
        filepath = log_dir / "tool_use.jsonl"
        events = [
            _make_pre_event("Bash", {"command": "ls"}),
            _make_post_event("Bash", "file1.txt"),
        ]
        with open(filepath, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        result = read_recent_tool_calls(sid)
        assert len(result) == 2
        assert result[0]["tool_name"] == "Bash"
        assert result[1]["event"] == "post_tool_use"

    def test_corrupted_lines(self, session_log_dir):
        sid, log_dir = session_log_dir
        filepath = log_dir / "tool_use.jsonl"
        with open(filepath, "w") as f:
            f.write(json.dumps(_make_pre_event("Bash")) + "\n")
            f.write("THIS IS NOT JSON\n")
            f.write("{broken json\n")
            f.write(json.dumps(_make_post_event("Bash", "ok")) + "\n")
        result = read_recent_tool_calls(sid)
        assert len(result) == 2

    def test_limit(self, session_log_dir):
        sid, log_dir = session_log_dir
        filepath = log_dir / "tool_use.jsonl"
        with open(filepath, "w") as f:
            for i in range(20):
                f.write(json.dumps(_make_pre_event(f"Tool_{i}")) + "\n")
        result = read_recent_tool_calls(sid, limit=5)
        assert len(result) == 5
        assert result[0]["tool_name"] == "Tool_15"
        assert result[-1]["tool_name"] == "Tool_19"

    def test_empty_file(self, session_log_dir):
        sid, log_dir = session_log_dir
        (log_dir / "tool_use.jsonl").touch()
        result = read_recent_tool_calls(sid)
        assert result == []


# ===================================================================
# assess_session_health
# ===================================================================


class TestAssessSessionHealth:
    def test_healthy_session(self):
        session = _make_session(
            last_activity=time.time(),
            started_at=time.time() - 300,
        )
        result = assess_session_health(session)
        assert result["healthy"] is True
        assert result["issues"] == []

    def test_silent_session(self):
        session = _make_session(
            last_activity=time.time() - 700,
            started_at=time.time() - 900,
        )
        result = assess_session_health(session)
        assert result["healthy"] is False
        assert any("Silent" in issue for issue in result["issues"])
        assert result["severity"] == "warning"

    def test_long_running_session(self):
        session = _make_session(
            last_activity=time.time(),
            started_at=time.time() - 7500,
        )
        result = assess_session_health(session)
        assert result["healthy"] is False
        assert any("Running" in issue for issue in result["issues"])

    def test_multiple_issues_critical(self):
        session = _make_session(
            last_activity=time.time() - 700,
            started_at=time.time() - 7500,
        )
        result = assess_session_health(session)
        assert result["healthy"] is False
        assert len(result["issues"]) >= 2
        assert result["severity"] == "critical"

    def test_boundary_not_silent(self):
        session = _make_session(
            last_activity=time.time() - 599,
            started_at=time.time() - 300,
        )
        result = assess_session_health(session)
        assert result["healthy"] is True

    def test_boundary_not_long_running(self):
        session = _make_session(
            last_activity=time.time(),
            started_at=time.time() - 7199,
        )
        result = assess_session_health(session)
        assert result["healthy"] is True


# ===================================================================
# check_all_sessions - ModelException handling
# ===================================================================


class TestCheckAllSessionsModelException:
    """Verify that ModelException is caught by type, not string matching."""

    @pytest.mark.asyncio
    async def test_model_exception_marks_session_failed(self, monkeypatch):
        """When assess_session_health raises ModelException, session is marked failed."""
        session = _make_session()
        session.status = "active"
        save_calls = []

        def fake_save(self_=None):
            save_calls.append(session.status)

        session.save = fake_save

        # Patch query to return our fake session
        monkeypatch.setattr(
            "monitoring.session_watchdog.AgentSession.query",
            SimpleNamespace(filter=lambda **kw: [session]),
        )

        # Patch assess_session_health to raise ModelException
        def raise_model_exception(s):
            raise ModelException("Unique constraint violated")

        monkeypatch.setattr(
            "monitoring.session_watchdog.assess_session_health",
            raise_model_exception,
        )

        await check_all_sessions()

        assert session.status == "failed"
        assert "failed" in save_calls

    @pytest.mark.asyncio
    async def test_generic_exception_not_caught_as_model_exception(self, monkeypatch):
        """A generic Exception should go to the else branch, not ModelException."""
        session = _make_session()
        session.status = "active"
        logged_errors = []

        monkeypatch.setattr(
            "monitoring.session_watchdog.AgentSession.query",
            SimpleNamespace(filter=lambda **kw: [session]),
        )

        def raise_generic(s):
            raise RuntimeError("something else went wrong")

        monkeypatch.setattr(
            "monitoring.session_watchdog.assess_session_health",
            raise_generic,
        )

        # Capture logger.error calls
        import logging

        def capture_error(self_, msg, *args, **kwargs):
            logged_errors.append(msg % args if args else msg)

        monkeypatch.setattr(logging.Logger, "error", capture_error)

        await check_all_sessions()

        # Session should NOT be marked as failed (that's the ModelException path)
        assert session.status == "active"
        # Error should have been logged
        assert any("Error handling session" in e for e in logged_errors)
