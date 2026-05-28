"""Unit tests for `agent.claude_session.ClaudeSession` (granite PoC).

These tests mock `subprocess.Popen` so the suite never spawns a real Claude
Code process. The behaviours we want to lock down:

* env construction strips `ANTHROPIC_API_KEY` and injects task list isolation
* `send_message` writes the verified stream-json envelope and rejects empty input
* `read_until_result` parses well-formed events and stops at `{"type": "result"}`
* `read_until_result` surfaces synthetic events for timeout, decode errors,
  broken pipe, and stdout EOF -- never raises out
* `restart()` kills the prior process and respawns
"""

from __future__ import annotations

import io
import json
import os
from unittest import mock

import pytest

from agent.claude_session import (
    ClaudeSession,
    ClaudeSessionConfig,
    ClaudeSessionError,
    _build_env,
    _envelope,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_build_env_blanks_api_key_and_sets_task_list_id():
    with mock.patch.dict(
        os.environ, {"ANTHROPIC_API_KEY": "secret-xxx", "PATH": "/usr/bin"}, clear=True
    ):
        env = _build_env({"EXTRA": "1"}, "granite-poc-abc12345")
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CODE_TASK_LIST_ID"] == "granite-poc-abc12345"
    assert env["EXTRA"] == "1"
    assert env["PATH"] == "/usr/bin"


def test_envelope_shape_is_stream_json_user_message():
    env = _envelope("hello world")
    assert env.endswith("\n")
    decoded = json.loads(env.strip())
    assert decoded == {
        "type": "user",
        "message": {"role": "user", "content": "hello world"},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeStdout:
    """Stand-in for subprocess.PIPE stdout that yields scripted lines."""

    def __init__(self, lines: list[str]):
        self._lines = list(lines)

    def readline(self) -> str:
        if not self._lines:
            return ""  # EOF
        return self._lines.pop(0)

    def fileno(self) -> int:  # required by select.select
        return 0


class _FakeStdin:
    def __init__(self):
        self.written: list[str] = []
        self.closed = False

    def write(self, data: str) -> int:
        if self.closed:
            raise BrokenPipeError("closed")
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self, stdout_lines: list[str], poll_value: int | None = None):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines)
        self.stderr = io.StringIO()
        self._poll_value = poll_value
        self.pid = 4242
        self.killed = False
        self.terminated = False
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self._poll_value

    def send_signal(self, sig):
        self.terminated = True
        self._poll_value = 0

    def kill(self):
        self.killed = True
        self._poll_value = -9

    def wait(self, timeout=None):
        self.wait_calls += 1
        return self._poll_value if self._poll_value is not None else 0


def _make_session(stdout_lines: list[str]) -> tuple[ClaudeSession, _FakeProc]:
    fake = _FakeProc(stdout_lines)
    cfg = ClaudeSessionConfig(
        model="sonnet",
        cwd="/tmp",
        task_list_id="granite-poc-test1234",
    )
    session = ClaudeSession(cfg)
    session._proc = fake  # type: ignore[assignment]
    return session, fake


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_writes_envelope():
    session, fake = _make_session([])
    session.send_message("hello")
    written = fake.stdin.written[0]
    payload = json.loads(written.strip())
    assert payload["type"] == "user"
    assert payload["message"]["content"] == "hello"


def test_send_message_rejects_empty_text():
    session, _ = _make_session([])
    with pytest.raises(ClaudeSessionError):
        session.send_message("")
    with pytest.raises(ClaudeSessionError):
        session.send_message("   ")


def test_send_message_raises_when_subprocess_dead():
    session, fake = _make_session([])
    fake._poll_value = 1  # process has exited
    with pytest.raises(BrokenPipeError):
        session.send_message("hi")


def test_send_message_raises_when_session_not_started():
    cfg = ClaudeSessionConfig(model="sonnet", cwd="/tmp")
    session = ClaudeSession(cfg)
    with pytest.raises(ClaudeSessionError):
        session.send_message("hi")


# ---------------------------------------------------------------------------
# read_until_result
# ---------------------------------------------------------------------------


def _make_result_line(text: str = "ok") -> str:
    return json.dumps({"type": "result", "subtype": "success", "result": text}) + "\n"


def test_read_until_result_returns_on_result_event(monkeypatch):
    lines = [
        json.dumps({"type": "system", "subtype": "init"}) + "\n",
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}})
        + "\n",
        _make_result_line("ok"),
        # extra line should NOT be read
        json.dumps({"type": "system", "subtype": "extra"}) + "\n",
    ]
    session, _ = _make_session(lines)
    monkeypatch.setattr("agent.claude_session.select.select", lambda r, w, x, t: (r, [], []))
    events = session.read_until_result(timeout=5)
    assert events[-1]["type"] == "result"
    assert len(events) == 3  # system, assistant, result -- extra line untouched


def test_read_until_result_handles_decode_error(monkeypatch):
    lines = [
        "not json at all\n",
        _make_result_line(),
    ]
    session, _ = _make_session(lines)
    monkeypatch.setattr("agent.claude_session.select.select", lambda r, w, x, t: (r, [], []))
    events = session.read_until_result(timeout=5)
    assert any(e["type"] == "decode_error" for e in events)
    assert events[-1]["type"] == "result"


def test_read_until_result_handles_eof(monkeypatch):
    session, _ = _make_session([])  # readline immediately returns ""
    monkeypatch.setattr("agent.claude_session.select.select", lambda r, w, x, t: (r, [], []))
    events = session.read_until_result(timeout=2)
    assert any(e["type"] == "broken_pipe" for e in events)


def test_read_until_result_returns_timeout_when_no_data(monkeypatch):
    session, _ = _make_session([])
    # Force select() to always say "no data", which makes the loop wait for
    # the deadline and then return a synthetic timeout.
    monkeypatch.setattr("agent.claude_session.select.select", lambda r, w, x, t: ([], [], []))
    # Make time.monotonic jump past the deadline on the second call.
    fake_time = iter([1000.0, 1000.5, 1500.0, 1500.0, 1500.0, 1500.0])
    monkeypatch.setattr("agent.claude_session.time.monotonic", lambda: next(fake_time))
    events = session.read_until_result(timeout=2)
    assert events[-1]["type"] == "timeout"


def test_read_until_result_rejects_non_object_json(monkeypatch):
    lines = [
        json.dumps(["not", "an", "object"]) + "\n",
        _make_result_line(),
    ]
    session, _ = _make_session(lines)
    monkeypatch.setattr("agent.claude_session.select.select", lambda r, w, x, t: (r, [], []))
    events = session.read_until_result(timeout=5)
    assert any(e["type"] == "decode_error" for e in events)
    assert events[-1]["type"] == "result"


# ---------------------------------------------------------------------------
# restart / stop
# ---------------------------------------------------------------------------


def test_stop_is_idempotent():
    session, fake = _make_session([])
    session.stop()
    session.stop()  # second call must not raise
    assert session._proc is None
    assert fake.terminated  # got SIGTERM


def test_restart_replaces_process(monkeypatch):
    session, original = _make_session([])
    replaced = {"called": False}

    def fake_popen(*args, **kwargs):
        replaced["called"] = True
        return _FakeProc([_make_result_line()])

    monkeypatch.setattr("agent.claude_session.subprocess.Popen", fake_popen)
    session.restart()
    assert replaced["called"] is True
    assert session._proc is not original
    assert session.is_running
