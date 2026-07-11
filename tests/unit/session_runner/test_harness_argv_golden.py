"""Golden argv/env + behavioral-parity tests for the HarnessAdapter seam
extraction (plan #2000 Task 2.2).

The extraction moved argv/env assembly and stream-json parsing out of
``agent/sdk_client.py`` into ``agent/session_runner/harness/claude.py``
verbatim. A golden test alone (pinning the exact argv/env) does not prove
*behavior* preservation, so this file pairs it with the three
behavioral-parity fixtures the plan calls out:

(a) the final assembled argv **string** on both the first-turn (no
    ``--resume``) and resume (``--resume <uuid>``) paths;
(b) ``_store_claude_session_uuid`` fires with the harness-reported session
    id on turn completion;
(c) the #1980 retry-without-``--resume`` branch: a resumed subprocess that
    exits non-zero *without* a ``result`` event re-runs once with the
    full-context message and ``--resume`` stripped, while a non-zero exit
    *after* a valid ``result`` does NOT retry (that fuller matrix also
    lives in ``tests/unit/test_harness_stale_uuid_result_preservation.py``;
    this file pins the minimal argv-level assertion of the same contract).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.session_runner.harness.claude import get_response_via_harness

VALID_UUID = "36514af3-c4e9-455d-9087-f5850101990e"


class _AsyncLineIterator:
    """Async iterator yielding encoded stdout lines."""

    def __init__(self, data: str):
        self._lines = [(line + "\n").encode("utf-8") for line in data.splitlines() if line.strip()]
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._index]
        self._index += 1
        return line


def _result_stdout(result: str = "ok", session_id: str = "sess_abc") -> str:
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
        json.dumps({"type": "result", "result": result, "session_id": session_id}),
    ]
    return "\n".join(lines) + "\n"


def _make_mock_proc(stdout_data: str, returncode: int = 0):
    proc = AsyncMock()
    proc.stdout = _AsyncLineIterator(stdout_data)
    proc.stderr = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = returncode
    proc.pid = 4242
    return proc


# ---------------------------------------------------------------------------
# Golden argv/env assembly
# ---------------------------------------------------------------------------


class TestGoldenArgvAssembly:
    """Pins the exact argv/env the subprocess is spawned with."""

    @pytest.mark.asyncio
    async def test_first_turn_argv_no_resume(self):
        """No prior_uuid: no --resume flag; model + system prompt flags land
        before the positional message."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_proc(_result_stdout(session_id="new-sess"))

            await get_response_via_harness(
                message="do the thing",
                working_dir="/tmp/work",
                env={"AGENT_SESSION_ID": "sess-1"},
                model="opus",
                system_prompt="persona body",
            )

        assert mock_exec.call_count == 1
        argv = mock_exec.call_args.args
        assert argv == (
            "claude",
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--permission-mode",
            "bypassPermissions",
            "--model",
            "opus",
            "--exclude-dynamic-system-prompt-sections",
            "--append-system-prompt",
            "persona body",
            "do the thing",
        )
        assert "--resume" not in argv
        kwargs = mock_exec.call_args.kwargs
        assert kwargs["cwd"] == "/tmp/work"

    @pytest.mark.asyncio
    async def test_resume_turn_argv_includes_resume_flag(self):
        """prior_uuid set: --resume <uuid> is injected immediately before the
        positional message; no model/system-prompt flags this time."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_proc(_result_stdout(session_id=VALID_UUID))

            await get_response_via_harness(
                message="continue",
                working_dir="/tmp/work",
                prior_uuid=VALID_UUID,
            )

        argv = mock_exec.call_args.args
        assert argv == (
            "claude",
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--permission-mode",
            "bypassPermissions",
            "--resume",
            VALID_UUID,
            "continue",
        )

    @pytest.mark.asyncio
    async def test_invalid_prior_uuid_falls_back_to_first_turn_argv(self):
        """A malformed prior_uuid is treated as None -- no --resume flag."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_proc(_result_stdout())

            await get_response_via_harness(
                message="hello",
                working_dir="/tmp/work",
                prior_uuid="not-a-real-uuid",
            )

        argv = mock_exec.call_args.args
        assert "--resume" not in argv
        assert argv[-1] == "hello"

    @pytest.mark.asyncio
    async def test_env_strips_api_key_and_sets_columns(self):
        """The subprocess env always strips ANTHROPIC_API_KEY (popped, even
        when the caller's overlay tries to set it) and forces a wide COLUMNS
        so Claude Code doesn't narrow-wrap result text."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_mock_proc(_result_stdout())

            await get_response_via_harness(
                message="hi",
                working_dir="/tmp/work",
                env={"ANTHROPIC_API_KEY": "sk-leaked", "AGENT_SESSION_ID": "sess-9"},
            )

        env = mock_exec.call_args.kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in env
        assert env["COLUMNS"] == "999"
        assert env["AGENT_SESSION_ID"] == "sess-9"


# ---------------------------------------------------------------------------
# Behavioral parity (b): _store_claude_session_uuid fires on completion
# ---------------------------------------------------------------------------


class TestStoreClaudeSessionUuidParity:
    @pytest.mark.asyncio
    async def test_store_claude_session_uuid_fires_with_harness_session_id(self):
        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("agent.sdk_client._store_claude_session_uuid") as mock_store,
        ):
            mock_exec.return_value = _make_mock_proc(
                _result_stdout(result="done", session_id="harness-reported-uuid")
            )

            await get_response_via_harness(
                message="go",
                working_dir="/tmp/work",
                session_id="runner-session-1",
            )

        mock_store.assert_called_once_with("runner-session-1", "harness-reported-uuid")

    @pytest.mark.asyncio
    async def test_store_claude_session_uuid_not_called_without_session_id(self):
        """No session_id supplied: the side effect is a pure no-op (no crash,
        no spurious store call)."""
        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("agent.sdk_client._store_claude_session_uuid") as mock_store,
        ):
            mock_exec.return_value = _make_mock_proc(_result_stdout(session_id="whatever"))

            await get_response_via_harness(message="go", working_dir="/tmp/work")

        mock_store.assert_not_called()


# ---------------------------------------------------------------------------
# Behavioral parity (c): #1980 retry-without-resume branch
# ---------------------------------------------------------------------------


def _make_fake_run_harness_subprocess(responses):
    """Fake replacement for _run_harness_subprocess replaying an 8-tuple per
    call, invoking on_exit_status faithfully (mirrors
    test_harness_stale_uuid_result_preservation.py's helper)."""
    state = {"i": 0, "calls": 0}

    async def _fake(cmd, working_dir, proc_env, *, on_exit_status=None, **_kw):
        spec = responses[state["i"]] if state["i"] < len(responses) else responses[-1]
        state["i"] += 1
        state["calls"] += 1
        if on_exit_status is not None:
            on_exit_status(spec["returncode"], spec["fired"])
        return (
            spec["result_text"],
            spec.get("session_id"),
            spec["returncode"],
            None,
            None,
            None,
            spec.get("num_turns", 0),
            spec.get("tool_calls", 0),
        )

    _fake.state = state  # type: ignore[attr-defined]
    return _fake


class TestRetryWithoutResumeBranchParity:
    @pytest.mark.asyncio
    async def test_retries_without_resume_on_nonzero_exit_without_result(self):
        """A resumed subprocess that exits non-zero WITHOUT a result event
        re-runs once with full_context_message and --resume stripped."""
        fake = _make_fake_run_harness_subprocess(
            [
                {"result_text": "partial", "returncode": 1, "fired": False},
                {"result_text": "fresh answer", "returncode": 0, "fired": True, "session_id": "x"},
            ]
        )
        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake),
        ):
            reply = await get_response_via_harness(
                message="continue",
                working_dir="/tmp/work",
                prior_uuid=VALID_UUID,
                full_context_message="full context for a cold retry",
            )

        assert reply == "fresh answer"
        assert fake.state["calls"] == 2, "the stale-UUID fallback must fire exactly once"

    @pytest.mark.asyncio
    async def test_no_retry_after_valid_result_nonzero_exit(self):
        """A resumed subprocess that emits a valid result event and THEN
        exits non-zero must NOT retry — the result event is the protocol's
        completion signal (issue #1980)."""
        fake = _make_fake_run_harness_subprocess(
            [
                {
                    "result_text": "the real completion",
                    "returncode": 1,
                    "fired": True,
                    "session_id": VALID_UUID,
                },
                {"result_text": "SHOULD-NOT-APPEAR", "returncode": 0, "fired": False},
            ]
        )
        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake),
        ):
            reply = await get_response_via_harness(
                message="wrap up",
                working_dir="/tmp/work",
                prior_uuid=VALID_UUID,
                full_context_message="full context",
            )

        assert reply == "the real completion"
        assert fake.state["calls"] == 1, "no retry once a result event has fired"
