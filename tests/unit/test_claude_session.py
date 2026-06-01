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

import json
import os
from unittest import mock

import pytest

from agent.claude_session import (
    ClaudeSession,
    ClaudeSessionConfig,
    ClaudeSessionError,
    _build_cmd,
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


def test_build_cmd_includes_required_streamjson_flags():
    """Regression guard for the Spike-3 correction.

    `claude --input-format stream-json` is rejected unless paired with BOTH
    `-p/--print` AND `--verbose`. If a refactor drops either flag the CLI
    breaks at runtime with no Python-side error, so we pin the exact contract.
    """
    cmd = _build_cmd(ClaudeSessionConfig(model="sonnet", cwd="/tmp"))
    assert "-p" in cmd
    assert "--verbose" in cmd
    # --input-format must be immediately followed by stream-json
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"


def test_build_cmd_appends_system_prompt_only_when_set():
    with_prompt = _build_cmd(
        ClaudeSessionConfig(model="opus", cwd="/tmp", system_prompt="be terse")
    )
    assert with_prompt[with_prompt.index("--append-system-prompt") + 1] == "be terse"
    without = _build_cmd(ClaudeSessionConfig(model="opus", cwd="/tmp"))
    assert "--append-system-prompt" not in without


def test_build_env_extra_env_overrides_blanked_api_key():
    """extra_env is merged last, so a caller can override the blanked key."""
    with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret"}, clear=True):
        env = _build_env({"ANTHROPIC_API_KEY": "override"}, "granite-poc-x")
    assert env["ANTHROPIC_API_KEY"] == "override"


def test_auto_task_list_id_format_when_none():
    import re

    session = ClaudeSession(ClaudeSessionConfig(model="sonnet", cwd="/tmp"))
    assert re.fullmatch(r"granite-poc-[0-9a-f]{8}", session.task_list_id)


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


class _FakeStderr:
    """Stand-in for subprocess.PIPE stderr that yields scripted lines."""

    def __init__(self, lines: list[str] | None = None):
        self._lines = list(lines or [])

    def readline(self) -> str:
        if not self._lines:
            return ""  # EOF
        return self._lines.pop(0)

    def fileno(self) -> int:  # required by select.select
        return 1


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
    def __init__(
        self,
        stdout_lines: list[str],
        poll_value: int | None = None,
        stderr_lines: list[str] | None = None,
    ):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines)
        self.stderr = _FakeStderr(stderr_lines)
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


# ---------------------------------------------------------------------------
# read_until_result -- additional failure modes
# ---------------------------------------------------------------------------


def test_read_until_result_per_line_timeout(monkeypatch):
    """Per-line 30s deadline fires before the (larger) overall deadline."""
    session, _ = _make_session([])
    monkeypatch.setattr("agent.claude_session.select.select", lambda r, w, x, t: ([], [], []))
    # deadline=+300, per_line=+30, then now jumps past per_line but under deadline.
    fake_time = iter([1000.0, 1000.0, 1100.0, 1100.0])
    monkeypatch.setattr("agent.claude_session.time.monotonic", lambda: next(fake_time))
    events = session.read_until_result(timeout=300)
    assert events[-1]["type"] == "timeout"
    assert "per-line" in events[-1]["reason"]


def test_read_until_result_select_oserror_is_broken_pipe(monkeypatch):
    session, _ = _make_session([_make_result_line()])

    def boom(r, w, x, t):
        raise OSError("bad file descriptor")

    monkeypatch.setattr("agent.claude_session.select.select", boom)
    events = session.read_until_result(timeout=10)
    assert events[-1]["type"] == "broken_pipe"
    assert "select" in events[-1]["reason"]


def test_read_until_result_when_not_started():
    session = ClaudeSession(ClaudeSessionConfig(model="sonnet", cwd="/tmp"))
    events = session.read_until_result(timeout=1)
    assert events == [{"type": "broken_pipe", "reason": "session not started"}]


# ---------------------------------------------------------------------------
# lifecycle: start idempotency + context manager
# ---------------------------------------------------------------------------


def test_start_is_idempotent_when_already_running(monkeypatch):
    session, fake = _make_session([])  # fake.poll() returns None -> "running"

    def fail_popen(*args, **kwargs):
        raise AssertionError("Popen must not be called when already running")

    monkeypatch.setattr("agent.claude_session.subprocess.Popen", fail_popen)
    session.start()  # must early-return without spawning
    assert session._proc is fake


def test_context_manager_starts_and_stops(monkeypatch):
    fake = _FakeProc([_make_result_line()])
    monkeypatch.setattr("agent.claude_session.subprocess.Popen", lambda *a, **k: fake)
    cfg = ClaudeSessionConfig(model="sonnet", cwd="/tmp")
    with ClaudeSession(cfg) as session:
        assert session.is_running
    assert session._proc is None  # stopped on exit


# ---------------------------------------------------------------------------
# Resume / session_id capture
# ---------------------------------------------------------------------------

_UUID = "636c494d-f552-499b-a2b4-0844f235e783"


def test_build_cmd_includes_resume_when_session_id_set():
    cmd = _build_cmd(ClaudeSessionConfig(model="sonnet", cwd="/tmp"), resume_session_id=_UUID)
    assert cmd[cmd.index("--resume") + 1] == _UUID


def test_read_until_result_captures_session_id_from_stream(monkeypatch):
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": _UUID}) + "\n",
        _make_result_line(),
    ]
    session, _ = _make_session(lines)
    monkeypatch.setattr("agent.claude_session.select.select", lambda r, w, x, t: (r, [], []))
    session.read_until_result(timeout=5)
    assert session.session_id == _UUID


def test_stderr_resume_hint_captures_session_id_on_eof(monkeypatch):
    """On crash/ctrl-c Claude prints `claude --resume <uuid>` -- capture it."""
    fake = _FakeProc(
        [],  # stdout immediately EOFs (process died)
        stderr_lines=["Resume this session with:\n", f"claude --resume {_UUID}\n"],
    )
    session = ClaudeSession(ClaudeSessionConfig(model="sonnet", cwd="/tmp"))
    session._proc = fake  # type: ignore[assignment]
    monkeypatch.setattr("agent.claude_session.select.select", lambda r, w, x, t: (r, [], []))
    events = session.read_until_result(timeout=5)
    assert events[-1]["type"] == "broken_pipe"
    assert session.session_id == _UUID


def test_resume_respawns_with_resume_flag(monkeypatch):
    session, _ = _make_session([])
    session._session_id = _UUID
    captured = {}

    def fake_popen(cmd, *a, **k):
        captured["cmd"] = cmd
        return _FakeProc([_make_result_line()])

    monkeypatch.setattr("agent.claude_session.subprocess.Popen", fake_popen)
    assert session.resume() is True
    assert captured["cmd"][captured["cmd"].index("--resume") + 1] == _UUID


def test_resume_falls_back_to_fresh_without_session_id(monkeypatch):
    session, _ = _make_session([])
    captured = {}

    def fake_popen(cmd, *a, **k):
        captured["cmd"] = cmd
        return _FakeProc([_make_result_line()])

    monkeypatch.setattr("agent.claude_session.subprocess.Popen", fake_popen)
    assert session.resume() is False
    assert "--resume" not in captured["cmd"]
