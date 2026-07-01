"""Fast-path / import-deferral tests for the PostToolUse catch-all hook.

The hook fires on EVERY tool call, so its latency is dominated by whether it
imports the popoto-heavy ``hook_utils.memory_bridge`` module (via
``config.memory_defaults`` -> ``from popoto import Defaults``). These tests pin
the optimization: the common "ignored tool / counter-only" path must early-exit
WITHOUT importing that module, while still producing sidecar state identical to
the real ``memory_bridge.recall()``.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "post_tool_use.py"


def _sidecar(session_id: str) -> Path:
    return REPO_ROOT / "data" / "sessions" / session_id / "memory_buffer.json"


def _run_hook(event: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
    )


@pytest.fixture
def clean_session():
    sid = "test-post-tool-fast-path"
    d = REPO_ROOT / "data" / "sessions" / sid
    shutil.rmtree(d, ignore_errors=True)
    yield sid
    shutil.rmtree(d, ignore_errors=True)


def test_ignored_tool_event_exits_zero(clean_session):
    """An ignored tool (Read) event runs cleanly and exits 0."""
    event = {
        "session_id": clean_session,
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.txt"},
        "tool_output": "hello",
        "cwd": str(REPO_ROOT),
    }
    result = _run_hook(event)
    assert result.returncode == 0, result.stderr


def test_counter_only_call_does_not_import_popoto(clean_session):
    """The counter-only fast path must NOT pull memory_bridge/popoto/redis.

    Uses ``-X importtime`` so we observe the real subprocess import graph.
    """
    event = {
        "session_id": clean_session,
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.txt"},
        "tool_output": "hello",
        "cwd": str(REPO_ROOT),
    }
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", str(HOOK)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    heavy = ("popoto", "redis", "config.settings", "models.memory", "models.agent_session")
    offenders = [m for m in heavy if m in proc.stderr]
    assert not offenders, f"counter-only path imported heavy modules: {offenders}"


def test_counter_only_call_bumps_sidecar_counter(clean_session):
    """The fast path still persists the sliding-window counter + buffer."""
    event = {
        "session_id": clean_session,
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.txt"},
        "tool_output": "hello",
        "cwd": str(REPO_ROOT),
    }
    _run_hook(event)
    state = json.loads(_sidecar(clean_session).read_text())
    assert state["count"] == 1
    assert state["buffer"] == [{"tool_name": "Read", "tool_input": {"file_path": "/tmp/x.txt"}}]


def test_fast_path_state_matches_real_recall():
    """Driving N calls through the hook helper yields the same sidecar state
    as driving them through ``memory_bridge.recall()`` directly."""
    sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
    sys.path.insert(0, str(REPO_ROOT))
    import post_tool_use as p
    from hook_utils import memory_bridge as mb

    events = [{"tool_name": f"T{i}", "tool_input": {"file_path": f"/x/{i}.py"}} for i in range(7)]

    a, b = "test-parity-recall", "test-parity-inline"
    for sid in (a, b):
        shutil.rmtree(REPO_ROOT / "data" / "sessions" / sid, ignore_errors=True)
    try:
        for e in events:
            mb.recall(a, e["tool_name"], e["tool_input"], cwd=str(REPO_ROOT))
        for e in events:
            p._run_memory_recall(
                {
                    "session_id": b,
                    "tool_name": e["tool_name"],
                    "tool_input": e["tool_input"],
                    "cwd": str(REPO_ROOT),
                }
            )
        state_a = json.loads(_sidecar(a).read_text())
        state_b = json.loads(_sidecar(b).read_text())
        assert state_a["count"] == state_b["count"]
        assert state_a["buffer"] == state_b["buffer"]
        assert state_a.get("injected") == state_b.get("injected")
    finally:
        for sid in (a, b):
            shutil.rmtree(REPO_ROOT / "data" / "sessions" / sid, ignore_errors=True)
