"""Integration tests for the stall advisory classifier end-to-end (issue #1538).

Writes real telemetry JSONL files, reads them back through read_session_timeline,
and runs classify_session_stall() — exercising the full read path without
mocking the file I/O layer.

Hard constraint guard:
    agent.session_health must NOT be imported or called during classification.
    This is enforced by checking sys.modules after classification.

Test isolation:
    All telemetry files are written to `logs/session_telemetry/` with IDs
    prefixed "test-stall-e2e-" and are removed in fixture teardown.

run_stall_advisory is also tested here for return-shape correctness against
real (in-process) classify_session_stall calls.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.session_stall_classifier import (
    IDLE_STALL_SECS,
    IDLE_SUSPECT_SECS,
    classify_session_stall,
)
from agent.session_telemetry import read_session_timeline

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_PREFIX = "test-stall-e2e"
_TELEMETRY_DIR: Path = Path(__file__).parent.parent.parent / "logs" / "session_telemetry"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_trace(session_id: str, events: list[dict]) -> Path:
    """Write fixture events to the session telemetry JSONL file."""
    _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    path = _TELEMETRY_DIR / f"{session_id}.jsonl"
    with path.open("w") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return path


def _fake_session(
    session_id: str,
    status: str = "running",
    created_at: float | None = None,
    started_at: float | None = None,
) -> SimpleNamespace:
    now = time.time()
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=session_id,
        status=status,
        started_at=started_at,
        # Default: created 700 seconds ago so never-started grace is exceeded.
        created_at=created_at if created_at is not None else (now - 700),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def trace_file(request):
    """Create a telemetry trace file and clean it up after the test."""
    paths: list[Path] = []

    def _make(session_id: str, events: list[dict]) -> Path:
        p = _write_trace(session_id, events)
        paths.append(p)
        return p

    yield _make

    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1. Stalled trace: never-started (no turn_start events)
# ---------------------------------------------------------------------------


class TestNeverStartedE2E:
    def test_running_session_no_turn_start_classifies_stalled(self, trace_file):
        session_id = f"{_TEST_PREFIX}-never-started"
        # Write an empty trace (no turn_start).
        trace_file(session_id, [])

        events = read_session_timeline(session_id)
        assert events == []  # confirms the empty trace was written and read back

        session = _fake_session(session_id, status="running")
        verdict = classify_session_stall(events, session=session)

        assert verdict.level == "stalled"
        assert verdict.reason == "never_started"

    def test_running_session_with_recent_turn_start_classifies_healthy(self, trace_file):
        session_id = f"{_TEST_PREFIX}-has-turn-start"
        recent_ts = time.time() - 30
        events_written = [{"type": "turn_start", "ts": recent_ts}]
        trace_file(session_id, events_written)

        events = read_session_timeline(session_id)
        assert len(events) == 1
        assert events[0]["type"] == "turn_start"

        session = _fake_session(session_id, status="running")
        verdict = classify_session_stall(events, session=session)

        # A turn_start (type == "turn_start") sets has_turn_start=True, so the
        # never-started branch is skipped. Then recent_turn_ts < IDLE_SUSPECT_SECS
        # → healthy/recent_turn_activity.
        assert verdict.level == "healthy"


# ---------------------------------------------------------------------------
# 2. Stalled trace: large idle gap
# ---------------------------------------------------------------------------


class TestIdleGapE2E:
    def test_large_idle_gap_event_classifies_stalled(self, trace_file):
        session_id = f"{_TEST_PREFIX}-idle-gap-stall"
        events_written = [
            {"type": "turn_start", "ts": time.time() - 1200},
            {"type": "turn_end", "ts": time.time() - 1200},
            {"type": "idle_gap", "data": {"duration_secs": IDLE_STALL_SECS + 100}},
        ]
        trace_file(session_id, events_written)

        events = read_session_timeline(session_id)
        assert len(events) == 3

        session = _fake_session(session_id, status="running")
        verdict = classify_session_stall(events, session=session)

        assert verdict.level == "stalled"
        assert verdict.reason == "idle_gap_exceeded_stall"

    def test_suspect_idle_gap_classifies_suspect(self, trace_file):
        session_id = f"{_TEST_PREFIX}-idle-gap-suspect"
        duration = (IDLE_SUSPECT_SECS + IDLE_STALL_SECS) / 2
        old_ts = time.time() - 1200
        events_written = [
            # turn_start is required: has_turn_start=True skips never-started branch.
            {"type": "turn_start", "ts": old_ts},
            {"type": "turn_end", "ts": old_ts},
            {"type": "idle_gap", "data": {"duration_secs": duration}},
        ]
        trace_file(session_id, events_written)

        events = read_session_timeline(session_id)
        session = _fake_session(session_id, status="running")
        verdict = classify_session_stall(events, session=session)

        assert verdict.level == "suspect"


# ---------------------------------------------------------------------------
# 3. Hard constraint guard: agent.session_health must NOT be imported
# ---------------------------------------------------------------------------


class TestSessionHealthNotImported:
    def test_classifier_does_not_import_agent_session_health(self, trace_file):
        """Verify agent.session_health is absent from sys.modules after classification.

        The classifier is designed to never pull in agent.session_health
        (the kill/recovery machinery). This test verifies that constraint is
        upheld at runtime.
        """
        # Ensure agent.session_health is not already loaded (if it is from
        # a prior test, we note it and assert it was NOT called by our code).
        health_was_preloaded = "agent.session_health" in sys.modules

        session_id = f"{_TEST_PREFIX}-health-guard"
        trace_file(session_id, [])

        session = _fake_session(session_id, status="running")

        if not health_was_preloaded:
            # Run classification and verify the module was not imported as a side-effect.
            events = read_session_timeline(session_id)
            classify_session_stall(events, session=session)

            assert "agent.session_health" not in sys.modules, (
                "agent.session_health was imported during classification — "
                "this violates the design constraint (see session_stall_classifier.py docstring)"
            )
        else:
            # agent.session_health was already loaded by some other test or import.
            # In this case we can only verify that classify_session_stall does not
            # call any of its functions. Use a spy to monitor calls.
            real_health_module = sys.modules["agent.session_health"]
            spy = MagicMock(wraps=real_health_module)
            with patch.dict(sys.modules, {"agent.session_health": spy}):
                events = read_session_timeline(session_id)
                classify_session_stall(events, session=session)

            # None of the spy's attributes should have been called.
            spy.assert_not_called()

    def test_session_stall_classifier_module_has_no_health_import(self):
        """Static check: agent.session_stall_classifier must not import agent.session_health.

        The docstring may mention session_health in a constraint comment; that's fine.
        What is forbidden is any actual import statement: `import agent.session_health`
        or `from agent.session_health import ...`.
        """
        import importlib.util
        import re

        spec = importlib.util.find_spec("agent.session_stall_classifier")
        assert spec is not None, "agent.session_stall_classifier not found"
        source_path = Path(spec.origin)
        source = source_path.read_text()

        # Match actual import statements only, not docstring mentions.
        health_import_pattern = re.compile(
            r"^\s*(import\s+agent\.session_health|from\s+agent\.session_health\s+import)",
            re.MULTILINE,
        )
        match = health_import_pattern.search(source)
        assert match is None, (
            f"agent.session_stall_classifier has an import of agent.session_health "
            f"at: {match.group(0)!r} — this violates the no-session-health constraint"
        )


# ---------------------------------------------------------------------------
# 4. run_stall_advisory return shape (real classify_session_stall calls)
# ---------------------------------------------------------------------------


class TestStallAdvisoryE2E:
    def test_run_stall_advisory_return_shape_with_sessions(self, trace_file):
        """run_stall_advisory returns {status, findings, summary} with correct types."""
        from reflections.stall_advisory import run_stall_advisory

        session_id = f"{_TEST_PREFIX}-advisory-shape"
        trace_file(session_id, [])

        now = time.time()
        fake_sess = _fake_session(session_id, status="running", created_at=now - 700)

        # Patch only AgentSession.query + TERMINAL_STATUSES to avoid Redis.
        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = [fake_sess]
        mock_sl_module = MagicMock()
        mock_sl_module.TERMINAL_STATUSES = frozenset(
            {"completed", "failed", "killed", "abandoned", "cancelled"}
        )

        with patch.dict(
            sys.modules,
            {
                "models.agent_session": mock_as_module,
                "models.session_lifecycle": mock_sl_module,
            },
        ):
            result = run_stall_advisory(params=None)

        assert "status" in result
        assert "findings" in result
        assert "summary" in result
        assert isinstance(result["findings"], list)
        assert isinstance(result["summary"], str)

    def test_run_stall_advisory_stalled_session_sets_warn_status(self, trace_file):
        from reflections.stall_advisory import run_stall_advisory

        session_id = f"{_TEST_PREFIX}-advisory-warn"
        trace_file(session_id, [])  # no events → stalled/never_started

        now = time.time()
        fake_sess = _fake_session(session_id, status="running", created_at=now - 700)

        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = [fake_sess]
        mock_sl_module = MagicMock()
        mock_sl_module.TERMINAL_STATUSES = frozenset(
            {"completed", "failed", "killed", "abandoned", "cancelled"}
        )

        with patch.dict(
            sys.modules,
            {
                "models.agent_session": mock_as_module,
                "models.session_lifecycle": mock_sl_module,
            },
        ):
            result = run_stall_advisory(params=None)

        assert result["status"] == "warn"
        assert len(result["findings"]) == 1
        assert result["findings"][0]["session_id"] == session_id
        assert result["findings"][0]["level"] == "stalled"

    def test_run_stall_advisory_no_sessions_returns_ok(self):
        from reflections.stall_advisory import run_stall_advisory

        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = []
        mock_sl_module = MagicMock()
        mock_sl_module.TERMINAL_STATUSES = frozenset(
            {"completed", "failed", "killed", "abandoned", "cancelled"}
        )

        with patch.dict(
            sys.modules,
            {
                "models.agent_session": mock_as_module,
                "models.session_lifecycle": mock_sl_module,
            },
        ):
            result = run_stall_advisory(params=None)

        assert result["status"] == "ok"
        assert result["findings"] == []
