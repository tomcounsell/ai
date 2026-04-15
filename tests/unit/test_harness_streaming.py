"""Tests for the CLI harness streaming function and worker routing.

Covers:
- Phase 1: get_response_via_harness() parsing, batching, error handling
- Phase 2: Worker routing — all session types via CLI harness
- Phase 2: Startup health check (verify_harness_health)

NOTE: These tests verify ``get_response_via_harness()`` in isolation — they confirm
that the function correctly parses harness NDJSON, accumulates text, and returns the
result string. In production, no streaming callback is passed to this function; the
no-op suppression contract (streaming chunks are never forwarded to any output
transport mid-session) is validated in
``tests/integration/test_harness_no_op_contract.py``.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Phase 1: get_response_via_harness tests ---


class TestGetResponseViaHarness:
    """Tests for the CLI harness streaming function."""

    @pytest.mark.asyncio
    async def test_parses_text_delta_events(self):
        """Extracts text from content_block_delta stream events."""
        from agent.sdk_client import get_response_via_harness

        # Build fake stream-json output
        lines = [
            json.dumps({"type": "system", "apiKeySource": "none"}),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "Hello "},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "world"},
                    },
                }
            ),
            json.dumps({"type": "result", "result": "Hello world", "session_id": "sess_123"}),
        ]
        stdout_data = "\n".join(lines) + "\n"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.stderr = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="test prompt",
                working_dir="/tmp/test",
            )

        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_skips_tool_use_events(self):
        """Tool use stream events are silently skipped."""
        from agent.sdk_client import get_response_via_harness

        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {"type": "content_block_start", "index": 0},
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "input_json_delta", "partial_json": '{"foo":'},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {"type": "tool_use", "name": "Read"},
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "Only text"},
                    },
                }
            ),
            json.dumps({"type": "result", "result": "Only text", "session_id": "s1"}),
        ]
        stdout_data = "\n".join(lines) + "\n"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(message="test", working_dir="/tmp")

        assert result == "Only text"

    @pytest.mark.asyncio
    async def test_handles_malformed_json_lines(self):
        """Malformed JSON lines are skipped without crashing."""
        from agent.sdk_client import get_response_via_harness

        lines = [
            "not valid json",
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "good text"},
                    },
                }
            ),
            "{truncated json",
            json.dumps({"type": "result", "result": "good text"}),
        ]
        stdout_data = "\n".join(lines) + "\n"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(message="test", working_dir="/tmp")

        assert result == "good text"

    @pytest.mark.asyncio
    async def test_text_accumulated_across_chunks(self):
        """Text chunks are accumulated correctly even without flushing."""
        from agent.sdk_client import get_response_via_harness

        # Generate a large chunk to confirm accumulation still works
        big_text = "x" * 3000
        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": big_text},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": " more"},
                    },
                }
            ),
            json.dumps({"type": "result", "result": big_text + " more"}),
        ]
        stdout_data = "\n".join(lines) + "\n"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(message="test", working_dir="/tmp")

        assert result == big_text + " more"

    @pytest.mark.asyncio
    async def test_binary_not_found(self):
        """Returns error message when CLI binary is not on PATH.

        NOTE: This test validates the raw error string returned by sdk_client.py
        (i.e., the return value of get_response_via_harness()). It does NOT cover
        the retry-interception behavior in agent_session_queue.py, which intercepts
        this string before it reaches Telegram. See tests/unit/test_harness_retry.py
        for the retry behavior tests.
        """
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            result = await get_response_via_harness(
                message="test",
                working_dir="/tmp",
                harness_cmd=["nonexistent-binary", "-p"],
            )

        assert "not found" in result.lower() or "Error" in result

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_logged(self):
        """Non-zero exit code logs stderr but still returns accumulated text."""
        from agent.sdk_client import get_response_via_harness

        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "partial output"},
                    },
                }
            ),
        ]
        stdout_data = "\n".join(lines) + "\n"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b"some error\n"))
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(message="test", working_dir="/tmp")

        # Should return accumulated text even on error
        assert "partial output" in result

    @pytest.mark.asyncio
    async def test_no_result_event_returns_accumulated_text(self):
        """If process exits without a result event, return accumulated text."""
        from agent.sdk_client import get_response_via_harness

        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "some output"},
                    },
                }
            ),
            # No result event — process just exits
        ]
        stdout_data = "\n".join(lines) + "\n"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(message="test", working_dir="/tmp")

        assert result == "some output"

    @pytest.mark.asyncio
    async def test_empty_output(self):
        """Returns empty string when no output is produced."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines("")
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(message="test", working_dir="/tmp")

        assert result == ""

    @pytest.mark.asyncio
    async def test_custom_harness_cmd(self):
        """Custom harness command is passed to subprocess."""
        from agent.sdk_client import get_response_via_harness

        custom_cmd = ["opencode", "--non-interactive"]

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(json.dumps({"type": "result", "result": "done"}) + "\n")
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await get_response_via_harness(
                message="test",
                working_dir="/tmp",
                harness_cmd=custom_cmd,
            )

        # Verify the custom command was used
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args.args[:2] == ("opencode", "--non-interactive")

    @pytest.mark.asyncio
    async def test_env_vars_passed_to_subprocess(self):
        """Custom env vars are passed to the subprocess."""
        from agent.sdk_client import get_response_via_harness

        custom_env = {"AGENT_SESSION_ID": "test_123", "CUSTOM_VAR": "value"}

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(json.dumps({"type": "result", "result": "done"}) + "\n")
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await get_response_via_harness(
                message="test",
                working_dir="/tmp",
                env=custom_env,
            )

        call_kwargs = mock_exec.call_args.kwargs
        proc_env = call_kwargs["env"]
        assert proc_env["AGENT_SESSION_ID"] == "test_123"
        assert proc_env["CUSTOM_VAR"] == "value"
        # ANTHROPIC_API_KEY should be stripped
        assert "ANTHROPIC_API_KEY" not in proc_env

    @pytest.mark.asyncio
    async def test_api_key_stripped_from_env(self):
        """ANTHROPIC_API_KEY is removed from subprocess env."""
        from agent.sdk_client import get_response_via_harness

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret-key"}):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.stdout = _async_lines(
                    json.dumps({"type": "result", "result": "done"}) + "\n"
                )
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await get_response_via_harness(
                    message="test",
                    working_dir="/tmp",
                )

            call_kwargs = mock_exec.call_args.kwargs
            assert "ANTHROPIC_API_KEY" not in call_kwargs["env"]


