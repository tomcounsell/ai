"""Integration tests for session telemetry e2e — CLI and JSONL sink."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.session_telemetry import _get_telemetry_dir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKTREE = Path(__file__).parent.parent.parent


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run `python -m tools.valor_session telemetry <args>` in the worktree."""
    return subprocess.run(
        [sys.executable, "-m", "tools.valor_session", "telemetry", *args],
        capture_output=True,
        text=True,
        cwd=str(WORKTREE),
    )


def _write_test_events(session_id: str, count: int = 3) -> Path:
    """Write *count* events for *session_id* directly into the telemetry dir."""
    tdir = _get_telemetry_dir()
    trace = tdir / f"{session_id}.jsonl"
    tdir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(count):
        ev = {
            "session_id": session_id,
            "ts": f"2026-01-01T00:0{i}:00Z",
            "type": "turn_start",
            "turn": i,
        }
        lines.append(json.dumps(ev))
    trace.write_text("\n".join(lines) + "\n")
    return trace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def cleanup_test_traces():
    """Delete any test trace files written during the test."""
    created: list[Path] = []
    yield created
    for p in created:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_no_trace():
    """CLI returns exit 0 with 'No telemetry recorded' when no trace exists."""
    result = _run_cli("--id", "unknown-session-xyz123")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "No telemetry recorded" in result.stdout


@pytest.mark.integration
def test_cli_renders_trace(cleanup_test_traces):
    """CLI renders event types from a pre-written JSONL trace."""
    session_id = "test-e2e-render-001"
    trace = _write_test_events(session_id, count=3)
    cleanup_test_traces.append(trace)

    result = _run_cli("--id", session_id)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "turn_start" in result.stdout


@pytest.mark.integration
def test_cli_json_flag(cleanup_test_traces):
    """CLI with --json emits one valid JSON object per line."""
    session_id = "test-e2e-json-001"
    trace = _write_test_events(session_id, count=3)
    cleanup_test_traces.append(trace)

    result = _run_cli("--id", session_id, "--json")
    assert result.returncode == 0, f"stderr: {result.stderr}"

    lines = [ln for ln in result.stdout.strip().splitlines() if ln]
    assert len(lines) == 3, f"Expected 3 JSON lines, got {len(lines)}: {lines}"
    for ln in lines:
        parsed = json.loads(ln)
        assert parsed["type"] == "turn_start"


@pytest.mark.integration
def test_cli_tail_flag(cleanup_test_traces):
    """CLI with --tail N shows only the last N events."""
    session_id = "test-e2e-tail-001"
    trace = _write_test_events(session_id, count=5)
    cleanup_test_traces.append(trace)

    result = _run_cli("--id", session_id, "--json", "--tail", "2")
    assert result.returncode == 0, f"stderr: {result.stderr}"

    lines = [ln for ln in result.stdout.strip().splitlines() if ln]
    assert len(lines) == 2, f"Expected 2 lines with --tail 2, got {len(lines)}"
    # The tail events should be the last two (turn indices 3 and 4)
    events = [json.loads(ln) for ln in lines]
    turns = [e["turn"] for e in events]
    assert turns == [3, 4], f"Expected last two turns, got {turns}"
