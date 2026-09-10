"""Unit tests for the Codex harness adapter (plan #2001 Task 1).

Covers recorded success/failure JSONL fixtures (shapes live-measured on
``codex-cli 0.154.0`` with thread ids redacted — no rollout/auth data),
argv/stdin assembly, callbacks, cleanup, auth, version, cancellation, and
schema cases. The subprocess layer is faked; the adapter's own parsing,
persistence-contract events, and failure mapping run for real.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

from agent.session_runner.harness import events as harness_events
from agent.session_runner.harness.base import TurnRequest
from agent.session_runner.harness.codex import (
    CODEX_DEFAULT_SANDBOX,
    CODEX_DEV_REPORT_SCHEMA,
    CODEX_MIN_VERSION,
    CodexHarnessAdapter,
    build_codex_argv,
    codex_spawn_env,
    parse_codex_version,
    preflight_codex,
)

THREAD_A = "01a08c06-6d38-7611-9144-c64562b4e27c"
THREAD_B = "19f5a7e3-39a7-3323-bf14-1d30931abc86"


def _success_stdout(thread_id: str = THREAD_A, report: str = "did the thing") -> bytes:
    lines = [
        json.dumps({"type": "thread.started", "thread_id": thread_id}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps({"report": report, "complete": True}),
                },
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 18230,
                    "cached_input_tokens": 0,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 0,
                },
            }
        ),
    ]
    return ("\n".join(lines) + "\n").encode()


def _failed_stdout(thread_id: str = THREAD_A) -> bytes:
    lines = [
        json.dumps({"type": "thread.started", "thread_id": thread_id}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "error",
                "message": '{"type": "error", "error": {"code": "invalid_json_schema"}}',
            }
        ),
        json.dumps({"type": "turn.failed", "error": {"message": "bad schema", "status": 400}}),
    ]
    return ("\n".join(lines) + "\n").encode()


class _FakeStdin:
    def __init__(self):
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 4242
        self.stdin = _FakeStdin()
        self.killed = False

    async def communicate(self):
        return (self._stdout, self._stderr)

    def kill(self) -> None:
        self.killed = True


def _run(coro):
    return asyncio.run(coro)


def _patch_spawn(fake: _FakeProc, captured: dict):
    async def _spawn(*argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return fake

    return patch(
        "agent.session_runner.harness.codex.asyncio.create_subprocess_exec",
        side_effect=_spawn,
    )


def _request(**overrides) -> TurnRequest:
    base = {"message": "do the thing", "working_dir": "/tmp"}
    base.update(overrides)
    return TurnRequest(**base)


# --- argv / stdin ------------------------------------------------------------


def test_first_turn_argv_globals_before_exec_and_prompt_on_stdin():
    captured: dict = {}
    fake = _FakeProc(stdout=_success_stdout())
    with _patch_spawn(fake, captured):
        result = _run(CodexHarnessAdapter().run_turn(_request()))
    argv = captured["argv"]
    assert argv[:7] == ["codex", "-a", "never", "-s", "workspace-write", "-C", "/tmp"]
    assert argv[7] == "exec"
    assert "resume" not in argv
    assert "--json" in argv and "--output-schema" in argv and argv[-1] == "-"
    assert "--ephemeral" not in argv and "--full-auto" not in argv
    # Prompt traveled on stdin, never as an argv element.
    assert "do the thing" not in argv
    assert fake.stdin.written == b"do the thing"
    assert result.error_detail is None
    assert result.resume_handle == THREAD_A


def test_resume_argv_inserts_resume_and_drops_color():
    captured: dict = {}
    fake = _FakeProc(stdout=_success_stdout())
    with _patch_spawn(fake, captured):
        _run(CodexHarnessAdapter().run_turn(_request(prior_uuid=THREAD_A)))
    argv = captured["argv"]
    assert argv[7:10] == ["exec", "resume", THREAD_A]
    assert "--color" not in argv
    assert "--json" in argv and "--output-schema" in argv


def test_resume_transcript_still_emits_session_started():
    # Resume-shaped fixture: a resume turn (prior_uuid set) whose transcript
    # carries thread.started pins the budget-accounting event. If the CLI
    # ever omits thread.started on resume, this test fails loudly instead
    # of letting resumed turns silently stop consuming max_resumed_turns.
    seen: list = []
    captured: dict = {}
    fake = _FakeProc(stdout=_success_stdout(thread_id=THREAD_A))
    with _patch_spawn(fake, captured):
        result = _run(
            CodexHarnessAdapter().run_turn(_request(prior_uuid=THREAD_A), on_event=seen.append)
        )
    assert result.error_detail is None
    assert result.resume_handle == THREAD_A
    started = [e for e in seen if e.type == harness_events.SESSION_STARTED]
    assert len(started) == 1
    assert started[0].data["handle"] == THREAD_A


def test_build_codex_argv_color_only_first_turn():
    first = build_codex_argv(thread_id=None, schema_path="/tmp/s.json", worktree="/tmp")
    resumed = build_codex_argv(thread_id=THREAD_A, schema_path="/tmp/s.json", worktree="/tmp")
    assert "--color" in first
    assert "--color" not in resumed


def test_hostile_instruction_goes_to_stdin_not_argv():
    hostile = "x\U0001f600'; rm -rf /; $(evil)\x00\nline2"
    captured: dict = {}
    fake = _FakeProc(stdout=_success_stdout())
    with _patch_spawn(fake, captured):
        _run(CodexHarnessAdapter().run_turn(_request(message=hostile)))
    assert hostile not in captured["argv"]
    assert fake.stdin.written == hostile.encode("utf-8", errors="replace")


def test_empty_instruction_rejected_before_spawn():
    with patch("agent.session_runner.harness.codex.asyncio.create_subprocess_exec") as spawn:
        result = _run(CodexHarnessAdapter().run_turn(_request(message="   ")))
    spawn.assert_not_called()
    assert result.error_detail is not None and "Empty" in result.error_detail


def test_malformed_thread_id_refuses_resume():
    with patch("agent.session_runner.harness.codex.asyncio.create_subprocess_exec") as spawn:
        result = _run(CodexHarnessAdapter().run_turn(_request(prior_uuid="not-a-uuid")))
    spawn.assert_not_called()
    assert "Malformed" in (result.error_detail or "")


# --- success path ------------------------------------------------------------


def test_success_maps_usage_structured_output_and_events():
    seen: list = []
    fake = _FakeProc(stdout=_success_stdout(report="hello"))
    with _patch_spawn(fake, {}):
        result = _run(CodexHarnessAdapter().run_turn(_request(), on_event=seen.append))
    assert result.error_detail is None
    assert result.final_text == "hello"
    assert result.structured_output == {"report": "hello", "complete": True}
    assert result.usage is not None and result.usage["input_tokens"] == 18230
    assert result.returncode == 0
    assert result.result_event_fired is True
    types = [e.type for e in seen]
    assert harness_events.SESSION_STARTED in types
    assert harness_events.TURN_SPAWNED in types
    assert harness_events.TURN_COMPLETED in types
    started = next(e for e in seen if e.type == harness_events.SESSION_STARTED)
    assert started.data["handle"] == THREAD_A


def test_default_schema_requires_every_property_key():
    # Live-measured: the Codex API rejects schemas whose `required` omits
    # any key in `properties` (invalid_json_schema).
    props = set(CODEX_DEV_REPORT_SCHEMA["properties"])
    assert set(CODEX_DEV_REPORT_SCHEMA["required"]) == props


# --- failure paths -----------------------------------------------------------


def test_native_turn_failed_produces_bounded_error_detail():
    fake = _FakeProc(stdout=_failed_stdout(), returncode=1)
    with _patch_spawn(fake, {}):
        result = _run(CodexHarnessAdapter().run_turn(_request()))
    assert result.error_detail is not None
    assert "Codex-native failure" in result.error_detail
    assert result.final_text == ""
    assert result.structured_output is None
    # Thread id persists even on failure (already-persisted handle is kept).
    assert result.resume_handle == THREAD_A


def test_nonzero_exit_without_terminal_event_reports_stderr_tail():
    fake = _FakeProc(stdout=b"", stderr=b"boom: something broke", returncode=2)
    with _patch_spawn(fake, {}):
        result = _run(CodexHarnessAdapter().run_turn(_request()))
    assert result.error_detail is not None
    assert "code 2" in result.error_detail
    assert "boom" in result.error_detail


def test_malformed_jsonl_skipped_and_terminal_still_parsed():
    stdout = b"not json at all\n" + _success_stdout()
    fake = _FakeProc(stdout=stdout)
    with _patch_spawn(fake, {}):
        result = _run(CodexHarnessAdapter().run_turn(_request()))
    assert result.error_detail is None
    assert result.final_text == "did the thing"


def test_non_json_schema_text_is_typed_failure_not_loop():
    # Craft an agent message with plain prose.
    lines = [
        json.dumps({"type": "thread.started", "thread_id": THREAD_A}),
        json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "plain prose"}}
        ),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]
    fake = _FakeProc(stdout=("\n".join(lines) + "\n").encode())
    with _patch_spawn(fake, {}):
        result = _run(CodexHarnessAdapter().run_turn(_request()))
    assert result.error_detail is not None
    assert "schema" in result.error_detail.lower()


def test_empty_agent_message_is_typed_failure():
    lines = [
        json.dumps({"type": "thread.started", "thread_id": THREAD_A}),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]
    fake = _FakeProc(stdout=("\n".join(lines) + "\n").encode())
    with _patch_spawn(fake, {}):
        result = _run(CodexHarnessAdapter().run_turn(_request()))
    assert result.error_detail is not None
    assert "no final message" in result.error_detail.lower()


def test_missing_binary_is_actionable():
    async def _raise(*argv, **kwargs):
        raise FileNotFoundError("codex")

    with patch(
        "agent.session_runner.harness.codex.asyncio.create_subprocess_exec",
        side_effect=_raise,
    ):
        result = _run(CodexHarnessAdapter().run_turn(_request()))
    assert result.error_detail is not None
    assert "not found" in result.error_detail


def test_missing_working_dir_reports_cwd_not_binary():
    # create_subprocess_exec raises FileNotFoundError for a missing cwd
    # too — the adapter must blame the directory, not the install.
    async def _raise(*argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    with patch(
        "agent.session_runner.harness.codex.asyncio.create_subprocess_exec",
        side_effect=_raise,
    ):
        result = _run(
            CodexHarnessAdapter().run_turn(_request(working_dir="/nonexistent-codex-cwd-xyz"))
        )
    assert result.error_detail is not None
    assert "working directory" in result.error_detail.lower()
    assert "/nonexistent-codex-cwd-xyz" in result.error_detail


def test_secrets_scrubbed_from_error_detail():
    fake = _FakeProc(stdout=b"", stderr=b"auth failed sk-abcdef1234567890 bye", returncode=1)
    with _patch_spawn(fake, {}):
        result = _run(CodexHarnessAdapter().run_turn(_request()))
    assert result.error_detail is not None
    assert "sk-abcdef1234567890" not in result.error_detail
    assert "[REDACTED]" in result.error_detail


# --- spawn env allowlist -----------------------------------------------------


def test_spawn_env_allowlist():
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk-should-never-appear",
            "SOME_TOKEN": "tok-should-never-appear",
            "ANTHROPIC_API_KEY": "x",
            "PATH": "/usr/bin",
        },
        clear=False,
    ):
        env = codex_spawn_env()
    assert "OPENAI_API_KEY" not in env
    assert "SOME_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert not any(k.endswith("_API_KEY") or k.endswith("_TOKEN") for k in env)
    assert env.get("PATH") == "/usr/bin"


def test_spawn_env_single_use_api_key_only_when_passed():
    with patch.dict(os.environ, {"CODEX_API_KEY": "ambient-must-not-leak"}, clear=False):
        env = codex_spawn_env()
    assert "CODEX_API_KEY" not in env
    env2 = codex_spawn_env(api_key="single-use")
    assert env2["CODEX_API_KEY"] == "single-use"


# --- version / preflight -----------------------------------------------------


def test_parse_codex_version():
    assert parse_codex_version("codex-cli 0.154.0") == (0, 154, 0)
    assert parse_codex_version("0.144.3") == (0, 144, 3)
    assert parse_codex_version("garbage") is None


def test_preflight_rejects_missing_binary():
    with patch("agent.session_runner.harness.codex.codex_binary", return_value=None):
        version, error = preflight_codex(sandbox=CODEX_DEFAULT_SANDBOX, worktree="/tmp")
    assert version is None
    assert error is not None and "not found" in error


def test_preflight_rejects_old_version_and_bad_sandbox(tmp_path):
    with (
        patch("agent.session_runner.harness.codex.codex_binary", return_value="/usr/bin/codex"),
        patch("agent.session_runner.harness.codex.subprocess.run") as run,
    ):
        run.return_value.stdout = "codex-cli 0.100.0"
        run.return_value.returncode = 0
        version, error = preflight_codex(sandbox=CODEX_DEFAULT_SANDBOX, worktree=str(tmp_path))
        assert version is None and "below the verified minimum" in (error or "")
        run.return_value.stdout = "codex-cli 0.154.0"
        version, error = preflight_codex(sandbox="yolo", worktree=str(tmp_path))
        assert version is None and "Invalid Codex sandbox" in (error or "")


def test_preflight_min_version_gate_matches_plan_floor():
    assert CODEX_MIN_VERSION == (0, 144, 3)