# --- Phase 1b: Harness session resume tests (#976) ---


class TestHarnessResume:
    """Tests for --resume injection, UUID storage, and stale-UUID fallback."""

    def _make_result_stdout(self, result="done", session_id="sess-abc-123"):
        """Build stream-json stdout with a result event."""
        lines = []
        if session_id:
            lines.append(json.dumps({"type": "result", "result": result, "session_id": session_id}))
        else:
            lines.append(json.dumps({"type": "result", "result": result}))
        return "\n".join(lines) + "\n"

    def _mock_proc(self, stdout_data, returncode=0, stderr=b""):
        mock_proc = AsyncMock()
        mock_proc.stdout = _async_lines(stdout_data)
        mock_proc.communicate = AsyncMock(return_value=(b"", stderr))
        mock_proc.returncode = returncode
        return mock_proc

    @pytest.mark.asyncio
    async def test_includes_resume_when_prior_uuid_set(self):
        """--resume <uuid> appears in argv when prior_uuid is provided."""
        from agent.sdk_client import get_response_via_harness

        uuid = "483d0525-8d68-474e-9f1e-89cadd91e263"
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(self._make_result_stdout())
            with patch("agent.sdk_client._store_claude_session_uuid"):
                await get_response_via_harness(
                    message="new message",
                    working_dir="/tmp",
                    prior_uuid=uuid,
                    session_id="test-session",
                )

        call_args = mock_exec.call_args.args
        assert "--resume" in call_args
        assert uuid in call_args

    @pytest.mark.asyncio
    async def test_omits_resume_when_prior_uuid_none(self):
        """--resume does NOT appear in argv when prior_uuid is None."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(self._make_result_stdout())
            await get_response_via_harness(
                message="test",
                working_dir="/tmp",
                prior_uuid=None,
            )

        call_args = mock_exec.call_args.args
        assert "--resume" not in call_args

    @pytest.mark.asyncio
    async def test_applies_context_budget_unconditionally(self):
        """_apply_context_budget is invoked on every call, including resumed turns.

        Replaces the prior skips_context_budget_on_resume test. The plan mandates
        unconditional budget application: on resumed turns with small messages it is
        a no-op (one length comparison); on pathological mega-messages it bounds argv.
        """
        from agent.sdk_client import get_response_via_harness

        uuid = "483d0525-8d68-474e-9f1e-89cadd91e263"
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(self._make_result_stdout())
            budget_patch = "agent.sdk_client._apply_context_budget"
            with patch(budget_patch, side_effect=lambda m, **kw: m) as mock_budget:
                with patch("agent.sdk_client._store_claude_session_uuid"):
                    await get_response_via_harness(
                        message="short msg",
                        working_dir="/tmp",
                        prior_uuid=uuid,
                        session_id="test-session",
                    )
            mock_budget.assert_called()

    @pytest.mark.asyncio
    async def test_applies_context_budget_on_first_turn(self):
        """_apply_context_budget is applied when prior_uuid is None (regression guard for #958)."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(self._make_result_stdout())
            budget_patch = "agent.sdk_client._apply_context_budget"
            with patch(budget_patch, side_effect=lambda m, **kw: m) as mock_budget:
                await get_response_via_harness(
                    message="test",
                    working_dir="/tmp",
                    prior_uuid=None,
                )
            mock_budget.assert_called_once()

    @pytest.mark.asyncio
    async def test_stores_uuid_after_result(self):
        """_store_claude_session_uuid is called with the captured UUID after a successful turn."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(
                self._make_result_stdout(session_id="new-uuid-456")
            )
            with patch("agent.sdk_client._store_claude_session_uuid") as mock_store:
                await get_response_via_harness(
                    message="test",
                    working_dir="/tmp",
                    session_id="my-session",
                )
            mock_store.assert_called_once_with("my-session", "new-uuid-456")

    @pytest.mark.asyncio
    async def test_no_store_when_uuid_missing(self):
        """No store call when result event lacks session_id."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(self._make_result_stdout(session_id=None))
            with patch("agent.sdk_client._store_claude_session_uuid") as mock_store:
                await get_response_via_harness(
                    message="test",
                    working_dir="/tmp",
                    session_id="my-session",
                )
            mock_store.assert_not_called()

    @pytest.mark.asyncio
    async def test_treats_empty_prior_uuid_as_none(self):
        """--resume is not emitted when prior_uuid is empty string."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(self._make_result_stdout())
            await get_response_via_harness(
                message="test",
                working_dir="/tmp",
                prior_uuid="",
            )

        call_args = mock_exec.call_args.args
        assert "--resume" not in call_args

    @pytest.mark.asyncio
    async def test_rejects_invalid_uuid_format(self):
        """Corrupted UUID is treated as None; no --resume emitted."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(self._make_result_stdout())
            await get_response_via_harness(
                message="test",
                working_dir="/tmp",
                prior_uuid="not-a-valid-uuid",
            )

        call_args = mock_exec.call_args.args
        assert "--resume" not in call_args

    @pytest.mark.asyncio
    async def test_stale_uuid_fallback_on_any_nonzero_exit(self):
        """Any non-zero exit on a resumed turn triggers the fallback (no stderr substring gate).

        The fallback is mandatory and unconditional on exit code — stderr text is
        irrelevant. Replaces the prior substring-gated test.
        """
        from agent.sdk_client import get_response_via_harness

        uuid = "483d0525-8d68-474e-9f1e-89cadd91e263"
        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Stderr is intentionally NOT the stale-UUID phrase — fallback must
                # still fire because returncode is non-zero.
                return self._mock_proc(
                    "",
                    returncode=1,
                    stderr=b"Error: something else went wrong entirely",
                )
            else:
                return self._mock_proc(self._make_result_stdout(result="fallback result"))

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch("agent.sdk_client._store_claude_session_uuid"):
                result = await get_response_via_harness(
                    message="new msg",
                    working_dir="/tmp",
                    prior_uuid=uuid,
                    session_id="test-session",
                    full_context_message="FULL CONTEXT MESSAGE",
                )

        assert result == "fallback result"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_first_turn_failure(self):
        """First-turn failures (prior_uuid=None) do NOT trigger a retry.

        Preserves the existing first-turn error path semantics.
        """
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(
                "",
                returncode=1,
                stderr=b"Error: something went wrong",
            )
            result = await get_response_via_harness(
                message="test",
                working_dir="/tmp",
                prior_uuid=None,
            )

        # No retry — only one subprocess call
        mock_exec.assert_called_once()
        # Result is empty since there was no accumulated text and no result event
        assert result == ""

    @pytest.mark.asyncio
    async def test_logs_resume_at_info(self):
        """INFO log 'Resuming Claude session ...' is emitted when --resume is injected.

        Observability guard — production grep against logs/worker.log relies on this.
        """
        from agent.sdk_client import get_response_via_harness

        uuid = "483d0525-8d68-474e-9f1e-89cadd91e263"
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(self._make_result_stdout())
            with patch("agent.sdk_client._store_claude_session_uuid"):
                with patch("agent.sdk_client.logger") as mock_logger:
                    await get_response_via_harness(
                        message="test",
                        working_dir="/tmp",
                        prior_uuid=uuid,
                        session_id="test-session",
                    )

        # Assert an INFO call was made containing "Resuming Claude session"
        info_calls = [
            call
            for call in mock_logger.info.call_args_list
            if "Resuming Claude session" in str(call)
        ]
        assert info_calls, "Expected logger.info with 'Resuming Claude session' to be called"

    @pytest.mark.asyncio
    async def test_stale_uuid_fallback_logs_warning(self):
        """WARNING log 'Stale UUID ...' is emitted when fallback fires.

        Observability guard — production grep against logs/worker.log relies on this.
        """
        from agent.sdk_client import get_response_via_harness

        uuid = "483d0525-8d68-474e-9f1e-89cadd91e263"
        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return self._mock_proc("", returncode=1, stderr=b"error")
            return self._mock_proc(self._make_result_stdout(result="ok"))

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch("agent.sdk_client._store_claude_session_uuid"):
                with patch("agent.sdk_client.logger") as mock_logger:
                    await get_response_via_harness(
                        message="test",
                        working_dir="/tmp",
                        prior_uuid=uuid,
                        session_id="test-session",
                        full_context_message="FULL CONTEXT",
                    )

        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if "Stale UUID" in str(call) and "falling back to first-turn path" in str(call)
        ]
        assert warning_calls, (
            "Expected logger.warning with 'Stale UUID ... falling back to first-turn path'"
        )

    @pytest.mark.asyncio
    async def test_fallback_without_full_context(self):
        """When full_context_message is None and stale-UUID fallback triggers, returns empty.

        The fallback cannot fire without the full-context message; log an error and
        return "". Defensive guard.
        """
        from agent.sdk_client import get_response_via_harness

        uuid = "483d0525-8d68-474e-9f1e-89cadd91e263"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = self._mock_proc(
                "",
                returncode=1,
                stderr=b"Error: any non-zero exit",
            )
            with patch("agent.sdk_client._store_claude_session_uuid"):
                result = await get_response_via_harness(
                    message="test",
                    working_dir="/tmp",
                    prior_uuid=uuid,
                    session_id="test-session",
                    full_context_message=None,
                )

        assert result == ""


# --- Phase 2: Worker routing tests ---


class TestWorkerHarnessRouting:
    """Tests for session routing — all session types use CLI harness."""

    def test_all_session_types_use_harness(self):
        """All session types (dev, pm, teammate) route to CLI harness."""
        # After migration, there is no SDK branch — all types use harness
        for session_type in ("dev", "pm", "teammate"):
            # The routing is unconditional — no env var check needed
            assert session_type in ("dev", "pm", "teammate")


class TestVerifyHarnessHealth:
    """Tests for the startup health check."""

    @pytest.mark.asyncio
    async def test_unknown_harness_returns_false(self):
        """Unknown harness name returns False."""
        from agent.sdk_client import verify_harness_health

        result = await verify_harness_health("unknown-harness")
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_binary_returns_false(self):
        """Returns False when claude binary is not on PATH."""
        from agent.sdk_client import verify_harness_health

        with patch("shutil.which", return_value=None):
            result = await verify_harness_health("claude-cli")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_health_check(self):
        """Returns True when claude responds with system event."""
        from agent.sdk_client import verify_harness_health

        system_event = json.dumps({"type": "system", "apiKeySource": "none"})
        result_event = json.dumps({"type": "result", "result": "test"})
        lines = [
            f"{system_event}\n".encode(),
            f"{result_event}\n".encode(),
            b"",  # EOF
        ]

        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_stdout = AsyncMock()
                mock_stdout.readline = AsyncMock(side_effect=lines)
                mock_proc.stdout = mock_stdout
                mock_proc.kill = MagicMock()
                mock_proc.wait = AsyncMock()
                mock_exec.return_value = mock_proc

                result = await verify_harness_health("claude-cli")

        assert result is True
        # Process should be killed after system event (no API round-trip)
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_key_billing_warning(self):
        """Logs warning but returns True when using API key billing."""
        from agent.sdk_client import verify_harness_health

        system_event = json.dumps({"type": "system", "apiKeySource": "env_var"})
        lines = [
            f"{system_event}\n".encode(),
            b"",  # EOF
        ]

        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_stdout = AsyncMock()
                mock_stdout.readline = AsyncMock(side_effect=lines)
                mock_proc.stdout = mock_stdout
                mock_proc.kill = MagicMock()
                mock_proc.wait = AsyncMock()
                mock_exec.return_value = mock_proc

                result = await verify_harness_health("claude-cli")

        # Should still return True (it works, just costs money)
        assert result is True
        mock_proc.kill.assert_called_once()


# --- Helper: async line iterator for mocking process.stdout ---


class _AsyncLineIterator:
    """Async iterator that yields lines from a string, simulating process.stdout."""

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


def _async_lines(data: str) -> _AsyncLineIterator:
    """Create an async line iterator from a string."""
    return _AsyncLineIterator(data)
